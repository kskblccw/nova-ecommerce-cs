"""记忆系统：会话摘要 + 用户画像，按 token 预算注入上下文。"""

import asyncio
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.messages import HumanMessage

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.llm.circuit import run_llm
from ecommerce_cs.llm.factory import get_llm

log = get_logger("memory")

SUMMARY_PROMPT = """把以下客服对话压缩成 150 字以内的中文摘要。
保留：用户诉求、订单号/商品名/金额、已执行或待确认的操作、结果。
省略寒暄。句式连贯，不要编号。

已有摘要：{summary}

对话：
{history}

只输出摘要："""

PROFILE_PROMPT = """从对话中提取用户长期偏好与事实，输出 JSON：
{{"preferences":["最多5条明确偏好"],"facts":{{"城市":"..."}}}}
只提取明确信息，不确定的不写。已有画像：{profile}

对话：{history}
只输出 JSON："""


async def build_context_block(db, user_id: int, conversation_id: str) -> str:
    summary = await chat_repo.get_summary(db, conversation_id)
    profile = await chat_repo.get_profile(db, user_id)

    parts: list[str] = []
    if summary:
        parts.append(f"【历史摘要】{summary[:300]}")

    if profile and (profile.preferences or profile.facts):
        lines = ["【用户画像】"]
        if profile.preferences:
            lines.append("偏好：" + "、".join(str(item) for item in profile.preferences[:5]))
        if profile.facts:
            pairs = "、".join(f"{key}:{value}" for key, value in list(profile.facts.items())[:5])
            lines.append(f"信息：{pairs}")
        parts.append("\n".join(lines))

    block = "\n\n".join(parts)
    return block[:450]


def _format_history(messages: Sequence[Any], limit: int = 20) -> str:
    rows = []
    for message in messages[-limit:]:
        if message.type not in ("human", "ai"):
            continue
        role = "用户" if message.type == "human" else "客服"
        rows.append(f"[{role}] {str(message.content)[:200]}")
    return "\n".join(rows)


async def refresh_memory(conversation_id: str, user_id: int) -> None:
    """后台任务：压缩摘要并更新画像，失败不影响主流程。"""
    lock = refresh_lock(conversation_id)
    if lock.locked():
        return
    async with lock:
        await _refresh_memory(conversation_id, user_id)


_REFRESH_LOCK_TTL_SECONDS = 600.0
_MAX_REFRESH_LOCKS = 2048

_refresh_locks: dict[str, tuple[asyncio.Lock, float]] = {}
_background_tasks: set[asyncio.Task] = set()


def _prune_refresh_locks(now: float) -> None:
    """只回收空闲超过 TTL 的锁；正在持有的锁永远不会被淘汰。"""
    stale = [
        key
        for key, (lock, seen) in _refresh_locks.items()
        if not lock.locked() and now - seen > _REFRESH_LOCK_TTL_SECONDS
    ]
    for key in stale:
        _refresh_locks.pop(key, None)


def refresh_lock(conversation_id: str) -> asyncio.Lock:
    """按会话串行化记忆刷新，空闲锁可回收，字典另有容量上限兜底。"""
    now = time.monotonic()
    entry = _refresh_locks.get(conversation_id)
    if entry is not None:
        lock, _seen = entry
        if lock.locked() or now - _seen <= _REFRESH_LOCK_TTL_SECONDS:
            _refresh_locks[conversation_id] = (lock, now)
            return lock
    if len(_refresh_locks) >= _MAX_REFRESH_LOCKS:
        _prune_refresh_locks(now)
    lock = asyncio.Lock()
    _refresh_locks[conversation_id] = (lock, now)
    return lock


def schedule_refresh(conversation_id: str, user_id: int) -> None:
    """后台刷新记忆：持有任务引用，避免被事件循环提前回收。"""
    task = asyncio.create_task(refresh_memory(conversation_id, user_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def drain_background_tasks(timeout: float = 5.0) -> None:
    """停机前等待后台任务收尾，超时后放弃。"""
    tasks = set(_background_tasks)
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


def _too_soon(updated_at: datetime | None) -> bool:
    if not updated_at:
        return False
    settings = get_settings()
    now = datetime.now(UTC)
    value = updated_at if updated_at.tzinfo else updated_at.replace(tzinfo=UTC)
    return now - value < timedelta(minutes=settings.memory_min_interval_minutes)


async def _refresh_memory(conversation_id: str, user_id: int) -> None:
    try:
        async with SessionLocal() as db:
            existing = await chat_repo.get_summary_record(db, conversation_id)
            if existing and _too_soon(existing.updated_at):
                return

            messages = await chat_repo.list_messages(db, conversation_id)
            if len(messages) < 4:
                return

            history = _format_history(messages)
            current_summary = existing.summary if existing else ""

            llm = get_llm(temperature=0.2)
            summary_response = await run_llm(
                llm.ainvoke(
                    [
                        HumanMessage(
                            content=SUMMARY_PROMPT.replace(
                                "{summary}", current_summary or "（无）"
                            ).replace("{history}", history[-2000:])
                        )
                    ]
                )
            )
            new_summary = str(summary_response.content).strip()[:300]
            await chat_repo.upsert_summary(
                db, conversation_id, new_summary, token_count=len(new_summary)
            )

            profile = await chat_repo.get_profile(db, user_id)
            profile_text = json.dumps(
                {
                    "preferences": list(profile.preferences) if profile else [],
                    "facts": dict(profile.facts) if profile else {},
                },
                ensure_ascii=False,
            )
            profile_response = await run_llm(
                llm.ainvoke(
                    [
                        HumanMessage(
                            content=PROFILE_PROMPT.replace(
                                "{profile}", profile_text
                            ).replace("{history}", history[-1500:])
                        )
                    ]
                )
            )
            raw = str(profile_response.content).strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(raw[start : end + 1])
                preferences = [
                    str(item) for item in data.get("preferences", []) if str(item).strip()
                ][:5]
                facts = {
                    str(key): str(value)
                    for key, value in (data.get("facts") or {}).items()
                    if str(value).strip()
                }
                old_preferences = (
                    [str(item) for item in profile.preferences] if profile else []
                )
                merged_preferences = list(
                    dict.fromkeys(old_preferences[-3:] + preferences)
                )[-5:]
                old_facts = dict(profile.facts) if profile else {}
                merged_facts = {**old_facts, **facts}
                weight = round(min(1.0, (profile.weight if profile else 1.0) + 0.05), 3)
                await chat_repo.upsert_profile(
                    db,
                    user_id=user_id,
                    preferences=merged_preferences,
                    facts=merged_facts,
                    weight=weight,
                )
            log.info("memory_refreshed", conversation_id=conversation_id)
    except Exception:
        log.exception("memory_refresh_failed", conversation_id=conversation_id)
