import re
import os
import logging
from datetime import datetime, timezone
from typing import Callable, Any, Dict, List, Optional
from sqlmodel import Session

from backend.models import ChatMessage, ChatSession
from backend.app_manager import AppManager
from backend.context_manager import ContextManager
from backend.agent_parser import parse_widget_from_text
from backend.opencode_service import run_opencode_agent_acp
from backend.agent.router import IntentRouter
from backend.agent.providers import get_llm_provider
from backend.agent.prompts.manager import PromptManager
from backend.agent.tools import registry as tool_registry

logger = logging.getLogger("agent.harness")

class AgentOrchestrator:
    """
    OpenClaw-inspired main orchestrator. Coordinates user sessions, 
    intent routing, memory/context assembly, tool calling, and providers.
    """
    def __init__(self, db_session: Session, app_manager: AppManager, run_opencode_agent_acp_fn=None):
        self.db = db_session
        self.app_manager = app_manager
        self.context_manager = ContextManager(db_session=db_session, app_manager=app_manager)
        self.run_opencode_agent_acp_fn = run_opencode_agent_acp_fn or run_opencode_agent_acp

    async def handle_message(
        self,
        session_id: str,
        content: str,
        on_update: Callable[[str], Any]
    ) -> tuple[ChatMessage, Optional[dict[str, Any]]]:
        # 1. Fetch or initialize user session metadata
        db_session_obj = self.db.get(ChatSession, session_id)
        if not db_session_obj:
            db_session_obj = ChatSession(id=session_id, title="Active Chat")
            self.db.add(db_session_obj)
            self.db.commit()
            self.db.refresh(db_session_obj)

        # Update updated_at timestamp
        db_session_obj.updated_at = datetime.now(timezone.utc)
        self.db.add(db_session_obj)
        self.db.commit()

        # 2. Classify intent via IntentRouter
        existing_apps = self.app_manager.list_apps()
        try:
            is_coding, app_id, instruction = await IntentRouter.route(content, existing_apps, db_session=self.db)
        except Exception as e:
            # Save error message to DB and return to client
            error_msg = ChatMessage(
                session_id=session_id,
                role="agent",
                sender="agent",
                content=f"⚠️ 意图路由分类失败：无法连接大模型服务或解析返回结果。错误信息：{str(e)}"
            )
            self.db.add(error_msg)
            self.db.commit()
            self.db.refresh(error_msg)
            return error_msg, None

        if is_coding:
            # Spawns OpenCode agent via ACP mode
            status_text = f"🛠️ Starting OpenCode agent to process request for app '{app_id}'...\nThis might take a moment."
            await self._run_callback(on_update, status_text)
            
            cli_output = await self.run_opencode_agent_acp_fn(app_id, instruction, on_update=on_update)
            
            # Save agent run logs
            agent_msg = ChatMessage(
                session_id=session_id,
                role="agent",
                sender="agent",
                content=f"OpenCode Execution Log:\n\n```\n{cli_output}\n```"
            )
            self.db.add(agent_msg)
            
            # Retrieve updated widget details from disk
            widget_to_send = self.app_manager.get_app_files(app_id)
            if widget_to_send:
                code_msg = ChatMessage(
                    session_id=session_id,
                    role="code",
                    sender="agent",
                    content=(
                        f'<ambient-widget id="{widget_to_send["id"]}" title="{widget_to_send["title"]}">\n'
                        f'<html-content>\n{widget_to_send["html"]}\n</html-content>\n'
                        f'<css-styles>\n{widget_to_send["css"]}\n</css-styles>\n'
                        f'<js-script>\n{widget_to_send["js"]}\n</js-script>\n'
                        f'</ambient-widget>'
                    )
                )
                self.db.add(code_msg)
                
                # Dynamic visual title extraction
                title = app_id.replace("-", " ").title()
                title_match = re.search(r"<title>(.*?)</title>", widget_to_send["html"], re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                
                self.app_manager.create_or_update_app(
                    app_id=widget_to_send["id"],
                    title=title,
                    html=widget_to_send["html"],
                    css=widget_to_send["css"],
                    js=widget_to_send["js"]
                )
                widget_to_send = self.app_manager.get_app_files(app_id)

            self.db.commit()
            self.db.refresh(agent_msg)

            return agent_msg, widget_to_send

        else:
            # Conversational pipeline via standard LLMProvider
            await self._run_callback(on_update, "🤔 Thinking...")

            provider_name = os.getenv("LLM_PROVIDER", "ollama")
            model_name = os.getenv("LLM_MODEL", "llama3")
            provider = get_llm_provider(provider_name, model_name)

            pm = PromptManager()
            agent_system_prompt = pm.get_prompt("agent_system.md")

            llm_prompt_messages = self.context_manager.build_llm_prompt(session_id)
            llm_prompt_messages.insert(0, {"role": "system", "content": agent_system_prompt})

            tools = tool_registry.get_tool_schemas()
            raw_response = await provider.generate(
                messages=llm_prompt_messages,
                db_session=self.db,
                tools=tools
            )
            widget_to_send = parse_widget_from_text(raw_response)

            if widget_to_send:
                reply_content = re.sub(r"<ambient-widget.*?>.*?</ambient-widget>", "", raw_response, flags=re.DOTALL).strip()
            else:
                reply_content = raw_response

            agent_msg = ChatMessage(
                session_id=session_id,
                role="agent",
                sender="agent",
                content=reply_content
            )
            self.db.add(agent_msg)

            if widget_to_send:
                code_msg = ChatMessage(
                    session_id=session_id,
                    role="code",
                    sender="agent",
                    content=raw_response
                )
                self.db.add(code_msg)

                self.app_manager.create_or_update_app(
                    app_id=widget_to_send["id"],
                    title=widget_to_send["title"],
                    html=widget_to_send["html"],
                    css=widget_to_send["css"],
                    js=widget_to_send["js"]
                )

            self.db.commit()
            self.db.refresh(agent_msg)

            return agent_msg, widget_to_send

    async def _run_callback(self, callback: Callable[[Any], Any], data: Any) -> None:
        try:
            import inspect
            if inspect.iscoroutinefunction(callback):
                await callback(data)
            else:
                callback(data)
        except Exception as e:
            logger.error(f"Error in execution loop callback: {e}")
