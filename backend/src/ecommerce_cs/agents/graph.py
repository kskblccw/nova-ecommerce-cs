"""客服主图：确定性预路由 → 单次分类 → 域 Agent → 结果整理 → 审批。"""

import json
from collections.abc import Hashable
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import get_runtime
from langgraph.types import interrupt

from ecommerce_cs.agents.factory import build_react_agent, recent_messages
from ecommerce_cs.agents.formatting import (
    DEGRADED_MESSAGE,
    MUTATION_CLAIM_RE,
    UNSUPPORTED_CLAIM_HINT,
    sanitize_answer,
    sanitize_pending_answer,
)
from ecommerce_cs.agents.guard import new_budget, reset_budget, set_budget
from ecommerce_cs.agents.prompts import (
    AFTERSALE_PROMPT,
    FAQ_PROMPT,
    HUMAN_PROMPT,
    ORDER_PROMPT,
    PRODUCT_PROMPT,
)
from ecommerce_cs.agents.routing import classify, pre_route
from ecommerce_cs.agents.state import CSRState
from ecommerce_cs.agents.tools.aftersale import AFTERSALE_TOOLS
from ecommerce_cs.agents.tools.catalog import PRODUCT_TOOLS
from ecommerce_cs.agents.tools.faq import FAQ_TOOLS
from ecommerce_cs.agents.tools.order import ORDER_TOOLS
from ecommerce_cs.agents.tools.service import SERVICE_TOOLS, get_current_user
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.llm.circuit import LLMUnavailable, run_llm
from ecommerce_cs.services.approval import ensure_action, resolve

log = get_logger("graph")

PART_KINDS = {
    "orders",
    "order_detail",
    "shipment",
    "products",
    "product_detail",
    "comparison",
    "returns",
    "return_detail",
    "faq",
    "knowledge",
    "handoff",
    "user",
    "info",
}

_AGENTS: dict[str, Any] = {}


def get_agents() -> dict[str, Any]:
    if not _AGENTS:
        _AGENTS.update(
            {
                "order": build_react_agent(ORDER_PROMPT, ORDER_TOOLS + [get_current_user]),
                "product": build_react_agent(PRODUCT_PROMPT, PRODUCT_TOOLS + [get_current_user]),
                "aftersale": build_react_agent(
                    AFTERSALE_PROMPT, AFTERSALE_TOOLS + SERVICE_TOOLS
                ),
                "faq": build_react_agent(FAQ_PROMPT, FAQ_TOOLS + SERVICE_TOOLS),
                "human": build_react_agent(HUMAN_PROMPT, SERVICE_TOOLS),
            }
        )
    return _AGENTS


def _last_human_message(messages: list[BaseMessage]) -> BaseMessage | None:
    for message in reversed(messages):
        if message.type == "human":
            return message
    return None


def _current_turn(messages: list[BaseMessage]) -> list[BaseMessage]:
    for index in range(len(messages) - 1, -1, -1):
        if messages[index].type == "human":
            return messages[index:]
    return messages


def _parse_tool_payload(message: BaseMessage) -> dict | None:
    if not isinstance(message, ToolMessage):
        return None
    artifact = getattr(message, "artifact", None)
    if isinstance(artifact, dict) and artifact.get("kind"):
        return artifact
    content = message.content
    if not isinstance(content, str):
        return None
    try:
        body = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(body, dict) and body.get("kind"):
        return body
    return None


def pre_route_node(state: CSRState) -> dict:
    message = _last_human_message(state.get("messages", []))
    intent = pre_route(str(message.content)) if message else None
    return {"intent": intent or "__classify__"}


async def classify_node(state: CSRState) -> dict:
    message = _last_human_message(state.get("messages", []))
    intent = await classify(str(message.content)) if message else "faq"
    return {"intent": intent}


def dispatch_route(state: CSRState) -> str:
    intent = state.get("intent", "faq")
    if intent not in ("order", "product", "aftersale", "faq", "human"):
        return "faq"
    return intent


def postprocess_node(state: CSRState) -> dict:
    messages = _current_turn(state.get("messages", []))
    parts: list[dict] = []
    seen: set[str] = set()
    pending_action: dict | None = None

    for message in messages:
        payload = _parse_tool_payload(message)
        if not payload:
            continue
        kind = str(payload.get("kind", ""))
        if kind == "pending_action":
            action = (payload.get("data") or {}).get("action")
            if isinstance(action, dict) and action.get("type"):
                pending_action = action
            continue
        if kind not in PART_KINDS:
            continue
        part = {
            "kind": kind,
            "summary": payload.get("summary", ""),
            "data": payload.get("data"),
        }
        fingerprint = json.dumps(part, ensure_ascii=False, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        parts.append(part)

    updates: dict[str, Any] = {"parts": parts, "pending_action": pending_action}

    if pending_action:
        last_ai = next(
            (m for m in reversed(state.get("messages", [])) if m.type == "ai"), None
        )
        if last_ai is not None:
            content = str(getattr(last_ai, "content", ""))
            cleaned = sanitize_pending_answer(content)
            if cleaned != content:
                updates["messages"] = [AIMessage(id=last_ai.id, content=cleaned)]
    else:
        last_ai = next(
            (m for m in reversed(state.get("messages", [])) if m.type == "ai"), None
        )
        if last_ai is not None:
            content = str(getattr(last_ai, "content", ""))
            if MUTATION_CLAIM_RE.search(content):
                updates["messages"] = [
                    AIMessage(id=last_ai.id, content=UNSUPPORTED_CLAIM_HINT)
                ]
    return updates


def postprocess_route(state: CSRState) -> str:
    return "approval" if state.get("pending_action") else "finalize"


async def approval_node(state: CSRState) -> dict:
    action = state.get("pending_action") or {}
    if not action:
        return {}

    context: dict[str, Any] = get_runtime().context or {}
    user_id = int(context.get("user_id") or 0)
    conversation_id = str(context.get("conversation_id") or "")

    await ensure_action(
        action=action,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    decision = interrupt(
        {
            "type": "approval_required",
            "action": action,
            "title": "请确认此操作",
        }
    )
    if isinstance(decision, dict):
        decision = decision.get("decision", "reject")
    normalized = "approve" if str(decision) == "approve" else "reject"

    answer = await resolve(action=action, decision=normalized, user_id=user_id)
    return {"messages": [AIMessage(content=answer)], "pending_action": None, "parts": []}


def finalize_node(state: CSRState) -> dict:
    messages = state.get("messages", [])
    if not messages:
        return {"messages": [AIMessage(content="抱歉，我没有获取到有效结果，请再试一次。")]}
    last = messages[-1]
    if last.type == "ai":
        content = str(last.content)
        cleaned = sanitize_answer(content)
        if cleaned and cleaned != content:
            return {"messages": [AIMessage(id=last.id, content=cleaned)]}
        if cleaned:
            return {}
    return {"messages": [AIMessage(content="抱歉，我没有获取到有效结果，请换个说法再试一次。")]}


def build_graph(checkpointer=None):
    agents = get_agents()
    builder = StateGraph(CSRState)

    builder.add_node("pre_route", pre_route_node)
    builder.add_node("classify", classify_node)

    def make_agent_node(name: str):
        async def node(state: CSRState, config) -> dict:
            trimmed = recent_messages(state.get("messages", []))
            context: dict[str, Any] = get_runtime().context or {}
            settings = get_settings()
            budget = new_budget()
            token = set_budget(budget)
            agent_config = dict(config or {})
            recursion_limit = settings.agent_recursion_limit or (
                settings.max_agent_steps * 2 + 2
            )
            agent_config["recursion_limit"] = recursion_limit
            try:
                result = await run_llm(
                    agents[name].ainvoke(
                        {"messages": trimmed}, agent_config, context=context
                    ),
                    timeout=settings.agent_timeout_seconds,
                )
            except LLMUnavailable:
                log.warning("agent_degraded", agent=name, reason="llm_unavailable")
                return {
                    "messages": [AIMessage(content=DEGRADED_MESSAGE)],
                    "degraded": True,
                    "steps": int(state.get("steps", 0)) + 1,
                }
            except Exception:
                log.exception("agent_failed", agent=name)
                return {
                    "messages": [AIMessage(content=DEGRADED_MESSAGE)],
                    "degraded": True,
                    "steps": int(state.get("steps", 0)) + 1,
                }
            finally:
                reset_budget(token)

            new_messages = result.get("messages", [])[len(trimmed) :]
            return {
                "messages": new_messages,
                "steps": int(state.get("steps", 0)) + 1,
            }

        return node

    for name in ("order", "product", "aftersale", "faq", "human"):
        builder.add_node(f"{name}_agent", make_agent_node(name))

    builder.add_node("postprocess", postprocess_node)
    builder.add_node("approval", approval_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "pre_route")
    dispatch_map: dict[Hashable, str] = {
        "order": "order_agent",
        "product": "product_agent",
        "aftersale": "aftersale_agent",
        "faq": "faq_agent",
        "human": "human_agent",
    }
    pre_route_map: dict[Hashable, str] = {"classify": "classify", **dispatch_map}
    builder.add_conditional_edges(
        "pre_route",
        lambda state: "classify"
        if state.get("intent") == "__classify__"
        else dispatch_route(state),
        pre_route_map,
    )
    builder.add_conditional_edges(
        "classify",
        dispatch_route,
        dispatch_map,
    )

    for name in ("order", "product", "aftersale", "faq", "human"):
        builder.add_edge(f"{name}_agent", "postprocess")

    builder.add_conditional_edges(
        "postprocess",
        postprocess_route,
        {"approval": "approval", "finalize": "finalize"},
    )
    builder.add_edge("approval", "finalize")
    builder.add_edge("finalize", END)

    if checkpointer is None:
        return builder.compile()
    return builder.compile(checkpointer=checkpointer)


async def chat_once(message: str, *, user_id: int = 0, conversation_id: str = "test") -> str:
    graph = build_graph()
    result = await graph.ainvoke(
        {
            "messages": [HumanMessage(content=message)],
            "intent": "",
            "pending_action": None,
            "parts": [],
            "steps": 0,
            "degraded": False,
        },
        context={"user_id": user_id, "conversation_id": conversation_id},
    )
    return str(result["messages"][-1].content)
