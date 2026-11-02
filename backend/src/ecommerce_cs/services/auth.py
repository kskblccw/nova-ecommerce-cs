"""注册、登录、登出与改密。"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.errors import ConflictError, UnauthorizedError
from ecommerce_cs.core.security import (
    create_access_token,
    hash_password,
    hash_phone,
    hash_token,
    mask_phone,
    verify_password,
)
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import AuthSession, User
from ecommerce_cs.db.repositories import users as users_repo

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")


def validate_phone(phone: str) -> str:
    phone = phone.strip()
    if not PHONE_RE.match(phone):
        raise ConflictError("请输入有效的 11 位手机号")
    return phone


def validate_password(password: str) -> str:
    if len(password) < 6:
        raise ConflictError("密码至少 6 位")
    return password


async def _issue_token(db: AsyncSession, user: User) -> str:
    """生成 JWT，并把 JWT 的哈希作为会话令牌存入 DB（Cookie 里只放 JWT）。"""
    settings = get_settings()
    session_id = uuid.uuid4().hex
    token = create_access_token(subject=str(user.id), session_id=session_id)
    db.add(
        AuthSession(
            id=session_id,
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes),
        )
    )
    await db.commit()
    return token


async def register(
    db: AsyncSession, *, phone: str, password: str, name: str
) -> tuple[User, str]:
    phone = validate_phone(phone)
    password = validate_password(password)
    name = (name or "").strip() or f"用户{mask_phone(phone)[-4:]}"
    if await users_repo.get_user_by_phone_hash(db, hash_phone(phone)):
        raise ConflictError("该手机号已注册，请直接登录")

    user = await users_repo.create_user(
        db,
        name=name[:50],
        phone_hash=hash_phone(phone),
        phone_masked=mask_phone(phone),
        password_hash=hash_password(password),
    )
    return user, await _issue_token(db, user)


async def login(db: AsyncSession, *, phone: str, password: str) -> tuple[User, str]:
    phone = validate_phone(phone)
    user = await users_repo.get_user_by_phone_hash(db, hash_phone(phone))
    if not user or not verify_password(password, user.password_hash):
        raise UnauthorizedError("手机号或密码错误")
    return user, await _issue_token(db, user)


async def change_password(
    db: AsyncSession, *, user: User, old_password: str, new_password: str
) -> None:
    if not verify_password(old_password, user.password_hash):
        raise UnauthorizedError("原密码错误")
    new_password = validate_password(new_password)
    await users_repo.update_password(db, user.id, hash_password(new_password))
    await users_repo.revoke_user_sessions(db, user.id)
