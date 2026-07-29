from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.llm_config import ModelSelection


class ChatSession(BaseModel):
    id: str
    title: str
    language: str = "zh"
    model_selection: ModelSelection | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessage(BaseModel):
    id: int | None = None
    session_id: str | None = "default-session"
    run_id: str | None = None
    role: str = "user"  # 'user', 'agent', 'code', 'system', 'tool_call'
    sender: str = "user"  # 'user' or 'agent', kept for compatibility
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class LLMAuditLog(BaseModel):
    id: int | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: str
    model: str
    prompt: str
    response: str
    stage: str = "chat"  # e.g. chat | route | plan | mutation | verify
    run_id: str | None = None
    session_id: str | None = None
    step_id: str | None = None
    attempt: int | None = None
    trace_id: str | None = None
    latency_ms: float | None = None
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    error: str | None = None
    prompt_hash: str | None = None
    tool_schema_hash: str | None = None
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
