"""
Stub for domain.auth — qiqiclaw 移植后完全去鉴权。

保留接口签名兼容性：所有调用都返回固定 admin 用户，永不抛 401。
原始鉴权实现已移除，四个文件模块由 QiQiClaw 本地会话驱动。
"""
from typing import Any, Optional

from pydantic import BaseModel


class User(BaseModel):
    id: int = 1
    username: str = "admin"
    email: Optional[str] = "admin@local"
    full_name: Optional[str] = "Administrator"
    disabled: Optional[bool] = False
    is_admin: bool = True


class UserInDB(User):
    hashed_password: str = ""


class Token(BaseModel):
    access_token: str = "stub-token"
    token_type: str = "bearer"


class TokenData(BaseModel):
    username: Optional[str] = "admin"


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str
    full_name: Optional[str] = None


class UpdateMeRequest(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str


_ADMIN = User()


async def get_current_user() -> User:
    return _ADMIN


async def get_current_active_user() -> User:
    return _ADMIN


def authenticate_user_db(*args: Any, **kwargs: Any):
    return _ADMIN


def create_access_token(*args: Any, **kwargs: Any) -> str:
    return "stub-token"


def get_password_hash(password: str) -> str:
    return ""


def verify_password(plain: str, hashed: str) -> bool:
    return True


async def has_users() -> bool:
    return True


async def register_user(*args: Any, **kwargs: Any) -> User:
    return _ADMIN


async def request_password_reset(*args: Any, **kwargs: Any) -> None:
    return None


def verify_password_reset_token(*args: Any, **kwargs: Any) -> Optional[str]:
    return _ADMIN.username


async def reset_password_with_token(*args: Any, **kwargs: Any) -> User:
    return _ADMIN


ALGORITHM = "HS256"


class AuthService:
    @staticmethod
    async def create_access_token(*args: Any, **kwargs: Any) -> str:
        return "stub-token"

    @staticmethod
    async def get_current_user(*args: Any, **kwargs: Any) -> User:
        return _ADMIN

    @staticmethod
    async def get_current_active_user(*args: Any, **kwargs: Any) -> User:
        return _ADMIN

    @staticmethod
    async def login(*args: Any, **kwargs: Any) -> Token:
        return Token()

    @staticmethod
    async def register_user(*args: Any, **kwargs: Any) -> User:
        return _ADMIN


__all__ = [
    "ALGORITHM",
    "AuthService",
    "authenticate_user_db",
    "create_access_token",
    "get_current_active_user",
    "get_current_user",
    "get_password_hash",
    "has_users",
    "register_user",
    "request_password_reset",
    "reset_password_with_token",
    "verify_password",
    "verify_password_reset_token",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "RegisterRequest",
    "Token",
    "TokenData",
    "UpdateMeRequest",
    "User",
    "UserInDB",
]
