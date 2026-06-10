"""Tests for qiqiclaw_cli.orchestration_graph — multi-node LangGraph orchestration.

Runs against the REAL bundled LangGraph (StateGraph compile + invoke) but uses
stub execute/ensemble functions so no model tokens are spent.
"""

from __future__ import annotations

import pytest

from qiqiclaw_cli.orchestration_graph import (
    invoke_orchestration,
    build_orchestration_graph,
    dry_run_execute_fn,
    OrchestrationState,
    ORCHESTRATION_STATE_VERSION,
)


def _stub_exec(prompt, model=None, provider=None, toolsets=None):
    return f"exec:{model}:{prompt}"


def test_single_mode_runs_and_finishes():
    state = invoke_orchestration("do x", mode="single", execute_fn=_stub_exec)
    assert state["status"] == "done"
    assert state["final"] == "exec:None:do x"
    assert len(state["steps_done"]) == 1
    assert state["schema_version"] == ORCHESTRATION_STATE_VERSION


def test_model_assignments_route_to_execute_node():
    state = invoke_orchestration(
        "do x", mode="single", execute_fn=_stub_exec,
        model_assignments={"execute": "fast-model"},
    )
    assert state["final"] == "exec:fast-model:do x"


def test_model_assignments_default_fallback():
    state = invoke_orchestration(
        "y", mode="single", execute_fn=_stub_exec,
        model_assignments={"default": "d-model"},
    )
    assert state["final"] == "exec:d-model:y"


def test_ensemble_mode_uses_ensemble_fn():
    def stub_ensemble(state):
        return {"response": "winner", "candidates": [{"model": "A", "summary": "a"}]}
    state = invoke_orchestration(
        "hard", mode="ensemble", models=["A", "B"], ensemble_fn=stub_ensemble
    )
    assert state["status"] == "done"
    assert state["final"] == "winner"
    assert len(state["candidates"]) == 1


def test_auto_mode_picks_ensemble_when_models_given():
    def stub_ensemble(state):
        return {"response": "auto-ens", "candidates": []}
    state = invoke_orchestration("t", models=["A", "B"], ensemble_fn=stub_ensemble)
    assert state["mode"] == "ensemble"
    assert state["final"] == "auto-ens"


def test_auto_mode_picks_single_when_no_models():
    state = invoke_orchestration("t", execute_fn=_stub_exec)
    assert state["mode"] == "single"
    assert state["final"] == "exec:None:t"


def test_empty_task_errors():
    state = invoke_orchestration("   ", mode="single", execute_fn=_stub_exec)
    assert state["status"] == "error"
    assert "task is required" in state["error"]


def test_execute_exception_surfaces_in_state():
    def boom(prompt, model=None, provider=None, toolsets=None):
        raise RuntimeError("exec failed")
    state = invoke_orchestration("x", mode="single", execute_fn=boom)
    assert state["status"] == "error"
    assert "exec failed" in state["error"]


def test_multi_step_loops_until_budget():
    calls = {"n": 0}
    def counting_exec(prompt, model=None, provider=None, toolsets=None):
        calls["n"] += 1
        return f"step{calls['n']}"
    state = invoke_orchestration(
        "multi", mode="single", execute_fn=counting_exec, max_steps=3
    )
    assert state["status"] == "done"
    assert calls["n"] == 3
    assert len(state["steps_done"]) == 3


def test_custom_decide_fn():
    def decide(state):
        return {"mode": "single"}
    state = invoke_orchestration("x", execute_fn=_stub_exec, decide_fn=decide)
    assert state["final"] == "exec:None:x"


def test_dry_run_execute_fn():
    out = dry_run_execute_fn("hello", model="m", provider="p")
    assert "dry-run" in out
    assert "model=m" in out
    assert "hello" in out


def test_graph_compiles_with_real_langgraph():
    # build_orchestration_graph must produce a compiled, invokable graph.
    app = build_orchestration_graph(execute_fn=_stub_exec)
    assert hasattr(app, "invoke")
    result = app.invoke({
        "schema_version": ORCHESTRATION_STATE_VERSION,
        "task": "compiled?",
        "mode": "single",
        "max_steps": 1,
        "steps_done": [],
        "candidates": [],
        "status": "init",
    })
    assert result["final"] == "exec:None:compiled?"
