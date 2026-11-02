"""认证相关请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    phone: str = Field(..., description="11 位手机号")
    password: str = Field(..., min_length=6, max_length=72)
    name: str = Field(default="", max_length=50)


class LoginRequest(BaseModel):
    phone: str
    password: str = Field(..., min_length=1, max_length=72)


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=72)


class UserOut(BaseModel):
    id: int
    name: str
    phone_masked: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuthResponse(BaseModel):
    user: UserOut
    message: str = "ok"
