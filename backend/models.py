from datetime import datetime, timezone
from typing import Optional
from sqlmodel import SQLModel, Field

class ChatSession(SQLModel, table=True):
    id: str = Field(primary_key=True)
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: Optional[str] = Field(default="default-session", foreign_key="chatsession.id", index=True, nullable=True)
    role: str = Field(default="user")  # 'user', 'agent', 'code', 'system', 'tool_call'
    sender: str = Field(default="user")  # 'user' or 'agent', kept for compatibility
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class LLMAuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str
    model: str
    prompt: str
    response: str
