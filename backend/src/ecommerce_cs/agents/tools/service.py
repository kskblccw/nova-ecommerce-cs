"""服务类工具：人工转接工单与当前用户信息。"""

from langchain_core.tools import tool

from ecommerce_cs.agents.tools.common import (
    ToolReturn,
    current_conversation_id,
    current_user_id,
    login_required,
    tool_result,
    user_public,
)
from ecommerce_cs.db.repositories import ops as ops_repo
from ecommerce_cs.db.repositories import users as users_repo
from ecommerce_cs.db.session import SessionLocal


@tool(response_format="content_and_artifact")
async def get_current_user() -> ToolReturn:
    """获取当前登录用户的基本信息（昵称、脱敏手机号、角色）。"""
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)
    async with SessionLocal() as db:
        user = await users_repo.get_user_by_id(db, user_id)
        if not user:
            return tool_result("info", "未找到当前用户信息。", None)
        return tool_result("user", f"当前用户：{user.name}（{user.phone_masked}）", user_public(user))


@tool(response_format="content_and_artifact")
async def transfer_to_human(reason: str) -> ToolReturn:
    """创建人工客服转接工单，并告知用户联系方式与排队情况。

    Args:
        reason: 转人工原因，如「需要人工处理退款」「投诉」
    """
    guard = login_required()
    if guard:
        return guard
    user_id = int(current_user_id() or 0)
    conversation_id = current_conversation_id()

    async with SessionLocal() as db:
        ticket = await ops_repo.create_handoff_ticket(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
            reason=reason,
        )
        data = {"ticket_id": ticket.id, "status": ticket.status, "reason": reason}
        return tool_result(
            "handoff",
            "已为您创建人工客服工单，当前排队中。客服工作时间为每天 9:00-22:00，"
            "也可拨打 400-888-8888。",
            data,
        )


SERVICE_TOOLS = [get_current_user, transfer_to_human]
