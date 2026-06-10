"""
Stub for domain.audit — qiqiclaw 移植后审计被关闭。

audit() 装饰器变成 no-op，AuditAction 保留以兼容 import。
"""
from enum import Enum
from functools import wraps
from typing import Any, Callable


class AuditAction(str, Enum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    LOGIN = "login"
    LOGOUT = "logout"
    REGISTER = "register"
    SHARE = "share"
    SEARCH = "search"
    EXECUTE = "execute"


def audit(*args: Any, **kwargs: Any) -> Callable:
    """No-op decorator. Accepts any positional/keyword args for compatibility."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*a: Any, **kw: Any):
            return await func(*a, **kw)

        @wraps(func)
        def sync_wrapper(*a: Any, **kw: Any):
            return func(*a, **kw)

        import inspect

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


__all__ = ["audit", "AuditAction"]
