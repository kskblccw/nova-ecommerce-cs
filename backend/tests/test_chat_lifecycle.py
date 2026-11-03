"""对话生命周期测试：审批恢复、流式取消、心跳与取消操作的确定性预执行。"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from ecommerce_cs.agents import graph as graph_module
from ecommerce_cs.api.routers import chat as chat_router
from ecommerce_cs.core.errors import AppError
from ecommerce_cs.services import chat as chat_service


def _user(user_id: int = 7) -> SimpleNamespace:
    return SimpleNamespace(id=user_id)


def _conversation(conversation_id: str = "c1") -> SimpleNamespace:
    return SimpleNamespace(id=conversation_id, title="新会话")


# ── 审批恢复 ──────────────────────────────────────────────


class _FakeGraph:
    def __init__(self, state, result=None):
        self._state = state
        self._result = result

    async def aget_state(self, _config):
        return self._state

    async def ainvoke(self, _command, config=None, context=None):
        return self._result


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


def _patch_chat_storage(monkeypatch, stored_id: str = "m1") -> None:
    async def fake_add_message(_db, **kwargs):
        return SimpleNamespace(id=stored_id, content=kwargs.get("content", ""))

    async def fake_touch(_db, _conversation_id):
        return None

    monkeypatch.setattr(chat_service, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(chat_service.chat_repo, "add_message", fake_add_message)
    monkeypatch.setattr(chat_service.chat_repo, "touch_conversation", fake_touch)


@pytest.mark.asyncio
async def test_resolve_approval_rejects_when_no_interrupt(monkeypatch):
    monkeypatch.setattr(chat_service, "get_graph", lambda: _FakeGraph(None))

    with pytest.raises(AppError) as exc:
        await chat_service.resolve_approval(_user(), _conversation(), "approve")

    assert exc.value.code == "no_pending"


@pytest.mark.asyncio
async def test_resolve_approval_persists_assistant_answer(monkeypatch):
    state = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[{"value": {}}])])
    result = {
        "messages": [AIMessage(content="已为您取消订单。")],
        "parts": [{"kind": "info", "summary": "取消结果"}],
    }
    monkeypatch.setattr(chat_service, "get_graph", lambda: _FakeGraph(state, result))
    _patch_chat_storage(monkeypatch)

    payload = await chat_service.resolve_approval(_user(), _conversation(), "approve")

    assert payload["decision"] == "approve"
    assert payload["message_id"] == "m1"
    assert payload["content"] == "已为您取消订单。"
    assert payload["parts"] == result["parts"]


@pytest.mark.asyncio
async def test_resolve_approval_falls_back_when_model_has_no_answer(monkeypatch):
    state = SimpleNamespace(tasks=[SimpleNamespace(interrupts=[{"value": {}}])])
    monkeypatch.setattr(chat_service, "get_graph", lambda: _FakeGraph(state, {"messages": []}))
    _patch_chat_storage(monkeypatch)

    payload = await chat_service.resolve_approval(_user(), _conversation(), "reject")

    assert payload["content"] == "操作已取消。"


@pytest.mark.asyncio
async def test_resolve_approval_validates_decision(monkeypatch):
    monkeypatch.setattr(chat_service, "get_graph", lambda: _FakeGraph(None))

    with pytest.raises(AppError) as exc:
        await chat_service.resolve_approval(_user(), _conversation(), "unknown")

    assert exc.value.message == "无效的审批决策"


# ── 流式生成取消 ──────────────────────────────────────────


class _CancellingGraph:
    async def aget_state(self, _config):
        return None

    async def astream(self, *_args, **_kwargs):
        raise asyncio.CancelledError()
        yield ""  # pragma: no cover


@pytest.mark.asyncio
async def test_stream_chat_records_client_cancellation(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(chat_service, "get_graph", lambda: _CancellingGraph())
    monkeypatch.setattr(chat_service, "incr", events.append)
    monkeypatch.setattr(chat_service, "SessionLocal", lambda: _FakeSession())

    async def fake_prepare(_db, _user, _conversation, _message, _client_message_id):
        return "标题", ""

    monkeypatch.setattr(chat_service, "_prepare", fake_prepare)

    with pytest.raises(asyncio.CancelledError):
        [chunk async for chunk in chat_service.stream_chat(_user(), _conversation(), "你好")]

    assert events == ["chat_stream", "chat_cancelled"]


# ── 心跳与槽位释放 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_with_slot_injects_heartbeat_and_releases(monkeypatch):
    released: list[int] = []

    async def slow_stream(_user, _conversation, _message, _client_message_id=None):
        await asyncio.sleep(0.05)
        yield chat_service.sse(
            "done",
            {"message_id": "m1", "content": "完成", "parts": [], "degraded": False},
        )

    async def fake_release(user_id):
        released.append(user_id)

    monkeypatch.setattr(chat_router, "stream_chat", slow_stream)
    monkeypatch.setattr(chat_router, "release_stream_slot", fake_release)
    monkeypatch.setattr(chat_router, "SSE_HEARTBEAT_SECONDS", 0.01)

    lock = asyncio.Lock()
    chunks = [
        chunk
        async for chunk in chat_router._stream_with_slot(
            _user(), _conversation(), "你好", None, lock
        )
    ]

    assert any(chunk.startswith(": ping") for chunk in chunks)
    assert any("event: done" in chunk for chunk in chunks)
    assert released == [7]
    assert lock.locked() is False


# ── 取消操作的确定性预执行（图级） ─────────────────────────


class _FakeAgent:
    def __init__(self, answer: str):
        self.answer = answer

    async def ainvoke(self, payload, config=None, context=None):
        return {"messages": [*payload["messages"], AIMessage(content=self.answer)]}


class _FakeCancelTool:
    def __init__(self, order_no: str = "ORD202607160001"):
        self.order_no = order_no
        self.calls: list[str] = []

    async def coroutine(self, order_no: str):
        self.calls.append(order_no)
        artifact = {
            "kind": "pending_action",
            "summary": f"即将取消订单 {order_no}，请在弹窗中确认。",
            "data": {
                "action": {
                    "type": "cancel_order",
                    "summary": f"取消订单 {order_no}",
                    "payload": {"order_no": order_no},
                    "idempotency_key": "k1",
                }
            },
            "requires_confirmation": True,
        }
        return artifact["summary"], artifact


@pytest.mark.asyncio
async def test_graph_prefetch_injects_tool_call_and_pending_action(monkeypatch):
    cancel_tool = _FakeCancelTool()
    monkeypatch.setattr(graph_module, "cancel_order", cancel_tool)
    monkeypatch.setattr(
        graph_module,
        "get_agents",
        lambda: {
            name: _FakeAgent("即将取消订单，请在确认弹窗中核对后再执行。")
            for name in ("order", "product", "aftersale", "faq", "human")
        },
    )

    ensured: list[dict] = []

    async def fake_ensure_action(**kwargs):
        ensured.append(kwargs)

    async def fake_run_llm(awaitable, timeout=None):
        return await awaitable

    monkeypatch.setattr(graph_module, "ensure_action", fake_ensure_action)
    monkeypatch.setattr(graph_module, "run_llm", fake_run_llm)

    graph = graph_module.build_graph(checkpointer=MemorySaver())
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content="帮我取消订单 ORD202607160001")],
            "intent": "order",
            "pending_action": None,
            "parts": [],
            "steps": 0,
            "degraded": False,
        },
        config={"configurable": {"thread_id": "t-cancel"}},
        context={"user_id": 1, "conversation_id": "c1", "context_block": ""},
    )

    assert cancel_tool.calls == ["ORD202607160001"]
    assert result["pending_action"]["type"] == "cancel_order"
    assert ensured and ensured[0]["action"]["type"] == "cancel_order"
    tool_calls = [
        call
        for message in result["messages"]
        if isinstance(message, AIMessage)
        for call in (message.tool_calls or [])
    ]
    assert tool_calls and tool_calls[0]["name"] == "cancel_order"
    assert any(isinstance(message, ToolMessage) for message in result["messages"])
