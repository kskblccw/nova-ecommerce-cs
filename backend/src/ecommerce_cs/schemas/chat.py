"""会话与消息模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class MessagePart(BaseModel):
    kind: str
    summary: str = ""
    data: Any = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    parts: list[MessagePart] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class MessagePage(BaseModel):
    items: list[MessageOut] = Field(default_factory=list)
    has_more: bool = False
    next_cursor: datetime | None = None


class ConversationOut(BaseModel):
    id: str
    title: str
    created_at: datetime
    last_message_at: datetime | None = None
    message_count: int = 0
    preview: str = ""


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", max_length=120)


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    client_message_id: str | None = Field(
        default=None,
        min_length=6,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )

    @field_validator("message")
    @classmethod
    def _strip_message(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("消息不能为空")
        return cleaned


class ApprovalRequest(BaseModel):
    decision: Literal["approve", "reject"]


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)
    comment: str = Field(default="", max_length=500)


class PendingActionOut(BaseModel):
    has_pending: bool
    action_id: str = ""
    type: str = ""
    summary: str = ""
    payload: dict = Field(default_factory=dict)
