"""Agent 工具护栏：单轮调用次数上限与单次执行超时。

预算通过 ContextVar 按请求隔离，不依赖全局可变状态。
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass

from langchain_core.tools import BaseTool, StructuredTool

from ecommerce_cs.agents.tools.common import error_result
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings

log = get_logger("agent_guard")


@dataclass
class ToolBudget:
    max_calls: int
    timeout_seconds: float
    calls: int = 0

    def take(self) -> bool:
        if self.calls >= self.max_calls:
            return False
        self.calls += 1
        return True


_budget: ContextVar[ToolBudget | None] = ContextVar("agent_tool_budget", default=None)


def new_budget() -> ToolBudget:
    settings = get_settings()
    return ToolBudget(
        max_calls=settings.max_tool_calls,
        timeout_seconds=settings.tool_timeout_seconds,
    )


def set_budget(budget: ToolBudget) -> Token:
    return _budget.set(budget)


def reset_budget(token: Token) -> None:
    _budget.reset(token)


def guard_tool(original: BaseTool) -> BaseTool:
    """包装异步工具：限制调用次数，单次超时后返回可读错误而不是抛出异常。"""
    coroutine = getattr(original, "coroutine", None)
    if coroutine is None:
        return original

    async def runner(**kwargs):
        budget = _budget.get() or new_budget()
        if not budget.take():
            return error_result(
                "本次请求的工具调用次数已达上限，请基于已获取的信息回答，"
                "并提示用户稍后再试或换个问法。",
                code="tool_budget_exceeded",
            )
        try:
            return await asyncio.wait_for(coroutine(**kwargs), timeout=budget.timeout_seconds)
        except TimeoutError:
            log.warning("tool_timeout", tool=original.name)
            return error_result(
                f"工具 {original.name} 执行超时，请告知用户稍后重试。",
                code="tool_timeout",
            )
        except Exception:
            log.exception("tool_failed", tool=original.name)
            return error_result(
                "工具执行失败，请告知用户稍后重试或转人工客服。",
                code="tool_failed",
            )

    return StructuredTool.from_function(
        coroutine=runner,
        name=original.name,
        description=original.description,
        args_schema=original.args_schema,
        response_format="content_and_artifact",
    )


def guard_tools(tools: list[BaseTool]) -> list[BaseTool]:
    return [guard_tool(item) for item in tools]
