from __future__ import annotations

import time
import uuid
from copy import deepcopy
from pathlib import Path
import re
from typing import Any

from fastapi import HTTPException

from .mention_routing import ALL_AGENTS_MENTION, extract_mentions, resolve_mention_targets
from .model_client import AgentModelError, AgentModelReply, call_agent_model
from .storage import GroupChatStorage
from .types import (
    GroupAgentCreate,
    GroupAgentUpdate,
    GroupAssistantMessageCreate,
    GroupDecisionCreate,
    GroupMessageCreate,
    GroupRoomCreate,
    GroupRoomUpdate,
    GroupRunCreate,
)


DEFAULT_AGENTS: tuple[dict[str, Any], ...] = (
    {
        "name": "方案提出者",
        "description": "提出可执行方案、拆分步骤和预期结果。",
        "role_type": "proposer",
        "can_spawn_validation_subagents": False,
    },
    {
        "name": "反对者",
        "description": "寻找反例、边界条件、失败路径和未覆盖约束。",
        "role_type": "opponent",
        "can_spawn_validation_subagents": False,
    },
    {
        "name": "事实核查员",
        "description": "核查事实、文件、接口、依赖和上下文一致性。",
        "role_type": "fact_checker",
        "can_spawn_validation_subagents": True,
    },
    {
        "name": "风险审查员",
        "description": "审查权限、数据、发布、回滚和破坏性操作风险。",
        "role_type": "risk_reviewer",
        "can_spawn_validation_subagents": True,
    },
    {
        "name": "执行评审员",
        "description": "判断方案是否足够清晰，可否交给主 agent 执行。",
        "role_type": "execution_reviewer",
        "can_spawn_validation_subagents": False,
    },
    {
        "name": "意见统一者",
        "description": "归纳共识、分歧、待确认项和建议下一步，不替用户裁决。",
        "role_type": "consensus_builder",
        "can_spawn_validation_subagents": False,
    },
)


class GroupChatService:
    storage = GroupChatStorage()

    @classmethod
    def list_rooms(cls) -> dict[str, Any]:
        data = cls.storage.load()
        rooms = sorted(data["rooms"].values(), key=lambda row: row.get("updated_at", 0), reverse=True)
        return {"rooms": rooms}

    @classmethod
    def list_sessions(cls) -> dict[str, Any]:
        data = cls.storage.load()
        rooms = sorted(data["rooms"].values(), key=lambda row: row.get("updated_at", 0), reverse=True)
        return {"sessions": [cls._session_info(data, room) for room in rooms]}

    @classmethod
    def create_room(cls, payload: GroupRoomCreate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            now = time.time()
            room_id = cls._id("room")
            room = {
                "id": room_id,
                "title": payload.title.strip() or "新建群聊",
                "objective": payload.objective.strip(),
                "status": "idle",
                "created_at": now,
                "updated_at": now,
                "last_run_id": None,
            }
            data["rooms"][room_id] = room
            data["agents"][room_id] = [cls._agent_record(room_id, agent, now) for agent in DEFAULT_AGENTS]
            data["messages"][room_id] = []
            return cls._room_bundle(data, room_id)

        return cls.storage.mutate(op)

    @classmethod
    def get_room(cls, room_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        cls._require_room(data, room_id)
        return cls._room_bundle(data, room_id)

    @classmethod
    def update_room(cls, room_id: str, payload: GroupRoomUpdate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            room = cls._require_room(data, room_id)
            updates = payload.model_dump(exclude_unset=True)
            for key in ("title", "objective", "status"):
                if key in updates and updates[key] is not None:
                    room[key] = updates[key].strip() if isinstance(updates[key], str) else updates[key]
            room["updated_at"] = time.time()
            return cls._room_bundle(data, room_id)

        return cls.storage.mutate(op)

    @classmethod
    def delete_room(cls, room_id: str) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            cls._require_room(data, room_id)
            data["rooms"].pop(room_id, None)
            data["agents"].pop(room_id, None)
            data["messages"].pop(room_id, None)
            for run_id, run in list(data["runs"].items()):
                if run.get("room_id") == room_id:
                    data["runs"].pop(run_id, None)
            for decision_id, decision in list(data["decisions"].items()):
                if decision.get("room_id") == room_id:
                    data["decisions"].pop(decision_id, None)
            return {"ok": True}

        return cls.storage.mutate(op)

    @classmethod
    def list_agents(cls, room_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        cls._require_room(data, room_id)
        return {"agents": deepcopy(data["agents"].get(room_id, []))}

    @classmethod
    def create_agent(cls, room_id: str, payload: GroupAgentCreate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            cls._require_room(data, room_id)
            now = time.time()
            agent = cls._agent_record(room_id, payload.model_dump(), now)
            data["agents"].setdefault(room_id, []).append(agent)
            data["rooms"][room_id]["updated_at"] = now
            return agent

        return cls.storage.mutate(op)

    @classmethod
    def update_agent(cls, room_id: str, agent_id: str, payload: GroupAgentUpdate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            cls._require_room(data, room_id)
            agent = cls._require_agent(data, room_id, agent_id)
            updates = payload.model_dump(exclude_unset=True)
            for key, value in updates.items():
                if value is None and key not in {"saved_model_id", "provider", "model", "credential_index", "base_url"}:
                    continue
                if key == "provider":
                    agent["provider_snapshot"] = value
                elif key == "model":
                    agent["model_snapshot"] = value
                elif key == "credential_index":
                    agent["credential_index_snapshot"] = value
                elif key == "base_url":
                    agent["base_url_snapshot"] = value
                else:
                    agent[key] = value.strip() if isinstance(value, str) else value
            agent["model_status"] = cls._model_status(agent)
            agent["updated_at"] = time.time()
            data["rooms"][room_id]["updated_at"] = agent["updated_at"]
            return agent

        return cls.storage.mutate(op)

    @classmethod
    def delete_agent(cls, room_id: str, agent_id: str) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            cls._require_room(data, room_id)
            agents = data["agents"].setdefault(room_id, [])
            next_agents = [agent for agent in agents if agent.get("id") != agent_id]
            if len(next_agents) == len(agents):
                raise HTTPException(status_code=404, detail="Group agent not found")
            data["agents"][room_id] = next_agents
            data["rooms"][room_id]["updated_at"] = time.time()
            return {"ok": True}

        return cls.storage.mutate(op)

    @classmethod
    def validate_agent_model(cls, room_id: str, agent_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        cls._require_room(data, room_id)
        agent = cls._require_agent(data, room_id, agent_id)
        status = cls._model_status(agent)
        return {
            "ok": status != "missing",
            "model_status": status,
            "saved_model_id": agent.get("saved_model_id"),
            "provider": agent.get("provider_snapshot"),
            "model": agent.get("model_snapshot"),
            "message": "模型引用来自模型库" if status != "missing" else "请从模型库为该智能体选择模型",
        }

    @classmethod
    def list_messages(cls, room_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        cls._require_room(data, room_id)
        return {"messages": deepcopy(data["messages"].get(room_id, []))}

    @classmethod
    def append_assistant_message(cls, room_id: str, payload: GroupAssistantMessageCreate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            room = cls._require_room(data, room_id)
            now = time.time()
            message = cls._message_record(
                room_id=room_id,
                sender_type="agent",
                sender_role_id=payload.sender_role_id or "main-agent",
                sender_name=payload.sender_name.strip() or "主 agent",
                content=payload.content,
                attachments=[],
                mentions=[],
                now=now,
                phase="reply",
                round_index=0,
                reasoning=payload.reasoning,
                reasoning_content=payload.reasoning_content,
            )
            data["messages"].setdefault(room_id, []).append(message)
            room["status"] = "idle"
            room["updated_at"] = now
            return {
                "message": deepcopy(message),
                "room": deepcopy(room),
            }

        return cls.storage.mutate(op)

    @classmethod
    def create_message(cls, room_id: str, payload: GroupMessageCreate) -> dict[str, Any]:
        def append_user_message(data: dict[str, Any]):
            room = cls._require_room(data, room_id)
            now = time.time()
            agents = data["agents"].get(room_id, [])
            message = cls._message_record(
                room_id=room_id,
                sender_type="user",
                sender_name="用户",
                content=payload.content,
                attachments=payload.attachments,
                mentions=extract_mentions(payload.content, agents),
                now=now,
            )
            data["messages"].setdefault(room_id, []).append(message)
            cls._maybe_autoname_room(room, payload.content, agents)
            routed_agents = cls._route_agents(message, agents, fallback_to_all=False)
            room["status"] = "running" if routed_agents else "idle"
            room["updated_at"] = now
            return {
                "room": deepcopy(room),
                "message": deepcopy(message),
                "recent_messages": deepcopy(data["messages"].get(room_id, [])),
                "routed_agents": deepcopy(routed_agents),
            }

        prepared = cls.storage.mutate(append_user_message)
        routed_agents = prepared["routed_agents"]
        replies = cls._agent_reply_messages(
            room_id,
            None,
            routed_agents,
            payload.content,
            time.time(),
            room=prepared["room"],
            recent_messages=prepared["recent_messages"],
        )

        def append_replies(data: dict[str, Any]):
            room = cls._require_room(data, room_id)
            now = time.time()
            data["messages"][room_id].extend(replies)
            room["status"] = "idle"
            room["updated_at"] = now
            return {"message": prepared["message"], "replies": replies, "routed_agents": routed_agents}

        if not routed_agents:
            def mark_idle(data: dict[str, Any]):
                room = cls._require_room(data, room_id)
                room["status"] = "idle"
                room["updated_at"] = time.time()
                return {"message": prepared["message"], "replies": [], "routed_agents": []}

            return cls.storage.mutate(mark_idle)

        return cls.storage.mutate(append_replies)

    @classmethod
    def create_run(cls, room_id: str, payload: GroupRunCreate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            room = cls._require_room(data, room_id)
            now = time.time()
            run_id = cls._id("run")
            prompt = payload.prompt.strip()
            if prompt:
                data["messages"].setdefault(room_id, []).append(
                    cls._message_record(
                        room_id=room_id,
                        sender_type="user",
                        sender_name="用户",
                        content=prompt,
                        attachments=[],
                        mentions=extract_mentions(prompt, data["agents"].get(room_id, [])),
                        now=now,
                        run_id=run_id,
                    )
                )

            run = {
                "id": run_id,
                "room_id": room_id,
                "phase": "consensus",
                "round_index": 1,
                "status": "completed",
                "started_at": now,
                "ended_at": now,
                "user_decision_state": "pending",
            }
            data["runs"][run_id] = run
            room["status"] = "idle"
            room["last_run_id"] = run_id
            room["updated_at"] = now

            summaries = cls._debate_messages(room_id, run_id, data, prompt or cls._last_user_text(data, room_id), now)
            data["messages"].setdefault(room_id, []).extend(summaries)
            decision = cls._decision_record(room_id, run_id, summaries, now)
            data["decisions"][decision["id"]] = decision
            return {"run": run, "messages": summaries, "decision": decision}

        return cls.storage.mutate(op)

    @classmethod
    def continue_run(cls, run_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        run = cls._require_run(data, run_id)
        return {"run": run, "ok": True}

    @classmethod
    def interrupt_run(cls, run_id: str) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            run = cls._require_run(data, run_id)
            run["status"] = "interrupted"
            run["ended_at"] = time.time()
            return {"run": run, "ok": True}

        return cls.storage.mutate(op)

    @classmethod
    def sandbox_run(cls, run_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        run = cls._require_run(data, run_id)
        return {
            "run_id": run_id,
            "room_id": run["room_id"],
            "read_only": True,
            "can_read_files": True,
            "can_monitor_main_agent": True,
            "can_suggest_changes": True,
            "can_real_execute": False,
            "plan": [
                "读取房间目标和最近消息",
                "由启用角色读取相关文件并提出验证问题",
                "监视主 agent 执行过程并提出更改建议",
                "等待用户判断是否采纳，再交由主 agent 执行",
            ],
        }

    @classmethod
    def subagent_validate(cls, run_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        run = cls._require_run(data, run_id)
        agents = [
            agent
            for agent in data["agents"].get(run["room_id"], [])
            if agent.get("enabled") and agent.get("can_spawn_validation_subagents")
        ]
        return {
            "run_id": run_id,
            "read_only": True,
            "can_read_files": True,
            "validation_agents": agents,
            "tasks": [
                {
                    "agent_id": agent["id"],
                    "agent_name": agent["name"],
                    "task": f"{agent['name']} 子代理只读文件查看与验证",
                    "status": "planned",
                }
                for agent in agents
            ],
        }

    @classmethod
    def create_decision(cls, run_id: str, payload: GroupDecisionCreate) -> dict[str, Any]:
        def op(data: dict[str, Any]):
            run = cls._require_run(data, run_id)
            now = time.time()
            decision = {
                "id": cls._id("decision"),
                "room_id": run["room_id"],
                "run_id": run_id,
                "decision_type": payload.decision_type,
                "summary": payload.note,
                "consensus": "",
                "disagreements": "",
                "risks": "",
                "recommended_next_action": "",
                "user_confirmed": payload.user_confirmed,
                "confirmed_at": now if payload.user_confirmed else None,
                "created_at": now,
            }
            data["decisions"][decision["id"]] = decision
            run["user_decision_state"] = "confirmed" if payload.user_confirmed else "pending"
            return decision

        return cls.storage.mutate(op)

    @classmethod
    def prepare_handoff(cls, run_id: str) -> dict[str, Any]:
        data = cls.storage.load()
        run = cls._require_run(data, run_id)
        messages = data["messages"].get(run["room_id"], [])
        return {
            "run_id": run_id,
            "room_id": run["room_id"],
            "ready": True,
            "requires_user_confirmation": True,
            "can_real_execute": False,
            "handoff_prompt": cls._handoff_prompt(messages),
        }

    @classmethod
    def confirm_handoff(cls, run_id: str, payload: GroupDecisionCreate) -> dict[str, Any]:
        decision = cls.create_decision(run_id, payload)
        return {
            "ok": True,
            "run_id": run_id,
            "decision": decision,
            "handoff_status": "confirmed_pending_main_agent",
            "message": "用户已确认，可在主 agent 执行层继续处理；本接口不直接执行破坏性操作。",
        }

    @classmethod
    def _room_bundle(cls, data: dict[str, Any], room_id: str) -> dict[str, Any]:
        return {
            "room": deepcopy(data["rooms"][room_id]),
            "agents": deepcopy(data["agents"].get(room_id, [])),
            "messages": deepcopy(data["messages"].get(room_id, [])),
            "last_run": deepcopy(data["runs"].get(data["rooms"][room_id].get("last_run_id"))),
        }

    @classmethod
    def _session_info(cls, data: dict[str, Any], room: dict[str, Any]) -> dict[str, Any]:
        room_id = room["id"]
        messages = data["messages"].get(room_id, [])
        last_message = messages[-1] if messages else None
        preview = None
        if last_message:
            sender = str(last_message.get("sender_name") or "").strip()
            content = str(last_message.get("content") or "").strip()
            preview = f"@{sender} {content}" if last_message.get("sender_type") == "agent" else content

        return {
            "archived": False,
            "cwd": None,
            "ended_at": None,
            "id": room_id,
            "_lineage_root_id": room_id,
            "input_tokens": 0,
            "is_active": room.get("status") == "running",
            "is_default_profile": True,
            "last_active": room.get("updated_at") or room.get("created_at") or 0,
            "message_count": len(messages),
            "model": "group-chat",
            "output_tokens": 0,
            "preview": preview,
            "profile": "default",
            "source": "group-chat",
            "started_at": room.get("created_at") or room.get("updated_at") or 0,
            "title": room.get("title") or "新建群聊",
            "tool_call_count": 0,
        }

    @staticmethod
    def _maybe_autoname_room(room: dict[str, Any], content: str, agents: list[dict[str, Any]]) -> None:
        current = str(room.get("title") or "").strip()
        if current not in {"", "新建群聊", "自动辩论室"}:
            return

        title = content.strip().splitlines()[0] if content.strip() else ""
        for mention in extract_mentions(content, agents):
            title = title.replace(f"@{mention}", " ")
        title = " ".join(title.split()).strip(" ,，:：;；.!?。！？")
        if title:
            room["title"] = title[:64]

    @staticmethod
    def _id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:16]}"

    @classmethod
    def _agent_record(cls, room_id: str, values: dict[str, Any], now: float) -> dict[str, Any]:
        agent = {
            "id": cls._id("agent"),
            "room_id": room_id,
            "name": str(values.get("name") or "智能体").strip(),
            "description": str(values.get("description") or "").strip(),
            "role_type": values.get("role_type") or "custom",
            "saved_model_id": values.get("saved_model_id"),
            "provider_snapshot": values.get("provider"),
            "model_snapshot": values.get("model"),
            "credential_index_snapshot": values.get("credential_index"),
            "base_url_snapshot": values.get("base_url"),
            "sandbox_only": False,
            "can_read_files": True,
            "can_monitor_main_agent": True,
            "can_suggest_changes": True,
            "can_real_execute": False,
            "can_write_files": False,
            "can_publish": False,
            "can_push": False,
            "can_spawn_validation_subagents": bool(values.get("can_spawn_validation_subagents")),
            "participates_in_consensus": bool(values.get("participates_in_consensus", True)),
            "receives_all": bool(values.get("receives_all", True)),
            "enabled": True,
            "created_at": now,
            "updated_at": now,
        }
        agent["model_status"] = cls._model_status(agent)
        return agent

    @staticmethod
    def _model_status(agent: dict[str, Any]) -> str:
        if not agent.get("saved_model_id"):
            return "missing"
        if agent.get("provider_snapshot") and agent.get("model_snapshot"):
            return "ok"
        return "unverified"

    @classmethod
    def _message_record(
        cls,
        *,
        room_id: str,
        sender_type: str,
        sender_name: str,
        content: str,
        attachments: list[dict[str, Any]],
        mentions: list[str],
        now: float,
        run_id: str | None = None,
        sender_role_id: str | None = None,
        phase: str = "idle",
        round_index: int = 0,
        reasoning: str = "",
        reasoning_content: str = "",
        reasoning_details: Any = None,
    ) -> dict[str, Any]:
        record = {
            "id": cls._id("msg"),
            "room_id": room_id,
            "run_id": run_id,
            "sender_type": sender_type,
            "sender_role_id": sender_role_id,
            "sender_name": sender_name,
            "content": content.strip(),
            "mentions": mentions,
            "attachments": attachments,
            "round_index": round_index,
            "phase": phase,
            "created_at": now,
        }
        if reasoning:
            record["reasoning"] = reasoning
        if reasoning_content:
            record["reasoning_content"] = reasoning_content
        if reasoning_details is not None:
            record["reasoning_details"] = reasoning_details
        return record

    @staticmethod
    def _route_agents(
        message: dict[str, Any],
        agents: list[dict[str, Any]],
        *,
        fallback_to_all: bool = True,
    ) -> list[dict[str, Any]]:
        enabled = [agent for agent in agents if agent.get("enabled")]
        mentions = set(message.get("mentions") or [])
        if ALL_AGENTS_MENTION in mentions:
            return [agent for agent in enabled if agent.get("receives_all")]
        named = resolve_mention_targets(enabled, " ".join(f"@{name}" for name in mentions))
        return named or (enabled if fallback_to_all else [])

    @staticmethod
    def _implicit_route_agents(
        previous_messages: list[dict[str, Any]],
        agents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        enabled_by_id = {str(agent.get("id") or ""): agent for agent in agents if agent.get("enabled")}

        for message in reversed(previous_messages):
            if message.get("sender_type") != "agent":
                continue

            agent_id = str(message.get("sender_role_id") or "")
            agent = enabled_by_id.get(agent_id)
            if agent:
                return [agent]

        enabled = list(enabled_by_id.values())
        return enabled if len(enabled) == 1 else []

    @classmethod
    def _agent_reply_messages(
        cls,
        room_id: str,
        run_id: str | None,
        agents: list[dict[str, Any]],
        prompt: str,
        now: float,
        *,
        room: dict[str, Any] | None = None,
        recent_messages: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        replies = []
        readonly_context = cls._readonly_file_context(prompt)
        for index, agent in enumerate(agents, start=1):
            reply = AgentModelReply(content="")
            try:
                reply = call_agent_model(
                    agent,
                    current_prompt=prompt,
                    room=room or {},
                    recent_messages=recent_messages or [],
                    readonly_context=readonly_context if agent.get("can_read_files", True) else "",
                )
            except AgentModelError as exc:
                reply = AgentModelReply(content=f"模型调用失败：{exc}")
            replies.append(
                cls._message_record(
                    room_id=room_id,
                    run_id=run_id,
                    sender_type="agent",
                    sender_role_id=agent.get("id"),
                    sender_name=agent.get("name") or "智能体",
                    content=reply.content,
                    attachments=[],
                    mentions=[],
                    now=now + index / 1000,
                    phase="reply",
                    round_index=0,
                    reasoning=reply.reasoning,
                    reasoning_content=reply.reasoning_content,
                    reasoning_details=reply.reasoning_details,
                )
            )
        return replies

    @staticmethod
    def _readonly_file_context(prompt: str, *, max_chars: int = 60000) -> str:
        if not prompt:
            return ""

        try:
            from agent.context_references import preprocess_context_references
        except Exception:
            return ""

        cwd = Path.cwd()
        normalized_prompt = GroupChatService._normalize_readonly_file_references(prompt)
        try:
            expanded = preprocess_context_references(
                normalized_prompt,
                cwd=cwd,
                context_length=max(max_chars // 4, 1),
                allowed_root=Path("/"),
            )
        except Exception as exc:
            return (
                "--- Attached Read-Only File Context ---\n"
                f"- 文件读取上下文准备失败：{exc}\n"
                "说明：群聊智能体只能读取用户显式引用的文件内容，不能修改文件。"
            )

        if not expanded.expanded:
            return ""

        marker = "--- Attached Context ---"
        if marker in expanded.message:
            body = expanded.message.split(marker, 1)[1].strip()
        else:
            body = expanded.message.strip()

        if not body:
            return ""

        if len(body) > max_chars:
            body = f"{body[:max_chars]}\n\n[只读文件上下文已截断，最多 {max_chars} 字符]"

        warnings = "\n".join(f"- {warning}" for warning in expanded.warnings)
        warning_block = f"\n\n读取提示：\n{warnings}" if warnings else ""

        return (
            "--- Attached Read-Only File Context ---\n"
            "以下内容由 QiQiClaw 后端按用户消息中的 @file/@folder 引用只读读取后提供。"
            "你可以基于它分析、核验和提出建议，但不能修改、保存、删除、发布或推送任何文件。"
            f"{warning_block}\n\n{body}"
        )

    @staticmethod
    def _normalize_readonly_file_references(prompt: str) -> str:
        normalized = re.sub(r"(?<![\s([{<])(@(?:file|folder):)", r" \1", prompt)
        existing_refs = set(re.findall(r"@(?:file|folder):(?:`[^`\n]+`|\"[^\"\n]+\"|'[^'\n]+'|\S+)", normalized))
        additions: list[str] = []

        for match in re.finditer(r"(?P<path>/(?:[^\s`\"'，。！？；：,;!?])+(?:/[^\s`\"'，。！？；：,;!?]+)*)", prompt):
            raw_path = match.group("path").rstrip(")]}")
            try:
                path = Path(raw_path).expanduser()
            except Exception:
                continue
            if not path.exists():
                continue
            kind = "folder" if path.is_dir() else "file" if path.is_file() else ""
            if not kind:
                continue
            ref = f"@{kind}:`{path}`"
            if ref in existing_refs or ref in additions:
                continue
            additions.append(ref)

        if additions:
            normalized = f"{normalized}\n\n" + "\n".join(additions)

        return normalized

    @classmethod
    def _debate_messages(
        cls,
        room_id: str,
        run_id: str,
        data: dict[str, Any],
        prompt: str,
        now: float,
    ) -> list[dict[str, Any]]:
        agents = [agent for agent in data["agents"].get(room_id, []) if agent.get("enabled")]
        routed = cls._route_agents({"mentions": extract_mentions(prompt, agents)}, agents)
        if not routed:
            routed = agents

        content_by_role = {
            "proposer": "建议先明确目标、约束、验收标准，再拆成可执行步骤。",
            "opponent": "需要重点检查遗漏条件、失败路径和用户未确认的高风险动作。",
            "fact_checker": "应核查本机文件、接口字段、模型库引用和前后端类型是否一致。",
            "risk_reviewer": "所有真实写入、发布、推送必须等用户确认，群聊角色只能读取文件、监视执行并提出建议。",
            "execution_reviewer": "当前输出应形成可交给主 agent 的明确任务清单、回滚点和验证步骤。",
            "consensus_builder": "共识：先只读验证，再由用户判断是否采纳，最后交主 agent 执行；分歧和风险需保留给用户判断。",
            "custom": "已收到任务，将按角色描述参与交叉验证。",
        }
        messages = []
        for index, agent in enumerate(routed, start=1):
            role_type = agent.get("role_type") or "custom"
            messages.append(
                cls._message_record(
                    room_id=room_id,
                    run_id=run_id,
                    sender_type="agent",
                    sender_role_id=agent.get("id"),
                    sender_name=agent.get("name") or "智能体",
                    content=content_by_role.get(role_type, content_by_role["custom"]),
                    attachments=[],
                    mentions=[],
                    now=now + index / 1000,
                    phase="consensus" if role_type == "consensus_builder" else "verification",
                    round_index=1,
                )
            )
        return messages

    @staticmethod
    def _decision_record(room_id: str, run_id: str, messages: list[dict[str, Any]], now: float) -> dict[str, Any]:
        consensus = "\n".join(
            message["content"] for message in messages if message.get("sender_name") == "意见统一者"
        )
        return {
            "id": f"decision_{uuid.uuid4().hex[:16]}",
            "room_id": room_id,
            "run_id": run_id,
            "summary": "自动辩论已完成第一轮交叉验证。",
            "consensus": consensus,
            "disagreements": "真实执行前仍需用户确认范围、风险和验收方式。",
            "risks": "群聊角色只能读取文件、监视主 agent 并提出建议，不能直接执行文件写入、发布或推送。",
            "recommended_next_action": "用户确认后将整理后的任务交给主 agent 执行。",
            "user_confirmed": False,
            "confirmed_at": None,
            "created_at": now,
        }

    @staticmethod
    def _handoff_prompt(messages: list[dict[str, Any]]) -> str:
        tail = messages[-12:]
        lines = ["请根据以下群聊自动辩论结果执行，执行前再次检查风险和用户确认范围："]
        for message in tail:
            lines.append(f"- {message.get('sender_name')}: {message.get('content')}")
        return "\n".join(lines)

    @staticmethod
    def _last_user_text(data: dict[str, Any], room_id: str) -> str:
        for message in reversed(data["messages"].get(room_id, [])):
            if message.get("sender_type") == "user":
                return message.get("content") or ""
        return ""

    @staticmethod
    def _require_room(data: dict[str, Any], room_id: str) -> dict[str, Any]:
        room = data["rooms"].get(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Group room not found")
        return room

    @staticmethod
    def _require_agent(data: dict[str, Any], room_id: str, agent_id: str) -> dict[str, Any]:
        for agent in data["agents"].get(room_id, []):
            if agent.get("id") == agent_id:
                return agent
        raise HTTPException(status_code=404, detail="Group agent not found")

    @staticmethod
    def _require_run(data: dict[str, Any], run_id: str) -> dict[str, Any]:
        run = data["runs"].get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Group run not found")
        return run
