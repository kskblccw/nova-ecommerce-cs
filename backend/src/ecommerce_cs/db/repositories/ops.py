"""审批动作、审计、人工工单与满意度反馈。"""

from datetime import UTC, datetime

from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.db.models import (
    AnswerFeedback,
    AuditEvent,
    HandoffTicket,
    PendingAction,
)


async def create_pending_action(
    db: AsyncSession,
    *,
    conversation_id: str,
    user_id: int,
    action_type: str,
    summary: str,
    payload: dict,
    idempotency_key: str,
) -> PendingAction:
    action = PendingAction(
        conversation_id=conversation_id,
        user_id=user_id,
        type=action_type,
        summary=summary,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)
    return action


async def get_pending_action(db: AsyncSession, action_id: str) -> PendingAction | None:
    return await db.scalar(select(PendingAction).where(PendingAction.id == action_id))


async def get_latest_pending(db: AsyncSession, conversation_id: str) -> PendingAction | None:
    return await db.scalar(
        select(PendingAction)
        .where(
            PendingAction.conversation_id == conversation_id,
            PendingAction.status == "pending",
        )
        .order_by(desc(PendingAction.created_at))
        .limit(1)
    )


async def mark_action(db: AsyncSession, action: PendingAction, status: str) -> None:
    action.status = status
    action.resolved_at = datetime.now(UTC)
    await db.commit()


async def mark_action_by_id(db: AsyncSession, action_id: str, status: str) -> None:
    await db.execute(
        update(PendingAction)
        .where(PendingAction.id == action_id)
        .values(status=status, resolved_at=datetime.now(UTC))
    )
    await db.commit()


async def claim_action(db: AsyncSession, action_id: str) -> bool:
    """原子地把 pending 迁移到 executing，防止并发/重复提交执行多次。"""
    result = await db.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.status == "pending",
        )
        .values(status="executing")
    )
    await db.commit()
    return (getattr(result, "rowcount", 0) or 0) == 1


async def reject_action(db: AsyncSession, action_id: str) -> bool:
    result = await db.execute(
        update(PendingAction)
        .where(
            PendingAction.id == action_id,
            PendingAction.status == "pending",
        )
        .values(status="rejected", resolved_at=datetime.now(UTC))
    )
    await db.commit()
    return (getattr(result, "rowcount", 0) or 0) == 1


async def audit(
    db: AsyncSession,
    *,
    user_id: int | None,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    payload: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            payload=payload or {},
        )
    )
    await db.commit()


async def create_handoff_ticket(
    db: AsyncSession, *, conversation_id: str, user_id: int, reason: str
) -> HandoffTicket:
    ticket = HandoffTicket(
        conversation_id=conversation_id,
        user_id=user_id,
        reason=reason,
        status="queued",
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def add_feedback(
    db: AsyncSession,
    *,
    message_id: str,
    conversation_id: str,
    user_id: int,
    rating: int,
    comment: str = "",
) -> AnswerFeedback:
    """同一消息只保留一条反馈；重复点击用 upsert，避免唯一键竞态返回 500。"""
    statement = (
        insert(AnswerFeedback)
        .values(
            message_id=message_id,
            conversation_id=conversation_id,
            user_id=user_id,
            rating=rating,
            comment=comment,
        )
        .on_conflict_do_update(
            index_elements=["message_id"],
            set_={"rating": rating, "comment": comment},
        )
    )
    await db.execute(statement)
    await db.commit()

    feedback = await db.scalar(
        select(AnswerFeedback).where(AnswerFeedback.message_id == message_id)
    )
    if feedback is None:
        raise RuntimeError("反馈写入失败")
    return feedback


async def list_audit_events(db: AsyncSession, limit: int = 50) -> list[AuditEvent]:
    result = await db.scalars(
        select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(limit)
    )
    return list(result.all())
