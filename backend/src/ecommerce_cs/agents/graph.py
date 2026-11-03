"""客服主图：确定性预路由 → 单次分类 → 域 Agent → 结果整理 → 审批。"""

import asyncio
import json
import re
import uuid
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
from ecommerce_cs.agents.tools.catalog import PRODUCT_TOOLS, compare_products, search_products
from ecommerce_cs.agents.tools.faq import FAQ_TOOLS
from ecommerce_cs.agents.tools.order import ORDER_TOOLS, cancel_order
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


_CANCEL_ORDER_NO_RE = re.compile(r"ORD[0-9A-Za-z]{6,}")
_CANCEL_QUESTION_RE = re.compile(r"怎么取消|如何取消|取消流程|取消规则|能否取消|可以取消吗|取消政策")

_COMPARE_RE = re.compile(r"对比|比较|哪个更?好|哪个更|有什么区别|区别|差在哪|vs", re.I)
_COMPARE_SPLIT_RE = re.compile(r"\s*(?:和|与|、|，|,|vs\.?)\s*", re.I)
_COMPARE_NOISE_RE = re.compile(
    r"帮我|请|麻烦|一下|看看|对比一下|对比|比较一下|比较|哪个好|哪个更|"
    r"有什么区别|区别|差在哪|推荐|[?？。!！]"
)


async def _prefetch_cancel_tool(state: CSRState) -> list[BaseMessage]:
    """用户直接要求取消时先确定性生成待确认操作，避免模型只解释不调工具。

    仅在“取消 + 明确订单号 + 非询问流程”时触发；状态不允许取消时返回空，
    交给 Agent 正常解释，不替模型下结论。
    """
    if state.get("intent") != "order" or state.get("pending_action"):
        return []
    human = _last_human_message(state.get("messages", []))
    if human is None:
        return []
    text = str(human.content)
    if "取消" not in text or _CANCEL_QUESTION_RE.search(text):
        return []
    match = _CANCEL_ORDER_NO_RE.search(text)
    if not match:
        return []

    coroutine = getattr(cancel_order, "coroutine", None)
    if coroutine is None:
        return []

    order_no = match.group(0)
    try:
        summary, artifact = await asyncio.wait_for(
            coroutine(order_no=order_no),
            timeout=get_settings().tool_timeout_seconds,
        )
    except Exception:
        log.exception("cancel_prefetch_failed", order_no=order_no)
        return []

    if not isinstance(artifact, dict) or artifact.get("kind") != "pending_action":
        return []
    action = (artifact.get("data") or {}).get("action")
    if not isinstance(action, dict):
        return []

    call_id = f"prefetch-cancel-{uuid.uuid4().hex[:8]}"
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "cancel_order",
                    "args": {"order_no": order_no},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=summary,
            artifact=artifact,
            tool_call_id=call_id,
            name="cancel_order",
        ),
    ]


def _compare_candidates(text: str) -> list[str]:
    """把“A 和 B 对比一下”拆成两个候选商品名，并去掉口语噪声。"""
    candidates = []
    for raw in _COMPARE_SPLIT_RE.split(text):
        name = _COMPARE_NOISE_RE.sub("", raw).strip()
        if len(name) >= 2:
            candidates.append(name)
    return candidates[:4]


async def _resolve_compare_ids(names: list[str]) -> tuple[list[int], list[str]]:
    """逐个商品名搜索并取最匹配的商品 ID，解析不足 2 个时返回空。"""
    ids: list[int] = []
    resolved: list[str] = []
    timeout = get_settings().tool_timeout_seconds

    for name in names:
        coroutine = getattr(search_products, "coroutine", None)
        if coroutine is None:
            return [], []
        try:
            _, artifact = await asyncio.wait_for(
                coroutine(keyword=name), timeout=timeout
            )
        except Exception:
            log.exception("compare_prefetch_search_failed", keyword=name)
            continue
        items = ((artifact.get("data") or {}).get("products") or []) if isinstance(
            artifact, dict
        ) else []
        if not items:
            continue
        matched = next((item for item in items if item.get("name") == name), None)
        if matched is None:
            matched = next(
                (
                    item
                    for item in items
                    if name in str(item.get("name", ""))
                    or str(item.get("name", "")) in name
                ),
                None,
            )
        if matched is None:
            matched = items[0]
        product_id = int(matched.get("id") or 0)
        if product_id and product_id not in ids:
            ids.append(product_id)
            resolved.append(str(matched.get("name", name)))
    if len(ids) < 2:
        return [], []
    return ids, resolved


async def _prefetch_compare_tool(state: CSRState) -> list[BaseMessage]:
    """对比类问题先确定性解析商品并调用 compare_products，避免模型只搜不比。"""
    if state.get("intent") != "product" or state.get("pending_action"):
        return []
    human = _last_human_message(state.get("messages", []))
    if human is None:
        return []
    text = str(human.content)
    if not _COMPARE_RE.search(text):
        return []
    names = _compare_candidates(text)
    if len(names) < 2:
        return []

    ids, _resolved = await _resolve_compare_ids(names)
    if len(ids) < 2:
        return []

    coroutine = getattr(compare_products, "coroutine", None)
    if coroutine is None:
        return []
    try:
        summary, artifact = await asyncio.wait_for(
            coroutine(product_ids=ids[:4]),
            timeout=get_settings().tool_timeout_seconds,
        )
    except Exception:
        log.exception("compare_prefetch_failed", product_ids=ids[:4])
        return []
    if not isinstance(artifact, dict) or artifact.get("kind") != "comparison":
        return []

    call_id = f"prefetch-compare-{uuid.uuid4().hex[:8]}"
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "compare_products",
                    "args": {"product_ids": ids[:4]},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=summary,
            artifact=artifact,
            tool_call_id=call_id,
            name="compare_products",
        ),
    ]


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
            history = recent_messages(state.get("messages", []))
            preface: list[BaseMessage] = []
            if name == "order":
                preface = await _prefetch_cancel_tool(state)
            elif name == "product":
                preface = await _prefetch_compare_tool(state)
            trimmed = [*history, *preface]
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

            new_messages = [*preface, *result.get("messages", [])[len(trimmed) :]]
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
