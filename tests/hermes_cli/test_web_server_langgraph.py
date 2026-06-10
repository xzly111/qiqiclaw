from pathlib import Path

from fastapi.testclient import TestClient


BUNDLED_LANGGRAPH_SOURCE = Path("/home/szd/.qiqiclaw/qiqiclaw/vendor/langgraph-main")


def _client():
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN, app

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


def test_langgraph_status_endpoint_reports_workflow():
    client = _client()
    response = client.get("/api/langgraph/status")

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


def test_langgraph_run_endpoint_uses_dry_run():
    client = _client()
    response = client.post(
        "/api/langgraph/run",
        json={
            "prompt": "plan a smoke test",
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
