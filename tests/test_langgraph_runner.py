import pytest

from qiqiclaw_cli import langgraph_runner


LOCAL_LANGGRAPH_SOURCE = langgraph_runner.DEFAULT_LANGGRAPH_SOURCE


def _requires_local_langgraph_source():
    if not LOCAL_LANGGRAPH_SOURCE.exists():
        pytest.skip(f"local LangGraph source not found: {LOCAL_LANGGRAPH_SOURCE}")


def test_normalize_toolsets_splits_and_trims():
    assert langgraph_runner.normalize_toolsets("files, terminal, ,web_search") == [
        "files",
        "terminal",
        "web_search",
    ]


def test_invoke_qiqiclaw_graph_uses_injected_runner():
    def fake_runner(prompt, model, provider, toolsets):
        assert prompt == "plan this task"
        assert model == "test-model"
        assert provider == "test-provider"
        assert toolsets == ["files"]
        return "done"

    state = langgraph_runner.invoke_qiqiclaw_graph(
        "plan this task",
        model="test-model",
        provider="test-provider",
        toolsets="files",
        runner=fake_runner,
    )

    assert state["status"] == "ok"
    assert state["response"] == "done"


def test_invoke_qiqiclaw_graph_drives_local_langgraph_source():
    _requires_local_langgraph_source()

    state = langgraph_runner.invoke_qiqiclaw_graph(
        "drive local source",
        runner=lambda prompt, *_: f"ok: {prompt}",
        source_path=LOCAL_LANGGRAPH_SOURCE,
    )

    assert state["status"] == "ok"
    assert state["response"] == "ok: drive local source"
    assert state["langgraph_source"] == str(LOCAL_LANGGRAPH_SOURCE.resolve())


def test_langgraph_runtime_status_reports_graph_module_from_local_source():
    _requires_local_langgraph_source()

    status = langgraph_runner.get_langgraph_runtime_status(LOCAL_LANGGRAPH_SOURCE)

    assert status["available"] is True
    assert status["source_path"] == str(LOCAL_LANGGRAPH_SOURCE.resolve())
    assert status["using_local_source"] is True
    assert status["graph_module_file"]
    assert str(status["graph_module_file"]).startswith(str(LOCAL_LANGGRAPH_SOURCE.resolve()))
    assert str(LOCAL_LANGGRAPH_SOURCE / "libs" / "langgraph") in status["local_paths"]


def test_invoke_qiqiclaw_graph_requires_prompt():
    state = langgraph_runner.invoke_qiqiclaw_graph("", runner=lambda *_: "unused")

    assert state["status"] == "error"
    assert state["error"] == "prompt is required"


def test_run_cli_dry_run_outputs_json(capsys):
    class Args:
        prompt = ["hello", "workflow"]
        model = "dry-model"
        provider = "dry-provider"
        toolsets = "files,terminal"
        dry_run = True
        json = True

    assert langgraph_runner.run_cli(Args()) == 0
    captured = capsys.readouterr()

    assert '"status": "ok"' in captured.out
    assert "LangGraph dry-run routed prompt to QiQiClaw" in captured.out


def test_missing_langgraph_message(monkeypatch):
    def missing(*_args, **_kwargs):
        raise RuntimeError("missing")

    monkeypatch.setattr(langgraph_runner, "_load_langgraph", missing)

    with pytest.raises(RuntimeError, match="missing"):
        langgraph_runner.build_qiqiclaw_graph(runner=lambda *_: "unused")
