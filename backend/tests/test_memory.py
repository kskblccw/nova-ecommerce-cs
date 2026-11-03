"""记忆系统测试：历史格式化、上下文拼装与刷新节流。"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ecommerce_cs.services import memory as memory_service


def test_format_history_maps_roles_and_keeps_latest():
    messages = [
        HumanMessage(content="a"),
        AIMessage(content="b"),
        HumanMessage(content="c"),
        AIMessage(content="d"),
    ]
    assert memory_service._format_history(messages, limit=2) == "[用户] c\n[客服] d"


def test_format_history_skips_tool_messages():
    messages = [
        HumanMessage(content="查订单"),
        ToolMessage(content="工具结果", tool_call_id="call-1"),
        AIMessage(content="订单已发货"),
    ]
    assert memory_service._format_history(messages) == "[用户] 查订单\n[客服] 订单已发货"


def test_too_soon_handles_naive_and_aware_timestamps():
    now = datetime.now(UTC)
    naive = now.replace(tzinfo=None)

    assert memory_service._too_soon(naive) is True
    assert memory_service._too_soon(now - timedelta(minutes=60)) is False
    assert memory_service._too_soon(None) is False


@pytest.mark.asyncio
async def test_build_context_block_merges_summary_and_profile(monkeypatch):
    async def fake_summary(db, conversation_id):
        return "用户想退货，订单 ORD2026"

    async def fake_profile(db, user_id):
        return SimpleNamespace(preferences=["偏好自营", "喜欢蓝色"], facts={"城市": "上海"})

    monkeypatch.setattr(memory_service.chat_repo, "get_summary", fake_summary)
    monkeypatch.setattr(memory_service.chat_repo, "get_profile", fake_profile)

    block = await memory_service.build_context_block(None, 1, "c1")

    assert "【历史摘要】用户想退货，订单 ORD2026" in block
    assert "偏好：偏好自营、喜欢蓝色" in block
    assert "城市:上海" in block


@pytest.mark.asyncio
async def test_build_context_block_returns_empty_without_memory(monkeypatch):
    async def fake_summary(db, conversation_id):
        return ""

    async def fake_profile(db, user_id):
        return None

    monkeypatch.setattr(memory_service.chat_repo, "get_summary", fake_summary)
    monkeypatch.setattr(memory_service.chat_repo, "get_profile", fake_profile)

    assert await memory_service.build_context_block(None, 1, "c1") == ""


@pytest.mark.asyncio
async def test_build_context_block_truncates_long_summary(monkeypatch):
    async def fake_summary(db, conversation_id):
        return "长" * 500

    async def fake_profile(db, user_id):
        return None

    monkeypatch.setattr(memory_service.chat_repo, "get_summary", fake_summary)
    monkeypatch.setattr(memory_service.chat_repo, "get_profile", fake_profile)

    block = await memory_service.build_context_block(None, 1, "c1")

    assert len(block) <= 450
    assert block.startswith("【历史摘要】")


@pytest.mark.asyncio
async def test_refresh_memory_skips_when_already_running(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_refresh(conversation_id, user_id):
        calls.append((conversation_id, user_id))

    monkeypatch.setattr(memory_service, "_refresh_memory", fake_refresh)
    lock = asyncio.Lock()
    await lock.acquire()
    monkeypatch.setitem(memory_service._refresh_locks, "c1", (lock, 0.0))

    try:
        await memory_service.refresh_memory("c1", 1)
        assert calls == []
    finally:
        lock.release()

    await memory_service.refresh_memory("c1", 1)
    assert calls == [("c1", 1)]


@pytest.mark.asyncio
async def test_scheduled_refresh_keeps_task_reference(monkeypatch):
    finished = asyncio.Event()

    async def fake_refresh(conversation_id, user_id):
        finished.set()

    monkeypatch.setattr(memory_service, "refresh_memory", fake_refresh)

    memory_service.schedule_refresh("c-schedule", 1)

    assert memory_service._background_tasks
    await asyncio.wait_for(finished.wait(), timeout=2)
    await memory_service.drain_background_tasks(timeout=2)
    await asyncio.sleep(0)
    assert not memory_service._background_tasks


def test_refresh_lock_is_reused_within_ttl(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(memory_service.time, "monotonic", lambda: now[0])

    try:
        first = memory_service.refresh_lock("lock-reuse")
        now[0] = memory_service._REFRESH_LOCK_TTL_SECONDS - 1
        assert memory_service.refresh_lock("lock-reuse") is first
    finally:
        memory_service._refresh_locks.pop("lock-reuse", None)


def test_idle_refresh_lock_is_recycled_after_ttl(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(memory_service.time, "monotonic", lambda: now[0])

    try:
        first = memory_service.refresh_lock("lock-ttl")
        now[0] = memory_service._REFRESH_LOCK_TTL_SECONDS + 1
        assert memory_service.refresh_lock("lock-ttl") is not first
    finally:
        memory_service._refresh_locks.pop("lock-ttl", None)


@pytest.mark.asyncio
async def test_prune_keeps_held_refresh_lock():
    held = asyncio.Lock()
    await held.acquire()
    memory_service._refresh_locks["held-lock"] = (held, 0.0)
    memory_service._refresh_locks["idle-lock"] = (asyncio.Lock(), 0.0)

    try:
        memory_service._prune_refresh_locks(memory_service._REFRESH_LOCK_TTL_SECONDS + 1)
        assert "held-lock" in memory_service._refresh_locks
        assert "idle-lock" not in memory_service._refresh_locks
    finally:
        held.release()
        memory_service._refresh_locks.pop("held-lock", None)
        memory_service._refresh_locks.pop("idle-lock", None)


def test_capacity_limit_prunes_idle_refresh_locks(monkeypatch):
    monkeypatch.setattr(memory_service, "_MAX_REFRESH_LOCKS", 2)
    monkeypatch.setattr(memory_service.time, "monotonic", lambda: 700.0)
    memory_service._refresh_locks["old-a"] = (asyncio.Lock(), 0.0)
    memory_service._refresh_locks["old-b"] = (asyncio.Lock(), 0.0)

    try:
        assert memory_service.refresh_lock("new-lock") is not None
        assert "new-lock" in memory_service._refresh_locks
        assert "old-a" not in memory_service._refresh_locks
        assert "old-b" not in memory_service._refresh_locks
    finally:
        for key in ("old-a", "old-b", "new-lock"):
            memory_service._refresh_locks.pop(key, None)
