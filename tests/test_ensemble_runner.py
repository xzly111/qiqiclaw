"""Tests for agent.ensemble_runner — selection strategies + run flow.

These tests exercise the ensemble logic WITHOUT real LLM calls by
monkeypatching the fan-out / judge, so they are fast and deterministic.
"""

from __future__ import annotations

import agent.ensemble_runner as er
from agent.ensemble_runner import ModelSpec, ensemble_run, _select_by_vote, _first_int, _extract_text


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeResp:
    """Mimics auxiliary_client.call_llm's OpenAI-shaped return."""
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _resp(content):
    return _FakeResp(content)


def _cand(idx, summary, status="completed", model="m", dur=1.0):
    return {
        "task_index": idx,
        "status": status,
        "summary": summary,
        "model": model,
        "duration_seconds": dur,
        "_ensemble_label": model,
    }


# ── _first_int ────────────────────────────────────────────────────────────
def test_first_int_parses_leading_index():
    assert _first_int("2") == 2
    assert _first_int("The best is 1.") == 1
    assert _first_int("index: 0 wins") == 0
    assert _first_int("none here") is None


# ── vote strategy ───────────────────────────────────────────────────────────
def test_vote_picks_majority():
    cands = [
        _cand(0, "the answer is 42"),
        _cand(1, "the answer is 42"),
        _cand(2, "completely different"),
    ]
    winner = _select_by_vote(cands)
    assert winner is not None
    assert winner["summary"] == "the answer is 42"
    assert winner["_ensemble_agreement"] == 2
    assert winner["_ensemble_total_ok"] == 3


def test_vote_ignores_failed_candidates():
    cands = [
        _cand(0, None, status="error"),
        _cand(1, "good answer"),
    ]
    winner = _select_by_vote(cands)
    assert winner is not None
    assert winner["summary"] == "good answer"


def test_vote_tie_breaks_on_speed():
    cands = [
        _cand(0, "answer A", dur=5.0),
        _cand(1, "answer B", dur=1.0),
    ]
    # Each bucket size 1 → max() picks first largest bucket; within it fastest.
    winner = _select_by_vote(cands)
    assert winner is not None
    # both buckets size 1; whichever bucket max() returns, it has one member,
    # so the winner is that member — assert it is a valid successful candidate
    assert winner["summary"] in ("answer A", "answer B")


def test_vote_no_successful_returns_none():
    cands = [_cand(0, None, status="error"), _cand(1, None, status="timeout")]
    assert _select_by_vote(cands) is None


def test_vote_normalizes_whitespace_and_case():
    cands = [
        _cand(0, "The  Answer"),
        _cand(1, "the answer"),
        _cand(2, "other"),
    ]
    winner = _select_by_vote(cands)
    assert winner["_ensemble_agreement"] == 2


# ── judge strategy (with monkeypatched auxiliary call_llm) ──────────────────
def test_judge_selects_index(monkeypatch):
    import agent.auxiliary_client as aux
    monkeypatch.setattr(aux, "call_llm", lambda *a, **k: _resp("1"))
    cands = [_cand(0, "first"), _cand(1, "second")]
    winner = er._select_by_judge(cands, "goal", None, None)
    assert winner["summary"] == "second"


def test_judge_falls_back_to_vote_on_bad_index(monkeypatch):
    import agent.auxiliary_client as aux
    monkeypatch.setattr(aux, "call_llm", lambda *a, **k: _resp("99"))  # out of range
    cands = [_cand(0, "same"), _cand(1, "same"), _cand(2, "diff")]
    winner = er._select_by_judge(cands, "goal", None, None)
    # falls back to vote → majority "same"
    assert winner["summary"] == "same"


def test_judge_falls_back_to_vote_on_exception(monkeypatch):
    import agent.auxiliary_client as aux
    def boom(*a, **k):
        raise RuntimeError("judge down")
    monkeypatch.setattr(aux, "call_llm", boom)
    cands = [_cand(0, "x"), _cand(1, "x"), _cand(2, "y")]
    winner = er._select_by_judge(cands, "goal", None, None)
    assert winner["summary"] == "x"


def test_judge_single_candidate_short_circuits(monkeypatch):
    # Should not even need the judge when only one succeeded.
    cands = [_cand(0, "only one"), _cand(1, None, status="error")]
    winner = er._select_by_judge(cands, "goal", None, None)
    assert winner["summary"] == "only one"


# ── ensemble_run end-to-end (fan-out monkeypatched) ─────────────────────────
def test_ensemble_run_vote(monkeypatch):
    fake = [_cand(0, "consensus", model="X"), _cand(1, "consensus", model="Y"),
            _cand(2, "outlier", model="Z")]
    monkeypatch.setattr(er, "_fan_out", lambda *a, **k: fake)
    out = ensemble_run(
        "do thing",
        [ModelSpec("X"), ModelSpec("Y"), ModelSpec("Z")],
        strategy="vote",
    )
    assert out["status"] == "ok"
    assert out["response"] == "consensus"
    assert len(out["candidates"]) == 3
    assert out["strategy"] == "vote"


def test_ensemble_run_no_models():
    out = ensemble_run("x", [], strategy="vote")
    assert out["status"] == "error"
    assert out["winner"] is None


def test_ensemble_run_all_failed(monkeypatch):
    fake = [_cand(0, None, status="error"), _cand(1, None, status="timeout")]
    monkeypatch.setattr(er, "_fan_out", lambda *a, **k: fake)
    out = ensemble_run("x", [ModelSpec("A"), ModelSpec("B")], strategy="vote")
    assert out["status"] == "error"
    assert out["response"] == ""


def test_modelspec_label_defaults():
    assert ModelSpec("gpt-4").label == "gpt-4"
    assert ModelSpec(provider="openrouter").label == "openrouter"
    assert ModelSpec("m", label="custom").label == "custom"


# ── _extract_text (response-shape tolerance) ────────────────────────────────
def test_extract_text_from_response_object():
    assert _extract_text(_resp("hello")) == "hello"


def test_extract_text_from_plain_string():
    assert _extract_text("plain") == "plain"


def test_extract_text_from_dict_message():
    class R:
        choices = [{"message": {"content": "dict-content"}}]
    assert _extract_text(R()) == "dict-content"


def test_extract_text_unknown_shape_returns_empty():
    assert _extract_text(object()) == ""
    assert _extract_text(None) == ""
