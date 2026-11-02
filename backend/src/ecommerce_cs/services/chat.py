"""对话编排：消息持久化、SSE 事件流、审批恢复与记忆刷新。"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage

from ecommerce_cs.agents.formatting import sanitize_answer, sanitize_pending_answer
from ecommerce_cs.core.errors import AppError, ConflictError
from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.metrics import incr
from ecommerce_cs.db.models import Conversation, User
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.runtime import get_graph
from ecommerce_cs.services.memory import build_context_block, schedule_refresh

log = get_logger("chat")

NODE_LABELS = {
    "pre_route": "正在理解您的问题",
    "classify": "正在理解您的问题",
    "order_agent": "正在查询订单信息",
    "product_agent": "正在检索商品",
    "aftersale_agent": "正在处理售后",
    "faq_agent": "正在查询知识库",
    "human_agent": "正在转接人工客服",
    "postprocess": "正在整理结果",
    "approval": "等待您确认操作",
}


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _thread_config(
    conversation_id: str, *, run_name: str = "chat.turn", **metadata: Any
) -> dict:
    """checkpoint 用 thread_id；run_name/tags/metadata 用于 LangSmith 归组与检索。"""
    config: dict[str, Any] = {
        "configurable": {"thread_id": f"conv:{conversation_id}"},
        "run_name": run_name,
        "tags": ["chat"],
    }
    if metadata:
        config["metadata"] = metadata
    return config


_LOCK_TTL_SECONDS = 600.0
_MAX_CONVERSATION_LOCKS = 2048

_conversation_locks: dict[str, tuple[asyncio.Lock, float]] = {}


def _prune_locks(now: float) -> None:
    """只回收空闲且未持有的锁，崩溃或长连接期间不会被误删。"""
    stale = [
        key
        for key, (lock, seen) in _conversation_locks.items()
        if not lock.locked() and now - seen > _LOCK_TTL_SECONDS
    ]
    for key in stale:
        _conversation_locks.pop(key, None)


def conversation_lock(conversation_id: str) -> asyncio.Lock:
    """同一会话的生成与审批串行化，避免并发写同一个 checkpoint thread。

    锁在持有期间不会被淘汰；空闲 10 分钟后回收，字典另有容量上限兜底。
    """
    now = time.monotonic()
    entry = _conversation_locks.get(conversation_id)
    if entry is not None:
        lock, _seen = entry
        if lock.locked() or now - _seen <= _LOCK_TTL_SECONDS:
            _conversation_locks[conversation_id] = (lock, now)
            return lock

    if len(_conversation_locks) >= _MAX_CONVERSATION_LOCKS:
        _prune_locks(now)

    lock = asyncio.Lock()
    _conversation_locks[conversation_id] = (lock, now)
    return lock


def _last_ai(messages: list) -> str:
    start = 0
    for index in range(len(messages) - 1, -1, -1):
        if getattr(messages[index], "type", "") == "human":
            start = index
            break
    for message in reversed(messages[start:]):
        if getattr(message, "type", "") == "ai":
            text = str(getattr(message, "content", "")).strip()
            cleaned = sanitize_answer(text)
            if cleaned:
                return cleaned
    return ""


async def _prepare(
    db,
    user: User,
    conversation: Conversation,
    message: str,
    client_message_id: str | None,
) -> tuple[str, str]:
    await chat_repo.add_message(
        db,
        conversation_id=conversation.id,
        role="user",
        content=message,
        client_message_id=client_message_id,
    )
    title = conversation.title
    if title == "新会话":
        title = message.strip().replace("\n", " ")[:24] or "新会话"
        await chat_repo.update_conversation_title(db, conversation.id, title)
    await chat_repo.touch_conversation(db, conversation.id)
    context_block = await build_context_block(db, user.id, conversation.id)
    return title, context_block


async def stream_chat(
    user: User,
    conversation: Conversation,
    message: str,
    client_message_id: str | None = None,
) -> AsyncGenerator[str, None]:
    graph = get_graph()
    config = _thread_config(conversation.id, user_id=user.id)
    incr("chat_stream")

    # 兜底：审批记录可能已过期，但 graph 里仍保留 interrupt
    state = await graph.aget_state(config)
    if state and any(task.interrupts for task in state.tasks):
        yield sse(
            "error",
            {
                "code": "pending_approval",
                "message": "当前会话还有待确认操作，请先处理后再发送新消息。",
                "degraded": False,
                "retryable": False,
            },
        )
        return

    async with SessionLocal() as db:
        title, context_block = await _prepare(
            db, user, conversation, message, client_message_id
        )

    yield sse(
        "meta",
        {
            "conversation_id": conversation.id,
            "title": title,
            "run_id": conversation.id,
        },
    )

    context = {
        "user_id": user.id,
        "conversation_id": conversation.id,
        "context_block": context_block,
    }
    initial = {
        "messages": [HumanMessage(content=message)],
        "intent": "",
        "pending_action": None,
        "parts": [],
        "steps": 0,
        "degraded": False,
    }

    answer_text = ""
    stream_enabled = True
    current_message_id = ""
    emitted_text = False
    try:
        async for item in graph.astream(
            initial,
            config=config,
            context=context,
            stream_mode=["messages", "updates"],
            subgraphs=True,
        ):
            namespace, mode, data = item

            if mode == "messages":
                chunk, meta = data
                node = (meta or {}).get("langgraph_node", "")
                if node != "agent":
                    continue

                chunk_id = str(getattr(chunk, "id", "") or "")
                if chunk_id and chunk_id != current_message_id:
                    current_message_id = chunk_id
                    stream_enabled = True
                    emitted_text = False

                if getattr(chunk, "tool_calls", None) or getattr(
                    chunk, "tool_call_chunks", None
                ):
                    # 工具调用前的旁白已经流给前端时，用 reset 事件撤回，
                    # 最终答案在工具结果返回后重新开始流式输出。
                    if stream_enabled and emitted_text:
                        yield sse("reset", {"reason": "tool_call"})
                    stream_enabled = False
                    emitted_text = False
                    answer_text = ""
                    continue
                if not stream_enabled:
                    continue

                text = getattr(chunk, "content", "")
                if not isinstance(text, str) or not text:
                    continue
                if text.strip().startswith("[系统记忆]"):
                    continue
                emitted_text = True
                answer_text += text
                yield sse("token", {"text": text})

            elif mode == "updates":
                if namespace:
                    # 子图里出现工具结果后，允许下一段最终答案继续流式输出
                    if isinstance(data, dict):
                        for payload in data.values():
                            if not isinstance(payload, dict):
                                continue
                            batch = payload.get("messages") or []
                            if any(
                                getattr(message, "type", "") == "tool"
                                for message in batch
                            ):
                                stream_enabled = True
                                current_message_id = ""
                                emitted_text = False
                    continue
                if not isinstance(data, dict):
                    continue
                if "__interrupt__" in data:
                    continue
                for node_name in data:
                    label = NODE_LABELS.get(node_name)
                    if label:
                        yield sse("status", {"stage": node_name, "label": label})

        state = await graph.aget_state(config)
        pending = None
        if state and state.tasks:
            interrupts = state.tasks[0].interrupts
            if interrupts:
                pending = interrupts[0].value

        values = (state.values if state else {}) or {}
        parts = values.get("parts") or []
        degraded = bool(values.get("degraded"))
        if degraded:
            incr("chat_degraded")
        content = _last_ai(values.get("messages", [])) or answer_text.strip()
        content = sanitize_answer(content)
        if pending:
            content = sanitize_pending_answer(content)
        elif not content:
            content = "抱歉，我没有获取到有效结果，请换个说法再试一次。"

        async with SessionLocal() as db:
            stored = await chat_repo.add_message(
                db,
                conversation_id=conversation.id,
                role="assistant",
                content=content,
                parts=parts,
            )
            await chat_repo.touch_conversation(db, conversation.id)
            message_id = stored.id

        if pending:
            async with SessionLocal() as db:
                action = await ops_repo.get_latest_pending(db, conversation.id)
                approval = {
                    "action": pending.get("action", {}) if isinstance(pending, dict) else {},
                    "title": pending.get("title", "请确认此操作")
                    if isinstance(pending, dict)
                    else "请确认此操作",
                    "action_id": action.id if action else "",
                    "summary": action.summary if action else "",
                }
            yield sse("approval", approval)

        yield sse(
            "done",
            {
                "message_id": message_id,
                "content": content,
                "parts": parts,
                "degraded": degraded,
            },
        )
        incr("chat_done")

        schedule_refresh(conversation.id, user.id)
    except asyncio.CancelledError:
        incr("chat_cancelled")
        log.info("stream_chat_cancelled", conversation_id=conversation.id)
        raise
    except Exception:
        incr("chat_error")
        log.exception("stream_chat_failed", conversation_id=conversation.id)
        yield sse(
            "error",
            {
                "code": "stream_failed",
                "message": "对话服务暂时不可用，请稍后重试。",
                "degraded": True,
                "retryable": True,
            },
        )


async def resolve_approval(
    user: User, conversation: Conversation, decision: str
) -> dict:
    from langgraph.types import Command

    if decision not in ("approve", "reject"):
        raise ConflictError("无效的审批决策")

    graph = get_graph()
    config = _thread_config(conversation.id, run_name="chat.approval", user_id=user.id)
    state = await graph.aget_state(config)
    if not state or not state.tasks or not state.tasks[0].interrupts:
        raise AppError("当前会话没有待确认的操作", code="no_pending", status_code=400)

    context = {"user_id": user.id, "conversation_id": conversation.id, "context_block": ""}
    result = await graph.ainvoke(Command(resume=decision), config=config, context=context)

    values = result or {}
    messages = values.get("messages", [])
    content = _last_ai(messages)
    content = content or ("操作已取消。" if decision == "reject" else "操作已完成。")
    parts = values.get("parts") or []

    async with SessionLocal() as db:
        stored = await chat_repo.add_message(
            db,
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            parts=parts,
        )
        await chat_repo.touch_conversation(db, conversation.id)
        message_id = stored.id

    return {
        "decision": decision,
        "message_id": message_id,
        "content": content,
        "parts": parts,
    }
