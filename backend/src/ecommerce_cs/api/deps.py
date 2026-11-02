"""FastAPI 依赖：当前用户、管理员校验。"""

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.errors import ForbiddenError, UnauthorizedError
from ecommerce_cs.core.security import decode_access_token, hash_token
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import User
from ecommerce_cs.db.repositories import users as users_repo
from ecommerce_cs.db.session import get_db


def _extract_token(request: Request) -> str:
    token = request.cookies.get(get_settings().cookie_name, "")
    if token:
        return token
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


async def get_current_user(
    request: Request, db: AsyncSession = Depends(get_db)
) -> User:
    token = _extract_token(request)
    if not token:
        raise UnauthorizedError()

    payload = decode_access_token(token)
    if not payload:
        raise UnauthorizedError("登录已过期，请重新登录")

    session = await users_repo.get_active_session(db, hash_token(token))
    if not session or str(session.user_id) != str(payload.get("sub")):
        raise UnauthorizedError("登录已失效，请重新登录")

    user = await users_repo.get_user_by_id(db, session.user_id)
    if not user:
        raise UnauthorizedError()
    return user


async def get_admin_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise ForbiddenError("需要管理员权限")
    return user
