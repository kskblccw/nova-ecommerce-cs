"""订单域工具：列表、详情、物流、取消与改地址。"""

import uuid
from datetime import UTC, datetime, timedelta

from langchain_core.tools import tool

from ecommerce_cs.agents.tools.common import (
    ToolReturn,
    current_user_id,
    error_result,
    format_money,
    login_required,
    order_public,
    order_status_hint,
    tool_result,
)
from ecommerce_cs.db.repositories import commerce as commerce_repo
from ecommerce_cs.db.session import SessionLocal

CARRIERS = {
    "SF": "顺丰速运",
    "YT": "圆通速递",
    "ZTO": "中通快递",
    "JD": "京东物流",
    "DB": "德邦快递",
}

STATUS_FLOW = [
    "快递员已揽收",
    "到达分拣中心",
    "运输中",
    "到达目的地分拣中心",
    "派送中",
    "已签收",
]


async def _load_owned_order(db, order_no: str, user_id: int):
    order = await commerce_repo.get_order_by_no(db, order_no)
    if not order:
        return None, error_result(f"未找到订单 {order_no}，请确认订单号是否正确。", "not_found")
    if order.user_id != user_id:
        return None, error_result(f"订单 {order_no} 不属于当前用户，请核对后再试。", "forbidden")
    return order, None


@tool(response_format="content_and_artifact")
async def list_my_orders() -> ToolReturn:
    """查询当前登录用户的全部订单列表（含状态、金额、商品）。"""
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        orders = await commerce_repo.list_user_orders(db, user_id)
        if not orders:
            return tool_result("orders", "当前用户暂无订单记录。", {"orders": []})

        items_map = await commerce_repo.get_order_items_bulk(db, [o.id for o in orders])
        data = [order_public(order, items_map.get(order.id, [])) for order in orders]
        lines = [
            f"{order['order_no']}｜{order['status']}｜{format_money(order['total'])}"
            for order in data
        ]
        summary = f"共 {len(data)} 笔订单：\n" + "\n".join(lines)
        return tool_result("orders", summary, {"orders": data})


@tool(response_format="content_and_artifact")
async def get_order(order_no: str) -> ToolReturn:
    """根据订单号查询订单详情（状态、金额、地址、商品明细）。

    Args:
        order_no: 订单编号，如 ORD202607160001
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        order, failure = await _load_owned_order(db, order_no, user_id)
        if failure:
            return failure
        items = await commerce_repo.get_order_items(db, order.id)
        data = order_public(order, items)
        lines = [f"订单 {order.order_no}｜状态：{order.status}｜金额：{format_money(order.total)}"]
        if items:
            lines.append(
                "商品：" + "、".join(f"{product.name}×{item.quantity}" for item, product in items)
            )
        lines.append(f"收货地址：{order.address}")
        if order.tracking_no:
            lines.append(f"物流单号：{order.tracking_no}")
        return tool_result("order_detail", "\n".join(lines), data)


@tool(response_format="content_and_artifact")
async def track_shipment(order_no: str) -> ToolReturn:
    """查询订单物流轨迹，返回带时间线的结构化事件。

    Args:
        order_no: 订单编号
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        order, failure = await _load_owned_order(db, order_no, user_id)
        if failure:
            return failure

        if not order.tracking_no:
            return tool_result(
                "shipment",
                f"订单 {order.order_no} 暂未发货，当前状态：{order.status}。",
                {
                    "order_no": order.order_no,
                    "tracking_no": "",
                    "carrier": "",
                    "status": order.status,
                    "events": [],
                },
            )

        carrier = "快递"
        for prefix, name in CARRIERS.items():
            if prefix in order.tracking_no:
                carrier = name
                break

        seed = sum(ord(char) for char in order.tracking_no)
        current_index = 2 + seed % (len(STATUS_FLOW) - 2)
        if order.status in ("已签收", "已完成"):
            current_index = len(STATUS_FLOW) - 1

        now = datetime.now(UTC)
        events = []
        for index in range(current_index + 1):
            offset_hours = (current_index - index) * (6 + (seed % 5))
            events.append(
                {
                    "time": (now - timedelta(hours=offset_hours)).strftime("%m-%d %H:%M"),
                    "label": STATUS_FLOW[index],
                }
            )

        data = {
            "order_no": order.order_no,
            "tracking_no": order.tracking_no,
            "carrier": carrier,
            "status": STATUS_FLOW[current_index],
            "events": events,
        }
        summary = (
            f"{carrier} {order.tracking_no}\n当前状态：{STATUS_FLOW[current_index]}\n"
            + "\n".join(f"{event['time']} {event['label']}" for event in events)
        )
        return tool_result("shipment", summary, data)


@tool(response_format="content_and_artifact")
async def cancel_order(order_no: str) -> ToolReturn:
    """取消未付款订单。会返回待确认操作，用户确认后才真正取消。

    Args:
        order_no: 需要取消的订单号
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        order, failure = await _load_owned_order(db, order_no, user_id)
        if failure:
            return failure
        if order.status != "待付款":
            return error_result(
                f"订单 {order.order_no} 当前状态为「{order.status}」，无法取消。"
                + order_status_hint(order.status),
                "invalid_status",
            )

        payload = {
            "type": "cancel_order",
            "summary": f"取消订单 {order.order_no}（{format_money(order.total)}）",
            "payload": {"order_no": order.order_no},
            "idempotency_key": uuid.uuid4().hex,
        }
        return tool_result(
            "pending_action",
            f"即将取消订单 {order.order_no}，请在弹窗中确认。",
            {"action": payload},
            requires_confirmation=True,
        )


@tool(response_format="content_and_artifact")
async def request_address_change(order_no: str, new_address: str) -> ToolReturn:
    """申请修改未发货订单的收货地址。会返回待确认操作，确认后才写库。

    Args:
        order_no: 订单编号
        new_address: 新的完整收货地址
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        order, failure = await _load_owned_order(db, order_no, user_id)
        if failure:
            return failure
        if order.status == "已发货":
            return error_result(
                f"订单 {order.order_no} 已发货，无法修改地址。请联系人工客服协助转寄。",
                "invalid_status",
            )
        if order.status in ("已完成", "已取消"):
            return error_result(
                f"订单 {order.order_no} 当前状态为「{order.status}」，无法修改地址。",
                "invalid_status",
            )

        payload = {
            "type": "address_change",
            "summary": f"修改订单 {order.order_no} 的收货地址",
            "payload": {
                "order_no": order.order_no,
                "old_address": order.address,
                "new_address": new_address.strip(),
            },
            "idempotency_key": uuid.uuid4().hex,
        }
        return tool_result(
            "pending_action",
            f"即将把订单 {order.order_no} 的地址改为：{new_address}，请确认。",
            {"action": payload},
            requires_confirmation=True,
        )


ORDER_TOOLS = [list_my_orders, get_order, track_shipment, cancel_order, request_address_change]
