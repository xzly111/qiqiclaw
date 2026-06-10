"""QiQiClaw file-module tools for the main Agent.

These wrappers expose the same file-module handlers used by the embedded
Foxel/QiQiClaw pages to the normal CLI/TUI Agent tool loop.  The names are
prefixed to avoid collisions with the CLI's native file and web tools.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict

from domain.agent.tools import list_tool_specs
from domain.agent.tools.base import normalize_tool_result
from tools.registry import registry

logger = logging.getLogger(__name__)

TOOLSET = "qiqiclaw_file_modules"
PREFIX = "filemod_"

_WRITE_APPROVAL_KEY = "qiqiclaw_file_module_write"
_INIT_LOCK = threading.Lock()
_INIT_DONE = False


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def _schema_for(spec) -> dict:
    return {
        "name": f"{PREFIX}{spec.name}",
        "description": (
            "[QiQiClaw 桌面文件模块] "
            + spec.description
            + (
                " 此操作会修改文件模块状态，必须等待用户审批后才能执行。"
                if spec.requires_confirmation
                else ""
            )
        ),
        "parameters": spec.parameters,
    }


def _approval_description(spec_name: str) -> str:
    return f"QiQiClaw 文件模块写操作: {spec_name}"


def _format_approval_command(spec_name: str, args: Dict[str, Any]) -> str:
    return f"{PREFIX}{spec_name} " + _json(args)


async def _ensure_file_modules_ready() -> None:
    """Initialize the embedded file-module DB/runtime when called from Agent-only paths."""
    global _INIT_DONE
    if _INIT_DONE:
        return
    with _INIT_LOCK:
        if _INIT_DONE:
            return
    try:
        from tortoise import Tortoise

        if not getattr(Tortoise, "_inited", False):
            from db.session import init_db

            await init_db()

        from domain.adapters import runtime_registry
        from domain.tasks import task_queue_service

        await _ensure_local_home_adapter()
        await runtime_registry.refresh()
        await _load_plugin_processors_for_agent()
        await task_queue_service.start_worker()
        with _INIT_LOCK:
            _INIT_DONE = True
    except Exception:
        logger.exception("QiQiClaw file-module runtime initialization failed")
        raise


async def _ensure_local_home_adapter() -> None:
    from models import StorageAdapter
    import os

    home_dir = os.path.expanduser("~")
    local = await StorageAdapter.get_or_none(path="/local")
    if local is None:
        local = await StorageAdapter.get_or_none(name="Local Home")
    if local is None:
        await StorageAdapter.create(
            name="Local Home",
            type="local",
            config={"root": home_dir},
            enabled=True,
            path="/local",
            sub_path=None,
        )
        return

    name_owner = await StorageAdapter.get_or_none(name="Local Home")
    if name_owner is None or name_owner.id == local.id:
        local.name = "Local Home"
    local.type = "local"
    local.config = {"root": home_dir}
    local.enabled = True
    local.path = "/local"
    local.sub_path = None
    await local.save()


async def _load_plugin_processors_for_agent() -> None:
    """Load plugin processors when no FastAPI app is available to mount routes."""
    from domain.plugins import PluginLoader
    from domain.plugins.types import PluginManifest
    from models.database import Plugin

    plugins = await Plugin.all()
    for plugin in plugins:
        if not plugin.key:
            continue
        try:
            manifest = None
            if plugin.manifest:
                try:
                    manifest = PluginManifest.model_validate(plugin.manifest)
                except Exception:
                    manifest = PluginLoader.read_manifest(plugin.key)
            else:
                manifest = PluginLoader.read_manifest(plugin.key)
            if not manifest or not manifest.backend or not manifest.backend.processors:
                continue
            loaded_processors = PluginLoader.load_all_processors(plugin.key, manifest)
            plugin.loaded_processors = loaded_processors if loaded_processors else None
            await plugin.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("QiQiClaw file-module plugin processor load failed for %s: %s", plugin.key, exc)


async def _request_gateway_approval(spec_name: str, args: Dict[str, Any]) -> tuple[bool, str | None]:
    """Ask the active gateway session to approve a file-module write."""
    from tools.approval import (
        _ApprovalEntry,
        _fire_approval_hook,
        _gateway_notify_cbs,
        _gateway_queues,
        _get_approval_config,
        _lock,
        approve_session,
        get_current_session_key,
        is_approved,
        is_current_session_yolo_enabled,
    )
    import asyncio

    session_key = get_current_session_key(default="")
    if is_current_session_yolo_enabled() or is_approved(session_key, _WRITE_APPROVAL_KEY):
        return True, None

    command = _format_approval_command(spec_name, args)
    description = _approval_description(spec_name)
    with _lock:
        notify_cb = _gateway_notify_cbs.get(session_key)

    if notify_cb is None:
        return False, (
            "BLOCKED: QiQiClaw 文件模块写操作需要桌面端审批，但当前会话没有可用审批通道。"
        )

    approval_data = {
        "command": command,
        "pattern_key": _WRITE_APPROVAL_KEY,
        "pattern_keys": [_WRITE_APPROVAL_KEY],
        "description": description,
    }
    entry = _ApprovalEntry(approval_data)
    with _lock:
        _gateway_queues.setdefault(session_key, []).append(entry)

    _fire_approval_hook(
        "pre_approval_request",
        command=command,
        description=description,
        pattern_key=_WRITE_APPROVAL_KEY,
        pattern_keys=[_WRITE_APPROVAL_KEY],
        session_key=session_key,
        surface="gateway",
    )

    try:
        notify_cb(approval_data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("QiQiClaw file-module approval notify failed: %s", exc)
        with _lock:
            queue = _gateway_queues.get(session_key, [])
            if entry in queue:
                queue.remove(entry)
            if not queue:
                _gateway_queues.pop(session_key, None)
        return False, "BLOCKED: Failed to send file-module approval request to user. Do NOT retry."

    timeout = _get_approval_config().get("gateway_timeout", 300)
    try:
        timeout = max(int(timeout), 0)
    except (TypeError, ValueError):
        timeout = 300

    resolved = await asyncio.to_thread(entry.event.wait, timeout)
    with _lock:
        queue = _gateway_queues.get(session_key, [])
        if entry in queue:
            queue.remove(entry)
        if not queue:
            _gateway_queues.pop(session_key, None)

    choice = entry.result
    outcome = "timeout" if not resolved else (choice or "timeout")
    _fire_approval_hook(
        "post_approval_response",
        command=command,
        description=description,
        pattern_key=_WRITE_APPROVAL_KEY,
        pattern_keys=[_WRITE_APPROVAL_KEY],
        session_key=session_key,
        surface="gateway",
        choice=outcome,
    )

    if not resolved or choice is None or choice == "deny":
        reason = "timed out" if not resolved else "denied by user"
        return False, f"BLOCKED: QiQiClaw 文件模块写操作 {reason}. Do NOT retry this action."

    if choice in {"session", "always"}:
        approve_session(session_key, _WRITE_APPROVAL_KEY)
    return True, None


def _make_handler(spec):
    async def _handler(args: Dict[str, Any], **_: Any) -> str:
        call_args = args if isinstance(args, dict) else {}
        if spec.requires_confirmation:
            approved, block_message = await _request_gateway_approval(spec.name, call_args)
            if not approved:
                return _json({"error": block_message or "BLOCKED: approval required"})
        try:
            await _ensure_file_modules_ready()
            result = await spec.handler(call_args)
            return _json(normalize_tool_result(result))
        except Exception as exc:  # noqa: BLE001
            logger.exception("QiQiClaw file-module tool %s failed", spec.name)
            return _json({"error": f"{type(exc).__name__}: {exc}"})

    return _handler


for _spec in list_tool_specs():
    registry.register(
        name=f"{PREFIX}{_spec.name}",
        toolset=TOOLSET,
        schema=_schema_for(_spec),
        handler=_make_handler(_spec),
        is_async=True,
        description=_schema_for(_spec)["description"],
        emoji="Q",
        max_result_size_chars=100_000,
    )
