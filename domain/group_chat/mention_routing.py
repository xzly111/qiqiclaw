from __future__ import annotations

import re
from typing import Any, TypedDict, TypeVar


ALL_AGENTS_MENTION = "all"
BEFORE_BOUNDARY = {"(", "[", "{", "<"}
AFTER_BOUNDARY = {".", ",", "!", "?", ";", ":", "，", "。", "！", "？", "；", "：", ")", "]", "}", ">"}
SIMPLE_MENTION_DIRECTIVE_RE = re.compile(r"@simple:`(@[^`]+)`")


class MentionRange(TypedDict):
    start: int
    end: int


T = TypeVar("T", bound=dict[str, Any])


def is_reserved_mention_name(name: str) -> bool:
    return name.strip().lower() == ALL_AGENTS_MENTION


def _is_before_boundary(char: str | None) -> bool:
    return char is None or char.isspace() or char in BEFORE_BOUNDARY


def _is_after_boundary(char: str | None) -> bool:
    return char is None or char.isspace() or char in AFTER_BOUNDARY


def normalize_mention_content(content: str) -> str:
    return SIMPLE_MENTION_DIRECTIVE_RE.sub(lambda match: match.group(1), content or "")


def find_mention_ranges(content: str, mention_name: str) -> list[MentionRange]:
    if not content or not mention_name:
        return []

    content = normalize_mention_content(content)
    content_lower = content.lower()
    mention_lower = mention_name.lower()
    ranges: list[MentionRange] = []
    from_index = 0
    token = f"@{mention_lower}"

    while from_index < len(content):
        at_index = content_lower.find(token, from_index)
        if at_index == -1:
            break

        start = at_index
        end = at_index + len(mention_name) + 1
        before = content[start - 1] if start > 0 else None
        after = content[end] if end < len(content) else None
        if _is_before_boundary(before) and _is_after_boundary(after):
            ranges.append({"start": start, "end": end})
        from_index = at_index + 1

    return ranges


def is_agent_mentioned(content: str, agent_name: str) -> bool:
    return bool(find_mention_ranges(content, agent_name))


def is_all_agents_mentioned(content: str) -> bool:
    return is_agent_mentioned(content, ALL_AGENTS_MENTION)


def _is_sender_agent(agent: dict[str, Any], sender_id: str) -> bool:
    return bool(sender_id and (agent.get("id") == sender_id or agent.get("agentId") == sender_id))


def resolve_mention_targets(agents: list[T], content: str, sender_id: str = "") -> list[T]:
    candidates = [agent for agent in agents if agent.get("enabled") and not _is_sender_agent(agent, sender_id)]

    return [agent for agent in candidates if is_agent_mentioned(content, str(agent.get("name") or ""))]


def extract_mentions(content: str, agents: list[dict[str, Any]]) -> list[str]:
    mentions: list[str] = []
    online_agents = [agent for agent in agents if agent.get("enabled")]

    for agent in online_agents:
        name = str(agent.get("name") or "")
        if not name or is_reserved_mention_name(name):
            continue
        if is_agent_mentioned(content, name):
            mentions.append(name)

    return list(dict.fromkeys(mentions))


def strip_mention_routing_tokens(content: str, own_agent_name: str) -> str:
    ranges_by_key: dict[str, MentionRange] = {}
    for mention_range in [
        *find_mention_ranges(content, ALL_AGENTS_MENTION),
        *find_mention_ranges(content, own_agent_name),
    ]:
        ranges_by_key[f"{mention_range['start']}:{mention_range['end']}"] = mention_range

    result = content
    for mention_range in sorted(ranges_by_key.values(), key=lambda row: row["start"], reverse=True):
        result = f"{result[:mention_range['start']]}{result[mention_range['end']:]}"

    return re.sub(r"[ \t]{2,}", " ", result.strip(" \t\r\n,，:：;；.!?。！？")).strip()
