"""子 Agent 工厂：统一 ReAct 循环与上下文注入。"""

from typing import Any

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.prebuilt import create_react_agent
from langgraph.runtime import get_runtime

from ecommerce_cs.agents.guard import guard_tools
from ecommerce_cs.agents.state import CSRState
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.llm.factory import get_llm


def recent_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    window = get_settings().history_window
    if len(messages) <= window:
        return list(messages)
    return list(messages[-window:])


def build_react_agent(system_prompt: str, tools: list):
    """构建单个域的 ReAct 子图，Prompt 中注入用户画像与历史摘要。"""

    def prompt_builder(state: CSRState) -> list[BaseMessage]:
        context: dict[str, Any] = get_runtime().context or {}
        block = str(context.get("context_block", "")).strip()
        content = system_prompt if not block else f"{system_prompt}\n\n{block}"
        return [SystemMessage(content=content)] + list(state["messages"])

    return create_react_agent(
        get_llm(temperature=0.3),
        guard_tools(tools),
        prompt=prompt_builder,
    )
