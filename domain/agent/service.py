import asyncio
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from domain.auth import User

from .tools import get_tool, mcp_tool_descriptors, tool_result_to_content
from .types import AgentChatRequest, PendingMcpCall


def _normalize_path(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    value = str(path).strip().replace("\\", "/")
    if not value:
        return None
    if not value.startswith("/"):
        value = "/" + value
    return value.rstrip("/") or "/"


def _ensure_mcp_call_ids(message: Dict[str, Any]) -> Dict[str, Any]:
    mcp_calls = message.get("mcp_calls")
    if not isinstance(mcp_calls, list):
        return message
    for idx, call in enumerate(mcp_calls):
        if not isinstance(call, dict):
            continue
        call_id = call.get("id")
        if not isinstance(call_id, str) or not call_id.strip():
            call["id"] = f"call_{idx}"
    return message


def _find_last_assistant_mcp_calls(messages: List[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        mcp_calls = msg.get("mcp_calls")
        if isinstance(mcp_calls, list) and mcp_calls:
            return idx, msg
    raise HTTPException(status_code=400, detail="没有可确认的待执行操作")


def _existing_mcp_result_ids(messages: List[Dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        call_id = msg.get("mcp_call_id")
        if isinstance(call_id, str) and call_id.strip():
            ids.add(call_id)
    return ids


def _tool_requires_confirmation(tool_descriptor: Dict[str, Any]) -> bool:
    meta = tool_descriptor.get("meta") if isinstance(tool_descriptor.get("meta"), dict) else {}
    if "requires_confirmation" in meta:
        return bool(meta.get("requires_confirmation"))
    annotations = tool_descriptor.get("annotations") if isinstance(tool_descriptor.get("annotations"), dict) else {}
    return not bool(annotations.get("readOnlyHint"))


def _extract_pending(mcp_call: Dict[str, Any], requires_confirmation: bool) -> PendingMcpCall:
    arguments = mcp_call.get("arguments") if isinstance(mcp_call.get("arguments"), dict) else {}
    return PendingMcpCall(
        id=str(mcp_call.get("id") or ""),
        name=str(mcp_call.get("name") or ""),
        arguments=arguments,
        requires_confirmation=requires_confirmation,
    )


def _sse(event: str, data: Any) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _format_exc(exc: BaseException) -> str:
    text = str(exc)
    return text if text else exc.__class__.__name__


async def _list_mcp_tools() -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for item in mcp_tool_descriptors():
        tools.append(
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema,
                "annotations": item.annotations,
                "meta": item.meta,
            }
        )
    return tools


async def _execute_mcp_call(name: str, arguments: Dict[str, Any]) -> str:
    spec = get_tool(name)
    if not spec:
        return tool_result_to_content({"error": f"unknown_tool: {name}"})
    try:
        result = await spec.handler(arguments)
    except Exception as exc:  # noqa: BLE001
        result = {"error": str(exc)}
    return tool_result_to_content(result)


def _last_user_text(messages: List[Dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return ""


def _json_from_text(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _manual_call_from_user_text(text: str, tools: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    payload = _json_from_text(text)
    if not payload:
        return None
    name = payload.get("name") or payload.get("tool")
    if not isinstance(name, str) or name not in tools:
        return None
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {k: v for k, v in payload.items() if k not in {"name", "tool", "id"}}
    return {
        "id": str(payload.get("id") or f"manual_{uuid.uuid4().hex[:8]}"),
        "name": name,
        "arguments": arguments,
    }


def _extract_quoted_path(text: str) -> Optional[str]:
    for pattern in (r"`([^`]+)`", r'"([^"]+)"', r"'([^']+)'"):
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    match = re.search(r"(/[^\s，。；;]+)", text)
    if match:
        return match.group(1).strip()
    return None


def _resolve_user_path(text: str, current_path: Optional[str], *, default_to_current: bool = True) -> Optional[str]:
    raw = _extract_quoted_path(text)
    if raw:
        return _normalize_path(raw)
    return current_path if default_to_current else None


def _intent_call_from_user_text(
    text: str,
    current_path: Optional[str],
    tools: Dict[str, Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    compact = text.strip().lower()
    if not compact:
        return None

    call_id = f"intent_{uuid.uuid4().hex[:8]}"

    def has_any(*words: str) -> bool:
        return any(word.lower() in compact for word in words)

    if has_any("处理器", "processor", "processors") and has_any("列出", "查看", "list") and "processors_list" in tools:
        return {"id": call_id, "name": "processors_list", "arguments": {}}

    if has_any("列出", "浏览", "查看目录", "目录", "list", "browse") and "vfs_list_dir" in tools:
        path = _resolve_user_path(text, current_path) or "/"
        return {"id": call_id, "name": "vfs_list_dir", "arguments": {"path": path}}

    if has_any("信息", "属性", "详情", "stat", "metadata") and "vfs_stat" in tools:
        path = _resolve_user_path(text, current_path)
        if path:
            return {"id": call_id, "name": "vfs_stat", "arguments": {"path": path}}

    if has_any("读取", "打开文本", "查看文件", "内容", "read", "inspect") and "vfs_read_text" in tools:
        path = _resolve_user_path(text, current_path, default_to_current=False)
        if path:
            return {"id": call_id, "name": "vfs_read_text", "arguments": {"path": path, "max_chars": 8000}}

    if has_any("搜索", "查找", "find", "search") and "vfs_search" in tools:
        query = text
        for prefix in ("搜索", "查找", "find", "search"):
            query = re.sub(rf"^\s*{re.escape(prefix)}\s*", "", query, flags=re.I)
        query = query.strip(" ：:，。")
        if query:
            return {"id": call_id, "name": "vfs_search", "arguments": {"q": query, "mode": "filename"}}

    if has_any("新建文件夹", "创建目录", "mkdir") and "vfs_mkdir" in tools:
        path = _resolve_user_path(text, current_path, default_to_current=False)
        if path:
            return {"id": call_id, "name": "vfs_mkdir", "arguments": {"path": path}}

    return None


def _assistant_capability_message(current_path: Optional[str], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    read_tools = [t["name"] for t in tools if not _tool_requires_confirmation(t)]
    write_tools = [t["name"] for t in tools if _tool_requires_confirmation(t)]
    path_line = f"当前目录：{current_path}\n" if current_path else ""
    content = (
        f"{path_line}"
        "文件模块 Agent 接口已接入 MCP 工具和审批执行链。\n"
        "当前 QiQiClaw 的文件模块 AI 规划层尚未接到主聊天模型，因此这个接口现在可用于：列出工具、执行已确认的 MCP 调用、保留审批流。\n"
        "可直接读工具：" + (", ".join(read_tools) or "无") + "\n"
        "需确认工具：" + (", ".join(write_tools) or "无") + "\n"
        "可以直接说：列出当前目录、查看 `/local/foo.txt` 内容、搜索 合同、列出处理器。\n"
        "也支持 JSON 调用，例如 "
        '{"tool":"vfs_list_dir","arguments":{"path":"/local"}}。'
    )
    return {"role": "assistant", "content": content}


class AgentService:
    @classmethod
    async def chat(cls, req: AgentChatRequest, user: Optional[User]) -> Dict[str, Any]:
        history: List[Dict[str, Any]] = list(req.messages or [])
        current_path = _normalize_path(req.context.current_path if req.context else None)
        new_messages: List[Dict[str, Any]] = []
        pending: List[PendingMcpCall] = []

        approved_ids = {i for i in (req.approved_mcp_call_ids or []) if isinstance(i, str) and i.strip()}
        rejected_ids = {i for i in (req.rejected_mcp_call_ids or []) if isinstance(i, str) and i.strip()}

        tools_schema = await _list_mcp_tools()
        tool_index = {tool["name"]: tool for tool in tools_schema if tool.get("name")}

        if approved_ids or rejected_ids:
            tool_messages = await cls._resolve_approved_calls(history, tool_index, approved_ids, rejected_ids)
            new_messages.extend(tool_messages)

        user_text = _last_user_text(history)
        manual_call = _manual_call_from_user_text(user_text, tool_index)
        if manual_call is None:
            manual_call = _intent_call_from_user_text(user_text, current_path, tool_index)
        if manual_call:
            name = manual_call["name"]
            tool_desc = tool_index.get(name)
            if tool_desc and _tool_requires_confirmation(tool_desc) and not req.auto_execute:
                assistant = {
                    "role": "assistant",
                    "content": f"操作 `{name}` 需要确认。",
                    "mcp_calls": [manual_call],
                }
                new_messages.append(assistant)
                pending.append(_extract_pending(manual_call, True))
            else:
                content = await _execute_mcp_call(name, manual_call["arguments"])
                new_messages.append({"role": "tool", "mcp_call_id": manual_call["id"], "content": content})
        elif not new_messages:
            new_messages.append(_assistant_capability_message(current_path, tools_schema))

        payload: Dict[str, Any] = {"messages": new_messages}
        if pending:
            payload["pending_mcp_calls"] = [item.model_dump() for item in pending]
        return payload

    @classmethod
    async def _resolve_approved_calls(
        cls,
        messages: List[Dict[str, Any]],
        tool_index: Dict[str, Dict[str, Any]],
        approved_ids: set[str],
        rejected_ids: set[str],
    ) -> List[Dict[str, Any]]:
        _, last_call_msg = _find_last_assistant_mcp_calls(messages)
        last_call_msg = _ensure_mcp_call_ids(last_call_msg)
        mcp_calls = last_call_msg.get("mcp_calls") or []
        call_map: Dict[str, Dict[str, Any]] = {
            str(call.get("id")): call
            for call in mcp_calls
            if isinstance(call, dict) and isinstance(call.get("id"), str)
        }
        existing_ids = _existing_mcp_result_ids(messages)
        results: List[Dict[str, Any]] = []
        for call_id in approved_ids | rejected_ids:
            if call_id in existing_ids:
                continue
            mcp_call = call_map.get(call_id)
            if not mcp_call:
                continue
            name = str(mcp_call.get("name") or "")
            arguments = mcp_call.get("arguments") if isinstance(mcp_call.get("arguments"), dict) else {}
            tool_desc = tool_index.get(name)

            if call_id in rejected_ids:
                content = tool_result_to_content({"canceled": True, "reason": "user_rejected"})
            elif not tool_desc:
                content = tool_result_to_content({"error": f"unknown_tool: {name}"})
            else:
                content = await _execute_mcp_call(name, arguments)
            results.append({"role": "tool", "mcp_call_id": call_id, "content": content})
        return results

    @classmethod
    async def chat_stream(cls, req: AgentChatRequest, user: Optional[User]):
        try:
            yield _sse("assistant_start", {"id": "agent"})
            data = await cls.chat(req, user)
            for msg in data.get("messages", []):
                if isinstance(msg, dict) and msg.get("role") == "assistant":
                    yield _sse("assistant_end", {"id": "agent", "message": msg})
                elif isinstance(msg, dict) and msg.get("role") == "tool":
                    yield _sse(
                        "mcp_call_end",
                        {
                            "mcp_call_id": msg.get("mcp_call_id"),
                            "name": "",
                            "message": msg,
                        },
                    )
            if data.get("pending_mcp_calls"):
                yield _sse("pending", {"pending_mcp_calls": data["pending_mcp_calls"]})
            yield _sse("done", data)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            message = {"role": "assistant", "content": f"服务端异常: {_format_exc(exc)}"}
            payload = {"messages": [message]}
            yield _sse("assistant_end", {"id": "agent", "message": message})
            yield _sse("done", payload)
