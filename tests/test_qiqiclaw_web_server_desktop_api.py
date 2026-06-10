import base64
from pathlib import Path

from fastapi.testclient import TestClient


BUNDLED_LANGGRAPH_SOURCE = Path("/home/szd/.qiqiclaw/qiqiclaw/vendor/langgraph-main")


def _client():
    from qiqiclaw_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


def test_qiqiclaw_langgraph_status_endpoint_reports_workflow():
    response = _client().get("/api/langgraph/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow"]["nodes"] == ["qiqiclaw"]
    assert payload["workflow"]["edges"] == [["START", "qiqiclaw"], ["qiqiclaw", "END"]]
    assert "dry-run" in payload["modes"]
    assert "agent" in payload["modes"]
    assert payload["available"] is True
    assert payload["using_local_source"] is True
    assert payload["graph_module_file"].endswith("langgraph/graph/__init__.py")
    assert payload["source_path"] == str(BUNDLED_LANGGRAPH_SOURCE)
    assert payload["bundled_source_path"] == str(BUNDLED_LANGGRAPH_SOURCE)
    assert str(BUNDLED_LANGGRAPH_SOURCE / "libs" / "langgraph") in payload["local_paths"]


def test_qiqiclaw_langgraph_run_endpoint_uses_dry_run():
    response = _client().post(
        "/api/langgraph/run",
        json={
            "prompt": "规划桌面端冒烟测试",
            "model": "test-model",
            "provider": "test-provider",
            "toolsets": "files,terminal",
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["state"]["status"] == "ok"
    assert payload["state"]["toolsets"] == ["files", "terminal"]
    assert payload["state"]["langgraph_source"] == str(BUNDLED_LANGGRAPH_SOURCE)
    assert "LangGraph dry-run routed prompt to QiQiClaw" in payload["state"]["response"]


def test_qiqiclaw_audio_transcribe_endpoint_uses_transcription_tool(monkeypatch):
    import tools.transcription_tools as transcription_tools

    def fake_transcribe(path):
        assert path.endswith(".webm")
        return {"success": True, "transcript": "你好 QiQiClaw", "provider": "test"}

    monkeypatch.setattr(transcription_tools, "transcribe_audio", fake_transcribe)

    data_url = "data:audio/webm;base64," + base64.b64encode(b"fake-audio").decode("ascii")
    response = _client().post(
        "/api/audio/transcribe",
        json={"data_url": data_url, "mime_type": "audio/webm"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "provider": "test",
        "transcript": "你好 QiQiClaw",
    }


def test_orchestrate_endpoint_single_dry_run():
    response = _client().post(
        "/api/orchestrate",
        json={
            "task": "smoke test orchestration",
            "mode": "single",
            "dry_run": True,
            "model_assignments": {"execute": "fast-model"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "single"
    assert payload["dry_run"] is True
    assert payload["state"]["status"] == "done"
    assert "model=fast-model" in payload["state"]["final"]
    assert payload["workflow"]["nodes"] == ["decide", "execute", "aggregate"]


def test_orchestrate_endpoint_ensemble_dry_run():
    response = _client().post(
        "/api/orchestrate",
        json={
            "task": "hard task",
            "models": ["gpt-4", "openrouter:claude-3"],
            "dry_run": True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["mode"] == "ensemble"
    assert len(payload["state"]["candidates"]) == 2
    assert "ensemble of 2" in payload["state"]["final"]


def test_orchestrate_endpoint_requires_auth():
    # Without the session header the route must reject (401).
    from qiqiclaw_cli.web_server import app
    unauth = TestClient(app)
    response = unauth.post("/api/orchestrate", json={"task": "x", "dry_run": True})
    assert response.status_code == 401

