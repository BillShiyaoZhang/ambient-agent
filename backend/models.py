from datetime import UTC, datetime

from pydantic import BaseModel, Field


class ChatSession(BaseModel):
    id: str
    title: str
    language: str = "zh"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ChatMessage(BaseModel):
    id: int | None = None
    session_id: str | None = "default-session"
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
