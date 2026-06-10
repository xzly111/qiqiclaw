from typing import Any, Dict, Optional

from domain.adapters import AdapterCreate, AdapterService

from .base import ToolSpec


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


async def _adapters_list(_: Dict[str, Any]) -> Dict[str, Any]:
    adapters = await AdapterService.list_adapters()
    return {"adapters": [_dump(item) for item in adapters]}


async def _adapters_available_types(_: Dict[str, Any]) -> Dict[str, Any]:
    return {"types": await AdapterService.available_adapter_types()}


async def _adapters_usage(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter_id = args.get("adapter_id")
    if adapter_id is None:
        usages = await AdapterService.list_adapter_usages()
        return {"usages": [_dump(item) for item in usages]}
    return _dump(await AdapterService.get_adapter_usage(int(adapter_id)))


async def _adapters_create(args: Dict[str, Any]) -> Dict[str, Any]:
    data = AdapterCreate(
        name=str(args.get("name") or "").strip(),
        type=str(args.get("type") or "").strip(),
        config=args.get("config") if isinstance(args.get("config"), dict) else {},
        enabled=bool(args.get("enabled", True)),
        path=str(args.get("path") or "").strip(),
        sub_path=args.get("sub_path") if isinstance(args.get("sub_path"), str) else None,
    )
    return _dump(await AdapterService.create_adapter(data, current_user=None))


async def _adapters_update(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter_id = int(args.get("adapter_id"))
    data = AdapterCreate(
        name=str(args.get("name") or "").strip(),
        type=str(args.get("type") or "").strip(),
        config=args.get("config") if isinstance(args.get("config"), dict) else {},
        enabled=bool(args.get("enabled", True)),
        path=str(args.get("path") or "").strip(),
        sub_path=args.get("sub_path") if isinstance(args.get("sub_path"), str) else None,
    )
    return _dump(await AdapterService.update_adapter(adapter_id, data, current_user=None))


async def _adapters_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    adapter_id = int(args.get("adapter_id"))
    return await AdapterService.delete_adapter(adapter_id, current_user=None)


TOOLS: Dict[str, ToolSpec] = {
    "adapters_list": ToolSpec(
        name="adapters_list",
        description="列出存储挂载配置，包括挂载路径、类型、启用状态和配置。",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        requires_confirmation=False,
        handler=_adapters_list,
    ),
    "adapters_available_types": ToolSpec(
        name="adapters_available_types",
        description="列出可创建的存储挂载类型及配置字段。",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        requires_confirmation=False,
        handler=_adapters_available_types,
    ),
    "adapters_usage": ToolSpec(
        name="adapters_usage",
        description="查看存储挂载容量用量；不传 adapter_id 时返回全部挂载用量。",
        parameters={
            "type": "object",
            "properties": {
                "adapter_id": {"type": "integer", "description": "挂载 ID；可选"},
            },
            "additionalProperties": False,
        },
        requires_confirmation=False,
        handler=_adapters_usage,
    ),
    "adapters_create": ToolSpec(
        name="adapters_create",
        description="创建存储挂载。会修改挂载配置，必须经过用户确认。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "挂载名称"},
                "type": {"type": "string", "description": "挂载类型，例如 local/webdav/s3"},
                "path": {"type": "string", "description": "虚拟挂载路径，例如 /work"},
                "config": {"type": "object", "description": "挂载类型对应配置"},
                "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
                "sub_path": {"type": "string", "description": "可选子路径"},
            },
            "required": ["name", "type", "path", "config"],
            "additionalProperties": False,
        },
        requires_confirmation=True,
        handler=_adapters_create,
    ),
    "adapters_update": ToolSpec(
        name="adapters_update",
        description="更新存储挂载。会修改挂载配置，必须经过用户确认。",
        parameters={
            "type": "object",
            "properties": {
                "adapter_id": {"type": "integer", "description": "挂载 ID"},
                "name": {"type": "string", "description": "挂载名称"},
                "type": {"type": "string", "description": "挂载类型，例如 local/webdav/s3"},
                "path": {"type": "string", "description": "虚拟挂载路径，例如 /work"},
                "config": {"type": "object", "description": "挂载类型对应配置"},
                "enabled": {"type": "boolean", "description": "是否启用，默认 true"},
                "sub_path": {"type": "string", "description": "可选子路径"},
            },
            "required": ["adapter_id", "name", "type", "path", "config"],
            "additionalProperties": False,
        },
        requires_confirmation=True,
        handler=_adapters_update,
    ),
    "adapters_delete": ToolSpec(
        name="adapters_delete",
        description="删除存储挂载。会修改挂载配置，必须经过用户确认。",
        parameters={
            "type": "object",
            "properties": {
                "adapter_id": {"type": "integer", "description": "挂载 ID"},
            },
            "required": ["adapter_id"],
            "additionalProperties": False,
        },
        requires_confirmation=True,
        handler=_adapters_delete,
    ),
}
