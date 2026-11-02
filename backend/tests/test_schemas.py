"""请求/响应模型契约测试。"""

import pytest
from pydantic import ValidationError

from ecommerce_cs.schemas.chat import (
    ApprovalRequest,
    ChatRequest,
    ConversationCreate,
    FeedbackRequest,
    MessagePage,
)


def test_chat_request_strips_whitespace():
    assert ChatRequest(message="  你好  ").message == "你好"


def test_chat_request_rejects_blank_message():
    with pytest.raises(ValidationError):
        ChatRequest(message="   \n  ")


def test_chat_request_rejects_overlong_message():
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 2001)


def test_client_message_id_accepts_uuid_like_value():
    request = ChatRequest(
        message="hi", client_message_id="4f6c1b9e-8a2d-4c7e-9f10-2b3c4d5e6f70"
    )
    assert request.client_message_id == "4f6c1b9e-8a2d-4c7e-9f10-2b3c4d5e6f70"


@pytest.mark.parametrize("bad", ["with space", "中文", "a" * 65, "short"])
def test_client_message_id_rejects_invalid_value(bad):
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", client_message_id=bad)


def test_message_page_defaults():
    page = MessagePage()
    assert page.items == []
    assert page.has_more is False
    assert page.next_cursor is None


def test_conversation_create_defaults_to_new_title():
    assert ConversationCreate().title == "新会话"


def test_conversation_title_length_limit():
    with pytest.raises(ValidationError):
        ConversationCreate(title="x" * 121)


def test_feedback_rating_bounds():
    with pytest.raises(ValidationError):
        FeedbackRequest(rating=2)
    with pytest.raises(ValidationError):
        FeedbackRequest(rating=-2)
    assert FeedbackRequest(rating=-1).rating == -1


def test_approval_decision_only_accepts_literal():
    with pytest.raises(ValidationError):
        ApprovalRequest(decision="maybe")
