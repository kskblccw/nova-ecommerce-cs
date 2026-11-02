"""定时清理：过期审批、失效会话、检索日志与老旧 checkpoint。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, text, update

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import AuthSession, PendingAction, RetrievalLog
from ecommerce_cs.db.session import SessionLocal, engine

log = get_logger("cleanup")

CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes", "checkpoint_blobs")


async def _cleanup_checkpoints(cutoff: datetime) -> int:
    settings = get_settings()
    if settings.checkpointer_backend != "postgres":
        return 0

    # 只清理“最近无消息且无待确认操作”的会话，避免误删活跃上下文
    where_sql = """
        thread_id IN (
            SELECT 'conv:' || c.id
            FROM conversations c
            WHERE COALESCE(c.last_message_at, c.created_at) < :cutoff
              AND NOT EXISTS (
                  SELECT 1 FROM pending_actions p
                  WHERE p.conversation_id = c.id AND p.status = 'pending'
              )
        )
    """
    removed = 0
    try:
        async with engine.begin() as conn:
            for table in CHECKPOINT_TABLES:
                result = await conn.execute(
                    text(f"DELETE FROM {table} WHERE {where_sql}"),
                    {"cutoff": cutoff},
                )
                removed += result.rowcount or 0
    except Exception:
        log.exception("checkpoint_cleanup_failed")
    return removed


async def _delete_threads(thread_ids: set[str]) -> int:
    """删除指定会话的 checkpoint，避免过期审批的 interrupt 继续阻塞新消息。"""
    if get_settings().checkpointer_backend != "postgres" or not thread_ids:
        return 0

    placeholders = ",".join(f":t{index}" for index in range(len(thread_ids)))
    params = {f"t{index}": thread_id for index, thread_id in enumerate(thread_ids)}
    removed = 0
    try:
        async with engine.begin() as conn:
            for table in CHECKPOINT_TABLES:
                result = await conn.execute(
                    text(f"DELETE FROM {table} WHERE thread_id IN ({placeholders})"),
                    params,
                )
                removed += result.rowcount or 0
    except Exception:
        log.exception("approval_thread_cleanup_failed")
    return removed


async def run_cleanup() -> dict[str, int]:
    settings = get_settings()
    now = datetime.now(UTC)
    cutoff_pending = now - timedelta(minutes=settings.pending_action_ttl_minutes)
    cutoff_sessions = now - timedelta(days=settings.auth_session_retention_days)
    cutoff_logs = now - timedelta(days=settings.retrieval_log_retention_days)
    cutoff_checkpoints = now - timedelta(days=settings.checkpoint_retention_days)

    async with SessionLocal() as db:
        expired = await db.execute(
            update(PendingAction)
            .where(PendingAction.status == "pending", PendingAction.created_at < cutoff_pending)
            .values(status="expired", resolved_at=now)
            .returning(PendingAction.conversation_id)
        )
        expired_rows = expired.all()
        stale = await db.execute(
            update(PendingAction)
            .where(
                PendingAction.status == "executing",
                PendingAction.created_at < cutoff_pending,
            )
            .values(status="failed", resolved_at=now)
            .returning(PendingAction.conversation_id)
        )
        stale_rows = stale.all()
        sessions = await db.execute(
            delete(AuthSession).where(
                or_(
                    AuthSession.expires_at < cutoff_sessions,
                    AuthSession.revoked_at < cutoff_sessions,
                )
            )
        )
        logs = await db.execute(
            delete(RetrievalLog).where(RetrievalLog.created_at < cutoff_logs)
        )
        await db.commit()

    thread_ids = {
        f"conv:{row[0]}" for row in [*expired_rows, *stale_rows]
    }
    threads = await _delete_threads(thread_ids)
    checkpoints = await _cleanup_checkpoints(cutoff_checkpoints)
    stats = {
        "expired_actions": len(expired_rows),
        "stale_executions": len(stale_rows),
        "approval_threads": threads,
        "auth_sessions": getattr(sessions, "rowcount", 0) or 0,
        "retrieval_logs": getattr(logs, "rowcount", 0) or 0,
        "checkpoints": checkpoints,
    }
    log.info("cleanup_finished", **stats)
    return stats


async def cleanup_loop(stop_event: asyncio.Event) -> None:
    interval = max(300, get_settings().cleanup_interval_minutes * 60)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            return
        except TimeoutError:
            pass
        try:
            await run_cleanup()
        except Exception:
            log.exception("cleanup_failed")
