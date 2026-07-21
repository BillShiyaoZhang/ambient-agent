"""Read-only agent helpers used by the durable Run reducer.

Execution ownership lives in :mod:`backend.run_service` and
:mod:`backend.agent.durable_workflow`.  This module deliberately contains no
workflow loop, approval Future, graph mutation, or artifact publication path.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable
from typing import Any

from backend.agent.errors import BudgetExhaustedError, WorkflowError
from backend.agent.intent_plan import IntentKind, IntentPlan
from backend.agent.providers import ToolLoopBudget, get_llm_provider
from backend.agent.router import IntentRouter
from backend.agent.run_context import RunContext
from backend.agent.tools import ToolEffect, registry as tool_registry
from backend.agent_parser import parse_widget_from_text
from backend.app_manager import AppManager
from backend.context_manager import ContextManager
from backend.llm_config import LLMConfigError
from backend.llm_runtime import primary_selection, selection_ids
from backend.models import ChatMessage, ChatSession
from backend.workspace_storage import WorkspaceStorage

logger = logging.getLogger("agent.harness")


class AgentOrchestrator:
    """Router and bounded read-only Converse helper for one reducer step."""

    def __init__(
        self,
        db_session: WorkspaceStorage,
        app_manager: AppManager,
        run_context: RunContext | None = None,
        context_summary: str | None = None,
        artifact_ids: list[str] | None = None,
        tool_loop_budget: ToolLoopBudget | None = None,
    ) -> None:
        self.db = db_session
        self.app_manager = app_manager
        self.context_manager = ContextManager(db_session=db_session, app_manager=app_manager)
        self.run_context = run_context
        self.context_summary = context_summary
        self.artifact_ids = artifact_ids
        self.tool_loop_budget = tool_loop_budget

    async def handle_message(
        self,
        session_id: str,
        content: str,
        on_update: Callable[[Any], Any],
    ) -> tuple[ChatMessage, dict[str, Any] | None]:
        """Compatibility entry point restricted to side-effect-free outcomes.

        Mutating, composite, and widget intents must be submitted as a durable
        ``internal_agent`` Run; direct execution is intentionally rejected.
        """

        chat_session = self.db.get(ChatSession, session_id)
        language = chat_session.language if isinstance(chat_session, ChatSession) else "zh"
        plan = await self._classify_intent(content, session_id=session_id, language=language)
        if plan.kind == IntentKind.CLARIFY:
            message = ChatMessage(
                session_id=session_id,
                role="agent",
                sender="agent",
                content=plan.clarification_message
                or ("请提供更多信息。" if language == "zh" else "Please provide more details."),
            )
            self.db.add(message)
            self.db.commit()
            self.db.refresh(message)
            return message, None
        if plan.kind != IntentKind.CONVERSE:
            raise WorkflowError(
                "This intent requires the durable Run workflow",
                code="durable_workflow_required",
            )
        return await self._handle_converse(
            plan=plan,
            session_id=session_id,
            content=content,
            language=language,
            on_update=on_update,
        )

    async def _classify_intent(self, content: str, session_id: str, language: str = "zh") -> IntentPlan:
        router_context = None
        try:
            from backend.graph_db import create_graph_database
            from backend.router_context import RouterContext

            graph_db = create_graph_database(os.getenv("WORKSPACE_DIR", "workspace"))
            session_messages = [
                {"role": message.role, "content": message.content} for message in self.db.get_messages(session_id)
            ]
            router_context = RouterContext.build(
                app_manager=self.app_manager,
                graph_db=graph_db,
                session_messages=session_messages,
                recent_messages_count=5,
                session_summary=self.context_summary,
            )
        except Exception:
            logger.warning("Unable to build router context", exc_info=True)

        try:
            audit_context = self.run_context.audit_context(stage="route") if self.run_context else None
            plan = await IntentRouter.route(
                content,
                router_context,
                db_session=self.db,
                language=language,
                audit_context=audit_context,
                budget=self.tool_loop_budget,
            )
            if plan.kind in {IntentKind.MULTI_INTENT, IntentKind.PLAN_AND_ACT}:
                plan = await IntentRouter.refine_sub_intents(
                    plan,
                    router_context,
                    db_session=self.db,
                    language=language,
                    audit_context=audit_context,
                    budget=self.tool_loop_budget,
                )
            return plan
        except (LLMConfigError, BudgetExhaustedError):
            raise
        except Exception:
            # Routing failure is safe to degrade only to read-only Converse.
            logger.exception("Intent classification failed; degrading to Converse")
            return IntentPlan(
                kind=IntentKind.CONVERSE,
                confidence=0.0,
                rationale="routing failed",
                instruction=content,
            )

    async def _handle_converse(
        self,
        plan: IntentPlan,
        session_id: str,
        content: str,
        language: str,
        on_update: Callable[[Any], Any],
    ) -> tuple[ChatMessage, None]:
        del plan, content
        await self._run_callback(on_update, "🤔 思考中..." if language == "zh" else "🤔 Thinking...")

        provider_name, model_name = selection_ids(primary_selection())
        provider = get_llm_provider(provider_name, model_name)

        from backend.agent.prompts.manager import PromptManager

        system_prompt = PromptManager().get_prompt("agent_system.md", language=language)
        messages = self.context_manager.build_llm_prompt(
            session_id,
            context_summary=self.context_summary,
            artifact_ids=self.artifact_ids,
        )
        messages.insert(0, {"role": "system", "content": system_prompt})

        tools = tool_registry.get_tool_schemas(
            allowed_effects={ToolEffect.READ},
            scopes={"workspace:read"},
        )
        tool_context = (
            self.run_context.tool_context(scopes={"workspace:read"}, on_event=on_update)
            if self.run_context
            else {
                "session_id": session_id,
                "scopes": {"workspace:read"},
                "on_event": on_update,
            }
        )
        raw_response = await provider.generate(
            messages=messages,
            db_session=self.db,
            tools=tools,
            tool_context=tool_context,
            budget=self.tool_loop_budget,
            audit_context=self.run_context.audit_context(stage="converse") if self.run_context else None,
        )
        if parse_widget_from_text(raw_response):
            raise WorkflowError(
                "Converse produced an unverified App artifact; route UI generation through the widget workflow",
                code="unverified_inline_artifact",
            )

        message = ChatMessage(
            session_id=session_id,
            role="agent",
            sender="agent",
            content=raw_response,
        )
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message, None

    @staticmethod
    async def _run_callback(callback: Callable[[Any], Any], data: Any) -> None:
        try:
            result = callback(data)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("Agent update callback failed", exc_info=True)

    @staticmethod
    def _summarize_actions(actions: list[dict[str, Any]], language: str = "zh") -> str:
        is_zh = language == "zh"
        parts: list[str] = []
        for action in actions:
            kind = action.get("action")
            if kind == "create_node":
                title = (action.get("properties") or {}).get("title") or action.get("id")
                parts.append(
                    f"已新建 {action.get('type', '节点')}『{title}』"
                    if is_zh
                    else f"Created {action.get('type', 'node')} '{title}'"
                )
            elif kind == "update_node_property":
                parts.append(f"已更新节点 `{action.get('id')}`" if is_zh else f"Updated node '{action.get('id')}'")
            elif kind == "delete_node":
                parts.append(f"已删除节点 `{action.get('id')}`" if is_zh else f"Deleted node '{action.get('id')}'")
            elif kind == "create_edge":
                parts.append(
                    f"已创建关联 {action.get('from_id')} → {action.get('to_id')}"
                    if is_zh
                    else f"Created link {action.get('from_id')} → {action.get('to_id')}"
                )
            elif kind == "delete_edge":
                parts.append(
                    f"已删除关联 {action.get('from_id')} → {action.get('to_id')}"
                    if is_zh
                    else f"Deleted link {action.get('from_id')} → {action.get('to_id')}"
                )
        if not parts:
            return "已执行图形操作" if is_zh else "Applied graph mutation"
        if len(parts) == 1:
            return parts[0]
        return ("已完成：" if is_zh else "Completed: ") + "; ".join(parts)
