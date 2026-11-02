"""工具公共工具函数：运行时上下文、结构化返回与序列化。"""

import json
from datetime import datetime
from typing import Any

from langgraph.runtime import get_runtime

from ecommerce_cs.db.models import Order, OrderItem, Product, ReturnRequest, User


def context() -> dict[str, Any]:
    return dict(get_runtime().context or {})


def current_user_id() -> int | None:
    value = context().get("user_id")
    return int(value) if value else None


def current_conversation_id() -> str:
    return str(context().get("conversation_id") or "")


ToolReturn = tuple[str, dict[str, Any]]


def dumps(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def tool_result(kind: str, summary: str, data: Any = None, **extra: Any) -> ToolReturn:
    """返回 (给 LLM 看的精简文本, 给前端/后处理用的结构化 artifact)。"""
    artifact: dict[str, Any] = {"kind": kind, "summary": summary, "data": data}
    artifact.update(extra)
    return summary, artifact


def error_result(message: str, code: str = "error") -> ToolReturn:
    return message, {"kind": "error", "summary": message, "data": {"code": code}}


def login_required() -> ToolReturn | None:
    if not current_user_id():
        return error_result("请先登录后再进行此操作。", code="unauthorized")
    return None


def user_public(user: User) -> dict:
    return {"id": user.id, "name": user.name, "phone_masked": user.phone_masked, "role": user.role}


def product_public(product: Product) -> dict:
    return {
        "id": product.id,
        "name": product.name,
        "category": product.category,
        "price": product.price,
        "stock": product.stock,
        "description": (product.description or "")[:120],
        "image_url": product.image_url,
        "specs": product.specs or {},
    }


def order_public(
    order: Order, items: list[tuple[OrderItem, Product]] | None = None
) -> dict:
    return {
        "id": order.id,
        "order_no": order.order_no,
        "status": order.status,
        "total": round(order.total, 2),
        "address": order.address,
        "tracking_no": order.tracking_no,
        "created_at": order.created_at.isoformat(),
        "items": [
            {
                "product_id": product.id,
                "name": product.name,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
            }
            for item, product in (items or [])
        ],
    }


def return_public(request: ReturnRequest) -> dict:
    return {
        "return_no": request.return_no,
        "order_id": request.order_id,
        "type": request.type,
        "reason": request.reason,
        "status": request.status,
        "created_at": request.created_at.isoformat(),
        "updated_at": request.updated_at.isoformat(),
    }


def order_status_hint(status: str) -> str:
    hints = {
        "待付款": "订单尚未付款，可取消后重新下单。",
        "已发货": "订单已发货，可在物流时间线查看最新进度。",
        "已完成": "订单已完成，支持 7 天无理由退货。",
        "已取消": "订单已取消，如仍需商品请重新下单。",
    }
    return hints.get(status, "")


def format_money(value: float) -> str:
    return f"¥{value:,.2f}"


def format_dt(value: datetime | None) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M")
