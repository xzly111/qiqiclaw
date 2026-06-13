"""QiQiClaw desktop dashboard custom provider regression tests."""

from __future__ import annotations

import pytest


def test_openai_compatible_base_url_candidates_normalize_common_inputs():
    from qiqiclaw_cli.web_server import _openai_compatible_base_url_candidates

    assert _openai_compatible_base_url_candidates("https://oneapi.hk") == [
        "https://oneapi.hk",
        "https://oneapi.hk/v1",
    ]
    assert _openai_compatible_base_url_candidates("https://oneapi.hk/v1/chat/completions") == [
        "https://oneapi.hk/v1"
    ]
    assert _openai_compatible_base_url_candidates("http://localhost:1234/v1/models") == [
        "http://localhost:1234/v1"
    ]


def _client():
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")
    import hermes_state
    from hermes_constants import get_hermes_home
    from qiqiclaw_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    hermes_state.DEFAULT_DB_PATH = get_hermes_home() / "state.db"
    return client


class TestQiQiClawDashboardCustomProvider:
    @pytest.fixture(autouse=True)
    def _setup(self, _isolate_hermes_home, monkeypatch, tmp_path):
        qiqiclaw_home = tmp_path / "qiqiclaw_test"
        qiqiclaw_home.mkdir()
        monkeypatch.setenv("QIQICLAW_HOME", str(qiqiclaw_home))
        self.client = _client()

    def test_custom_provider_allows_base_url_without_api_key(self):
        response = self.client.post(
            "/api/credentials/pool",
            json={
                "provider": "custom",
                "api_key": "",
                "base_url": "http://127.0.0.1:8000/v1",
            },
        )

        assert response.status_code == 200
        assert response.json()["count"] == 1

        providers = self.client.get("/api/credentials/pool").json()["providers"]
        assert providers[0]["provider"] == "custom"
        assert providers[0]["entries"][0]["base_url"] == "http://127.0.0.1:8000/v1"

    def test_custom_discovery_uses_base_url_without_api_key(self, monkeypatch):
        calls = []

        def fake_discover(base_url, api_key):
            calls.append((base_url, api_key))
            return True, "ok", ["local-model"]

        monkeypatch.setattr("qiqiclaw_cli.web_server._discover_models_with_key", fake_discover)

        response = self.client.post(
            "/api/models/discover",
            json={"provider": "custom", "base_url": "http://127.0.0.1:8000/v1"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["models"] == ["local-model"]
        assert calls == [("http://127.0.0.1:8000/v1", "")]

    def test_custom_discovery_persists_resolved_openai_compatible_base_url(self, monkeypatch):
        def fake_discover(base_url, api_key):
            assert (base_url, api_key) == ("https://oneapi.hk", "sk-test")
            return True, "ok", ["gpt-5.5"], "https://oneapi.hk/v1"

        monkeypatch.setattr("qiqiclaw_cli.web_server._discover_models_with_key", fake_discover)

        pool = self.client.post(
            "/api/credentials/pool",
            json={
                "provider": "custom",
                "api_key": "sk-test",
                "base_url": "https://oneapi.hk",
            },
        )
        assert pool.status_code == 200

        response = self.client.post(
            "/api/models/discover",
            json={"provider": "custom", "base_url": "https://oneapi.hk"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["base_url"] == "https://oneapi.hk/v1"
        assert body["saved_models"][0]["base_url"] == "https://oneapi.hk/v1"

        providers = self.client.get("/api/credentials/pool").json()["providers"]
        assert providers[0]["entries"][0]["base_url"] == "https://oneapi.hk/v1"

    def test_custom_route_validation_uses_base_url_without_api_key(self, monkeypatch):
        calls = []

        def fake_probe(base_url, api_key, model):
            calls.append((base_url, api_key, model))
            return True, "ok"

        monkeypatch.setattr("qiqiclaw_cli.web_server._openai_compatible_chat_probe", fake_probe)

        response = self.client.post(
            "/api/models/route/validate",
            json={
                "provider": "custom",
                "model": "local-model",
                "base_url": "http://127.0.0.1:8000/v1",
                "name": "local-model",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert calls == [("http://127.0.0.1:8000/v1", "", "local-model")]

    def test_custom_route_validation_returns_resolved_openai_compatible_base_url(self, monkeypatch):
        def fake_probe(base_url, api_key, model):
            assert (base_url, api_key, model) == ("https://oneapi.hk", "sk-test", "gpt-5.5")
            return True, "ok", "https://oneapi.hk/v1"

        monkeypatch.setattr("qiqiclaw_cli.web_server._openai_compatible_chat_probe", fake_probe)

        pool = self.client.post(
            "/api/credentials/pool",
            json={
                "provider": "custom",
                "api_key": "sk-test",
                "base_url": "https://oneapi.hk",
            },
        )
        assert pool.status_code == 200

        response = self.client.post(
            "/api/models/route/validate",
            json={
                "provider": "custom",
                "model": "gpt-5.5",
                "base_url": "https://oneapi.hk",
                "name": "gpt-5.5",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["base_url"] == "https://oneapi.hk/v1"
        assert body["library_model"]["base_url"] == "https://oneapi.hk/v1"
