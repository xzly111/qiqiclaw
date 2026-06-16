from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


AgentRoleType = Literal[
    "proposer",
    "opponent",
    "fact_checker",
    "risk_reviewer",
    "execution_reviewer",
    "consensus_builder",
    "custom",
]

MessageSenderType = Literal["user", "agent", "system"]
RunPhase = Literal["idle", "proposal", "challenge", "verification", "risk_review", "consensus", "handoff"]
RunStatus = Literal["draft", "running", "completed", "interrupted"]
ModelStatus = Literal["missing", "ok", "unverified"]


class GroupRoomCreate(BaseModel):
    title: str = Field(default="新建群聊", max_length=120)
    objective: str = Field(default="", max_length=4000)


class GroupRoomUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=120)
    objective: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, max_length=32)


class GroupAgentCreate(BaseModel):
    name: str = Field(..., max_length=80)
    description: str = Field(default="", max_length=2000)
    role_type: AgentRoleType = "custom"
    saved_model_id: str | None = Field(default=None, max_length=160)
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=240)
    credential_index: int | None = None
    base_url: str | None = Field(default=None, max_length=1000)
    can_spawn_validation_subagents: bool = False
    participates_in_consensus: bool = True
    receives_all: bool = True


class GroupAgentUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    role_type: AgentRoleType | None = None
    saved_model_id: str | None = Field(default=None, max_length=160)
    provider: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=240)
    credential_index: int | None = None
    base_url: str | None = Field(default=None, max_length=1000)
    can_spawn_validation_subagents: bool | None = None
    participates_in_consensus: bool | None = None
    receives_all: bool | None = None
    enabled: bool | None = None


class GroupMessageCreate(BaseModel):
    content: str = Field(..., max_length=20000)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class GroupAssistantMessageCreate(BaseModel):
    content: str = Field(default="", max_length=200000)
    reasoning: str | None = Field(default=None, max_length=200000)
    reasoning_content: str | None = Field(default=None, max_length=200000)
    sender_name: str = Field(default="主 agent", max_length=80)
    sender_role_id: str | None = Field(default="main-agent", max_length=120)


class GroupRunCreate(BaseModel):
    prompt: str = Field(default="", max_length=20000)


class GroupDecisionCreate(BaseModel):
    decision_type: str = Field(default="handoff", max_length=80)
    user_confirmed: bool = False
    note: str = Field(default="", max_length=4000)
