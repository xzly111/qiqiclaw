from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentModelSpec:
    provider: str
    model: str
    base_url: str
    api_key: str
    credential_index: int | None


@dataclass(frozen=True)
class AgentModelReply:
    content: str
    reasoning: str = ""
    reasoning_content: str = ""
    reasoning_details: Any = None


class AgentModelError(RuntimeError):
    pass


def _normalize_base_url(raw_url: Any) -> str:
    return str(raw_url or "").strip().rstrip("/")


def _openai_compatible_base_url_candidates(raw_url: Any) -> list[str]:
    normalized = _normalize_base_url(raw_url)
    if not normalized:
        return []
    lowered = normalized.lower()
    for suffix in ("/chat/completions", "/models"):
        if lowered.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            lowered = normalized.lower()
            break
    candidates = [normalized]
    if not (lowered.endswith("/v1") or lowered.endswith("/v1beta")):
        candidates.append(f"{normalized}/v1")
    return candidates


def resolve_agent_model(agent: dict[str, Any]) -> AgentModelSpec:
    entry = _model_library_entry(agent)
    provider = str(entry.get("provider") or agent.get("provider_snapshot") or "").strip().lower()
    model = str(entry.get("model") or agent.get("model_snapshot") or "").strip()
    if not provider or not model:
        raise AgentModelError("智能体未绑定有效模型库条目")

    credential_index, credential = _resolve_credential(agent, entry, provider)
    base_url = _validated_base_url(credential, entry, provider) or _normalize_base_url(agent.get("base_url_snapshot"))
    api_key = str(
        credential.get("runtime_api_key")
        or credential.get("access_token")
        or credential.get("api_key")
        or ""
    ).strip()

    if not base_url:
        raise AgentModelError(f"{provider}/{model} 未解析到可用 Base URL")

    return AgentModelSpec(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        credential_index=credential_index,
    )


def call_agent_model(
    agent: dict[str, Any],
    *,
    current_prompt: str,
    room: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    readonly_context: str = "",
    timeout_seconds: float = 90.0,
) -> AgentModelReply:
    try:
        import httpx
    except ModuleNotFoundError as exc:
        raise AgentModelError("运行环境缺少 httpx，无法调用模型 API") from exc

    spec = resolve_agent_model(agent)
    messages = _build_chat_messages(
        agent,
        room=room,
        recent_messages=recent_messages,
        current_prompt=current_prompt,
        readonly_context=readonly_context,
    )
    payload: dict[str, Any] = {
        "model": spec.model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 1200,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if spec.api_key:
        headers["Authorization"] = f"Bearer {spec.api_key}"

    last_error = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout_seconds), follow_redirects=True) as client:
            for candidate in _openai_compatible_base_url_candidates(spec.base_url):
                url = f"{candidate.rstrip('/')}/chat/completions"
                response = client.post(url, headers=headers, json=payload)
                if response.status_code < 200 or response.status_code >= 300:
                    detail = response.text.strip().replace("\n", " ")[:500]
                    last_error = f"HTTP {response.status_code}: {detail or response.reason_phrase}"
                    continue

                try:
                    data = response.json()
                except ValueError as exc:
                    last_error = f"接口返回不是 JSON: {exc}"
                    continue

                reply = _extract_chat_reply(data)
                if reply.content:
                    return reply
                last_error = "接口返回 2xx，但未包含可用回复内容"
    except httpx.HTTPError as exc:
        raise AgentModelError(f"模型请求失败: {exc}") from exc

    raise AgentModelError(last_error or "模型请求失败")


def _model_library_entry(agent: dict[str, Any]) -> dict[str, Any]:
    try:
        from qiqiclaw_cli.web_server import _read_models_library
    except Exception as exc:
        raise AgentModelError(f"无法读取模型库: {exc}") from exc

    saved_model_id = str(agent.get("saved_model_id") or "").strip()
    provider = str(agent.get("provider_snapshot") or "").strip().lower()
    model = str(agent.get("model_snapshot") or "").strip()
    base_url = _normalize_base_url(agent.get("base_url_snapshot"))

    models = _read_models_library()
    for entry in models:
        if saved_model_id and str(entry.get("id") or "").strip() == saved_model_id:
            return dict(entry)

    for entry in models:
        if provider and str(entry.get("provider") or "").strip().lower() != provider:
            continue
        if model and str(entry.get("model") or "").strip() != model:
            continue
        if base_url and _normalize_base_url(entry.get("base_url")) != base_url:
            continue
        return dict(entry)

    raise AgentModelError("模型库中未找到该智能体绑定的模型")


def _resolve_credential(
    agent: dict[str, Any],
    entry: dict[str, Any],
    provider: str,
) -> tuple[int | None, dict[str, Any]]:
    from qiqiclaw_cli.auth import read_credential_pool

    requested_index = agent.get("credential_index_snapshot")
    pool_entries = read_credential_pool(provider)
    if not isinstance(pool_entries, list):
        pool_entries = []

    if isinstance(requested_index, int) and requested_index > 0 and requested_index <= len(pool_entries):
        credential = pool_entries[requested_index - 1]
        if isinstance(credential, dict) and _credential_matches_model_entry(credential, entry):
            return requested_index, credential

    for index, credential in enumerate(pool_entries, start=1):
        if _credential_validates_model(credential, entry):
            return index, credential

    try:
        from qiqiclaw_cli.web_server import _credential_entry_from_provider_env

        env_credential = _credential_entry_from_provider_env(provider, entry)
    except Exception:
        env_credential = None
    if isinstance(env_credential, dict):
        return None, env_credential

    raise AgentModelError("凭证池未找到该模型已验证通过的匹配凭证")


def _credential_matches_model_entry(credential: Any, entry: dict[str, Any]) -> bool:
    if not isinstance(credential, dict):
        return False
    provider = str(entry.get("provider") or "").strip().lower()
    model_base_url = _normalize_base_url(entry.get("base_url"))
    credential_base_url = _normalize_base_url(credential.get("base_url"))
    if provider == "custom":
        return bool(model_base_url and credential_base_url and model_base_url == credential_base_url)
    return not model_base_url or not credential_base_url or model_base_url == credential_base_url


def _credential_validates_model(credential: Any, entry: dict[str, Any]) -> bool:
    if not _credential_matches_model_entry(credential, entry):
        return False
    if not isinstance(credential, dict):
        return False
    model = str(entry.get("model") or "").strip()
    base_url = _normalize_base_url(entry.get("base_url"))
    validated = credential.get("validated_models")
    if isinstance(validated, dict):
        state = validated.get(model)
        if isinstance(state, dict) and state.get("status") == "ok":
            state_base_url = _normalize_base_url(state.get("base_url"))
            if not base_url or not state_base_url or state_base_url == base_url:
                return True
    return credential.get("last_status") == "ok" and str(credential.get("last_model") or "") == model


def _validated_base_url(credential: dict[str, Any], entry: dict[str, Any], provider: str) -> str:
    model = str(entry.get("model") or "").strip()
    validated = credential.get("validated_models") if isinstance(credential, dict) else None
    state = validated.get(model) if isinstance(validated, dict) else None
    if isinstance(state, dict):
        state_base_url = _normalize_base_url(state.get("base_url"))
        if state_base_url:
            return state_base_url

    try:
        from qiqiclaw_cli.web_server import _resolve_model_route_base_url

        return _normalize_base_url(_resolve_model_route_base_url(provider, entry, credential))
    except Exception:
        return _normalize_base_url(entry.get("base_url") or credential.get("base_url"))


def _build_chat_messages(
    agent: dict[str, Any],
    *,
    room: dict[str, Any],
    recent_messages: list[dict[str, Any]],
    current_prompt: str,
    readonly_context: str = "",
) -> list[dict[str, str]]:
    system = "\n".join(
        part
        for part in (
            f"你是 QiQiClaw 群聊智能体：{agent.get('name') or '智能体'}。",
            f"角色描述：{agent.get('description') or '按当前角色描述参与交叉验证。'}",
            f"房间目标：{room.get('objective') or room.get('title') or '未设置'}",
            "你可以查看用户显式提供或引用的文件内容，并基于这些只读上下文做交叉验证。",
            "你没有写入工具、没有修改工具、没有发布/推送工具；不得声称自己无法查看已附加的只读文件上下文。",
            "你可以监视主 agent 的执行过程并提出更改建议，但不能自行写入、发布、推送、删除或产生外部副作用。",
            "最终是否采纳建议由用户判断；需要实际执行时，明确说明应由用户确认后交由主 agent 执行。",
            "回复格式必须和普通新建会话一致：直接给出你的回答，使用自然 Markdown，不要写剧本式对话、旁白、齐声、主持流程或额外 UI 说明。",
            "如果用户要求写入、保存、发布、推送或删除，请说明该操作应由主 agent 执行；你只能给出只读分析、建议、草稿或风险提示。",
        )
        if part
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system}]

    for item in recent_messages[-24:]:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        sender_type = item.get("sender_type")
        if sender_type == "user":
            if content == current_prompt.strip() and readonly_context:
                messages.append({"role": "user", "content": f"{content}\n\n{readonly_context}"})
            else:
                messages.append({"role": "user", "content": content})
        elif sender_type == "agent":
            sender = str(item.get("sender_name") or "智能体").strip()
            messages.append({"role": "assistant", "content": f"@{sender}\n\n{content}"})

    if not messages or messages[-1].get("role") != "user" or messages[-1].get("content") != current_prompt.strip():
        content = current_prompt.strip()
        if readonly_context:
            content = f"{content}\n\n{readonly_context}"
        messages.append({"role": "user", "content": content})

    return messages


def _stringify_reasoning_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, list):
        chunks: list[str] = []
        for item in value:
            if isinstance(item, str):
                chunks.append(item)
                continue
            if isinstance(item, dict):
                for key in ("text", "content", "summary"):
                    text = item.get(key)
                    if isinstance(text, str) and text.strip():
                        chunks.append(text)
                        break
        return "\n".join(chunk.strip() for chunk in chunks if chunk.strip()).strip()

    if isinstance(value, dict):
        for key in ("text", "content", "summary", "reasoning", "reasoning_content"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()

    return ""


def _extract_message_text(message: dict[str, Any], keys: tuple[str, ...]) -> str:
    chunks: list[str] = []
    for key in keys:
        content = message.get(key)
        if isinstance(content, str) and content.strip():
            chunks.append(content.strip())
            continue
        if isinstance(content, list):
            text = "".join(
                str(part.get("text") or part.get("content") or "")
                for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            ).strip()
            if text:
                chunks.append(text)
    return "\n".join(chunks).strip()


def _extract_chat_reply(data: Any) -> AgentModelReply:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not isinstance(choices, list):
        return AgentModelReply(content="")
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    reasoning_content_chunks: list[str] = []
    reasoning_details: Any = None
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if isinstance(message, dict):
            content = _extract_message_text(message, ("content",))
            if content:
                chunks.append(content)

            reasoning = _stringify_reasoning_value(message.get("reasoning"))
            if reasoning:
                reasoning_chunks.append(reasoning)

            reasoning_content = _stringify_reasoning_value(message.get("reasoning_content"))
            if reasoning_content:
                reasoning_content_chunks.append(reasoning_content)

            if reasoning_details is None and message.get("reasoning_details") is not None:
                reasoning_details = message.get("reasoning_details")
                details_text = _stringify_reasoning_value(reasoning_details)
                if details_text:
                    reasoning_content_chunks.append(details_text)

        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())

            reasoning = _stringify_reasoning_value(delta.get("reasoning"))
            if reasoning:
                reasoning_chunks.append(reasoning)

            reasoning_content = _stringify_reasoning_value(delta.get("reasoning_content"))
            if reasoning_content:
                reasoning_content_chunks.append(reasoning_content)

    return AgentModelReply(
        content="\n".join(chunks).strip(),
        reasoning="\n".join(reasoning_chunks).strip(),
        reasoning_content="\n".join(reasoning_content_chunks).strip(),
        reasoning_details=reasoning_details,
    )


def _extract_chat_content(data: Any) -> str:
    return _extract_chat_reply(data).content
