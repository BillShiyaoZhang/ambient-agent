import json
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any

import backend.llm_service
from backend.models import ChatSession, LLMAuditLog
from backend.workspace_storage import WorkspaceStorage


PLACEHOLDER_TITLES = {"新对话", "New conversation", "Active Chat"}


def is_placeholder_title(title: str) -> bool:
    normalized = (title or "").strip()
    return normalized in PLACEHOLDER_TITLES or normalized.startswith("New Chat ")


def sanitize_title(raw_title: str, language: str) -> str:
    first_line = next((line.strip() for line in (raw_title or "").splitlines() if line.strip()), "")
    cleaned = re.sub(r"^(?:标题|Title)\s*[:：]\s*", "", first_line, flags=re.IGNORECASE)
    cleaned = cleaned.strip().strip('"\'`“”‘’')
    cleaned = cleaned.rstrip("。.!！?？;；:：").strip()
    return cleaned[:48]


class SessionTitleService:
    def __init__(
        self,
        storage: WorkspaceStorage,
        llm_call: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.storage = storage
        self.llm_call = llm_call or backend.llm_service.call_llm_api

    @staticmethod
    def sanitize_title(raw_title: str, language: str) -> str:
        return sanitize_title(raw_title, language)

    async def generate(self, session_id: str, first_message: str, language: str) -> str | None:
        chat_session = self.storage.get(ChatSession, session_id)
        if not chat_session or not is_placeholder_title(chat_session.title):
            return None

        is_zh = language == "zh"
        instruction = (
            "为这段对话生成一个简洁、具体的中文标题。只返回标题，不要引号或解释，长度为2到8个中文词语。"
            if is_zh
            else "Create a concise, specific title for this conversation. Return only 3 to 7 words with no quotes or explanation."
        )
        messages = [
            {"role": "system", "content": instruction},
            {"role": "user", "content": first_message[:2000]},
        ]
        provider = os.getenv("LLM_PROVIDER", "ollama")
        model = os.getenv("LLM_MODEL", "llama3")
        response = await self.llm_call(provider, model, messages)
        raw_title = response.get("content", "") if isinstance(response, dict) else str(response)
        title = sanitize_title(raw_title, language)
        if not title or title.lower().startswith(("error", "failed", "mock response")):
            return None

        current = self.storage.get(ChatSession, session_id)
        if not current or not is_placeholder_title(current.title):
            return None
        current.title = title
        self.storage.add(current)
        self.storage.add(
            LLMAuditLog(
                provider=provider,
                model=model,
                prompt=json.dumps(messages, ensure_ascii=False),
                response=raw_title,
                stage="session_title",
            )
        )
        self.storage.commit()
        return title
