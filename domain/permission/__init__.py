"""
Stub for domain.permission — qiqiclaw 移植后所有权限放开。

require_path_permission / require_system_permission 装饰器变成 passthrough；
PermissionService 所有方法返回 True / 空列表。
枚举类保留原值，避免上层引用 AdapterPermission.CREATE 等出错。
"""
from functools import wraps
from typing import Any, Callable, Iterable, List


class PathAction:
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    SHARE = "share"
    LIST = "list"
    UPLOAD = "upload"
    DOWNLOAD = "download"
    EXECUTE = "execute"


class SystemPermission:
    USER_CREATE = "system.user.create"
    USER_EDIT = "system.user.edit"
    USER_DELETE = "system.user.delete"
    USER_LIST = "system.user.list"
    ROLE_MANAGE = "system.role.manage"
    CONFIG_EDIT = "system.config.edit"
    AUDIT_VIEW = "system.audit.view"
    ADAPTER_MANAGE = "system.adapter.manage"
    PROCESSOR_MANAGE = "system.processor.manage"
    PLUGIN_MANAGE = "system.plugin.manage"
    CONFIG_MANAGE = "system.config.manage"
    AI_MANAGE = "system.ai.manage"
    TASK_MANAGE = "system.task.manage"


class AdapterPermission:
    CREATE = "adapter.create"
    EDIT = "adapter.edit"
    DELETE = "adapter.delete"
    LIST = "adapter.list"


def require_path_permission(*args: Any, **kwargs: Any) -> Callable:
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


def require_system_permission(*args: Any, **kwargs: Any) -> Callable:
    return require_path_permission(*args, **kwargs)


class PathMatcher:
    @staticmethod
    def match(*args: Any, **kwargs: Any) -> bool:
        return True


class PermissionService:
    @staticmethod
    async def has_path_permission(*args: Any, **kwargs: Any) -> bool:
        return True

    @staticmethod
    async def check_path_permission(*args: Any, **kwargs: Any) -> bool:
        return True

    @staticmethod
    async def list_permissions(*args: Any, **kwargs: Any) -> List[Any]:
        return []

    @staticmethod
    async def get_user_path_permissions(*args: Any, **kwargs: Any) -> List[Any]:
        return []

    @staticmethod
    async def filter_visible_paths(
        user: Any, paths: Iterable[Any], *args: Any, **kwargs: Any
    ) -> List[Any]:
        return list(paths)

    @staticmethod
    async def filter_paths_by_permission(
        user: Any, paths: Iterable[Any], *args: Any, **kwargs: Any
    ) -> List[Any]:
        return list(paths)

    @staticmethod
    async def has_system_permission(*args: Any, **kwargs: Any) -> bool:
        return True


__all__ = [
    "PermissionService",
    "PathMatcher",
    "require_system_permission",
    "require_path_permission",
    "PathAction",
    "SystemPermission",
    "AdapterPermission",
]
