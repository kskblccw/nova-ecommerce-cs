"""Agent 状态与结构化模型。"""

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class IntentResult(BaseModel):
    """意图分类的结构化输出。"""

    intent: Literal["order", "product", "aftersale", "faq", "human"] = Field(
        description="用户意图"
    )


class PendingActionPayload(BaseModel):
    """需要用户确认的变更操作。"""

    type: Literal["address_change", "cancel_order", "create_return", "cancel_return"]
    summary: str
    payload: dict = Field(default_factory=dict)
    idempotency_key: str


class CSRState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    intent: str
    pending_action: dict | None
    parts: list[dict]
    steps: int
    degraded: bool
