"""工具护栏与回答净化测试。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from ecommerce_cs.agents import graph as graph_module
from ecommerce_cs.agents.formatting import (
    MUTATION_CLAIM_RE,
    PENDING_HINT,
    UNSUPPORTED_CLAIM_HINT,
    sanitize_pending_answer,
    strip_narration,
)
from ecommerce_cs.agents.graph import postprocess_node
from ecommerce_cs.agents.guard import (
    ToolBudget,
    guard_tool,
    new_budget,
    reset_budget,
    set_budget,
)


@tool(response_format="content_and_artifact")
async def echo(value: str) -> tuple[str, dict]:
    """回显。"""
    return value, {"kind": "info", "data": {"value": value}}


@tool(response_format="content_and_artifact")
async def slow(value: str) -> tuple[str, dict]:
    """慢工具。"""
    await asyncio.sleep(0.2)
    return value, {"kind": "info", "data": {"value": value}}


@pytest.mark.asyncio
async def test_tool_budget_blocks_extra_calls():
    guarded = guard_tool(echo)
    token = set_budget(ToolBudget(max_calls=1, timeout_seconds=5))
    try:
        first = await guarded.arun({"value": "a"}, tool_call_id="call-1")
        second = await guarded.arun({"value": "b"}, tool_call_id="call-2")
    finally:
        reset_budget(token)

    assert first.artifact["kind"] == "info"
    assert second.artifact["kind"] == "error"
    assert second.artifact["data"]["code"] == "tool_budget_exceeded"


@pytest.mark.asyncio
async def test_tool_timeout_degrades_to_error_artifact():
    guarded = guard_tool(slow)
    token = set_budget(ToolBudget(max_calls=1, timeout_seconds=0.05))
    try:
        message = await guarded.arun({"value": "x"}, tool_call_id="call-3")
    finally:
        reset_budget(token)

    assert message.artifact["kind"] == "error"
    assert message.artifact["data"]["code"] == "tool_timeout"


def test_budget_uses_settings():
    budget = new_budget()
    assert budget.max_calls >= 1
    assert budget.timeout_seconds > 0


def test_strip_narration_keeps_logistics_status():
    text = "正在查询您的订单信息。\n订单 ORD2026 已发货，正在派送中。"
    cleaned = strip_narration(text)
    assert "正在查询" not in cleaned
    assert "正在派送中" in cleaned


def test_pending_answer_blocks_false_success_claim():
    text = "已为您提交退货申请，请等待审核。"
    assert sanitize_pending_answer(text) == PENDING_HINT
    assert sanitize_pending_answer("正在为您准备退货申请。") == PENDING_HINT
    assert sanitize_pending_answer("订单已完成，可申请售后。") == "订单已完成，可申请售后。"


def test_postprocess_blocks_claim_without_pending_action():
    tool_message = ToolMessage(
        content="订单 ORD202607160001｜状态：待付款",
        artifact={"kind": "order_detail", "summary": "订单详情", "data": {"order_no": "ORD1"}},
        tool_call_id="call-detail",
    )
    state = {
        "messages": [
            HumanMessage(content="帮我改地址"),
            AIMessage(content="已为您提交地址变更申请，请等待生效。"),
            tool_message,
        ]
    }

    updates = postprocess_node(state)

    assert {"order_detail"}.issubset(
        {part["kind"] for part in updates["parts"]}
    )
    assert updates["messages"][0].content == UNSUPPORTED_CLAIM_HINT


def test_postprocess_keeps_status_sentences():
    tool_message = ToolMessage(
        content="订单已取消",
        artifact={"kind": "order_detail", "summary": "订单详情", "data": {}},
        tool_call_id="call-status",
    )
    state = {
        "messages": [
            HumanMessage(content="查订单"),
            AIMessage(content="订单已完成，物流已签收。"),
            tool_message,
        ]
    }

    updates = postprocess_node(state)

    assert "messages" not in updates


def test_mutation_claim_ignores_process_descriptions():
    """退款时效这类流程描述不应被当成“助手已执行”的成功声明。"""
    assert MUTATION_CLAIM_RE.search("退款已提交至支付渠道，1-3 个工作日到账") is None
    assert MUTATION_CLAIM_RE.search("订单已完成，支持 7 天无理由退货") is None


def test_mutation_claim_flags_first_person_or_assistant_subject():
    assert MUTATION_CLAIM_RE.search("我已提交取消申请") is not None
    assert MUTATION_CLAIM_RE.search("已为您生成取消申请") is not None
    assert MUTATION_CLAIM_RE.search("已帮您修改收货地址") is not None


def test_pending_answer_keeps_refund_timeline():
    text = "退款已提交至支付渠道，1-3 个工作日到账。"
    assert sanitize_pending_answer(text) == text


class _FakeCancelTool:
    def __init__(self, artifact: dict | None = None, error: Exception | None = None):
        self.artifact = artifact
        self.error = error
        self.calls: list[str] = []

    async def coroutine(self, order_no: str):
        self.calls.append(order_no)
        if self.error:
            raise self.error
        action = {"type": "cancel_order", "summary": "取消订单", "idempotency_key": "k"}
        artifact = self.artifact or {
            "kind": "pending_action",
            "summary": "即将取消订单，请在弹窗中确认。",
            "data": {"action": action},
        }
        return artifact.get("summary", ""), artifact


def _state(text: str, *, intent: str = "order") -> dict:
    return {
        "intent": intent,
        "pending_action": None,
        "messages": [HumanMessage(content=text)],
    }


def _cancel_state(text: str, *, intent: str = "order") -> dict:
    return _state(text, intent=intent)


@pytest.mark.asyncio
async def test_prefetch_cancel_emits_tool_call_and_pending_artifact(monkeypatch):
    tool = _FakeCancelTool()
    monkeypatch.setattr(graph_module, "cancel_order", tool)

    messages = await graph_module._prefetch_cancel_tool(
        _cancel_state("帮我取消订单 ORD202607160001")
    )

    assert tool.calls == ["ORD202607160001"]
    assert len(messages) == 2
    assert messages[0].tool_calls[0]["name"] == "cancel_order"
    assert messages[1].artifact["kind"] == "pending_action"


@pytest.mark.asyncio
async def test_prefetch_cancel_skips_questions_and_missing_order_no(monkeypatch):
    tool = _FakeCancelTool()
    monkeypatch.setattr(graph_module, "cancel_order", tool)

    assert await graph_module._prefetch_cancel_tool(_cancel_state("如何取消订单？")) == []
    assert await graph_module._prefetch_cancel_tool(_cancel_state("帮我取消订单")) == []
    assert await graph_module._prefetch_cancel_tool(
        _cancel_state("帮我取消订单 ORD202607160001", intent="faq")
    ) == []
    assert tool.calls == []


@pytest.mark.asyncio
async def test_prefetch_cancel_ignores_non_pending_tool_result(monkeypatch):
    tool = _FakeCancelTool(
        artifact={
            "kind": "error",
            "summary": "订单已发货，无法取消。",
            "data": {"code": "invalid_status"},
        }
    )
    monkeypatch.setattr(graph_module, "cancel_order", tool)

    assert await graph_module._prefetch_cancel_tool(
        _cancel_state("帮我取消订单 ORD202607160001")
    ) == []


@pytest.mark.asyncio
async def test_prefetch_cancel_swallows_tool_errors(monkeypatch):
    tool = _FakeCancelTool(error=RuntimeError("db down"))
    monkeypatch.setattr(graph_module, "cancel_order", tool)

    assert await graph_module._prefetch_cancel_tool(
        _cancel_state("帮我取消订单 ORD202607160001")
    ) == []


class _FakeSearchTool:
    def __init__(self, products):
        self.products = products
        self.keywords: list[str] = []

    async def coroutine(self, keyword: str):
        self.keywords.append(keyword)
        return (
            f"搜索 {keyword}",
            {"kind": "products", "data": {"products": self.products.get(keyword, [])}},
        )


class _FakeCompareTool:
    def __init__(self, artifact: dict | None = None, error: Exception | None = None):
        self.artifact = artifact
        self.error = error
        self.calls: list[list[int]] = []

    async def coroutine(self, product_ids: list[int]):
        self.calls.append(list(product_ids))
        if self.error:
            raise self.error
        artifact = self.artifact or {
            "kind": "comparison",
            "summary": "商品对比",
            "data": {"products": []},
        }
        return artifact.get("summary", ""), artifact


def test_compare_candidates_strips_noise_and_splits_names():
    names = graph_module._compare_candidates("iPhone 15 Pro Max 和小米14 Ultra 对比一下")
    assert names == ["iPhone 15 Pro Max", "小米14 Ultra"]


@pytest.mark.asyncio
async def test_prefetch_compare_resolves_products_and_emits_tool_call(monkeypatch):
    search = _FakeSearchTool(
        {
            "iPhone 15 Pro Max": [{"id": 1, "name": "iPhone 15 Pro Max"}],
            "小米14 Ultra": [{"id": 2, "name": "小米14 Ultra"}],
        }
    )
    compare = _FakeCompareTool()
    monkeypatch.setattr(graph_module, "search_products", search)
    monkeypatch.setattr(graph_module, "compare_products", compare)

    messages = await graph_module._prefetch_compare_tool(
        _state("iPhone 15 Pro Max 和小米14 Ultra 对比一下", intent="product")
    )

    assert search.keywords == ["iPhone 15 Pro Max", "小米14 Ultra"]
    assert compare.calls == [[1, 2]]
    assert len(messages) == 2
    assert messages[0].tool_calls[0]["name"] == "compare_products"
    assert messages[0].tool_calls[0]["args"] == {"product_ids": [1, 2]}


@pytest.mark.asyncio
async def test_prefetch_compare_skips_non_compare_queries(monkeypatch):
    search = _FakeSearchTool({})
    compare = _FakeCompareTool()
    monkeypatch.setattr(graph_module, "search_products", search)
    monkeypatch.setattr(graph_module, "compare_products", compare)

    assert await graph_module._prefetch_compare_tool(
        _state("推荐一款降噪耳机", intent="product")
    ) == []
    assert search.keywords == []
    assert compare.calls == []


@pytest.mark.asyncio
async def test_prefetch_compare_skips_when_products_cannot_be_resolved(monkeypatch):
    search = _FakeSearchTool({"iPhone 15 Pro Max": [{"id": 1, "name": "iPhone 15 Pro Max"}]})
    compare = _FakeCompareTool()
    monkeypatch.setattr(graph_module, "search_products", search)
    monkeypatch.setattr(graph_module, "compare_products", compare)

    assert await graph_module._prefetch_compare_tool(
        _state("iPhone 15 Pro Max 和小米14 Ultra 对比一下", intent="product")
    ) == []
    assert compare.calls == []


@pytest.mark.asyncio
async def test_prefetch_compare_ignores_non_comparison_result(monkeypatch):
    search = _FakeSearchTool(
        {
            "iPhone 15 Pro Max": [{"id": 1, "name": "iPhone 15 Pro Max"}],
            "小米14 Ultra": [{"id": 2, "name": "小米14 Ultra"}],
        }
    )
    compare = _FakeCompareTool(
        artifact={"kind": "error", "summary": "有效商品不足 2 个", "data": {}}
    )
    monkeypatch.setattr(graph_module, "search_products", search)
    monkeypatch.setattr(graph_module, "compare_products", compare)

    assert await graph_module._prefetch_compare_tool(
        _state("iPhone 15 Pro Max 和小米14 Ultra 对比一下", intent="product")
    ) == []
