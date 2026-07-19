from unittest.mock import AsyncMock

import pytest

from backend.models import ChatSession
from backend.session_title import SessionTitleService, sanitize_title
from backend.workspace_storage import WorkspaceStorage


def test_sanitize_title_removes_wrappers_and_limits_length():
    assert sanitize_title('  "规划上海周末旅行。"\n更多文字  ', "zh") == "规划上海周末旅行"
    assert len(sanitize_title("x" * 80, "en")) == 48


@pytest.mark.asyncio
async def test_generates_sanitizes_and_persists_llm_session_title(tmp_path):
    storage = WorkspaceStorage(str(tmp_path))
    storage.add(ChatSession(id="session-1", title="新对话", language="zh"))
    storage.commit()
    llm_call = AsyncMock(return_value={"content": '"规划上海周末旅行"', "tool_calls": None})
    service = SessionTitleService(storage, llm_call=llm_call)

    title = await service.generate("session-1", "帮我计划周末上海行程", "zh")

    assert title == "规划上海周末旅行"
    assert storage.get(ChatSession, "session-1").title == title
    assert storage.get_audit_logs()[0].stage == "session_title"


@pytest.mark.asyncio
async def test_does_not_overwrite_an_existing_session_title(tmp_path):
    storage = WorkspaceStorage(str(tmp_path))
    storage.add(ChatSession(id="session-1", title="已有标题", language="zh"))
    storage.commit()
    llm_call = AsyncMock()
    service = SessionTitleService(storage, llm_call=llm_call)

    assert await service.generate("session-1", "新消息", "zh") is None
    llm_call.assert_not_awaited()
