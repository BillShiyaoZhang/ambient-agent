import os
import json
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from sqlmodel import Session

from backend.models import LLMAuditLog
import backend.llm_service

logger = logging.getLogger("agent.providers")

class BaseLLMProvider(ABC):
    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], db_session: Optional[Session] = None) -> str:
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


class OllamaProvider(BaseLLMProvider):
    async def generate(self, messages: List[Dict[str, str]], db_session: Optional[Session] = None) -> str:
        response_text = await backend.llm_service.call_llm_api("ollama", self.model, messages)
        self._log_to_db(db_session, "ollama", messages, response_text)
        return response_text


class CloudLLMProvider(BaseLLMProvider):
    async def generate(self, messages: List[Dict[str, str]], db_session: Optional[Session] = None) -> str:
        provider_name = os.getenv("LLM_PROVIDER", "openai")
        response_text = await backend.llm_service.call_llm_api(provider_name, self.model, messages)
        self._log_to_db(db_session, provider_name, messages, response_text)
        return response_text


def get_llm_provider(provider_name: str, model_name: str) -> BaseLLMProvider:
    if provider_name.lower() == "ollama":
        return OllamaProvider(model=model_name)
    else:
        return CloudLLMProvider(model=model_name)
