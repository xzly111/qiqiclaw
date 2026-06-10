from typing import Any, Dict

from domain.plugins import PluginService

from .base import ToolSpec


async def _plugins_list(_: Dict[str, Any]) -> Dict[str, Any]:
    plugins = await PluginService.list_plugins()
    return {
        "plugins": [
            plugin.model_dump(mode="json") if hasattr(plugin, "model_dump") else dict(plugin)
            for plugin in plugins
        ]
    }


async def _plugins_get(args: Dict[str, Any]) -> Dict[str, Any]:
    key_or_id = str(args.get("key_or_id") or args.get("key") or "").strip()
    if not key_or_id:
        raise ValueError("missing_key_or_id")
    plugin = await PluginService.get_plugin(key_or_id)
    return plugin.model_dump(mode="json") if hasattr(plugin, "model_dump") else dict(plugin)


TOOLS: Dict[str, ToolSpec] = {
    "plugins_list": ToolSpec(
        name="plugins_list",
        description="列出应用中心已安装应用/插件，包含名称、支持格式、独立应用入口和插件元数据。",
        parameters={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        requires_confirmation=False,
        handler=_plugins_list,
    ),
    "plugins_get": ToolSpec(
        name="plugins_get",
        description="查看应用中心指定应用/插件详情。",
        parameters={
            "type": "object",
            "properties": {
                "key_or_id": {"type": "string", "description": "插件 key 或数字 ID"},
            },
            "required": ["key_or_id"],
            "additionalProperties": False,
        },
        requires_confirmation=False,
        handler=_plugins_get,
    ),
}
