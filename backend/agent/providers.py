import os
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any
from sqlmodel import Session

from backend.models import LLMAuditLog
import backend.llm_service

logger = logging.getLogger("agent.providers")

class BaseLLMProvider(ABC):
    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        db_session: Optional[Session] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """
        Generates a completion response given the list of chat messages.
        Logs the query in the LLMAuditLog if a db_session is provided.
        """
        pass

    def _log_to_db(self, db_session: Optional[Session], provider: str, prompt: List[Dict[str, str]], response: str) -> None:
        if db_session is None:
            return
        try:
            try:
                prompt_str = json.dumps(prompt, ensure_ascii=False)
            except Exception:
                prompt_str = str(prompt)

            audit_log = LLMAuditLog(
                provider=provider,
                model=self.model,
                prompt=prompt_str,
                response=response
            )
            db_session.add(audit_log)
            db_session.commit()
            db_session.refresh(audit_log)
        except Exception as e:
            logger.error(f"Failed to log LLM transaction: {e}")

    async def _run_tool_loop(
        self,
        provider_name: str,
        messages: List[Dict[str, str]],
        db_session: Optional[Session] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        from backend.agent.tools import registry as tool_registry
        
        local_messages = list(messages)
        max_iterations = 5
        content = ""
        
        for iteration in range(max_iterations):
            func = backend.llm_service.call_llm_api
            import inspect
            try:
                sig = inspect.signature(func)
                accepts_tools = len(sig.parameters) >= 4 or any(
                    p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                    for p in sig.parameters.values()
                )
            except Exception:
                accepts_tools = True

            if accepts_tools:
                response_data = await func(
                    provider_name,
                    self.model,
                    local_messages,
                    tools
                )
            else:
                response_data = await func(
                    provider_name,
                    self.model,
                    local_messages
                )

            if isinstance(response_data, str):
                response_data = {"content": response_data, "tool_calls": None}
            
            content = response_data.get("content", "")
            tool_calls = response_data.get("tool_calls", None)
            
            # Log this specific LLM call to DB
            self._log_to_db(db_session, provider_name, local_messages, content)
            
            if not tool_calls:
                return content
                
            # Append assistant message with tool calls to prompt history
            assistant_msg = {
                "role": "assistant",
                "content": content or "",
                "tool_calls": tool_calls
            }
            local_messages.append(assistant_msg)
            
            # Execute each requested tool call
            for tool_call in tool_calls:
                tool_name = tool_call.get("function", {}).get("name")
                tool_args_raw = tool_call.get("function", {}).get("arguments", {})
                tool_id = tool_call.get("id")
                
                # Parse arguments
                if isinstance(tool_args_raw, str):
                    try:
                        tool_args = json.loads(tool_args_raw)
                    except Exception as e:
                        logger.error(f"Failed to parse tool arguments JSON: {e}")
                        tool_args = {}
                else:
                    tool_args = tool_args_raw or {}
                    
                logger.info(f"Executing tool '{tool_name}' with args {tool_args}")
                try:
                    tool_context = {"db_session": db_session}
                    result = await tool_registry.execute(tool_name, tool_args, context=tool_context)
                    result_str = str(result)
                except Exception as e:
                    logger.error(f"Error executing tool '{tool_name}': {e}")
                    result_str = f"Error: {str(e)}"
                    
                # Append tool response message to history
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": tool_name,
                    "content": result_str
                }
                local_messages.append(tool_msg)
                
        logger.warning(f"Tool loop exceeded maximum iterations ({max_iterations})")
        return content


class OllamaProvider(BaseLLMProvider):
    async def generate(
        self,
        messages: List[Dict[str, str]],
        db_session: Optional[Session] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        return await self._run_tool_loop("ollama", messages, db_session, tools)


class CloudLLMProvider(BaseLLMProvider):
    async def generate(
        self,
        messages: List[Dict[str, str]],
        db_session: Optional[Session] = None,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        provider_name = os.getenv("LLM_PROVIDER", "openai")
        return await self._run_tool_loop(provider_name, messages, db_session, tools)


def get_llm_provider(provider_name: str, model_name: str) -> BaseLLMProvider:
    if provider_name.lower() == "ollama":
        return OllamaProvider(model=model_name)
    else:
        return CloudLLMProvider(model=model_name)
