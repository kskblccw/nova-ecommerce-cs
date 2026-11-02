"""审批执行：确认后才写库，幂等可重放。"""

from sqlalchemy import select

from ecommerce_cs.core.logging import get_logger
from ecommerce_cs.db.models import PendingAction
from ecommerce_cs.db.repositories import commerce as commerce_repo
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.session import SessionLocal

log = get_logger("approval")


async def ensure_action(
    *,
    action: dict,
    conversation_id: str,
    user_id: int,
) -> PendingAction:
    """按 idempotency_key 创建审批记录，重复调用不会产生多条。"""
    async with SessionLocal() as db:
        existing = await db.scalar(
            select(PendingAction).where(
                PendingAction.idempotency_key == action["idempotency_key"]
            )
        )
        if existing:
            return existing
        return await ops_repo.create_pending_action(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            action_type=action["type"],
            summary=action.get("summary", ""),
            payload=action.get("payload", {}),
            idempotency_key=action["idempotency_key"],
        )


async def _execute(db, action: PendingAction, user_id: int) -> tuple[bool, str]:
    payload = action.payload or {}
    kind = action.type

    if kind == "address_change":
        order = await commerce_repo.get_order_by_no(db, payload.get("order_no", ""))
        if not order or order.user_id != user_id:
            return False, "操作失败：订单不存在或不属于当前用户。"
        new_address = payload.get("new_address", "").strip()
        if not new_address:
            return False, "操作失败：新地址为空。"
        await commerce_repo.update_order_address(db, order.id, new_address)
        await ops_repo.audit(
            db,
            user_id=user_id,
            action="order.address_changed",
            resource_type="order",
            resource_id=order.order_no,
            payload={"old": payload.get("old_address", ""), "new": new_address},
        )
        return True, f"已确认，订单 {order.order_no} 的收货地址已更新。\n新地址：{new_address}"

    if kind == "cancel_order":
        order = await commerce_repo.get_order_by_no(db, payload.get("order_no", ""))
        if not order or order.user_id != user_id:
            return False, "操作失败：订单不存在或不属于当前用户。"
        if order.status != "待付款":
            return False, f"订单 {order.order_no} 当前状态为「{order.status}」，无法取消。"
        await commerce_repo.cancel_order(db, order.id)
        await ops_repo.audit(
            db,
            user_id=user_id,
            action="order.cancelled",
            resource_type="order",
            resource_id=order.order_no,
        )
        return True, f"已确认，订单 {order.order_no} 已取消。"

    if kind == "create_return":
        order = await commerce_repo.get_order_by_no(db, payload.get("order_no", ""))
        if not order or order.user_id != user_id:
            return False, "操作失败：订单不存在或不属于当前用户。"
        if order.status in ("待付款", "已取消"):
            return False, f"订单 {order.order_no} 当前状态为「{order.status}」，无法申请售后。"
        if await commerce_repo.has_active_return(db, order.id):
            return False, f"订单 {order.order_no} 已有处理中的售后申请，请勿重复提交。"
        request = await commerce_repo.create_return_request(
            db,
            order_id=order.id,
            user_id=user_id,
            return_type=payload.get("return_type", "退货"),
            reason=payload.get("reason", "用户申请"),
        )
        await ops_repo.audit(
            db,
            user_id=user_id,
            action="return.created",
            resource_type="return",
            resource_id=request.return_no,
            payload={"order_no": order.order_no},
        )
        return (
            True,
            f"已确认，售后申请已提交。\n申请编号：{request.return_no}\n"
            f"类型：{request.type}｜订单：{order.order_no}\n"
            "请按短信提示寄回商品，我们将在 1-3 个工作日内处理。",
        )

    if kind == "cancel_return":
        return_no = payload.get("return_no", "")
        existing_request = await commerce_repo.get_return_by_no(db, return_no)
        if not existing_request or existing_request.user_id != user_id:
            return False, f"操作失败：售后申请 {return_no} 不存在或不属于当前用户。"
        cancelled = await commerce_repo.cancel_return_request(db, return_no)
        if not cancelled:
            return False, f"申请 {return_no} 当前状态无法取消。"
        await ops_repo.audit(
            db,
            user_id=user_id,
            action="return.cancelled",
            resource_type="return",
            resource_id=return_no,
        )
        return True, f"已确认，售后申请 {return_no} 已取消。"

    return False, f"操作失败：不支持的操作类型（{kind}）。"


async def _status_message(db, action_id: str) -> str:
    record = await ops_repo.get_pending_action(db, action_id)
    status = record.status if record else "unknown"
    return f"该操作已处理过（状态：{status}），无需重复操作。"


async def resolve(*, action: dict, decision: str, user_id: int) -> str:
    """执行或取消审批动作：原子抢占 + 状态机，保证同一动作只执行一次。"""
    async with SessionLocal() as db:
        record = await db.scalar(
            select(PendingAction).where(
                PendingAction.idempotency_key == action["idempotency_key"]
            )
        )
        if not record:
            return "操作失败：审批记录不存在。"
        if record.user_id != user_id:
            return "操作失败：无权处理该审批。"
        if record.status != "pending":
            return f"该操作已处理过（状态：{record.status}），无需重复操作。"

        if decision != "approve":
            if not await ops_repo.reject_action(db, record.id):
                return await _status_message(db, record.id)
            await ops_repo.audit(
                db,
                user_id=user_id,
                action="approval.rejected",
                resource_type=record.type,
                resource_id=record.id,
            )
            log.info("approval_rejected", action_id=record.id, type=record.type)
            return "已取消本次操作，未做任何变更。"

        if not await ops_repo.claim_action(db, record.id):
            return await _status_message(db, record.id)

        try:
            ok, message = await _execute(db, record, user_id)
        except Exception:
            log.exception("approval_execute_failed", action_id=record.id)
            await db.rollback()
            await ops_repo.mark_action_by_id(db, record.id, "failed")
            return "操作执行失败，请稍后重试或联系人工客服。"

        await ops_repo.mark_action(db, record, "approved" if ok else "failed")
        await ops_repo.audit(
            db,
            user_id=user_id,
            action="approval.approved" if ok else "approval.failed",
            resource_type=record.type,
            resource_id=record.id,
        )
        log.info(
            "approval_executed",
            action_id=record.id,
            type=record.type,
            ok=ok,
        )
        return message
