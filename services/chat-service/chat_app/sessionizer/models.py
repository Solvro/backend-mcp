from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MessageRole(StrEnum):
    """Role of a message author within a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(BaseModel):
    """A single message, stored as one document in the messages collection."""

    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    session_id: str
    role: MessageRole
    content: str
    timestamp: datetime = Field(default_factory=utcnow)
    metadata: dict = Field(default_factory=dict)
    expires_at: datetime | None = None


class Conversation(BaseModel):
    """Session metadata document in the conversation collection."""

    session_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str | None = None  # None for anonymous conversations
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    message_count: int = 0
    metadata: dict = Field(default_factory=dict)
    is_active: bool = True
    expires_at: datetime | None = None
