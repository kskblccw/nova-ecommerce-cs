"""售后域工具：政策咨询、工单查询、申请与取消。"""

import uuid

from langchain_core.tools import tool

from ecommerce_cs.agents.tools.common import (
    ToolReturn,
    current_user_id,
    error_result,
    login_required,
    return_public,
    tool_result,
)
from ecommerce_cs.db.repositories import commerce as commerce_repo
from ecommerce_cs.db.session import SessionLocal
from ecommerce_cs.rag.retriever import format_hits
from ecommerce_cs.rag.retriever import search as rag_search


@tool(response_format="content_and_artifact")
async def check_return_policy() -> ToolReturn:
    """查询退换货与售后政策（7 天无理由、15 天换货、退款时效）。"""
    async with SessionLocal() as db:
        hits = await rag_search(db, "退换货政策 退款时效 质量问题 换货流程", limit=3)
        return tool_result(
            "knowledge",
            format_hits(hits),
            {"hits": [hit.to_dict() for hit in hits], "query": "售后政策"},
        )


@tool(response_format="content_and_artifact")
async def list_my_returns() -> ToolReturn:
    """查询当前用户的售后申请列表与处理进度。"""
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        requests = await commerce_repo.list_user_returns(db, user_id)
        if not requests:
            return tool_result("returns", "当前用户暂无售后申请。", {"returns": []})
        data = [return_public(request) for request in requests]
        summary = "\n".join(
            f"{item['return_no']}｜{item['type']}｜{item['status']}｜{item['reason']}"
            for item in data
        )
        return tool_result("returns", f"共 {len(data)} 条售后申请：\n{summary}", {"returns": data})


@tool(response_format="content_and_artifact")
async def get_return_status(return_no: str) -> ToolReturn:
    """根据售后申请编号查询处理状态。

    Args:
        return_no: 售后申请编号，如 RTN202607160001
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        request = await commerce_repo.get_return_by_no(db, return_no)
        if not request:
            return error_result(f"未找到售后申请 {return_no}。", "not_found")
        if request.user_id != user_id:
            return error_result(f"售后申请 {return_no} 不属于当前用户。", "forbidden")
        data = return_public(request)
        return tool_result(
            "return_detail",
            f"申请 {request.return_no}｜类型：{request.type}｜状态：{request.status}",
            data,
        )


@tool(response_format="content_and_artifact")
async def request_return(order_no: str, reason: str, return_type: str = "退货") -> ToolReturn:
    """提交退换货申请。会返回待确认操作，用户确认后才创建工单。

    Args:
        order_no: 关联订单号
        reason: 售后原因，如「质量问题」「尺码不合适」
        return_type: 售后类型，可选「退货」「换货」「退款」
    """
    guard = login_required()
    if guard:
        return guard
    if return_type not in ("退货", "换货", "退款"):
        return error_result("售后类型仅支持 退货 / 换货 / 退款。", "invalid_args")

    user_id = int(current_user_id() or 0)
    async with SessionLocal() as db:
        order = await commerce_repo.get_order_by_no(db, order_no)
        if not order:
            return error_result(f"未找到订单 {order_no}，请确认订单号。", "not_found")
        if order.user_id != user_id:
            return error_result(f"订单 {order_no} 不属于当前用户，请核对订单号。", "forbidden")
        if order.status in ("待付款", "已取消"):
            return error_result(
                f"订单 {order_no} 当前状态为「{order.status}」，无法申请售后。",
                "invalid_status",
            )
        existing = await commerce_repo.list_user_returns(db, user_id)
        if any(item.order_id == order.id and item.status in ("待审核", "已通过") for item in existing):
            return error_result(
                f"订单 {order_no} 已有处理中的售后申请，请勿重复提交。", "conflict"
            )

    payload = {
        "type": "create_return",
        "summary": f"为订单 {order_no} 提交「{return_type}」申请（原因：{reason}）",
        "payload": {"order_no": order_no, "return_type": return_type, "reason": reason},
        "idempotency_key": uuid.uuid4().hex,
    }
    return tool_result(
        "pending_action",
        f"即将提交订单 {order_no} 的{return_type}申请，请在弹窗中确认。",
        {"action": payload},
        requires_confirmation=True,
    )


@tool(response_format="content_and_artifact")
async def cancel_return(return_no: str) -> ToolReturn:
    """取消待审核的售后申请。会返回待确认操作。

    Args:
        return_no: 售后申请编号
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)

    async with SessionLocal() as db:
        request = await commerce_repo.get_return_by_no(db, return_no)
        if not request:
            return error_result(f"未找到售后申请 {return_no}。", "not_found")
        if request.user_id != user_id:
            return error_result(f"售后申请 {return_no} 不属于当前用户。", "forbidden")
        if request.status != "待审核":
            return error_result(
                f"申请 {return_no} 当前状态为「{request.status}」，无法取消。", "invalid_status"
            )

    payload = {
        "type": "cancel_return",
        "summary": f"取消售后申请 {return_no}",
        "payload": {"return_no": return_no},
        "idempotency_key": uuid.uuid4().hex,
    }
    return tool_result(
        "pending_action",
        f"即将取消售后申请 {return_no}，请确认。",
        {"action": payload},
        requires_confirmation=True,
    )


AFTERSALE_TOOLS = [
    check_return_policy,
    list_my_returns,
    get_return_status,
    request_return,
    cancel_return,
]
