"""Skill-evolution plugin — borrows GenericAgent (skill crystallization) and
HyperAgents (outcome scoring) ideas via QiQiClaw's EXISTING hook system.

This is pure opt-in observability + suggestion. It does NOT modify AIAgent,
conversation_loop, or any tool. It only registers callbacks on already-present
lifecycle hooks (see hermes_cli/plugins.py VALID_HOOKS):

  - post_tool_call:        track which skills were touched this session
  - on_session_finalize:   record success/failure outcomes for those skills
                           (HyperAgents-style archive scoring, no code rewrite)

Disable = don't load the plugin → behavior is exactly as before.

NOTE: skill auto-crystallization (auto-saving a new skill from a successful
trajectory, GenericAgent-style) is intentionally left as a SUGGESTION rather
than an automatic write: auto-creating skill files without user review is a
side effect with real risk (clutter, bad skills). This plugin logs a
crystallization suggestion; turning it into an actual skill_manage(create)
remains a deliberate, separately-gated step.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)

# Per-session set of skills touched, keyed by session id. Module-level state is
# acceptable here: hooks are invoked on the agent's own thread per session.
_touched_skills: Dict[str, Set[str]] = {}


def _session_key(kwargs: Dict[str, Any]) -> str:
    return str(kwargs.get("session_id") or kwargs.get("turn_id") or "default")


def _extract_skill_name(kwargs: Dict[str, Any]) -> str | None:
    """Best-effort: detect a skill invocation from a post_tool_call payload.

    QiQiClaw surfaces skills through the skill tooling; we look for a skill
    name in the common argument shapes without assuming a single schema.
    """
    tool_name = str(kwargs.get("tool_name") or "")
    if "skill" not in tool_name.lower():
        return None
    args = kwargs.get("arguments") or kwargs.get("args") or {}
    if isinstance(args, dict):
        name = args.get("name") or args.get("skill") or args.get("skill_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def on_post_tool_call(**kwargs: Any) -> None:
    """Track skill usage per session (best-effort, never raises)."""
    try:
        name = _extract_skill_name(kwargs)
        if name:
            _touched_skills.setdefault(_session_key(kwargs), set()).add(name)
    except Exception as exc:  # noqa: BLE001 - observability must not break turns
        logger.debug("skill_evolution post_tool_call failed: %s", exc)


def _session_succeeded(kwargs: Dict[str, Any]) -> bool:
    """Infer session success from the finalize payload. Defaults to True
    (a finalized session with no explicit failure is treated as a success)."""
    status = kwargs.get("status") or kwargs.get("exit_reason")
    if isinstance(status, str):
        return status.lower() not in ("error", "failed", "timeout", "aborted")
    err = kwargs.get("error")
    return not err


def on_session_finalize(**kwargs: Any) -> None:
    """Record success/failure for every skill touched this session, then clear
    the per-session set. Best-effort; never raises into the agent loop."""
    key = _session_key(kwargs)
    skills = _touched_skills.pop(key, set())
    if not skills:
        return
    try:
        from tools import skill_usage
    except Exception as exc:  # pragma: no cover - import guard
        logger.debug("skill_evolution: skill_usage unavailable: %s", exc)
        return
    success = _session_succeeded(kwargs)
    for name in skills:
        try:
            skill_usage.record_outcome(name, success)
        except Exception as exc:  # noqa: BLE001
            logger.debug("skill_evolution: record_outcome(%s) failed: %s", name, exc)
    # GenericAgent-style crystallization SUGGESTION (logged, not auto-written).
    if success and len(skills) == 0:
        logger.info(
            "skill_evolution: successful session created no skill — candidate "
            "for crystallization (suggestion only; not auto-created)."
        )


def register(ctx) -> None:
    """Plugin entry point (matches QiQiClaw's plugin convention)."""
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("on_session_finalize", on_session_finalize)
