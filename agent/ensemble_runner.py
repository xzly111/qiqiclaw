"""Ensemble runner — drive ONE task across multiple models in parallel.

QiQiClaw already has Provider *failover* (try next provider when one fails,
see agent/auxiliary_client.py) but NOT *ensemble* (run several models on the
same task at once and pick the best). This module adds ensemble on top of the
existing, already-verified delegation machinery:

  - parallelism: reuses delegate_tool's ThreadPoolExecutor child runner
  - per-model agents: reuses delegate_tool._build_child_agent, which already
    accepts per-child model + provider overrides (just never exposed for this)
  - selection: "judge" (an auxiliary model scores candidates) or
    "vote" (pick the modal / highest-agreement answer)

Design constraints (functional-completeness guarantee):
  - Adds a NEW module; does not modify AIAgent or conversation_loop.
  - Reuses _build_child_agent / _run_single_child verbatim — no new agent
    instantiation path is invented.
  - If only one model is given, behaves like a single delegated run.

Returns the winning result plus ALL candidates so the caller (the QiQiClaw
orchestration layer) can audit or re-decide.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Model spec ────────────────────────────────────────────────────────────
class ModelSpec:
    """One participant in an ensemble: a model + optional provider overrides.

    Mirrors the override surface that delegate_tool._build_child_agent already
    accepts, so an ensemble member can run on a completely different
    provider:model pair than the parent agent.
    """

    __slots__ = ("model", "provider", "base_url", "api_key", "api_mode", "label")

    def __init__(
        self,
        model: Optional[str] = None,
        *,
        provider: Optional[str] = None,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_mode: Optional[str] = None,
        label: Optional[str] = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.base_url = base_url
        self.api_key = api_key
        self.api_mode = api_mode
        self.label = label or (model or provider or "default")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ModelSpec(label={self.label!r}, model={self.model!r}, provider={self.provider!r})"


# ── Parallel fan-out ──────────────────────────────────────────────────────
def _run_one_member(
    index: int,
    spec: ModelSpec,
    goal: str,
    context: Optional[str],
    toolsets: Optional[List[str]],
    max_iterations: int,
    member_count: int,
    parent_agent: Any,
) -> Dict[str, Any]:
    """Build + run a single ensemble member, returning delegate's result dict
    augmented with the member's label/spec. Failures are captured, not raised,
    so one bad model never sinks the whole ensemble."""
    from tools.delegate_tool import _build_child_agent, _run_single_child

    try:
        child = _build_child_agent(
            task_index=index,
            goal=goal,
            context=context,
            toolsets=toolsets,
            model=spec.model,
            max_iterations=max_iterations,
            task_count=member_count,
            parent_agent=parent_agent,
            override_provider=spec.provider,
            override_base_url=spec.base_url,
            override_api_key=spec.api_key,
            override_api_mode=spec.api_mode,
        )
        result = _run_single_child(
            task_index=index, goal=goal, child=child, parent_agent=parent_agent
        )
    except Exception as exc:  # noqa: BLE001 - isolate per-member failure
        logger.warning("ensemble member %s (%s) failed: %s", index, spec.label, exc)
        result = {
            "task_index": index,
            "status": "error",
            "summary": None,
            "error": str(exc),
            "api_calls": 0,
            "duration_seconds": 0.0,
        }
    result["_ensemble_label"] = spec.label
    result.setdefault("model", spec.model)
    return result


def _fan_out(
    specs: List[ModelSpec],
    goal: str,
    context: Optional[str],
    toolsets: Optional[List[str]],
    max_iterations: int,
    parent_agent: Any,
    max_workers: Optional[int],
) -> List[Dict[str, Any]]:
    """Run every member in parallel (reusing delegate's ThreadPoolExecutor
    pattern) and return their result dicts in member order."""
    n = len(specs)
    if n == 1:
        return [
            _run_one_member(0, specs[0], goal, context, toolsets, max_iterations, 1, parent_agent)
        ]
    workers = max_workers or n
    results: List[Optional[Dict[str, Any]]] = [None] * n
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _run_one_member, i, spec, goal, context, toolsets, max_iterations, n, parent_agent
            ): i
            for i, spec in enumerate(specs)
        }
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as exc:  # noqa: BLE001 - defensive; _run_one_member already guards
                logger.warning("ensemble future %s raised: %s", i, exc)
                results[i] = {
                    "task_index": i,
                    "status": "error",
                    "summary": None,
                    "error": str(exc),
                    "_ensemble_label": specs[i].label,
                    "model": specs[i].model,
                }
    return [r for r in results if r is not None]


# ── Selection strategies ──────────────────────────────────────────────────
def _successful(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        c for c in candidates
        if c.get("status") in ("completed", "ok", "success") and c.get("summary")
    ]


def _select_by_vote(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the answer with the most agreement. Agreement is measured by
    normalized exact-match of the response text; ties break toward the
    candidate that finished fastest (cheapest signal we have without a judge).
    """
    ok = _successful(candidates)
    if not ok:
        return None
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for c in ok:
        key = " ".join((c.get("summary") or "").split()).lower()
        buckets.setdefault(key, []).append(c)
    # Largest bucket wins; within it, the fastest member.
    best_bucket = max(buckets.values(), key=len)
    winner = min(best_bucket, key=lambda c: c.get("duration_seconds") or float("inf"))
    winner["_ensemble_agreement"] = len(best_bucket)
    winner["_ensemble_total_ok"] = len(ok)
    return winner


def _select_by_judge(
    candidates: List[Dict[str, Any]],
    goal: str,
    judge_model: Optional[str],
    judge_provider: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Ask an auxiliary model to score the candidates and pick the best.

    Uses the existing auxiliary_client routing (the same path compression /
    vision side-tasks use). On any judge failure, degrades to vote so the
    ensemble still returns a result.
    """
    ok = _successful(candidates)
    if not ok:
        return None
    if len(ok) == 1:
        return ok[0]
    try:
        from agent.auxiliary_client import call_llm
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("judge unavailable (%s); falling back to vote", exc)
        return _select_by_vote(candidates)

    listing = "\n\n".join(
        f"[Candidate {i}] (model={c.get('model')})\n{c.get('summary')}"
        for i, c in enumerate(ok)
    )
    prompt = (
        "You are judging candidate answers to a task. Choose the single best "
        "candidate by correctness, completeness, and clarity.\n\n"
        f"TASK:\n{goal}\n\n"
        f"CANDIDATES:\n{listing}\n\n"
        "Reply with ONLY the integer index of the best candidate (0-based)."
    )
    try:
        resp = call_llm(
            task="ensemble_judge",
            provider=judge_provider,
            model=judge_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=16,
        )
        text = _extract_text(resp)
        idx = _first_int(text)
        if idx is not None and 0 <= idx < len(ok):
            winner = ok[idx]
            winner["_ensemble_judge_raw"] = text.strip()[:200]
            return winner
        logger.warning("judge returned unparseable index %r; falling back to vote", text[:80])
    except Exception as exc:  # noqa: BLE001 - judge must never sink the ensemble
        logger.warning("judge call failed (%s); falling back to vote", exc)
    return _select_by_vote(candidates)


def _extract_text(resp: Any) -> str:
    """Pull text out of an auxiliary_client.call_llm response object
    (OpenAI-shape: resp.choices[0].message.content). Tolerates plain strings
    and missing fields so a judge response shape change degrades gracefully."""
    if isinstance(resp, str):
        return resp
    try:
        choice = resp.choices[0]
        msg = getattr(choice, "message", None) or choice.get("message", {})
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        return content if isinstance(content, str) else str(content or "")
    except Exception:  # noqa: BLE001 - unknown shape → empty, triggers vote fallback
        return ""


def _first_int(text: str) -> Optional[int]:
    token = ""
    for ch in text:
        if ch.isdigit():
            token += ch
        elif token:
            break
    return int(token) if token else None


# ── Public entry point ────────────────────────────────────────────────────
def ensemble_run(
    goal: str,
    models: List[ModelSpec],
    *,
    strategy: str = "judge",
    context: Optional[str] = None,
    toolsets: Optional[List[str]] = None,
    max_iterations: int = 12,
    parent_agent: Any = None,
    max_workers: Optional[int] = None,
    judge_model: Optional[str] = None,
    judge_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Run ``goal`` across every model in ``models`` in parallel and select a
    winner.

    strategy: "judge" (auxiliary model scores + picks) or "vote"
              (highest text-agreement; fastest breaks ties).

    Returns:
        {
          "status": "ok" | "error",
          "winner": <result dict | None>,
          "response": <winning summary text | "">,
          "strategy": <strategy used>,
          "candidates": [<all member result dicts>],
        }
    """
    if not models:
        return {"status": "error", "winner": None, "response": "",
                "strategy": strategy, "candidates": [], "error": "no models provided"}

    candidates = _fan_out(
        models, goal, context, toolsets, max_iterations, parent_agent, max_workers
    )

    if strategy == "vote":
        winner = _select_by_vote(candidates)
    else:
        winner = _select_by_judge(candidates, goal, judge_model, judge_provider)

    return {
        "status": "ok" if winner else "error",
        "winner": winner,
        "response": (winner or {}).get("summary") or "",
        "strategy": strategy,
        "candidates": candidates,
        "error": None if winner else "no successful candidate",
    }
