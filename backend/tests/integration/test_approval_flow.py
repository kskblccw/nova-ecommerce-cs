"""审批动作集成测试：同一 action 并发提交只执行一次。"""

import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select

from ecommerce_cs.core.security import hash_phone, mask_phone
from ecommerce_cs.db.models import (
    AuditEvent,
    Conversation,
    ConversationSummary,
    Message,
    Order,
    PendingAction,
    ReturnRequest,
    User,
)
from ecommerce_cs.db.repositories import chat as chat_repo
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.services.approval import resolve

pytestmark = pytest.mark.integration


async def _seed_actor(db, suffix: str) -> tuple[User, Conversation, Order]:
    phone = f"199{suffix}"
    user = User(
        name="集成测试用户",
        phone_hash=hash_phone(phone),
        phone_masked=mask_phone(phone),
        password_hash="x",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    conversation = await chat_repo.create_conversation(db, user_id=user.id)
    order = Order(
        order_no=f"IT{suffix}",
        user_id=user.id,
        status="已完成",
        total=1.0,
        address="集成测试地址",
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return user, conversation, order


async def _seed_action(db, conversation_id: str, user_id: int, **kwargs) -> PendingAction:
    return await ops_repo.create_pending_action(
        db,
        conversation_id=conversation_id,
        user_id=user_id,
        idempotency_key=f"it-{uuid.uuid4().hex}",
        **kwargs,
    )


async def _cleanup(user_id: int, conversation_id: str, order_id: int) -> None:
    async with SessionLocal() as db:
        await db.execute(delete(ReturnRequest).where(ReturnRequest.order_id == order_id))
        await db.execute(delete(PendingAction).where(PendingAction.conversation_id == conversation_id))
        await db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        await db.execute(
            delete(ConversationSummary).where(ConversationSummary.conversation_id == conversation_id)
        )
        await db.execute(delete(AuditEvent).where(AuditEvent.user_id == user_id))
        await db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        await db.execute(delete(Order).where(Order.id == order_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest.mark.asyncio
async def test_concurrent_create_return_executes_once(db_ready):
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    async with SessionLocal() as db:
        user, conversation, order = await _seed_actor(db, suffix)
        action = await _seed_action(
            db,
            conversation.id,
            user.id,
            action_type="create_return",
            summary="提交退货申请",
            payload={"order_no": order.order_no, "return_type": "退货", "reason": "测试"},
        )

    payload = {
        "idempotency_key": action.idempotency_key,
        "type": action.type,
        "payload": action.payload,
        "summary": action.summary,
    }
    try:
        messages = await asyncio.gather(
            resolve(action=payload, decision="approve", user_id=user.id),
            resolve(action=payload, decision="approve", user_id=user.id),
        )

        async with SessionLocal() as db:
            returns = int(
                await db.scalar(
                    select(func.count(ReturnRequest.id)).where(
                        ReturnRequest.order_id == order.id
                    )
                )
                or 0
            )
            record = await ops_repo.get_pending_action(db, action.id)

        assert returns == 1, "并发审批只能创建一条售后申请"
        assert sum("售后申请已提交" in message for message in messages) == 1
        assert record is not None and record.status == "approved"
    finally:
        await _cleanup(user.id, conversation.id, order.id)


@pytest.mark.asyncio
async def test_second_approval_is_noop(db_ready):
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    async with SessionLocal() as db:
        user, conversation, order = await _seed_actor(db, suffix)
        order.status = "待付款"
        await db.commit()
        action = await _seed_action(
            db,
            conversation.id,
            user.id,
            action_type="cancel_order",
            summary="取消订单",
            payload={"order_no": order.order_no},
        )

    payload = {
        "idempotency_key": action.idempotency_key,
        "type": action.type,
        "payload": action.payload,
        "summary": action.summary,
    }
    try:
        first = await resolve(action=payload, decision="approve", user_id=user.id)
        second = await resolve(action=payload, decision="approve", user_id=user.id)

        async with SessionLocal() as db:
            current = await db.scalar(select(Order).where(Order.id == order.id))
            record = await ops_repo.get_pending_action(db, action.id)

        assert "已取消" in first
        assert "已处理过" in second
        assert current is not None and current.status == "已取消"
        assert record is not None and record.status == "approved"
    finally:
        await _cleanup(user.id, conversation.id, order.id)


@pytest.mark.asyncio
async def test_reject_does_not_change_business_data(db_ready):
    suffix = f"{uuid.uuid4().int % 10**8:08d}"
    async with SessionLocal() as db:
        user, conversation, order = await _seed_actor(db, suffix)
        order.status = "待付款"
        await db.commit()
        action = await _seed_action(
            db,
            conversation.id,
            user.id,
            action_type="cancel_order",
            summary="取消订单",
            payload={"order_no": order.order_no},
        )

    payload = {
        "idempotency_key": action.idempotency_key,
        "type": action.type,
        "payload": action.payload,
        "summary": action.summary,
    }
    try:
        message = await resolve(action=payload, decision="reject", user_id=user.id)
        async with SessionLocal() as db:
            current = await db.scalar(select(Order).where(Order.id == order.id))
            record = await ops_repo.get_pending_action(db, action.id)

        assert "未做任何变更" in message
        assert current is not None and current.status == "待付款"
        assert record is not None and record.status == "rejected"
    finally:
        await _cleanup(user.id, conversation.id, order.id)
