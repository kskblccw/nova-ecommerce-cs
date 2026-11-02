"""认证路由：注册、登录、登出、当前用户与改密。"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ecommerce_cs.api.deps import get_current_user
from ecommerce_cs.core.errors import UnauthorizedError
from ecommerce_cs.core.metrics import incr
from ecommerce_cs.core.ratelimit import (
    client_ip,
    enforce_login_rate,
    enforce_register_rate,
    ensure_login_allowed,
    record_login_failure,
    reset_login_failure,
)
from ecommerce_cs.core.security import hash_token
from ecommerce_cs.core.settings import get_settings
from ecommerce_cs.db.models import User
from ecommerce_cs.db.repositories import users as users_repo
from ecommerce_cs.db.session import get_db
from ecommerce_cs.schemas.auth import (
    AuthResponse,
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UserOut,
)
from ecommerce_cs.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


def set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    await enforce_register_rate(request)
    user, token = await auth_service.register(
        db, phone=payload.phone, password=payload.password, name=payload.name
    )
    set_auth_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user), message="注册成功")


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> AuthResponse:
    await enforce_login_rate(request)
    ip = client_ip(request)
    await ensure_login_allowed(payload.phone, ip)
    try:
        user, token = await auth_service.login(
            db, phone=payload.phone, password=payload.password
        )
    except UnauthorizedError:
        incr("auth_login_failure")
        await record_login_failure(payload.phone, ip)
        raise
    await reset_login_failure(payload.phone)
    incr("auth_login_success")
    set_auth_cookie(response, token)
    return AuthResponse(user=UserOut.model_validate(user), message="登录成功")


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> dict:
    settings = get_settings()
    token = request.cookies.get(settings.cookie_name, "")
    if token:
        session = await users_repo.get_active_session(db, hash_token(token))
        if session:
            await users_repo.revoke_session(db, session.id)
    clear_auth_cookie(response)
    return {"message": "已退出登录"}


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if not get_settings().cookie_name:
        raise UnauthorizedError()
    await auth_service.change_password(
        db,
        user=user,
        old_password=payload.old_password,
        new_password=payload.new_password,
    )
    clear_auth_cookie(response)
    return {"message": "密码已修改，请重新登录"}
