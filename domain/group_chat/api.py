from __future__ import annotations

from fastapi import APIRouter

from api.response import success

from .service import GroupChatService
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


router = APIRouter(prefix="/api/group-chat", tags=["Group Chat"])


@router.get("/rooms")
async def list_rooms():
    return success(GroupChatService.list_rooms())


@router.get("/sessions")
async def list_sessions():
    return success(GroupChatService.list_sessions())


@router.post("/rooms")
async def create_room(payload: GroupRoomCreate):
    return success(GroupChatService.create_room(payload))


@router.get("/rooms/{room_id}")
async def get_room(room_id: str):
    return success(GroupChatService.get_room(room_id))


@router.patch("/rooms/{room_id}")
async def update_room(room_id: str, payload: GroupRoomUpdate):
    return success(GroupChatService.update_room(room_id, payload))


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str):
    return success(GroupChatService.delete_room(room_id))


@router.get("/rooms/{room_id}/agents")
async def list_agents(room_id: str):
    return success(GroupChatService.list_agents(room_id))


@router.post("/rooms/{room_id}/agents")
async def create_agent(room_id: str, payload: GroupAgentCreate):
    return success(GroupChatService.create_agent(room_id, payload))


@router.patch("/rooms/{room_id}/agents/{agent_id}")
async def update_agent(room_id: str, agent_id: str, payload: GroupAgentUpdate):
    return success(GroupChatService.update_agent(room_id, agent_id, payload))


@router.delete("/rooms/{room_id}/agents/{agent_id}")
async def delete_agent(room_id: str, agent_id: str):
    return success(GroupChatService.delete_agent(room_id, agent_id))


@router.post("/rooms/{room_id}/agents/{agent_id}/validate-model")
async def validate_agent_model(room_id: str, agent_id: str):
    return success(GroupChatService.validate_agent_model(room_id, agent_id))


@router.get("/rooms/{room_id}/messages")
async def list_messages(room_id: str):
    return success(GroupChatService.list_messages(room_id))


@router.post("/rooms/{room_id}/messages")
async def create_message(room_id: str, payload: GroupMessageCreate):
    return success(GroupChatService.create_message(room_id, payload))


@router.post("/rooms/{room_id}/assistant-messages")
async def append_assistant_message(room_id: str, payload: GroupAssistantMessageCreate):
    return success(GroupChatService.append_assistant_message(room_id, payload))


@router.post("/rooms/{room_id}/runs")
async def create_run(room_id: str, payload: GroupRunCreate):
    return success(GroupChatService.create_run(room_id, payload))


@router.post("/runs/{run_id}/continue")
async def continue_run(run_id: str):
    return success(GroupChatService.continue_run(run_id))


@router.post("/runs/{run_id}/interrupt")
async def interrupt_run(run_id: str):
    return success(GroupChatService.interrupt_run(run_id))


@router.post("/runs/{run_id}/sandbox")
async def sandbox_run(run_id: str):
    return success(GroupChatService.sandbox_run(run_id))


@router.post("/runs/{run_id}/subagent-validate")
async def subagent_validate(run_id: str):
    return success(GroupChatService.subagent_validate(run_id))


@router.post("/runs/{run_id}/decision")
async def create_decision(run_id: str, payload: GroupDecisionCreate):
    return success(GroupChatService.create_decision(run_id, payload))


@router.post("/runs/{run_id}/prepare-handoff")
async def prepare_handoff(run_id: str):
    return success(GroupChatService.prepare_handoff(run_id))


@router.post("/runs/{run_id}/confirm-handoff")
async def confirm_handoff(run_id: str, payload: GroupDecisionCreate):
    return success(GroupChatService.confirm_handoff(run_id, payload))
