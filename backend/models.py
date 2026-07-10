from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

class ChatSession(BaseModel):
    id: str
    title: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class ChatMessage(BaseModel):
    id: Optional[int] = None
    session_id: Optional[str] = "default-session"
    role: str = "user"  # 'user', 'agent', 'code', 'system', 'tool_call'
    sender: str = "user"  # 'user' or 'agent', kept for compatibility
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class LLMAuditLog(BaseModel):
    id: Optional[int] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    provider: str
    model: str
    prompt: str
    response: str
