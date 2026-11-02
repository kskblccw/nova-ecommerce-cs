"""对话服务测试：SSE 编码、追踪配置与早退路径（不依赖数据库）。"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from ecommerce_cs.services import chat as chat_service


def test_sse_event_format():
    chunk = chat_service.sse("token", {"text": "你好"})

    assert chunk.startswith("event: token\ndata: ")
    assert chunk.endswith("\n\n")
    payload = json.loads(chunk.split("data: ", 1)[1].strip())
    assert payload == {"text": "你好"}


def test_sse_serializes_datetime():
    chunk = chat_service.sse("done", {"at": datetime(2026, 1, 1, tzinfo=UTC)})
    assert "2026-01-01" in chunk


def test_thread_config_contains_trace_metadata():
    config = chat_service._thread_config("abc", user_id=7)

    assert config["configurable"]["thread_id"] == "conv:abc"
    assert config["run_name"] == "chat.turn"
    assert config["tags"] == ["chat"]
    assert config["metadata"] == {"user_id": 7}


def test_thread_config_can_name_approval_run():
    config = chat_service._thread_config("abc", run_name="chat.approval", user_id=7)

    assert config["run_name"] == "chat.approval"
    assert config["configurable"]["thread_id"] == "conv:abc"


def test_last_ai_ignores_narration_only_reply():
    messages = [HumanMessage(content="查订单"), AIMessage(content="正在查询您的订单信息。")]
    assert chat_service._last_ai(messages) == ""


def test_last_ai_picks_latest_after_last_human():
    messages = [
        HumanMessage(content="第一轮"),
        AIMessage(content="第一轮回答"),
        HumanMessage(content="第二轮"),
        AIMessage(content="第二轮回答"),
    ]
    assert chat_service._last_ai(messages) == "第二轮回答"


def test_conversation_locks_are_isolated_per_conversation():
    first = chat_service.conversation_lock("c1")
    again = chat_service.conversation_lock("c1")
    other = chat_service.conversation_lock("c2")

    assert first is again
    assert first is not other


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def test_idle_lock_is_recycled_after_ttl(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(chat_service, "time", clock)

    first = chat_service.conversation_lock("idle-lock")
    clock.now = chat_service._LOCK_TTL_SECONDS + 1
    second = chat_service.conversation_lock("idle-lock")

    assert second is not first


@pytest.mark.asyncio
async def test_held_lock_is_never_recycled(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(chat_service, "time", clock)

    lock = chat_service.conversation_lock("busy-lock")
    await lock.acquire()
    try:
        clock.now = chat_service._LOCK_TTL_SECONDS + 1
        assert chat_service.conversation_lock("busy-lock") is lock
    finally:
        lock.release()


class _PendingGraph:
    async def aget_state(self, config):
        return SimpleNamespace(tasks=[SimpleNamespace(interrupts=[{"value": {}}])])


@pytest.mark.asyncio
async def test_stream_chat_refuses_when_pending_action_exists(monkeypatch):
    monkeypatch.setattr(chat_service, "get_graph", lambda: _PendingGraph())
    user = SimpleNamespace(id=1)
    conversation = SimpleNamespace(id="c1")

    chunks = [chunk async for chunk in chat_service.stream_chat(user, conversation, "你好")]

    assert len(chunks) == 1
    payload = json.loads(chunks[0].split("data: ", 1)[1].strip())
    assert payload["code"] == "pending_approval"
    assert payload["retryable"] is False
