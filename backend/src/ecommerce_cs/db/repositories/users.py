"""用户与会话令牌的仓储。"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import AuthSession, User


async def get_user_by_phone_hash(db: AsyncSession, phone_hash: str) -> User | None:
    return await db.scalar(select(User).where(User.phone_hash == phone_hash))


async def get_user_by_id(db: AsyncSession, user_id: int) -> User | None:
    return await db.scalar(select(User).where(User.id == user_id))


async def create_user(
    db: AsyncSession,
    *,
    name: str,
    phone_hash: str,
    phone_masked: str,
    password_hash: str,
    role: str = "user",
) -> User:
    user = User(
        name=name,
        phone_hash=phone_hash,
        phone_masked=phone_masked,
        password_hash=password_hash,
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def update_password(db: AsyncSession, user_id: int, password_hash: str) -> None:
    await db.execute(
        update(User).where(User.id == user_id).values(password_hash=password_hash)
    )
    await db.commit()


async def create_auth_session(
    db: AsyncSession,
    *,
    user_id: int,
    token_hash: str,
) -> AuthSession:
    settings = get_settings()
    session = AuthSession(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def get_active_session(db: AsyncSession, token_hash: str) -> AuthSession | None:
    now = datetime.now(UTC)
    return await db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )


async def revoke_session(db: AsyncSession, session_id: str) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()


async def revoke_user_sessions(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
