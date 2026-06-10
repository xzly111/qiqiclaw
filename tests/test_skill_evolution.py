"""Tests for Phase 3 — skill outcome tracking + skill_evolution plugin.

These exercise the new skill_usage outcome mutators and the plugin's hook
callbacks in isolation, with a temp usage file so no real state is touched.
"""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def temp_usage(tmp_path, monkeypatch):
    """Point skill_usage at a temp file and reset module state."""
    import tools.skill_usage as su
    usage_file = tmp_path / "skill_usage.json"
    monkeypatch.setattr(su, "_usage_file", lambda: usage_file)
    return su


# ── skill_usage outcome mutators ────────────────────────────────────────────
def test_record_outcome_counts(temp_usage):
    su = temp_usage
    su.record_outcome("alpha", True)
    su.record_outcome("alpha", True)
    su.record_outcome("alpha", False)
    rec = su.get_record("alpha")
    assert rec["success_count"] == 2
    assert rec["failure_count"] == 1
    assert rec["last_outcome_at"] is not None


def test_success_rate(temp_usage):
    su = temp_usage
    assert su.success_rate("beta") is None  # no outcomes yet
    su.record_outcome("beta", True)
    su.record_outcome("beta", True)
    su.record_outcome("beta", False)
    assert su.success_rate("beta") == pytest.approx(2 / 3)


def test_empty_record_backfills_outcome_fields(temp_usage):
    su = temp_usage
    rec = su.get_record("never-seen")
    assert rec["success_count"] == 0
    assert rec["failure_count"] == 0
    assert "last_outcome_at" in rec


# ── skill_evolution plugin ──────────────────────────────────────────────────
@pytest.fixture
def plugin():
    import plugins.skill_evolution as p
    importlib.reload(p)  # fresh module state per test
    return p


def test_extract_skill_name_from_skill_tool(plugin):
    assert plugin._extract_skill_name(
        {"tool_name": "skill_manage", "arguments": {"name": "my-skill"}}
    ) == "my-skill"


def test_extract_skill_name_ignores_non_skill_tools(plugin):
    assert plugin._extract_skill_name(
        {"tool_name": "read_file", "arguments": {"name": "x"}}
    ) is None


def test_post_tool_call_tracks_skill(plugin):
    plugin.on_post_tool_call(session_id="s1", tool_name="skill_view", arguments={"name": "sk"})
    assert "sk" in plugin._touched_skills["s1"]


def test_session_succeeded_inference(plugin):
    assert plugin._session_succeeded({"status": "completed"}) is True
    assert plugin._session_succeeded({"status": "error"}) is False
    assert plugin._session_succeeded({"error": "boom"}) is False
    assert plugin._session_succeeded({}) is True


def test_finalize_records_outcomes(plugin, temp_usage):
    su = temp_usage
    plugin.on_post_tool_call(session_id="s2", tool_name="skill_view", arguments={"name": "sk2"})
    plugin.on_session_finalize(session_id="s2", status="completed")
    rec = su.get_record("sk2")
    assert rec["success_count"] == 1
    # session set cleared after finalize
    assert "s2" not in plugin._touched_skills


def test_finalize_records_failure(plugin, temp_usage):
    su = temp_usage
    plugin.on_post_tool_call(session_id="s3", tool_name="skill_manage", arguments={"name": "sk3"})
    plugin.on_session_finalize(session_id="s3", status="error")
    rec = su.get_record("sk3")
    assert rec["failure_count"] == 1


def test_finalize_no_skills_is_noop(plugin, temp_usage):
    # Should not raise when nothing was tracked.
    plugin.on_session_finalize(session_id="empty", status="completed")


def test_hooks_never_raise(plugin):
    # Malformed payloads must be swallowed (observability must not break turns).
    plugin.on_post_tool_call()  # no kwargs
    plugin.on_session_finalize()  # no kwargs


def test_register_wires_hooks(plugin):
    registered = []
    class Ctx:
        def register_hook(self, name, cb):
            registered.append(name)
    plugin.register(Ctx())
    assert "post_tool_call" in registered
    assert "on_session_finalize" in registered
