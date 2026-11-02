"""工具护栏与回答净化测试。"""

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from ecommerce_cs.agents.formatting import (
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
