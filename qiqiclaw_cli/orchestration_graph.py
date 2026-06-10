"""QiQiClaw orchestration graph — LangGraph as a TRUE multi-node orchestrator.

The existing langgraph_runner.build_qiqiclaw_graph() is a single-node shell
(START -> qiqiclaw -> END). This module adds a real orchestration graph that
keeps QiQiClaw as the DECISION-MAKER and LangGraph as the ORCHESTRATOR,
WITHOUT touching AIAgent / conversation_loop:

    START
      |
      v
   [decide]   QiQiClaw decides: how to handle this step (single | ensemble | done)
      | conditional edge
   [execute]  runs AIAgent.chat() (single) OR ensemble_run() (multi-model)
      |
   [aggregate] QiQiClaw integrates; decide whether to loop or finish
      | conditional edge
     END  (or back to decide for multi-step tasks)

Multi-model ROLE ASSIGNMENT: OrchestrationState.model_assignments maps a node
role -> model, so 'decide' can use a strong model, 'execute' a fast one, etc.

Design constraints (functional-completeness guarantee):
  - New module; does not modify AIAgent, conversation_loop, or the existing
    single-node graph.
  - Execution bodies REUSE the already-verified oneshot runner and the Phase-1
    ensemble runner — no new agent-instantiation path is invented.
  - Fully usable in dry-run (no token spend) to validate topology.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, NotRequired, Optional, TypedDict

logger = logging.getLogger(__name__)

# State schema version — bumps when the OrchestrationState shape changes, so a
# resumed/checkpointed run can detect an incompatible state (fixes the original
# unversioned-TypedDict fragility called out in the analysis).
ORCHESTRATION_STATE_VERSION = 1


class OrchestrationState(TypedDict):
    """State threaded through the orchestration graph."""

    schema_version: int
    task: str
    # planning / control
    plan: NotRequired[list]
    steps_done: NotRequired[list]
    max_steps: NotRequired[int]
    # execution config
    mode: NotRequired[str]          # "single" | "ensemble"
    models: NotRequired[list]       # ensemble member specs (list of dicts)
    model_assignments: NotRequired[dict]   # role -> model, e.g. {"execute": "X"}
    provider: NotRequired[str | None]
    toolsets: NotRequired[list | None]
    # results
    candidates: NotRequired[list]
    last_response: NotRequired[str]
    final: NotRequired[str]
    status: NotRequired[str]
    error: NotRequired[str]


# Execution callable: (prompt, model, provider, toolsets) -> response str.
# Defaults to the real oneshot runner; tests/dry-run inject a stub.
ExecuteFn = Callable[[str, Optional[str], Optional[str], Optional[list]], str]


def _default_execute_fn(
    prompt: str,
    model: Optional[str],
    provider: Optional[str],
    toolsets: Optional[list],
) -> str:
    """Real single-model execution — reuses the existing oneshot runner
    (which drives a full AIAgent.chat()). Imported lazily so dry-run / tests
    never require the agent stack."""
    from qiqiclaw_cli.langgraph_runner import qiqiclaw_oneshot_runner

    return qiqiclaw_oneshot_runner(prompt, model, provider, toolsets)


def _model_for_role(state: OrchestrationState, role: str) -> Optional[str]:
    """Resolve which model a node should use from model_assignments, falling
    back to the single `model` hint or None (provider default)."""
    assignments = state.get("model_assignments") or {}
    return assignments.get(role) or assignments.get("default")


def build_orchestration_graph(
    *,
    execute_fn: Optional[ExecuteFn] = None,
    ensemble_fn: Optional[Callable[..., dict]] = None,
    decide_fn: Optional[Callable[[OrchestrationState], dict]] = None,
    source_path: Optional[str] = None,
    prefer_local_source: bool = True,
):
    """Build + compile the multi-node orchestration graph.

    execute_fn:  single-model executor (defaults to oneshot/AIAgent).
    ensemble_fn: multi-model executor (defaults to Phase-1 ensemble_run).
    decide_fn:   custom decision node (defaults to a heuristic router).
    """
    from qiqiclaw_cli.langgraph_runner import _load_langgraph

    StateGraph, START, END = _load_langgraph(source_path, prefer_local=prefer_local_source)
    _execute = execute_fn or _default_execute_fn

    # ── decide node ──────────────────────────────────────────────────────
    def decide(state: OrchestrationState) -> OrchestrationState:
        if decide_fn is not None:
            return {**state, **decide_fn(state)}
        # Default heuristic: if caller pre-set mode use it; if models given and
        # mode unset, prefer ensemble; otherwise single. Mark done when the
        # step budget is exhausted.
        steps_done = state.get("steps_done") or []
        max_steps = state.get("max_steps", 1)
        if len(steps_done) >= max_steps:
            return {**state, "status": "done"}
        mode = state.get("mode")
        if not mode:
            mode = "ensemble" if (state.get("models") or []) else "single"
        return {**state, "mode": mode, "status": "deciding"}

    # ── execute node ─────────────────────────────────────────────────────
    def execute(state: OrchestrationState) -> OrchestrationState:
        task = (state.get("task") or "").strip()
        if not task:
            return {**state, "status": "error", "error": "task is required"}
        mode = state.get("mode") or "single"
        toolsets = state.get("toolsets")
        provider = state.get("provider")
        try:
            if mode == "ensemble":
                resp, candidates = _run_ensemble(state, ensemble_fn, toolsets)
            else:
                model = _model_for_role(state, "execute")
                resp = _execute(task, model, provider, toolsets)
                candidates = [{"model": model, "summary": resp, "status": "completed"}]
        except Exception as exc:  # noqa: BLE001 - surface node failure into state
            logger.warning("orchestration execute node failed: %s", exc)
            return {**state, "status": "error", "error": str(exc)}
        steps_done = list(state.get("steps_done") or [])
        steps_done.append({"mode": mode, "response": resp})
        return {
            **state,
            "last_response": resp,
            "candidates": (state.get("candidates") or []) + candidates,
            "steps_done": steps_done,
            "status": "executed",
        }

    # ── aggregate node ───────────────────────────────────────────────────
    def aggregate(state: OrchestrationState) -> OrchestrationState:
        # On a failed execute, do not overwrite the error status — let routing
        # terminate cleanly.
        if state.get("status") == "error":
            return {**state, "final": state.get("last_response", "")}
        # Minimal default: the last response is the final answer. A custom
        # decide_fn + multi-step plan can extend this to true integration.
        return {**state, "final": state.get("last_response", ""), "status": "done"}

    # ── routing ──────────────────────────────────────────────────────────
    def route_after_decide(state: OrchestrationState) -> str:
        if state.get("status") in ("done", "error"):
            return "done"
        return "execute"

    def route_after_execute(state: OrchestrationState) -> str:
        # An execute failure skips aggregation and ends the run.
        return "aggregate" if state.get("status") != "error" else "done"

    def route_after_aggregate(state: OrchestrationState) -> str:
        steps_done = state.get("steps_done") or []
        max_steps = state.get("max_steps", 1)
        if state.get("status") == "error":
            return "done"
        return "continue" if len(steps_done) < max_steps else "done"

    graph = StateGraph(OrchestrationState)
    graph.add_node("decide", decide)
    graph.add_node("execute", execute)
    graph.add_node("aggregate", aggregate)
    graph.add_edge(START, "decide")
    graph.add_conditional_edges("decide", route_after_decide, {"execute": "execute", "done": END})
    graph.add_conditional_edges(
        "execute", route_after_execute, {"aggregate": "aggregate", "done": END}
    )
    graph.add_conditional_edges(
        "aggregate", route_after_aggregate, {"continue": "decide", "done": END}
    )
    return graph.compile()


def _run_ensemble(state: OrchestrationState, ensemble_fn, toolsets):
    """Run the ensemble executor and return (winning_response, candidates)."""
    if ensemble_fn is None:
        from agent.ensemble_runner import ensemble_run, ModelSpec

        specs = [ModelSpec(**m) if isinstance(m, dict) else ModelSpec(m)
                 for m in (state.get("models") or [])]
        strategy = state.get("mode_strategy")
        out = ensemble_run(
            state["task"],
            specs,
            strategy=strategy if isinstance(strategy, str) else "judge",
            toolsets=toolsets,
            parent_agent=state.get("parent_agent"),
        )
    else:
        out = ensemble_fn(state)
    return out.get("response", ""), out.get("candidates", [])


def invoke_orchestration(
    task: str,
    *,
    mode: Optional[str] = None,
    models: Optional[list] = None,
    model_assignments: Optional[dict] = None,
    provider: Optional[str] = None,
    toolsets: Optional[list] = None,
    max_steps: int = 1,
    execute_fn: Optional[ExecuteFn] = None,
    ensemble_fn: Optional[Callable[..., dict]] = None,
    decide_fn: Optional[Callable[[OrchestrationState], dict]] = None,
    source_path: Optional[str] = None,
    prefer_local_source: bool = True,
) -> OrchestrationState:
    """Invoke the orchestration graph for a task and return the final state.

    mode: force "single" or "ensemble"; None lets the decide node choose.
    models: ensemble member specs (dicts or model-name strings).
    model_assignments: role -> model (e.g. {"execute": "fast", "decide": "strong"}).
    """
    app = build_orchestration_graph(
        execute_fn=execute_fn,
        ensemble_fn=ensemble_fn,
        decide_fn=decide_fn,
        source_path=source_path,
        prefer_local_source=prefer_local_source,
    )
    initial: OrchestrationState = {
        "schema_version": ORCHESTRATION_STATE_VERSION,
        "task": task,
        "mode": mode,
        "models": models or [],
        "model_assignments": model_assignments or {},
        "provider": provider,
        "toolsets": toolsets,
        "max_steps": max_steps,
        "steps_done": [],
        "candidates": [],
        "status": "init",
    }
    return app.invoke(initial)


def dry_run_execute_fn(
    prompt: str,
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[list] = None,
) -> str:
    """Deterministic executor for topology validation — no model call."""
    bits = []
    if model:
        bits.append(f"model={model}")
    if provider:
        bits.append(f"provider={provider}")
    suffix = f" ({'; '.join(bits)})" if bits else ""
    return f"[orchestration dry-run]{suffix}: {prompt}"


def _parse_models_arg(raw: Optional[str]) -> list:
    """Parse --models "X,Y,provider:Z" into ModelSpec-compatible dicts.

    Each comma-separated entry is either "model" or "provider:model".
    """
    if not raw:
        return []
    specs = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            provider, model = item.split(":", 1)
            specs.append({"model": model.strip() or None, "provider": provider.strip() or None})
        else:
            specs.append({"model": item})
    return specs


def _parse_assignments_arg(raw: Optional[str]) -> dict:
    """Parse --assign "decide=strong,execute=fast" into {role: model}."""
    if not raw:
        return {}
    out = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            role, model = pair.split("=", 1)
            if role.strip() and model.strip():
                out[role.strip()] = model.strip()
    return out


def run_cli(args) -> int:
    """CLI entry point for `qiqiclaw orchestrate`."""
    import json as _json

    task = " ".join(getattr(args, "task", []) or []).strip()
    if not task:
        print("error: task is required", file=__import__("sys").stderr)
        return 1

    models = _parse_models_arg(getattr(args, "models", None))
    assignments = _parse_assignments_arg(getattr(args, "assign", None))
    mode = getattr(args, "mode", None)
    if not mode:
        mode = "ensemble" if models else "single"

    execute_fn = dry_run_execute_fn if getattr(args, "dry_run", False) else None
    ensemble_fn = None
    if getattr(args, "dry_run", False) and mode == "ensemble":
        # Deterministic ensemble stub for dry-run topology checks.
        def ensemble_fn(state):  # noqa: ANN001
            specs = state.get("models") or []
            cands = [
                {"model": (m.get("model") if isinstance(m, dict) else m),
                 "summary": f"[dry] {state['task']}", "status": "completed"}
                for m in specs
            ]
            return {"response": f"[orchestration dry-run ensemble of {len(specs)}]: {state['task']}",
                    "candidates": cands}

    state = invoke_orchestration(
        task,
        mode=mode,
        models=models,
        model_assignments=assignments,
        provider=getattr(args, "provider", None),
        toolsets=(getattr(args, "toolsets", None) or "").split(",") if getattr(args, "toolsets", None) else None,
        max_steps=getattr(args, "max_steps", 1),
        execute_fn=execute_fn,
        ensemble_fn=ensemble_fn,
    )

    if getattr(args, "json", False):
        print(_json.dumps(state, ensure_ascii=False, sort_keys=True, default=str))
    elif state.get("status") == "done":
        print(state.get("final", ""))
    else:
        print(state.get("error", "orchestration failed"), file=__import__("sys").stderr)
    return 0 if state.get("status") == "done" else 1

