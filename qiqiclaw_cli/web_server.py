"""
QiQiClaw — Web UI server.

NOTE (architecture): diverged from hermes_cli/web_server.py (~7.6k lines of
difference) — NOT a copy. This is the highest-divergence pair; converge only
after the desktop/dashboard API contract is aligned. See ARCHITECTURE.md.

Provides a FastAPI backend serving the Vite/React frontend and REST API
endpoints for managing configuration, environment variables, and sessions.

Usage:
    python -m qiqiclaw_cli.main web          # Start on http://127.0.0.1:9119
    python -m qiqiclaw_cli.main web --port 8080
"""

import asyncio
import base64
import binascii
import hmac
import importlib.util
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from qiqiclaw_constants import ensure_project_root_on_syspath

from qiqiclaw_constants import _legacy_env
import yaml

PROJECT_ROOT = ensure_project_root_on_syspath()

from qiqiclaw_cli import __version__, __release_date__
from qiqiclaw_cli.config import (
    cfg_get,
    DEFAULT_CONFIG,
    OPTIONAL_ENV_VARS,
    get_config_path,
    get_env_path,
    get_qiqiclaw_home,
    load_config,
    load_env,
    save_config,
    save_env_value,
    remove_env_value,
    check_config_version,
    redact_key,
)
from gateway.status import get_running_pid, read_runtime_status

try:
    from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
except ImportError:
    raise SystemExit(
        "Web UI 需要 fastapi 和 uvicorn。\n"
        f"安装命令: {sys.executable} -m pip install 'fastapi' 'uvicorn[standard]'"
    )

WEB_DIST = Path(_legacy_env("WEB_DIST")) if (_legacy_env("WEB_DIST") is not None) else Path(__file__).parent / "web_dist"
_log = logging.getLogger(__name__)

app = FastAPI(title="QiQiClaw", version=__version__)

# ---------------------------------------------------------------------------
# Session token for protecting sensitive endpoints (reveal).
# Generated fresh on every server start — dies when the process exits.
# Injected into the SPA HTML so only the legitimate web UI can use it.
# ---------------------------------------------------------------------------
_SESSION_TOKEN = (
    os.environ.get("QIQICLAW_DASHBOARD_SESSION_TOKEN")
    or os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN")
    or secrets.token_urlsafe(32)
)
_SESSION_HEADER_NAME = "X-QiQi-Claw-Session-Token"

# In-browser Chat tab (/chat, /api/pty, ...). Off unless
# ``qiqiclaw dashboard --tui`` or QIQICLAW_DASHBOARD_TUI=1.
# Set from :func:`start_server`.
_DASHBOARD_EMBEDDED_CHAT_ENABLED = True

# QiQiClaw file-module API prefixes. These local file/app/processor/adapter
# routes are mounted before the SPA catch-all and bypass the dashboard token
# middleware because they run inside the QiQiClaw desktop shell.
_FILE_MODULE_API_PREFIXES: set = set()

# Simple rate limiter for the reveal endpoint
_reveal_timestamps: List[float] = []
_REVEAL_MAX_PER_WINDOW = 5
_REVEAL_WINDOW_SECONDS = 30

# CORS: restrict to localhost origins only.  The web UI is intended to run
# locally; binding to 0.0.0.0 with allow_origins=["*"] would let any website
# read/modify config and secrets.

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Endpoints that do NOT require the session token.  Everything else under
# /api/ is gated by the auth middleware below.  Keep this list minimal —
# only truly non-sensitive, read-only endpoints belong here.
# ---------------------------------------------------------------------------
_PUBLIC_API_PATHS: frozenset = frozenset({
    "/api/status",
    "/api/config/defaults",
    "/api/config/schema",
    "/api/model/info",
    "/api/profiles/active",
    "/api/dashboard/themes",
    "/api/dashboard/plugins",
    "/api/dashboard/plugins/rescan",
})


def _has_valid_session_token(request: Request) -> bool:
    """True if the request carries a valid dashboard session token.

    The dedicated session header avoids collisions with reverse proxies that
    already use ``Authorization`` (for example Caddy ``basic_auth``). We still
    accept the legacy Bearer path for backward compatibility with older
    dashboard bundles.
    """
    for header_name in (_SESSION_HEADER_NAME, "X-Hermes-Session-Token", "X-Session-Token"):
        session_header = request.headers.get(header_name, "")
        if session_header and hmac.compare_digest(
            session_header.encode(),
            _SESSION_TOKEN.encode(),
        ):
            return True

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {_SESSION_TOKEN}"
    return hmac.compare_digest(auth.encode(), expected.encode())


def _require_token(request: Request) -> None:
    """Validate the ephemeral session token.  Raises 401 on mismatch."""
    if not _has_valid_session_token(request):
        raise HTTPException(status_code=401, detail="未授权")


# Accepted Host header values for loopback binds. DNS rebinding attacks
# point a victim browser at an attacker-controlled hostname (evil.test)
# which resolves to 127.0.0.1 after a TTL flip — bypassing same-origin
# checks because the browser now considers evil.test and our dashboard
# "same origin". Validating the Host header at the app layer rejects any
# request whose Host isn't one we bound for. See GHSA-ppp5-vxwm-4cf7.
_LOOPBACK_HOST_VALUES: frozenset = frozenset({
    "localhost", "127.0.0.1", "::1",
})


def _is_accepted_host(host_header: str, bound_host: str) -> bool:
    """True if the Host header targets the interface we bound to.

    Accepts:
    - Exact bound host (with or without port suffix)
    - Loopback aliases when bound to loopback
    - Any host when bound to 0.0.0.0 (explicit opt-in to non-loopback,
      no protection possible at this layer)
    """
    if not host_header:
        return False
    # Strip port suffix. IPv6 addresses use bracket notation:
    #   [::1]         — no port
    #   [::1]:9119    — with port
    # Plain hosts/v4:
    #   localhost:9119
    #   127.0.0.1:9119
    h = host_header.strip()
    if h.startswith("["):
        # IPv6 bracketed — port (if any) follows "]:"
        close = h.find("]")
        if close != -1:
            host_only = h[1:close]  # strip brackets
        else:
            host_only = h.strip("[]")
    else:
        host_only = h.rsplit(":", 1)[0] if ":" in h else h
    host_only = host_only.lower()

    # 0.0.0.0 bind means operator explicitly opted into all-interfaces
    # (requires --insecure per web_server.start_server). No Host-layer
    # defence can protect that mode; rely on operator network controls.
    if bound_host in ("0.0.0.0", "::"):
        return True

    # Loopback bind: accept the loopback names
    bound_lc = bound_host.lower()
    if bound_lc in _LOOPBACK_HOST_VALUES:
        return host_only in _LOOPBACK_HOST_VALUES

    # Explicit non-loopback bind: require exact host match
    return host_only == bound_lc


@app.middleware("http")
async def host_header_middleware(request: Request, call_next):
    """Reject requests whose Host header doesn't match the bound interface.

    Defends against DNS rebinding: a victim browser on a localhost
    dashboard is tricked into fetching from an attacker hostname that
    TTL-flips to 127.0.0.1. CORS and same-origin checks don't help —
    the browser now treats the attacker origin as same-origin with the
    dashboard. Host-header validation at the app layer catches it.

    See GHSA-ppp5-vxwm-4cf7.
    """
    # Store the bound host on app.state so this middleware can read it —
    # set by start_server() at listen time.
    bound_host = getattr(app.state, "bound_host", None)
    if bound_host:
        host_header = request.headers.get("host", "")
        if not _is_accepted_host(host_header, bound_host):
            return JSONResponse(
                status_code=400,
                content={
                    "detail": (
                        "Invalid Host header. 仪表板请求必须使用"
                        "服务器绑定的主机名。"
                    ),
                },
            )
    return await call_next(request)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Require the session token on all /api/ routes except the public list."""
    path = request.url.path
    if path.startswith("/api/") and path not in _PUBLIC_API_PATHS and not path.startswith("/api/plugins/"):
        for _file_module_prefix in _FILE_MODULE_API_PREFIXES:
            if (
                path == _file_module_prefix
                or path.startswith(_file_module_prefix + "/")
                or path.startswith(_file_module_prefix + "?")
            ):
                return await call_next(request)
        if not _has_valid_session_token(request):
            return JSONResponse(
                status_code=401,
                content={"detail": "未授权"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Config schema — auto-generated from DEFAULT_CONFIG
# ---------------------------------------------------------------------------

# Manual overrides for fields that need select options or custom types
_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "model": {
        "type": "string",
        "description": "Default model (e.g. anthropic/claude-sonnet-4.6)",
        "category": "general",
    },
    "model_context_length": {
        "type": "number",
        "description": "Context window override (0 = auto-detect from model metadata)",
        "category": "general",
    },
    "terminal.backend": {
        "type": "select",
        "description": "Terminal execution backend",
        "options": ["local", "docker", "ssh", "modal", "daytona", "vercel_sandbox", "singularity"],
    },
    "terminal.vercel_runtime": {
        "type": "select",
        "description": "Vercel Sandbox runtime",
        "options": ["node24", "node22", "python3.13"],  # sync with _SUPPORTED_VERCEL_RUNTIMES in terminal_tool.py
    },
    "terminal.modal_mode": {
        "type": "select",
        "description": "Modal sandbox mode",
        "options": ["sandbox", "function"],
    },
    "tts.provider": {
        "type": "select",
        "description": "Text-to-speech provider",
        "options": ["edge", "elevenlabs", "openai", "neutts"],
    },
    "stt.provider": {
        "type": "select",
        "description": "Speech-to-text provider",
        "options": ["local", "openai", "mistral"],
    },
    "display.skin": {
        "type": "select",
        "description": "CLI visual theme",
        "options": ["default", "ares", "mono", "slate"],
    },
    "dashboard.theme": {
        "type": "select",
        "description": "Web dashboard visual theme",
        "options": ["default", "midnight", "ember", "mono", "cyberpunk", "rose"],
    },
    "display.resume_display": {
        "type": "select",
        "description": "How resumed sessions display history",
        "options": ["minimal", "full", "off"],
    },
    "display.busy_input_mode": {
        "type": "select",
        "description": "Input behavior while agent is running",
        "options": ["interrupt", "queue", "steer"],
    },
    "memory.provider": {
        "type": "select",
        "description": "Memory provider plugin",
        "options": ["builtin", "honcho"],
    },
    "approvals.mode": {
        "type": "select",
        "description": "Dangerous command approval mode",
        "options": ["ask", "yolo", "deny"],
    },
    "context.engine": {
        "type": "select",
        "description": "Context management engine",
        "options": ["default", "custom"],
    },
    "human_delay.mode": {
        "type": "select",
        "description": "Simulated typing delay mode",
        "options": ["off", "typing", "fixed"],
    },
    "logging.level": {
        "type": "select",
        "description": "Log level for agent.log",
        "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
    },
    "agent.service_tier": {
        "type": "select",
        "description": "API service tier (OpenAI/Anthropic)",
        "options": ["", "auto", "default", "flex"],
    },
    "delegation.reasoning_effort": {
        "type": "select",
        "description": "Reasoning effort for delegated subagents",
        "options": ["", "low", "medium", "high"],
    },
}

# Categories with fewer fields get merged into "general" to avoid tab sprawl.
_CATEGORY_MERGE: Dict[str, str] = {
    "privacy": "security",
    "context": "agent",
    "skills": "agent",
    "cron": "agent",
    "network": "agent",
    "checkpoints": "agent",
    "approvals": "security",
    "human_delay": "display",
    "dashboard": "display",
    "code_execution": "agent",
    "prompt_caching": "agent",
    "goals": "agent",
    # Only `telegram.reactions` currently lives under telegram — fold it in
    # with the other messaging-platform config (discord) so it isn't an
    # orphan tab of one field.
    "telegram": "discord",
}

# Display order for tabs — unlisted categories sort alphabetically after these.
_CATEGORY_ORDER = [
    "general", "agent", "terminal", "display", "delegation",
    "memory", "compression", "security", "browser", "voice",
    "tts", "stt", "logging", "discord", "auxiliary",
]


def _infer_type(value: Any) -> str:
    """Infer a UI field type from a Python value."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "string"


def _build_schema_from_config(
    config: Dict[str, Any],
    prefix: str = "",
) -> Dict[str, Dict[str, Any]]:
    """Walk DEFAULT_CONFIG and produce a flat dot-path → field schema dict."""
    schema: Dict[str, Dict[str, Any]] = {}
    for key, value in config.items():
        full_key = f"{prefix}.{key}" if prefix else key

        # Skip internal / version keys
        if full_key in ("_config_version",):
            continue

        # Category is the first path component for nested keys, or "general"
        # for top-level scalar fields (model, toolsets, timezone, etc.).
        if prefix:
            category = prefix.split(".")[0]
        elif isinstance(value, dict):
            category = key
        else:
            category = "general"

        if isinstance(value, dict):
            # Recurse into nested dicts
            schema.update(_build_schema_from_config(value, full_key))
        else:
            entry: Dict[str, Any] = {
                "type": _infer_type(value),
                "description": full_key.replace(".", " → ").replace("_", " ").title(),
                "category": category,
            }
            # Apply manual overrides
            if full_key in _SCHEMA_OVERRIDES:
                entry.update(_SCHEMA_OVERRIDES[full_key])
            # Merge small categories
            entry["category"] = _CATEGORY_MERGE.get(entry["category"], entry["category"])
            schema[full_key] = entry
    return schema


CONFIG_SCHEMA = _build_schema_from_config(DEFAULT_CONFIG)

# Inject virtual fields that don't live in DEFAULT_CONFIG but are surfaced
# by the normalize/denormalize cycle.  Insert model_context_length right after
# the "model" key so it renders adjacent in the frontend.
_mcl_entry = _SCHEMA_OVERRIDES["model_context_length"]
_ordered_schema: Dict[str, Dict[str, Any]] = {}
for _k, _v in CONFIG_SCHEMA.items():
    _ordered_schema[_k] = _v
    if _k == "model":
        _ordered_schema["model_context_length"] = _mcl_entry
CONFIG_SCHEMA = _ordered_schema


class ConfigUpdate(BaseModel):
    config: dict


class EnvVarUpdate(BaseModel):
    key: str
    value: str


class EnvVarDelete(BaseModel):
    key: str


class EnvVarReveal(BaseModel):
    key: str


class MessagingPlatformUpdate(BaseModel):
    enabled: Optional[bool] = None
    env: Dict[str, str] = {}
    clear_env: List[str] = []


class SavedModelUpdate(BaseModel):
    name: str
    provider: str
    model: str
    base_url: str = ""


class SavedModelCreate(SavedModelUpdate):
    api_key: Optional[str] = None


class SavedModelValidateRequest(BaseModel):
    credential_index: Optional[int] = None


class ModelRouteValidateRequest(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    name: Optional[str] = None
    credential_index: Optional[int] = None


class ModelDiscoverRequest(BaseModel):
    provider: str
    base_url: str = ""
    credential_index: Optional[int] = None


class AudioTranscriptionRequest(BaseModel):
    data_url: str
    mime_type: Optional[str] = None


class ModelAssignment(BaseModel):
    """Payload for POST /api/model/set — assign a provider/model to a slot.

    scope="main"        → writes model.provider + model.default
    scope="auxiliary"   → writes auxiliary.<task>.provider + auxiliary.<task>.model
    scope="auxiliary" with task=""  → applied to every auxiliary.* slot
    scope="auxiliary" with task="__reset__"  → resets every slot to provider="auto"
    """
    scope: str
    provider: str
    model: str
    task: str = ""
    base_url: str = ""


class LangGraphRunRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    provider: Optional[str] = None
    toolsets: Optional[Any] = None
    source_path: Optional[str] = None
    dry_run: bool = True


class OrchestrateRunRequest(BaseModel):
    task: str
    mode: Optional[str] = None              # "single" | "ensemble"
    models: Optional[Any] = None            # list of "model" or "provider:model" or dicts
    model_assignments: Optional[Dict[str, str]] = None
    provider: Optional[str] = None
    toolsets: Optional[Any] = None
    max_steps: int = 1
    dry_run: bool = True


class CredentialPoolAdd(BaseModel):
    provider: str
    api_key: str = ""
    label: Optional[str] = None
    base_url: Optional[str] = None


def _apply_main_model_assignment(
    model_cfg: Any, provider: str, model: str, base_url: str = ""
) -> dict:
    """Apply a main-slot model assignment without losing custom endpoints."""
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    prev_provider = str(model_cfg.get("provider") or "").strip().lower()
    new_provider = provider.strip().lower()
    if new_provider != "custom" and not new_provider.startswith("custom:"):
        base_url = ""
    model_cfg["provider"] = provider
    model_cfg["default"] = model
    if base_url.strip():
        model_cfg["base_url"] = base_url.strip()
    elif model_cfg.get("base_url") and new_provider != prev_provider:
        model_cfg["base_url"] = ""
    model_cfg.pop("context_length", None)
    return model_cfg


_AUDIO_MIME_EXTENSIONS: Dict[str, str] = {
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp3": ".mp3",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
    "video/webm": ".webm",
}
_MAX_TRANSCRIPTION_UPLOAD_BYTES = 25 * 1024 * 1024


def _audio_extension_for_mime(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    return _AUDIO_MIME_EXTENSIONS.get(normalized, ".webm")


_GATEWAY_HEALTH_URL = os.getenv("GATEWAY_HEALTH_URL")
try:
    _GATEWAY_HEALTH_TIMEOUT = float(os.getenv("GATEWAY_HEALTH_TIMEOUT", "3"))
except (ValueError, TypeError):
    _log.warning(
        "Invalid GATEWAY_HEALTH_TIMEOUT value %r — using default 3.0s",
        os.getenv("GATEWAY_HEALTH_TIMEOUT"),
    )
    _GATEWAY_HEALTH_TIMEOUT = 3.0


def _gateway_health_candidate_urls() -> list[str]:
    urls: list[str] = []

    def add_url(value: Any) -> None:
        if not value:
            return
        url = str(value).strip().rstrip("/")
        if url and url not in urls:
            urls.append(url)

    add_url(_GATEWAY_HEALTH_URL)

    try:
        from qiqiclaw_cli.gateway import gateway_setup_status

        api_server = gateway_setup_status().get("api_server") or {}
        if api_server.get("enabled"):
            host = str(api_server.get("host") or "127.0.0.1").strip()
            if host in ("0.0.0.0", "::"):
                host = "127.0.0.1"
            port = int(api_server.get("port") or 8642)
            add_url(f"http://{host}:{port}")
    except Exception:
        pass

    return urls


def _probe_gateway_health() -> tuple[bool, dict | None, str | None]:
    """Probe the gateway via its HTTP health endpoint (cross-container).

    Uses ``/health/detailed`` first (returns full state), falling back to
    the simpler ``/health`` endpoint.  Returns ``(is_alive, body_dict)``.

    Accepts any of these as ``GATEWAY_HEALTH_URL``:
    - ``http://gateway:8642``                (base URL — recommended)
    - ``http://gateway:8642/health``         (explicit health path)
    - ``http://gateway:8642/health/detailed`` (explicit detailed path)

    If no explicit ``GATEWAY_HEALTH_URL`` is set, it derives the local API
    server address from ``qiqiclaw setup gateway`` state.

    This is a **blocking** call — run via ``run_in_executor`` from async code.
    """
    candidates = _gateway_health_candidate_urls()
    if not candidates:
        return False, None, None

    # Normalise to base URL so we always probe the right paths regardless of
    # whether the user included /health or /health/detailed in the env var.
    for candidate in candidates:
        base = candidate.rstrip("/")
        if base.endswith("/health/detailed"):
            base = base[: -len("/health/detailed")]
        elif base.endswith("/health"):
            base = base[: -len("/health")]

        for path in (f"{base}/health/detailed", f"{base}/health"):
            try:
                req = urllib.request.Request(path, method="GET")
                with urllib.request.urlopen(req, timeout=_GATEWAY_HEALTH_TIMEOUT) as resp:
                    if resp.status == 200:
                        body = json.loads(resp.read())
                        return True, body, base
            except Exception:
                continue
    return False, None, None


@app.get("/api/status")
async def get_status():
    current_ver, latest_ver = check_config_version()

    # --- Gateway liveness detection ---
    # Try local PID check first (same-host). If that fails, probe the gateway
    # over HTTP via either GATEWAY_HEALTH_URL or the API server configured by
    # `qiqiclaw setup gateway`.
    gateway_pid = get_running_pid()
    gateway_running = gateway_pid is not None
    remote_health_body: dict | None = None

    detected_health_url = _GATEWAY_HEALTH_URL
    if not gateway_running:
        loop = asyncio.get_event_loop()
        alive, remote_health_body, detected_health_url = await loop.run_in_executor(
            None, _probe_gateway_health
        )
        if alive:
            gateway_running = True
            # PID from the remote container (display only — not locally valid)
            if remote_health_body:
                gateway_pid = remote_health_body.get("pid")

    gateway_state = None
    gateway_platforms: dict = {}
    gateway_exit_reason = None
    gateway_updated_at = None
    configured_gateway_platforms: set[str] | None = None
    try:
        from gateway.config import load_gateway_config

        gateway_config = load_gateway_config()
        configured_gateway_platforms = {
            platform.value for platform in gateway_config.get_connected_platforms()
        }
    except Exception:
        configured_gateway_platforms = None

    # Prefer the detailed health endpoint response (has full state) when the
    # local runtime status file is absent or stale (cross-container).
    runtime = read_runtime_status()
    if runtime is None and remote_health_body and remote_health_body.get("gateway_state"):
        runtime = remote_health_body

    if runtime:
        gateway_state = runtime.get("gateway_state")
        gateway_platforms = runtime.get("platforms") or {}
        if configured_gateway_platforms is not None:
            gateway_platforms = {
                key: value
                for key, value in gateway_platforms.items()
                if key in configured_gateway_platforms
            }
        gateway_exit_reason = runtime.get("exit_reason")
        gateway_updated_at = runtime.get("updated_at")
        if not gateway_running:
            gateway_state = gateway_state if gateway_state in ("stopped", "startup_failed") else "stopped"
            gateway_platforms = {}
        elif gateway_running and remote_health_body is not None:
            # The health probe confirmed the gateway is alive, but the local
            # runtime status file may be stale (cross-container).  Override
            # stopped/None state so the dashboard shows the correct badge.
            if gateway_state in (None, "stopped"):
                gateway_state = "running"

    # If there was no runtime info at all but the health probe confirmed alive,
    # ensure we still report the gateway as running (no shared volume scenario).
    if gateway_running and gateway_state is None and remote_health_body is not None:
        gateway_state = "running"

    active_sessions = 0
    try:
        from qiqiclaw_state import SessionDB
        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(limit=50)
            now = time.time()
            active_sessions = sum(
                1 for s in sessions
                if s.get("ended_at") is None
                and (now - s.get("last_active", s.get("started_at", 0))) < 300
            )
        finally:
            db.close()
    except Exception:
        pass

    return {
        "version": __version__,
        "release_date": __release_date__,
        "qiqiclaw_home": str(get_qiqiclaw_home()),
        "config_path": str(get_config_path()),
        "env_path": str(get_env_path()),
        "config_version": current_ver,
        "latest_config_version": latest_ver,
        "gateway_running": gateway_running,
        "gateway_pid": gateway_pid,
        "gateway_health_url": detected_health_url,
        "gateway_state": gateway_state,
        "gateway_platforms": gateway_platforms,
        "gateway_exit_reason": gateway_exit_reason,
        "gateway_updated_at": gateway_updated_at,
        "active_sessions": active_sessions,
    }


# ---------------------------------------------------------------------------
# Gateway + update actions (invoked from the Status page).
#
# Both commands are spawned as detached subprocesses so the HTTP request
# returns immediately.  stdin is closed (``DEVNULL``) so any stray ``input()``
# calls fail fast with EOF rather than hanging forever.  stdout/stderr are
# streamed to a per-action log file under ``~/.qiqiclaw/logs/<action>.log`` so
# the dashboard can tail them back to the user.
# ---------------------------------------------------------------------------

_ACTION_LOG_DIR: Path = get_qiqiclaw_home() / "logs"

# Short ``name`` (from the URL) → absolute log file path.
_ACTION_LOG_FILES: Dict[str, str] = {
    "gateway-restart": "gateway-restart.log",
    "qiqiclaw-update": "qiqiclaw-update.log",
}

# ``name`` → most recently spawned Popen handle.  Used so ``status`` can
# report liveness and exit code without shelling out to ``ps``.
_ACTION_PROCS: Dict[str, subprocess.Popen] = {}


def _spawn_hermes_action(subcommand: List[str], name: str) -> subprocess.Popen:
    """Spawn ``qiqiclaw <subcommand>`` detached and record the Popen handle.

    Uses the running interpreter's ``qiqiclaw_cli.main`` module so the action
    inherits the same venv/PYTHONPATH the web server is using.
    """
    log_file_name = _ACTION_LOG_FILES[name]
    _ACTION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _ACTION_LOG_DIR / log_file_name
    log_file = open(log_path, "ab", buffering=0)
    log_file.write(
        f"\n=== {name} started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode()
    )

    cmd = [sys.executable, "-m", "qiqiclaw_cli.main", *subcommand]

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(PROJECT_ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "env": {**os.environ, "HERMES_NONINTERACTIVE": "1"},
    }
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    _ACTION_PROCS[name] = proc
    return proc


def _tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path``.  Reads the whole file — fine
    for our small per-action logs.  Binary-decoded with ``errors='replace'``
    so log corruption doesn't 500 the endpoint."""
    if not path.exists():
        return []
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    return lines[-n:] if n > 0 else lines


@app.post("/api/gateway/restart")
async def restart_gateway():
    """Kick off a ``qiqiclaw gateway restart`` in the background."""
    try:
        proc = _spawn_hermes_action(["gateway", "restart"], "gateway-restart")
    except Exception as exc:
        _log.exception("Failed to spawn gateway restart")
        raise HTTPException(status_code=500, detail=f"重启网关失败: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "gateway-restart",
    }


@app.post("/api/qiqiclaw/update")
async def update_hermes():
    """Kick off ``qiqiclaw update`` in the background."""
    try:
        proc = _spawn_hermes_action(["update"], "qiqiclaw-update")
    except Exception as exc:
        _log.exception("Failed to spawn qiqiclaw update")
        raise HTTPException(status_code=500, detail=f"启动更新失败: {exc}")
    return {
        "ok": True,
        "pid": proc.pid,
        "name": "qiqiclaw-update",
    }


@app.post("/api/hermes/update")
async def update_hermes_compat():
    """Backward-compatible alias for older desktop builds."""
    return await update_hermes()


@app.get("/api/actions/{name}/status")
async def get_action_status(name: str, lines: int = 200):
    """Tail an action log and report whether the process is still running."""
    log_file_name = _ACTION_LOG_FILES.get(name)
    if log_file_name is None:
        raise HTTPException(status_code=404, detail=f"未知操作: {name}")

    log_path = _ACTION_LOG_DIR / log_file_name
    tail = _tail_lines(log_path, min(max(lines, 1), 2000))

    proc = _ACTION_PROCS.get(name)
    if proc is None:
        running = False
        exit_code: Optional[int] = None
        pid: Optional[int] = None
    else:
        exit_code = proc.poll()
        running = exit_code is None
        pid = proc.pid

    return {
        "name": name,
        "running": running,
        "exit_code": exit_code,
        "pid": pid,
        "lines": tail,
    }


def _archive_matches(session: Dict[str, Any], archived: str) -> bool:
    is_archived = bool(session.get("archived"))
    if archived == "only":
        return is_archived
    if archived == "exclude":
        return not is_archived
    return True


def _session_matches_filters(
    session: Dict[str, Any],
    *,
    min_message_count: int,
    archived: str,
    source: Optional[str],
    exclude_sources: Optional[List[str]],
) -> bool:
    if int(session.get("message_count") or 0) < min_message_count:
        return False
    if not _archive_matches(session, archived):
        return False
    session_source = session.get("source")
    if source and session_source != source:
        return False
    if exclude_sources and session_source in exclude_sources:
        return False
    return True


def _sort_sessions_for_dashboard(sessions: List[Dict[str, Any]], order: str) -> None:
    sort_key = "last_active" if order == "recent" else "started_at"
    sessions.sort(
        key=lambda s: (s.get(sort_key) or s.get("started_at") or 0, s.get("started_at") or 0, s.get("id") or ""),
        reverse=True,
    )


def _list_sessions_compat(
    db,
    *,
    limit: int,
    offset: int,
    min_messages: int,
    archived: str,
    order: str,
    source: Optional[str],
    exclude_sources: Optional[str],
) -> tuple[List[Dict[str, Any]], int]:
    min_message_count = max(0, min_messages)
    exclude_list = [s.strip() for s in (exclude_sources or "").split(",") if s.strip()]
    fetch_limit = min(max(limit + offset, limit, 100), 5000)

    try:
        rows = db.list_sessions_rich(
            source=source or None,
            exclude_sources=exclude_list or None,
            limit=fetch_limit,
            offset=0,
            order_by_last_active=order == "recent",
        )
    except TypeError:
        rows = db.list_sessions_rich(limit=fetch_limit, offset=0, order_by_last_active=order == "recent")

    filtered = [
        s for s in rows
        if _session_matches_filters(
            s,
            min_message_count=min_message_count,
            archived=archived,
            source=source or None,
            exclude_sources=exclude_list or None,
        )
    ]
    _sort_sessions_for_dashboard(filtered, order)
    return filtered[offset:offset + limit], len(filtered)


@app.get("/api/sessions")
async def get_sessions(
    limit: int = 20,
    offset: int = 0,
    min_messages: int = 0,
    archived: str = "exclude",
    order: str = "created",
    source: Optional[str] = None,
    exclude_sources: Optional[str] = None,
):
    if archived not in ("exclude", "only", "include"):
        raise HTTPException(status_code=400, detail="archived must be one of: exclude, only, include")
    if order not in ("created", "recent"):
        raise HTTPException(status_code=400, detail="order must be one of: created, recent")
    try:
        from qiqiclaw_state import SessionDB
        db = SessionDB()
        try:
            sessions, total = _list_sessions_compat(
                db,
                limit=max(1, limit),
                offset=max(0, offset),
                min_messages=min_messages,
                archived=archived,
                order=order,
                source=source,
                exclude_sources=exclude_sources,
            )
            now = time.time()
            for s in sessions:
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
                s["archived"] = bool(s.get("archived"))
            return {"sessions": sessions, "total": total, "limit": limit, "offset": offset}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions failed")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/sessions/search")
async def search_sessions(q: str = "", limit: int = 20):
    """Full-text search across session message content using FTS5."""
    if not q or not q.strip():
        return {"results": []}
    try:
        from qiqiclaw_state import SessionDB
        db = SessionDB()
        try:
            # Auto-add prefix wildcards so partial words match
            # e.g. "nimb" → "nimb*" matches "nimby"
            # Preserve quoted phrases and existing wildcards as-is
            import re
            terms = []
            for token in re.findall(r'"[^"]*"|\S+', q.strip()):
                if token.startswith('"') or token.endswith("*"):
                    terms.append(token)
                else:
                    terms.append(token + "*")
            prefix_query = " ".join(terms)
            matches = db.search_messages(query=prefix_query, limit=limit)
            # Group by session_id — return unique sessions with their best snippet
            seen: dict = {}
            for m in matches:
                sid = m["session_id"]
                if sid not in seen:
                    seen[sid] = {
                        "session_id": sid,
                        "snippet": m.get("snippet", ""),
                        "role": m.get("role"),
                        "source": m.get("source"),
                        "model": m.get("model"),
                        "session_started": m.get("session_started"),
                    }
            return {"results": list(seen.values())}
        finally:
            db.close()
    except Exception:
        _log.exception("GET /api/sessions/search failed")
        raise HTTPException(status_code=500, detail="搜索失败")


def _normalize_config_for_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize config for the web UI.

    QiQiClaw supports ``model`` as either a bare string (``"anthropic/claude-sonnet-4"``)
    or a dict (``{default: ..., provider: ..., base_url: ...}``).  The schema is built
    from DEFAULT_CONFIG where ``model`` is a string, but user configs often have the
    dict form.  Normalize to the string form so the frontend schema matches.

    Also surfaces ``model_context_length`` as a top-level field so the web UI can
    display and edit it.  A value of 0 means "auto-detect".
    """
    config = dict(config)  # shallow copy
    model_val = config.get("model")
    if isinstance(model_val, dict):
        # Extract context_length before flattening the dict
        ctx_len = model_val.get("context_length", 0)
        config["model"] = model_val.get("default", model_val.get("name", ""))
        config["model_context_length"] = ctx_len if isinstance(ctx_len, int) else 0
    else:
        config["model_context_length"] = 0
    return config


@app.get("/api/config")
async def get_config():
    config = _normalize_config_for_web(load_config())
    # Strip internal keys that the frontend shouldn't see or send back
    return {k: v for k, v in config.items() if not k.startswith("_")}


@app.get("/api/config/defaults")
async def get_defaults():
    return DEFAULT_CONFIG


@app.get("/api/config/schema")
async def get_schema():
    return {"fields": CONFIG_SCHEMA, "category_order": _CATEGORY_ORDER}


_EMPTY_MODEL_INFO: dict = {
    "model": "",
    "provider": "",
    "auto_context_length": 0,
    "config_context_length": 0,
    "effective_context_length": 0,
    "capabilities": {},
}


def _models_library_path() -> Path:
    return get_qiqiclaw_home() / "models.json"


def _read_models_library() -> List[Dict[str, Any]]:
    path = _models_library_path()
    try:
        if not path.exists():
            return []
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(raw, list):
        return []

    models: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        model = str(item.get("model") or "").strip()
        provider = str(item.get("provider") or "").strip().lower()
        if not model or not provider:
            continue
        name = str(item.get("name") or model).strip() or model
        base_url = str(item.get("base_url") or item.get("baseUrl") or "").strip()
        entry_id = str(item.get("id") or uuid.uuid4().hex).strip()
        created_at = item.get("created_at", item.get("createdAt", int(time.time() * 1000)))
        try:
            created_at = int(created_at)
        except Exception:
            created_at = int(time.time() * 1000)
        models.append({
            "id": entry_id,
            "name": name,
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "created_at": created_at,
        })
    return models


def _write_models_library(models: List[Dict[str, Any]]) -> None:
    path = _models_library_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _find_or_create_model_library_entry(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    name: str = "",
) -> Tuple[Dict[str, Any], bool]:
    model = str(model or "").strip()
    provider = str(provider or "").strip().lower()
    base_url = str(base_url or "").strip()
    name = str(name or "").strip() or model
    if not model or not provider:
        raise HTTPException(status_code=400, detail="provider and model are required")
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for custom models")

    models = _read_models_library()
    for entry in models:
        if (
            entry.get("provider") == provider
            and entry.get("model") == model
            and (entry.get("base_url") or "") == base_url
        ):
            return entry, True

    entry = {
        "id": uuid.uuid4().hex,
        "name": name,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "created_at": int(time.time() * 1000),
    }
    models.append(entry)
    _write_models_library(models)
    return entry, False


def _normalize_model_base_url(raw_url: Any) -> str:
    return str(raw_url or "").strip().rstrip("/")


def _openai_compatible_base_url_candidates(raw_url: Any) -> List[str]:
    normalized = _normalize_model_base_url(raw_url)
    if not normalized:
        return []
    lowered = normalized.lower()
    for suffix in ("/chat/completions", "/models"):
        if lowered.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            lowered = normalized.lower()
            break
    candidates = [normalized]
    if not (
        lowered.endswith("/v1")
        or lowered.endswith("/v1beta")
    ):
        candidates.append(f"{normalized}/v1")
    return candidates


def _base_url_from_probe_url(url: str, suffix: str) -> str:
    normalized = _normalize_model_base_url(url)
    if suffix and normalized.endswith(suffix):
        return normalized[: -len(suffix)].rstrip("/")
    return normalized


def _update_model_library_entry_base_url(model_entry: Dict[str, Any], base_url: str) -> Dict[str, Any]:
    resolved = _normalize_model_base_url(base_url)
    if not resolved or _normalize_model_base_url(model_entry.get("base_url")) == resolved:
        return model_entry
    model_id = str(model_entry.get("id") or "").strip()
    if not model_id:
        model_entry["base_url"] = resolved
        return model_entry
    models = _read_models_library()
    for entry in models:
        if entry.get("id") != model_id:
            continue
        entry["base_url"] = resolved
        model_entry["base_url"] = resolved
        _write_models_library(models)
        return entry
    model_entry["base_url"] = resolved
    return model_entry


def _model_route_host(base_url: str) -> str:
    if not base_url:
        return ""
    try:
        from urllib.parse import urlparse

        return urlparse(base_url).netloc or base_url
    except Exception:
        return base_url


def _model_route_label(provider: str, base_url: str) -> str:
    host = _model_route_host(base_url)
    return f"{provider} · {host}" if host else provider


def _credential_matches_model_entry(credential: Any, entry: Dict[str, Any]) -> bool:
    if not isinstance(credential, dict):
        return False
    provider = str(entry.get("provider") or "").strip().lower()
    model_base_url = _normalize_model_base_url(entry.get("base_url"))
    credential_base_url = _normalize_model_base_url(credential.get("base_url"))
    if provider == "custom":
        return bool(model_base_url and credential_base_url and model_base_url == credential_base_url)
    return not model_base_url or not credential_base_url or model_base_url == credential_base_url


def _credential_validates_model(credential: Any, entry: Dict[str, Any]) -> bool:
    if not _credential_matches_model_entry(credential, entry):
        return False
    if not isinstance(credential, dict):
        return False
    model = str(entry.get("model") or "").strip()
    base_url = _normalize_model_base_url(entry.get("base_url"))
    validated = credential.get("validated_models")
    if isinstance(validated, dict):
        state = validated.get(model)
        if isinstance(state, dict) and state.get("status") == "ok":
            state_base_url = _normalize_model_base_url(state.get("base_url"))
            if not base_url or not state_base_url or state_base_url == base_url:
                return True
    return credential.get("last_status") == "ok" and str(credential.get("last_model") or "") == model


def _find_verified_credential(entry: Dict[str, Any]) -> Optional[Tuple[int, Dict[str, Any]]]:
    from qiqiclaw_cli.auth import read_credential_pool

    provider = str(entry.get("provider") or "").strip().lower()
    pool_entries = read_credential_pool(provider)
    if not isinstance(pool_entries, list):
        return None
    for index, credential in enumerate(pool_entries, start=1):
        if _credential_validates_model(credential, entry):
            return index, credential
    return None


def _validated_model_base_url(credential: Dict[str, Any], entry: Dict[str, Any]) -> str:
    model = str(entry.get("model") or "").strip()
    validated = credential.get("validated_models") if isinstance(credential, dict) else None
    state = validated.get(model) if isinstance(validated, dict) else None
    if isinstance(state, dict):
        state_base_url = _normalize_model_base_url(state.get("base_url"))
        if state_base_url:
            return state_base_url
    return _resolve_model_route_base_url(str(entry.get("provider") or ""), entry, credential)


def _annotate_saved_model(entry: Dict[str, Any]) -> Dict[str, Any]:
    annotated = dict(entry)
    verified = _find_verified_credential(entry)
    if verified is None:
        annotated.update({
            "verified": False,
            "verification_status": "unverified",
            "verification_message": "未找到已验证通过的匹配凭证",
        })
        return annotated
    index, credential = verified
    model = str(entry.get("model") or "").strip()
    validated = credential.get("validated_models") if isinstance(credential, dict) else None
    state = validated.get(model) if isinstance(validated, dict) else None
    annotated.update({
        "verified": True,
        "credential_index": index,
        "verification_status": "ok",
        "verification_message": "凭证池验证通过",
        "last_checked_at": state.get("checked_at") if isinstance(state, dict) else credential.get("last_checked_at"),
        "resolved_base_url": _validated_model_base_url(credential, entry),
    })
    return annotated


def _verified_model_library_entries() -> List[Tuple[Dict[str, Any], int, Dict[str, Any]]]:
    entries: List[Tuple[Dict[str, Any], int, Dict[str, Any]]] = []
    for entry in _read_models_library():
        verified = _find_verified_credential(entry)
        if verified is None:
            continue
        index, credential = verified
        entries.append((entry, index, credential))
    return entries


def _credential_entry_from_provider_env(provider: str, model_entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    provider = str(provider or "").strip().lower()
    base_url = str(model_entry.get("base_url") or "").strip()

    if provider == "custom":
        from qiqiclaw_cli.config import get_env_value

        api_key = (get_env_value("CUSTOM_API_KEY") or "").strip()
        resolved_base_url = base_url or (get_env_value("OPENAI_BASE_URL") or get_env_value("CUSTOM_BASE_URL") or "").strip()
        if not api_key and not resolved_base_url:
            return None
        if not api_key:
            source = "custom:base_url"
            label = "custom endpoint"
        else:
            source = "env:CUSTOM_API_KEY"
            label = "setup/env CUSTOM_API_KEY"
        return {
            "id": uuid.uuid4().hex,
            "label": label,
            "source": source,
            "auth_type": "api_key",
            "access_token": api_key,
            "base_url": resolved_base_url,
            "priority": 50,
            "request_count": 0,
        }

    try:
        from qiqiclaw_cli.auth import PROVIDER_REGISTRY, has_usable_secret
        from qiqiclaw_cli.config import get_env_value
    except Exception:
        return None

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None or getattr(pconfig, "auth_type", "") != "api_key":
        return None

    for env_var in getattr(pconfig, "api_key_env_vars", ()) or ():
        api_key = (get_env_value(env_var) or "").strip()
        if not has_usable_secret(api_key):
            continue
        base_url_env = getattr(pconfig, "base_url_env_var", "") or ""
        configured_base_url = (get_env_value(base_url_env) or "").strip() if base_url_env else ""
        return {
            "id": uuid.uuid4().hex,
            "label": f"setup/env {env_var}",
            "source": f"env:{env_var}",
            "auth_type": "api_key",
            "access_token": api_key,
            "base_url": base_url or configured_base_url or getattr(pconfig, "inference_base_url", "") or "",
            "priority": 50,
            "request_count": 0,
        }
    return None


def _resolve_model_route_base_url(provider: str, model_entry: Dict[str, Any], credential: Dict[str, Any]) -> str:
    base_url = _normalize_model_base_url(model_entry.get("base_url") or credential.get("base_url"))
    if base_url:
        return base_url

    provider = str(provider or "").strip().lower()
    if provider == "custom":
        try:
            from qiqiclaw_cli.config import get_env_value

            return _normalize_model_base_url(get_env_value("OPENAI_BASE_URL") or get_env_value("CUSTOM_BASE_URL"))
        except Exception:
            return ""

    try:
        from qiqiclaw_cli.auth import PROVIDER_REGISTRY
        from qiqiclaw_cli.config import get_env_value
    except Exception:
        return ""

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        return ""
    base_url_env = getattr(pconfig, "base_url_env_var", "") or ""
    configured_base_url = (get_env_value(base_url_env) or "").strip() if base_url_env else ""
    return _normalize_model_base_url(configured_base_url or getattr(pconfig, "inference_base_url", "") or "")


def _provider_catalog_rows() -> List[Dict[str, Any]]:
    from qiqiclaw_cli.auth import PROVIDER_REGISTRY, read_credential_pool
    from qiqiclaw_cli.config import get_env_value

    pool = read_credential_pool()
    rows: List[Dict[str, Any]] = [{
        "slug": "custom",
        "name": "OpenAI 兼容 / 中转站 / 本地",
        "auth_type": "api_key",
        "base_url": "",
        "base_url_env_var": "CUSTOM_BASE_URL",
        "api_key_env_vars": ["CUSTOM_API_KEY", "OPENAI_API_KEY"],
        "key_env": "CUSTOM_API_KEY",
        "supports_model_discovery": True,
        "credential_count": len(pool.get("custom") or []),
        "verified_model_count": _count_verified_pool_models(pool.get("custom") or []),
        "source": "custom",
    }]

    for slug, pconfig in sorted(PROVIDER_REGISTRY.items(), key=lambda item: item[1].name.lower()):
        base_url_env = getattr(pconfig, "base_url_env_var", "") or ""
        configured_base_url = (get_env_value(base_url_env) or "").strip() if base_url_env else ""
        base_url = configured_base_url or getattr(pconfig, "inference_base_url", "") or ""
        api_key_env_vars = list(getattr(pconfig, "api_key_env_vars", ()) or ())
        entries = pool.get(slug) or []
        auth_type = getattr(pconfig, "auth_type", "") or ""
        rows.append({
            "slug": slug,
            "name": getattr(pconfig, "name", slug) or slug,
            "auth_type": auth_type,
            "base_url": base_url,
            "base_url_env_var": base_url_env,
            "api_key_env_vars": api_key_env_vars,
            "key_env": api_key_env_vars[0] if api_key_env_vars else "",
            "supports_model_discovery": _provider_supports_model_discovery(slug, base_url, auth_type),
            "credential_count": len(entries) if isinstance(entries, list) else 0,
            "verified_model_count": _count_verified_pool_models(entries if isinstance(entries, list) else []),
            "source": "setup",
        })
    return rows


def _count_verified_pool_models(entries: List[Any]) -> int:
    models: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        validated = entry.get("validated_models")
        if not isinstance(validated, dict):
            continue
        for model, state in validated.items():
            if isinstance(state, dict) and state.get("status") == "ok":
                models.add(str(model))
    return len(models)


def _provider_supports_model_discovery(provider: str, base_url: str, auth_type: str) -> bool:
    provider = str(provider or "").strip().lower()
    if provider == "custom":
        return True
    if auth_type != "api_key":
        return False
    mode = "chat_completions"
    try:
        from qiqiclaw_cli.providers import determine_api_mode
        mode = determine_api_mode(provider, base_url)
    except Exception:
        pass
    return mode in {"chat_completions", "codex_responses"}


def _discover_models_with_key(base_url: str, api_key: str) -> Tuple[bool, str, List[str], str]:
    import httpx

    candidates = _openai_compatible_base_url_candidates(base_url)
    if not candidates:
        return False, "Base URL 为空，无法发现模型", [], ""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_message = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), follow_redirects=True) as client:
            for candidate in candidates:
                url = candidate.rstrip("/") + "/models"
                resp = client.get(url, headers=headers)
                models = _parse_model_ids(resp)
                if resp.status_code < 200 or resp.status_code >= 300:
                    detail = resp.text.strip().replace("\n", " ")[:240]
                    last_message = f"HTTP {resp.status_code}: {detail or resp.reason_phrase}"
                    continue
                if not models:
                    last_message = "接口可访问，但未返回模型列表"
                    continue
                resolved = _base_url_from_probe_url(url, "/models")
                return True, f"发现 {len(models)} 个模型", models, resolved
    except Exception as exc:
        return False, f"请求失败: {exc}", [], ""
    return False, last_message or "没有发现可用模型", [], ""


def _discover_models_from_pool(provider: str, base_url: str = "", credential_index: Optional[int] = None) -> Dict[str, Any]:
    from qiqiclaw_cli.auth import read_credential_pool, write_credential_pool

    provider = str(provider or "").strip().lower()
    probe_entry = {"provider": provider, "model": "__discovery__", "base_url": base_url}
    resolved_base_url = _resolve_model_route_base_url(provider, probe_entry, {"base_url": base_url})
    catalog = {row["slug"]: row for row in _provider_catalog_rows()}
    provider_row = catalog.get(provider)
    if provider_row and not provider_row.get("supports_model_discovery"):
        return {
            "ok": False,
            "provider": provider,
            "base_url": resolved_base_url,
            "models": [],
            "checked": [],
            "message": "该 provider 不支持 OpenAI 兼容 /models 发现，请手动填写模型后验证。",
        }

    entries = read_credential_pool(provider)
    if not isinstance(entries, list) or not entries:
        seeded = _credential_entry_from_provider_env(provider, probe_entry)
        if seeded is not None:
            entries = [seeded]
        else:
            return {
                "ok": False,
                "provider": provider,
                "base_url": resolved_base_url,
                "models": [],
                "checked": [],
                "message": "该 provider 的凭证池为空，请先添加 key。",
            }

    models: List[str] = []
    saved_models: List[Dict[str, Any]] = []
    checked: List[Dict[str, Any]] = []
    changed = False
    now = int(time.time() * 1000)
    indexes = [credential_index] if credential_index else list(range(1, len(entries) + 1))
    for index in indexes:
        if not index or index < 1 or index > len(entries):
            continue
        credential = entries[index - 1]
        if not isinstance(credential, dict):
            continue
        if not _credential_matches_model_entry(credential, probe_entry):
            continue
        api_key = str(
            credential.get("access_token")
            or credential.get("api_key")
            or credential.get("runtime_api_key")
            or ""
        ).strip()
        entry_base_url = _resolve_model_route_base_url(provider, probe_entry, credential)
        if not api_key and provider != "custom":
            ok, message, found, resolved_entry_base_url = False, "凭证缺少 access_token", [], entry_base_url
        else:
            discovery = _discover_models_with_key(entry_base_url, api_key)
            if len(discovery) == 3:
                ok, message, found = discovery
                resolved_entry_base_url = entry_base_url
            else:
                ok, message, found, resolved_entry_base_url = discovery
            resolved_entry_base_url = resolved_entry_base_url or entry_base_url
        checked.append({
            "index": index,
            "ok": ok,
            "message": message,
            "base_url": resolved_entry_base_url,
            "count": len(found),
        })
        if ok and found:
            if resolved_entry_base_url and resolved_entry_base_url != entry_base_url:
                credential["base_url"] = resolved_entry_base_url
            validated = credential.get("validated_models")
            if not isinstance(validated, dict):
                validated = {}
                credential["validated_models"] = validated
            for model in found:
                validated[model] = {
                    "status": "ok",
                    "checked_at": now,
                    "base_url": resolved_entry_base_url,
                    "message": "模型发现接口验证通过",
                }
            credential["last_status"] = "ok"
            credential["last_checked_at"] = now
            credential["last_model"] = found[0]
            credential.pop("last_error", None)
            changed = True
        for model in found:
            if model not in models:
                models.append(model)
                entry, deduped = _find_or_create_model_library_entry(
                    provider=provider,
                    model=model,
                    base_url=resolved_entry_base_url,
                    name=model,
                )
                saved_models.append({
                    "id": entry.get("id"),
                    "model": model,
                    "provider": provider,
                    "base_url": entry.get("base_url") or resolved_entry_base_url,
                    "deduped": deduped,
                    "credential_index": index,
                })

    if changed:
        write_credential_pool(provider, entries)

    return {
        "ok": bool(models),
        "provider": provider,
        "base_url": next((item["base_url"] for item in checked if item.get("ok") and item.get("base_url")), resolved_base_url),
        "models": models,
        "saved_models": saved_models,
        "saved_count": len(saved_models),
        "checked": checked,
        "message": f"发现并加入模型库 {len(models)} 个可用模型" if models else (checked[-1]["message"] if checked else "没有匹配的凭证可用于发现模型"),
    }


def _validate_model_entry_with_pool(
    model_entry: Dict[str, Any],
    credential_index: Optional[int] = None,
) -> Dict[str, Any]:
    from qiqiclaw_cli.auth import read_credential_pool, write_credential_pool

    provider = str(model_entry.get("provider") or "").strip().lower()
    model = str(model_entry.get("model") or "").strip()
    if not provider or not model:
        raise HTTPException(status_code=400, detail="model library entry is incomplete")

    entries = read_credential_pool(provider)
    if not isinstance(entries, list) or not entries:
        seeded = _credential_entry_from_provider_env(provider, model_entry)
        if seeded is None:
            raise HTTPException(status_code=404, detail="No credential pool entries for this provider")
        entries = [seeded]
        write_credential_pool(provider, entries)

    candidate_indexes: List[int]
    if credential_index is not None:
        candidate_indexes = [credential_index]
    else:
        candidate_indexes = list(range(1, len(entries) + 1))

    checked: List[Dict[str, Any]] = []
    changed = False
    now = int(time.time() * 1000)
    for index in candidate_indexes:
        if index < 1 or index > len(entries):
            continue
        credential = entries[index - 1]
        if not isinstance(credential, dict) or not _credential_matches_model_entry(credential, model_entry):
            continue
        api_key = str(
            credential.get("access_token")
            or credential.get("api_key")
            or credential.get("runtime_api_key")
            or ""
        ).strip()
        base_url = _resolve_model_route_base_url(provider, model_entry, credential)
        if not api_key and provider != "custom":
            ok, message, resolved_base_url = False, "凭证缺少 access_token", base_url
        else:
            probe = _openai_compatible_chat_probe(base_url, api_key, model)
            if len(probe) == 2:
                ok, message = probe
                resolved_base_url = base_url
            else:
                ok, message, resolved_base_url = probe
            resolved_base_url = resolved_base_url or base_url
        if ok and resolved_base_url and resolved_base_url != base_url:
            credential["base_url"] = resolved_base_url
            model_entry = _update_model_library_entry_base_url(model_entry, resolved_base_url)
        validated = credential.get("validated_models")
        if not isinstance(validated, dict):
            validated = {}
            credential["validated_models"] = validated
        validated[model] = {
            "status": "ok" if ok else "error",
            "checked_at": now,
            "base_url": resolved_base_url,
            "message": message,
        }
        credential["last_status"] = "ok" if ok else "error"
        credential["last_checked_at"] = now
        credential["last_model"] = model
        if ok:
            credential.pop("last_error", None)
        else:
            credential["last_error"] = message
        changed = True
        checked.append({"base_url": resolved_base_url, "index": index, "ok": ok, "message": message})
        if ok:
            break

    if changed:
        write_credential_pool(provider, entries)
    if not checked:
        raise HTTPException(status_code=404, detail="No matching credential pool entries for this model")

    first_ok = next((item for item in checked if item["ok"]), None)
    return {
        "ok": first_ok is not None,
        "model": model,
        "provider": provider,
        "credential_index": first_ok["index"] if first_ok else checked[-1]["index"],
        "message": first_ok["message"] if first_ok else checked[-1]["message"],
        "base_url": first_ok["base_url"] if first_ok else checked[-1].get("base_url", ""),
        "checked": checked,
    }


def _build_verified_model_options_payload(
    *,
    current_model: str = "",
    current_provider: str = "",
) -> Dict[str, Any]:
    from qiqiclaw_cli.auth import get_auth_provider_display_name

    providers: List[Dict[str, Any]] = []
    rows_by_slug: Dict[str, Dict[str, Any]] = {}
    for entry, credential_index, credential in _verified_model_library_entries():
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip()
        if not provider or not model:
            continue
        row = rows_by_slug.get(provider)
        if row is None:
            row = {
                "slug": provider,
                "name": "OpenAI 兼容 / 中转站 / 本地" if provider == "custom" else get_auth_provider_display_name(provider),
                "is_current": provider == str(current_provider or "").strip().lower(),
                "is_user_defined": True,
                "models": [],
                "total_models": 0,
                "authenticated": True,
                "auth_type": "api_key",
                "key_env": "CUSTOM_API_KEY" if provider == "custom" else "",
                "source": "verified_model_library",
                "model_entries": {},
            }
            providers.append(row)
            rows_by_slug[provider] = row
        models = row["models"]
        if model not in models:
            models.append(model)
        resolved_base_url = _validated_model_base_url(credential, entry)
        existing = row["model_entries"].get(model)
        prefer_current = existing is None
        if existing is not None and provider != "custom":
            existing_base_url = _normalize_model_base_url(existing.get("base_url"))
            entry_base_url = _normalize_model_base_url(entry.get("base_url"))
            prefer_current = not entry_base_url or not existing_base_url
        if not prefer_current:
            row["total_models"] = len(models)
            continue
        row["model_entries"][model] = {
            "id": entry.get("id"),
            "name": entry.get("name") or model,
            "provider": provider,
            "model": model,
            "base_url": resolved_base_url,
            "endpoint_host": _model_route_host(resolved_base_url),
            "route_label": _model_route_label(provider, resolved_base_url),
            "credential_index": credential_index,
            "source": "verified_model_library",
        }
        row["total_models"] = len(models)
    return {
        "providers": providers,
        "model": current_model,
        "provider": current_provider,
    }


def _openai_compatible_chat_probe(base_url: str, api_key: str, model: str) -> Tuple[bool, str, str]:
    import httpx

    candidates = _openai_compatible_base_url_candidates(base_url)
    if not candidates:
        return False, "Base URL 为空，无法验证 OpenAI 兼容接口", ""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "你好，请简短回复你正在使用的模型名称"}],
        "temperature": 0,
        "max_tokens": 128,
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    last_message = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(30.0), follow_redirects=True) as client:
            for candidate in candidates:
                url = f"{candidate.rstrip('/')}/chat/completions"
                response = client.post(
                    url,
                    headers=headers,
                    json=payload,
                )
                resolved_base_url = _base_url_from_probe_url(url, "/chat/completions")
                if response.status_code < 200 or response.status_code >= 300:
                    detail = response.text.strip().replace("\n", " ")[:240]
                    last_message = f"HTTP {response.status_code}: {detail or response.reason_phrase}"
                    continue
                try:
                    data = response.json()
                except Exception:
                    last_message = "接口返回不是 JSON"
                    continue
                choices = data.get("choices") if isinstance(data, dict) else None
                if not isinstance(choices, list):
                    last_message = "响应缺少 choices"
                    continue
                for choice in choices:
                    if not isinstance(choice, dict):
                        continue
                    message = choice.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, str) and content.strip():
                            return True, "OpenAI 兼容聊天接口验证通过", resolved_base_url
                        if message.get("tool_calls"):
                            return True, "OpenAI 兼容聊天接口验证通过", resolved_base_url
                    delta = choice.get("delta")
                    if isinstance(delta, dict):
                        content = delta.get("content")
                        if isinstance(content, str) and content.strip():
                            return True, "OpenAI 兼容聊天接口验证通过", resolved_base_url
                last_message = "接口返回 2xx，但模型没有返回可用内容"
    except Exception as exc:
        return False, f"请求失败: {exc}", ""
    return False, last_message or "模型验证失败", ""


def _env_key_for_model_library_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    mappings = [
        (r"openrouter\.ai", "OPENROUTER_API_KEY"),
        (r"anthropic\.com", "ANTHROPIC_API_KEY"),
        (r"openai\.com", "OPENAI_API_KEY"),
        (r"api\.deepseek\.com", "DEEPSEEK_API_KEY"),
        (r"api\.x\.ai", "XAI_API_KEY"),
        (r"api\.z\.ai", "GLM_API_KEY"),
        (r"api\.moonshot\.ai", "KIMI_API_KEY"),
        (r"api\.moonshot\.cn", "KIMI_CN_API_KEY"),
        (r"api\.stepfun\.ai", "STEPFUN_API_KEY"),
        (r"api\.minimax\.io", "MINIMAX_API_KEY"),
        (r"api\.minimaxi\.com", "MINIMAX_CN_API_KEY"),
        (r"dashscope.*aliyuncs\.com", "DASHSCOPE_API_KEY"),
        (r"ollama\.com", "OLLAMA_API_KEY"),
        (r"api\.together\.xyz", "TOGETHER_API_KEY"),
        (r"api\.fireworks\.ai", "FIREWORKS_API_KEY"),
        (r"api\.mistral\.ai", "MISTRAL_API_KEY"),
        (r"api\.perplexity\.ai", "PERPLEXITY_API_KEY"),
    ]
    for pattern, key in mappings:
        if re.search(pattern, url, re.IGNORECASE):
            return key
    return "CUSTOM_API_KEY"


_CREDENTIAL_PROBES: dict[str, tuple[str, str]] = {
    "OPENROUTER_API_KEY": ("https://openrouter.ai/api/v1/key", "bearer"),
    "OPENAI_API_KEY": ("https://api.openai.com/v1/models", "bearer"),
    "XAI_API_KEY": ("https://api.x.ai/v1/models", "bearer"),
    "GEMINI_API_KEY": ("https://generativelanguage.googleapis.com/v1beta/models", "query"),
}


def _parse_model_ids(resp: "Any") -> List[str]:
    try:
        if not resp.is_success:
            return []
        payload = resp.json()
    except Exception:
        return []
    data = payload.get("data") if isinstance(payload, dict) else payload
    if not isinstance(data, list):
        return []
    ids: List[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id:
            ids.append(model_id)
    return ids


@app.post("/api/providers/validate")
async def validate_provider_credential(body: EnvVarUpdate, request: Request):
    _require_token(request)
    import httpx

    key = (body.key or "").strip()
    value = (body.value or "").strip()
    if not value:
        return {"ok": False, "reachable": True, "message": "Enter a value first."}

    if key == "OPENAI_BASE_URL":
        url = value.rstrip("/") + "/models"
        try:
            with httpx.Client(timeout=httpx.Timeout(8.0), follow_redirects=True) as client:
                resp = client.get(url)
            return {"ok": True, "reachable": True, "message": "", "models": _parse_model_ids(resp)}
        except Exception:
            return {"ok": False, "reachable": False, "message": f"Could not reach {url}."}

    probe = _CREDENTIAL_PROBES.get(key)
    if not probe:
        return {"ok": True, "reachable": False, "message": ""}

    url, auth = probe
    headers = {"Accept": "application/json"}
    params = {}
    if auth == "bearer":
        headers["Authorization"] = f"Bearer {value}"
    else:
        params["key"] = value

    try:
        with httpx.Client(timeout=httpx.Timeout(10.0), follow_redirects=True) as client:
            resp = client.get(url, headers=headers, params=params)
    except Exception:
        return {"ok": False, "reachable": False, "message": "Could not reach the provider to verify the key."}

    if resp.status_code in (401, 403):
        return {"ok": False, "reachable": True, "message": "That API key was rejected. Double-check it and try again."}
    if resp.status_code == 429 or resp.is_success:
        return {"ok": True, "reachable": True, "message": ""}
    return {"ok": False, "reachable": True, "message": f"Provider returned HTTP {resp.status_code} for this key."}


@app.get("/api/providers/catalog")
def get_provider_catalog():
    """Return the qiqiclaw setup provider/API catalog used by settings UIs."""
    return {"providers": _provider_catalog_rows()}


@app.post("/api/models/discover")
async def discover_models(body: ModelDiscoverRequest):
    provider = (body.provider or "").strip().lower()
    base_url = (body.base_url or "").strip()
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for custom model discovery")
    return _discover_models_from_pool(provider, base_url, body.credential_index)


def _merge_model_library_into_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    models = _read_models_library()
    if not models:
        return payload

    providers = payload.get("providers")
    if not isinstance(providers, list):
        providers = []
        payload["providers"] = providers

    rows_by_slug: Dict[str, Dict[str, Any]] = {
        str(row.get("slug") or "").lower(): row
        for row in providers
        if isinstance(row, dict) and row.get("slug")
    }

    for entry in models:
        provider = entry["provider"]
        row = rows_by_slug.get(provider)
        if row is None:
            row = {
                "slug": provider,
                "name": "OpenAI 兼容 / 中转站 / 本地" if provider == "custom" else provider,
                "is_current": provider == str(payload.get("provider") or "").lower(),
                "is_user_defined": True,
                "models": [],
                "total_models": 0,
                "authenticated": True,
                "auth_type": "api_key",
                "key_env": "CUSTOM_API_KEY" if provider == "custom" else "",
                "source": "models_library",
            }
            providers.append(row)
            rows_by_slug[provider] = row

        row_models = row.get("models")
        if not isinstance(row_models, list):
            row_models = []
            row["models"] = row_models
        if entry["model"] not in row_models:
            row_models.append(entry["model"])

        model_entries = row.get("model_entries")
        if not isinstance(model_entries, dict):
            model_entries = {}
            row["model_entries"] = model_entries
        existing = model_entries.get(entry["model"])
        if not isinstance(existing, dict) or entry.get("base_url"):
            model_entries[entry["model"]] = {
                "id": entry["id"],
                "name": entry["name"],
                "provider": provider,
                "model": entry["model"],
                "base_url": entry.get("base_url", ""),
                "source": "models_library",
            }
        row["total_models"] = max(int(row.get("total_models") or 0), len(row_models))

    return payload


@app.get("/api/model/info")
def get_model_info():
    """Return resolved model metadata for the currently configured model.

    Calls the same context-length resolution chain the agent uses, so the
    frontend can display "Auto-detected: 200K" alongside the override field.
    Also returns model capabilities (vision, reasoning, tools) when available.
    """
    try:
        cfg = load_config()
        model_cfg = cfg.get("model", "")

        # Extract model name and provider from the config
        if isinstance(model_cfg, dict):
            model_name = model_cfg.get("default", model_cfg.get("name", ""))
            provider = model_cfg.get("provider", "")
            base_url = model_cfg.get("base_url", "")
            config_ctx = model_cfg.get("context_length")
        else:
            model_name = str(model_cfg) if model_cfg else ""
            provider = ""
            base_url = ""
            config_ctx = None

        if not model_name:
            return dict(_EMPTY_MODEL_INFO, provider=provider)

        # Resolve auto-detected context length (pass config_ctx=None to get
        # purely auto-detected value, then separately report the override)
        try:
            from agent.model_metadata import get_model_context_length
            auto_ctx = get_model_context_length(
                model=model_name,
                base_url=base_url,
                provider=provider,
                config_context_length=None,  # ignore override — we want auto value
            )
        except Exception:
            auto_ctx = 0

        config_ctx_int = 0
        if isinstance(config_ctx, int) and config_ctx > 0:
            config_ctx_int = config_ctx

        # Effective is what the agent actually uses
        effective_ctx = config_ctx_int if config_ctx_int > 0 else auto_ctx

        # Try to get model capabilities from models.dev
        caps = {}
        try:
            from agent.models_dev import get_model_capabilities
            mc = get_model_capabilities(provider=provider, model=model_name)
            if mc is not None:
                caps = {
                    "supports_tools": mc.supports_tools,
                    "supports_vision": mc.supports_vision,
                    "supports_reasoning": mc.supports_reasoning,
                    "context_window": mc.context_window,
                    "max_output_tokens": mc.max_output_tokens,
                    "model_family": mc.model_family,
                }
        except Exception:
            pass

        return {
            "model": model_name,
            "provider": provider,
            "auto_context_length": auto_ctx,
            "config_context_length": config_ctx_int,
            "effective_context_length": effective_ctx,
            "capabilities": caps,
        }
    except Exception:
        _log.exception("GET /api/model/info failed")
        return dict(_EMPTY_MODEL_INFO)


# ---------------------------------------------------------------------------
# Model assignment — pick provider+model for main slot or auxiliary slots.
# Mirrors the model.options JSON-RPC from tui_gateway but uses REST so the
# Models page (which has no chat PTY open) can drive it.
# ---------------------------------------------------------------------------

# Canonical auxiliary task slots. Keep in sync with DEFAULT_CONFIG["auxiliary"]
# in qiqiclaw_cli/config.py — listed here for deterministic ordering in the UI.
_AUX_TASK_SLOTS: Tuple[str, ...] = (
    "vision",
    "web_extract",
    "compression",
    "session_search",
    "skills_hub",
    "approval",
    "mcp",
    "title_generation",
    "curator",
)


@app.get("/api/model/options")
def get_model_options():
    """Return model-library entries backed by verified credential-pool keys.

    REST equivalent of the ``model.options`` JSON-RPC on tui_gateway, so the
    dashboard Models page can render the picker without a live chat session.
    The response shape matches ``model.options`` 1:1 so ``ModelPickerDialog``
    can share the same types.
    """
    try:
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            current_model = model_cfg.get("default", model_cfg.get("name", "")) or ""
            current_provider = model_cfg.get("provider", "") or ""
        else:
            current_model = str(model_cfg) if model_cfg else ""
            current_provider = ""
        return _build_verified_model_options_payload(
            current_model=current_model,
            current_provider=current_provider,
        )
    except Exception:
        _log.exception("GET /api/model/options failed")
        raise HTTPException(status_code=500, detail="获取模型选项失败")


@app.get("/api/models/library")
def list_saved_models():
    return {"models": [_annotate_saved_model(entry) for entry in _read_models_library()]}


@app.post("/api/models/library")
async def add_saved_model(body: SavedModelCreate):
    model = (body.model or "").strip()
    provider = (body.provider or "").strip().lower()
    name = (body.name or "").strip() or model
    base_url = (body.base_url or "").strip()
    entry, deduped = _find_or_create_model_library_entry(
        provider=provider,
        model=model,
        base_url=base_url,
        name=name,
    )

    api_key = (body.api_key or "").strip()
    if api_key:
        try:
            save_env_value(_env_key_for_model_library_url(base_url), api_key)
        except Exception:
            _log.exception("saving model-library API key failed")

    return {"ok": True, "model": entry, "deduped": deduped}


@app.put("/api/models/library/{model_id}")
async def update_saved_model(model_id: str, body: SavedModelUpdate):
    model = (body.model or "").strip()
    provider = (body.provider or "").strip().lower()
    name = (body.name or "").strip() or model
    base_url = (body.base_url or "").strip()
    if not model or not provider:
        raise HTTPException(status_code=400, detail="provider and model are required")
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for custom models")

    models = _read_models_library()
    for entry in models:
        if entry.get("id") != model_id:
            continue
        entry.update({
            "name": name,
            "provider": provider,
            "model": model,
            "base_url": base_url,
        })
        _write_models_library(models)
        return {"ok": True, "model": entry}
    raise HTTPException(status_code=404, detail="Model not found")


@app.delete("/api/models/library/{model_id}")
async def remove_saved_model(model_id: str):
    models = _read_models_library()
    kept = [entry for entry in models if entry.get("id") != model_id]
    if len(kept) == len(models):
        raise HTTPException(status_code=404, detail="Model not found")
    _write_models_library(kept)
    return {"ok": True}


@app.post("/api/models/library/{model_id}/validate")
async def validate_saved_model(model_id: str, body: SavedModelValidateRequest):
    model_entry = next((entry for entry in _read_models_library() if entry.get("id") == model_id), None)
    if model_entry is None:
        raise HTTPException(status_code=404, detail="Model not found")

    return _validate_model_entry_with_pool(model_entry, body.credential_index)


@app.post("/api/models/route/validate")
async def validate_model_route(body: ModelRouteValidateRequest):
    provider = (body.provider or "").strip().lower()
    model = (body.model or "").strip()
    base_url = (body.base_url or "").strip()
    name = (body.name or "").strip() if body.name else model
    entry, deduped = _find_or_create_model_library_entry(
        provider=provider,
        model=model,
        base_url=base_url,
        name=name,
    )
    validation = _validate_model_entry_with_pool(entry, body.credential_index)
    cfg = load_config()
    model_cfg = cfg.get("model", {})
    if isinstance(model_cfg, dict):
        current_model = model_cfg.get("default", model_cfg.get("name", "")) or ""
        current_provider = model_cfg.get("provider", "") or ""
    else:
        current_model = str(model_cfg) if model_cfg else ""
        current_provider = ""
    return {
        **validation,
        "deduped": deduped,
        "library_model": _annotate_saved_model(entry),
        "options": _build_verified_model_options_payload(
            current_model=current_model,
            current_provider=current_provider,
        ),
    }


@app.get("/api/langgraph/status")
def get_langgraph_status():
    """Return LangGraph integration status for the desktop workflow view."""
    from qiqiclaw_cli.langgraph_runner import get_langgraph_runtime_status

    runtime = get_langgraph_runtime_status()

    return {
        "available": runtime["available"],
        "version": runtime["version"],
        "error": runtime["error"],
        "source_path": runtime["source_path"],
        "bundled_source_path": runtime["bundled_source_path"],
        "external_source_path": runtime["external_source_path"],
        "module_file": runtime["module_file"],
        "graph_module_file": runtime["graph_module_file"],
        "package_paths": runtime["package_paths"],
        "local_paths": runtime["local_paths"],
        "using_local_source": runtime["using_local_source"],
        "workflow": {
            "nodes": ["qiqiclaw"],
            "edges": [["START", "qiqiclaw"], ["qiqiclaw", "END"]],
            "entrypoint": "qiqiclaw_cli.langgraph_runner.build_qiqiclaw_graph",
            "executor": "qiqiclaw_cli.oneshot._run_agent",
        },
        "modes": ["dry-run", "agent"],
        "capabilities": [
            "stategraph",
            "state",
            "nodes",
            "edges",
            "qiqiclaw-memory",
            "qiqiclaw-tools",
            "mcp",
            "skills",
        ],
    }


@app.post("/api/langgraph/run")
async def run_langgraph_workflow(body: LangGraphRunRequest):
    """Run the QiQiClaw LangGraph workflow through the dashboard REST API."""
    try:
        from qiqiclaw_cli import langgraph_runner

        runner = langgraph_runner.dry_run_runner if body.dry_run else None

        state = await asyncio.to_thread(
            lambda: langgraph_runner.invoke_qiqiclaw_graph(
                body.prompt,
                model=body.model or None,
                provider=body.provider or None,
                toolsets=body.toolsets,
                runner=runner,
                source_path=body.source_path or None,
            )
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _log.exception("POST /api/langgraph/run failed")
        raise HTTPException(status_code=500, detail=str(exc) or "LangGraph workflow failed")

    return {
        "ok": state.get("status") == "ok",
        "dry_run": bool(body.dry_run),
        "state": state,
        "workflow": {
            "nodes": ["qiqiclaw"],
            "edges": [["START", "qiqiclaw"], ["qiqiclaw", "END"]],
        },
    }


def _normalize_orchestrate_models(models: Any) -> list:
    """Normalize the request's models field into ModelSpec-compatible dicts.

    Accepts a list of strings ("model" / "provider:model") or dicts, or a
    comma-separated string.
    """
    if not models:
        return []
    if isinstance(models, str):
        from qiqiclaw_cli.orchestration_graph import _parse_models_arg
        return _parse_models_arg(models)
    out = []
    for m in models:
        if isinstance(m, dict):
            out.append(m)
        elif isinstance(m, str):
            if ":" in m:
                provider, model = m.split(":", 1)
                out.append({"model": model.strip() or None, "provider": provider.strip() or None})
            else:
                out.append({"model": m.strip()})
    return out


def _resolve_model_spec_from_library(raw: Dict[str, Any]) -> Dict[str, Any]:
    spec = dict(raw)
    model = str(spec.get("model") or "").strip()
    provider = str(spec.get("provider") or "").strip().lower()
    base_url = str(spec.get("base_url") or "").strip()
    if not model:
        return spec

    matches = []
    for entry, credential_index, credential in _verified_model_library_entries():
        if str(entry.get("model") or "").strip() != model:
            continue
        if provider and str(entry.get("provider") or "").strip().lower() != provider:
            continue
        if base_url and _normalize_model_base_url(entry.get("base_url")) != _normalize_model_base_url(base_url):
            continue
        matches.append((entry, credential_index, credential))
    if not matches:
        return spec

    entry, credential_index, credential = matches[0]
    resolved_provider = str(entry.get("provider") or provider or "").strip().lower()
    resolved_base_url = _validated_model_base_url(credential, entry)
    api_key = str(
        credential.get("runtime_api_key")
        or credential.get("access_token")
        or credential.get("api_key")
        or ""
    ).strip()
    api_mode = ""
    try:
        from qiqiclaw_cli.providers import determine_api_mode
        api_mode = determine_api_mode(resolved_provider, resolved_base_url)
    except Exception:
        api_mode = ""
    spec.update({
        "model": model,
        "provider": resolved_provider,
        "base_url": resolved_base_url,
        "credential_index": credential_index,
    })
    if api_key:
        spec["api_key"] = api_key
    if api_mode:
        spec["api_mode"] = api_mode
    spec.setdefault("label", f"{resolved_provider}/{model}")
    return spec


def _resolve_orchestrate_models_from_library(models: list) -> list:
    return [
        _resolve_model_spec_from_library(item) if isinstance(item, dict) else item
        for item in models
    ]


def _resolve_orchestrate_assignments_from_library(assignments: Dict[str, str]) -> Dict[str, Any]:
    resolved: Dict[str, Any] = {}
    for role, value in (assignments or {}).items():
        if not isinstance(value, str) or not value.strip():
            continue
        item = value.strip()
        if ":" in item:
            provider, model = item.split(":", 1)
            resolved[role] = _resolve_model_spec_from_library({
                "provider": provider.strip(),
                "model": model.strip(),
            })
        else:
            resolved[role] = _resolve_model_spec_from_library({"model": item})
    return resolved


def _redact_orchestration_state(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if str(key).lower() in {"api_key", "access_token", "runtime_api_key", "refresh_token"}:
                redacted[key] = "<redacted>" if item else ""
            else:
                redacted[key] = _redact_orchestration_state(item)
        return redacted
    if isinstance(value, list):
        return [_redact_orchestration_state(item) for item in value]
    return value


@app.post("/api/orchestrate")
async def run_orchestration(body: OrchestrateRunRequest):
    """Run the multi-node QiQiClaw orchestration graph through the REST API.

    QiQiClaw is the decision-maker; LangGraph orchestrates decide/execute/
    aggregate with optional multi-model ensemble. Off the main event loop via
    asyncio.to_thread so a long agent run never blocks the server.
    """
    try:
        from qiqiclaw_cli import orchestration_graph as og

        models = _resolve_orchestrate_models_from_library(_normalize_orchestrate_models(body.models))
        mode = body.mode or ("ensemble" if models else "single")
        toolsets = (
            body.toolsets.split(",") if isinstance(body.toolsets, str) and body.toolsets
            else (body.toolsets if isinstance(body.toolsets, list) else None)
        )

        execute_fn = og.dry_run_execute_fn if body.dry_run else None
        ensemble_fn = None
        if body.dry_run and mode == "ensemble":
            def ensemble_fn(state):  # noqa: ANN001
                specs = state.get("models") or []
                cands = [
                    {"model": (m.get("model") if isinstance(m, dict) else m),
                     "summary": f"[dry] {state['task']}", "status": "completed"}
                    for m in specs
                ]
                return {
                    "response": f"[orchestration dry-run ensemble of {len(specs)}]: {state['task']}",
                    "candidates": cands,
                }

        state = await asyncio.to_thread(
            lambda: og.invoke_orchestration(
                body.task,
                mode=mode,
                models=models,
                model_assignments=_resolve_orchestrate_assignments_from_library(body.model_assignments or {}),
                provider=body.provider or None,
                toolsets=toolsets,
                max_steps=body.max_steps,
                execute_fn=execute_fn,
                ensemble_fn=ensemble_fn,
            )
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        _log.exception("POST /api/orchestrate failed")
        raise HTTPException(status_code=500, detail=str(exc) or "Orchestration failed")

    return {
        "ok": state.get("status") == "done",
        "dry_run": bool(body.dry_run),
        "mode": mode,
        "state": _redact_orchestration_state(state),
        "workflow": {
            "nodes": ["decide", "execute", "aggregate"],
            "edges": [
                ["START", "decide"], ["decide", "execute"], ["decide", "END"],
                ["execute", "aggregate"], ["execute", "END"],
                ["aggregate", "decide"], ["aggregate", "END"],
            ],
        },
    }


def _pool_entry_summary(entry: Any, index: int) -> Dict[str, Any]:
    if isinstance(entry, dict):
        token = str(entry.get("access_token") or "")
        return {
            "index": index,
            "id": entry.get("id"),
            "label": entry.get("label"),
            "auth_type": entry.get("auth_type"),
            "source": entry.get("source"),
            "priority": entry.get("priority", 0),
            "last_status": entry.get("last_status"),
            "request_count": entry.get("request_count", 0),
            "token_preview": redact_key(token) if token else "",
            "base_url": entry.get("base_url") or "",
            "has_refresh": bool(entry.get("refresh_token")),
        }
    token = getattr(entry, "access_token", "") or ""
    return {
        "index": index,
        "id": getattr(entry, "id", None),
        "label": getattr(entry, "label", None),
        "auth_type": getattr(entry, "auth_type", None),
        "source": getattr(entry, "source", None),
        "priority": getattr(entry, "priority", 0),
        "last_status": getattr(entry, "last_status", None),
        "request_count": getattr(entry, "request_count", 0),
        "token_preview": redact_key(token) if token else "",
        "base_url": getattr(entry, "base_url", None) or "",
        "has_refresh": bool(getattr(entry, "refresh_token", None)),
    }


@app.get("/api/credentials/pool")
async def list_credential_pool():
    from qiqiclaw_cli.auth import read_credential_pool

    providers = []
    raw_pool = read_credential_pool()
    for provider_id in sorted(raw_pool.keys()):
        entries = raw_pool.get(provider_id)
        if not isinstance(entries, list) or not entries:
            continue
        providers.append({
            "provider": provider_id,
            "entries": [
                _pool_entry_summary(e, i) for i, e in enumerate(entries, start=1)
            ],
        })
    return {"providers": providers}


@app.post("/api/credentials/pool")
async def add_credential_pool_entry(body: CredentialPoolAdd):
    from qiqiclaw_cli.auth import read_credential_pool, write_credential_pool

    provider = (body.provider or "").strip().lower()
    api_key = (body.api_key or "").strip()
    base_url = (body.base_url or "").strip()
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="base_url is required for custom provider credentials")
    if not provider or (provider != "custom" and not api_key):
        raise HTTPException(status_code=400, detail="provider and api_key are required")

    entries = read_credential_pool(provider)
    if not isinstance(entries, list):
        entries = []
    label = (body.label or "").strip() or f"key #{len(entries) + 1}"
    entry = {
        "id": uuid.uuid4().hex[:6],
        "label": label,
        "auth_type": "api_key",
        "priority": len(entries),
        "source": "manual",
        "access_token": api_key,
        "base_url": base_url or None,
        "request_count": 0,
    }
    entries.append(entry)
    try:
        write_credential_pool(provider, entries)
    except Exception as exc:
        _log.exception("POST /api/credentials/pool failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "provider": provider, "count": len(entries)}


@app.delete("/api/credentials/pool/{provider}/{index}")
async def remove_credential_pool_entry(provider: str, index: int):
    from qiqiclaw_cli.auth import read_credential_pool, write_credential_pool

    provider = (provider or "").strip().lower()
    entries = read_credential_pool(provider)
    if not isinstance(entries, list) or index < 1 or index > len(entries):
        raise HTTPException(status_code=404, detail="No pool entry at that index")
    entries.pop(index - 1)
    for priority, entry in enumerate(entries):
        if isinstance(entry, dict):
            entry["priority"] = priority
    try:
        write_credential_pool(provider, entries)
    except Exception as exc:
        _log.exception("DELETE /api/credentials/pool failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "provider": provider, "count": len(entries)}


@app.post("/api/audio/transcribe")
async def transcribe_audio_upload(payload: AudioTranscriptionRequest):
    """Transcribe browser-recorded microphone audio for the desktop composer."""
    data_url = (payload.data_url or "").strip()
    if not data_url.startswith("data:") or "," not in data_url:
        raise HTTPException(status_code=400, detail="无效的音频数据")

    header, encoded = data_url.split(",", 1)
    if ";base64" not in header:
        raise HTTPException(status_code=400, detail="音频数据必须使用 base64 编码")

    mime_type = (
        payload.mime_type or header[5:].split(";", 1)[0] or "audio/webm"
    ).strip()
    normalized_mime_type = mime_type.split(";", 1)[0].lower()
    if not (
        normalized_mime_type.startswith("audio/")
        or normalized_mime_type == "video/webm"
    ):
        raise HTTPException(status_code=400, detail="上传内容必须是音频录音")

    try:
        audio_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="音频 base64 数据无效")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频录音为空")
    if len(audio_bytes) > _MAX_TRANSCRIPTION_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="音频录音过大")

    temp_path = ""
    try:
        suffix = _audio_extension_for_mime(mime_type)
        with tempfile.NamedTemporaryFile(
            prefix="qiqiclaw-desktop-voice-",
            suffix=suffix,
            delete=False,
        ) as tmp:
            tmp.write(audio_bytes)
            temp_path = tmp.name

        from tools.transcription_tools import transcribe_audio

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, transcribe_audio, temp_path)
    except HTTPException:
        raise
    except Exception as exc:
        _log.exception("Desktop voice transcription failed")
        raise HTTPException(status_code=500, detail=f"语音识别失败: {exc}")
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    if not result.get("success"):
        raise HTTPException(
            status_code=400,
            detail=result.get("error") or "语音识别失败",
        )

    return {
        "ok": True,
        "transcript": str(result.get("transcript") or "").strip(),
        "provider": result.get("provider"),
    }


@app.get("/api/model/auxiliary")
def get_auxiliary_models():
    """Return current auxiliary task assignments.

    Shape:
      {
        "tasks": [
          {"task": "vision", "provider": "auto", "model": "", "base_url": ""},
          ...
        ],
        "main": {"provider": "openrouter", "model": "anthropic/claude-opus-4.7"},
      }
    """
    try:
        cfg = load_config()
        aux_cfg = cfg.get("auxiliary", {})
        if not isinstance(aux_cfg, dict):
            aux_cfg = {}

        tasks = []
        for slot in _AUX_TASK_SLOTS:
            slot_cfg = aux_cfg.get(slot, {}) if isinstance(aux_cfg.get(slot), dict) else {}
            tasks.append({
                "task": slot,
                "provider": str(slot_cfg.get("provider", "auto") or "auto"),
                "model": str(slot_cfg.get("model", "") or ""),
                "base_url": str(slot_cfg.get("base_url", "") or ""),
            })

        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            main = {
                "provider": str(model_cfg.get("provider", "") or ""),
                "model": str(model_cfg.get("default", model_cfg.get("name", "")) or ""),
            }
        else:
            main = {"provider": "", "model": str(model_cfg) if model_cfg else ""}

        return {"tasks": tasks, "main": main}
    except Exception:
        _log.exception("GET /api/model/auxiliary failed")
        raise HTTPException(status_code=500, detail="读取辅助配置失败")


@app.post("/api/model/set")
async def set_model_assignment(body: ModelAssignment):
    """Assign a model to the main slot or an auxiliary task slot.

    Writes to ``~/.qiqiclaw/config.yaml`` — applies to **new** sessions only.
    The currently running chat PTY (if any) is not affected; use the
    ``/model`` slash command inside a chat to hot-swap that specific session.
    """
    scope = (body.scope or "").strip().lower()
    provider = (body.provider or "").strip()
    model = (body.model or "").strip()
    task = (body.task or "").strip().lower()
    base_url = (body.base_url or "").strip()

    if scope not in ("main", "auxiliary"):
        raise HTTPException(status_code=400, detail="scope 必须是 'main' 或 'auxiliary'")

    try:
        cfg = load_config()

        if scope == "main":
            if not provider or not model:
                raise HTTPException(status_code=400, detail="main 需要 provider 和 model")
            model_cfg = _apply_main_model_assignment(
                cfg.get("model", {}), provider, model, base_url
            )
            cfg["model"] = model_cfg
            save_config(cfg)
            return {
                "ok": True,
                "scope": "main",
                "provider": provider,
                "model": model,
                "base_url": model_cfg.get("base_url", ""),
            }

        # scope == "auxiliary"
        aux = cfg.get("auxiliary")
        if not isinstance(aux, dict):
            aux = {}

        if task == "__reset__":
            # Reset every slot to provider="auto", model="" — keeps other fields intact.
            for slot in _AUX_TASK_SLOTS:
                slot_cfg = aux.get(slot)
                if not isinstance(slot_cfg, dict):
                    slot_cfg = {}
                slot_cfg["provider"] = "auto"
                slot_cfg["model"] = ""
                aux[slot] = slot_cfg
            cfg["auxiliary"] = aux
            save_config(cfg)
            return {"ok": True, "scope": "auxiliary", "reset": True}

        if not provider:
            raise HTTPException(status_code=400, detail="auxiliary 需要 provider")

        targets = [task] if task else list(_AUX_TASK_SLOTS)
        for slot in targets:
            if slot not in _AUX_TASK_SLOTS:
                raise HTTPException(status_code=400, detail=f"未知的辅助任务: {slot}")
            slot_cfg = aux.get(slot)
            if not isinstance(slot_cfg, dict):
                slot_cfg = {}
            slot_cfg["provider"] = provider
            slot_cfg["model"] = model
            aux[slot] = slot_cfg

        cfg["auxiliary"] = aux
        save_config(cfg)
        return {
            "ok": True,
            "scope": "auxiliary",
            "tasks": targets,
            "provider": provider,
            "model": model,
        }
    except HTTPException:
        raise
    except Exception:
        _log.exception("POST /api/model/set failed")
        raise HTTPException(status_code=500, detail="保存模型分配失败")




def _denormalize_config_from_web(config: Dict[str, Any]) -> Dict[str, Any]:
    """Reverse _normalize_config_for_web before saving.

    Reconstructs ``model`` as a dict by reading the current on-disk config
    to recover model subkeys (provider, base_url, api_mode, etc.) that were
    stripped from the GET response.  The frontend only sees model as a flat
    string; the rest is preserved transparently.

    Also handles ``model_context_length`` — writes it back into the model dict
    as ``context_length``.  A value of 0 or absent means "auto-detect" (omitted
    from the dict so get_model_context_length() uses its normal resolution).
    """
    config = dict(config)
    # Remove any _model_meta that might have leaked in (shouldn't happen
    # with the stripped GET response, but be defensive)
    config.pop("_model_meta", None)

    # Extract and remove model_context_length before processing model
    ctx_override = config.pop("model_context_length", 0)
    if not isinstance(ctx_override, int):
        try:
            ctx_override = int(ctx_override)
        except (TypeError, ValueError):
            ctx_override = 0

    model_val = config.get("model")
    if isinstance(model_val, str) and model_val:
        # Read the current disk config to recover model subkeys
        try:
            disk_config = load_config()
            disk_model = disk_config.get("model")
            if isinstance(disk_model, dict):
                # Preserve all subkeys, update default with the new value
                disk_model["default"] = model_val
                # Write context_length into the model dict (0 = remove/auto)
                if ctx_override > 0:
                    disk_model["context_length"] = ctx_override
                else:
                    disk_model.pop("context_length", None)
                config["model"] = disk_model
            else:
                # Model was previously a bare string — upgrade to dict if
                # user is setting a context_length override
                if ctx_override > 0:
                    config["model"] = {
                        "default": model_val,
                        "context_length": ctx_override,
                    }
        except Exception:
            pass  # can't read disk config — just use the string form
    return config


@app.put("/api/config")
async def update_config(body: ConfigUpdate):
    try:
        save_config(_denormalize_config_from_web(body.config))
        return {"ok": True}
    except Exception:
        _log.exception("PUT /api/config failed")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.get("/api/env")
async def get_env_vars():
    env_on_disk = load_env()
    result = {}
    for var_name, info in OPTIONAL_ENV_VARS.items():
        value = env_on_disk.get(var_name)
        result[var_name] = {
            "is_set": bool(value),
            "redacted_value": redact_key(value) if value else None,
            "description": info.get("description", ""),
            "url": info.get("url"),
            "category": info.get("category", ""),
            "is_password": info.get("password", False),
            "tools": info.get("tools", []),
            "advanced": info.get("advanced", False),
        }
    return result


@app.put("/api/env")
async def set_env_var(body: EnvVarUpdate):
    try:
        save_env_value(body.key, body.value)
        return {"ok": True, "key": body.key}
    except Exception:
        _log.exception("PUT /api/env failed")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.delete("/api/env")
async def remove_env_var(body: EnvVarDelete):
    try:
        removed = remove_env_value(body.key)
        if not removed:
            raise HTTPException(status_code=404, detail=f"{body.key} 在 .env 中未找到")
        return {"ok": True, "key": body.key}
    except HTTPException:
        raise
    except Exception:
        _log.exception("DELETE /api/env failed")
        raise HTTPException(status_code=500, detail="内部服务器错误")


@app.post("/api/env/reveal")
async def reveal_env_var(body: EnvVarReveal, request: Request):
    """Return the real (unredacted) value of a single env var.

    Protected by:
    - Ephemeral session token (generated per server start, injected into SPA)
    - Rate limiting (max 5 reveals per 30s window)
    - Audit logging
    """
    # --- Token check ---
    _require_token(request)

    # --- Rate limit ---
    now = time.time()
    cutoff = now - _REVEAL_WINDOW_SECONDS
    _reveal_timestamps[:] = [t for t in _reveal_timestamps if t > cutoff]
    if len(_reveal_timestamps) >= _REVEAL_MAX_PER_WINDOW:
        raise HTTPException(status_code=429, detail="显示请求过多。请稍后再试。")
    _reveal_timestamps.append(now)

    # --- Reveal ---
    env_on_disk = load_env()
    value = env_on_disk.get(body.key)
    if value is None:
        raise HTTPException(status_code=404, detail=f"{body.key} 在 .env 中未找到")

    _log.info("env/reveal: %s", body.key)
    return {"key": body.key, "value": value}


def _messaging_setup_payload() -> dict[str, Any]:
    from qiqiclaw_cli.gateway import gateway_setup_status

    status = gateway_setup_status()
    env_on_disk = load_env()
    runtime = read_runtime_status() or {}
    runtime_platforms = runtime.get("platforms") or {}
    gateway_running = bool(status.get("running"))
    if not gateway_running:
        health_alive, health_body, _health_url = _probe_gateway_health()
        if health_alive:
            gateway_running = True
            if health_body and not runtime:
                runtime = health_body
                runtime_platforms = runtime.get("platforms") or {}
    platforms = []

    for platform in status.get("platforms") or []:
        platform_id = str(platform.get("key") or "").strip()
        if not platform_id:
            continue

        env_vars = []
        for var in platform.get("vars") or []:
            key = str(var.get("name") or "").strip()
            if not key:
                continue
            value = env_on_disk.get(key, "")
            env_vars.append(
                {
                    "advanced": False,
                    "description": var.get("help", ""),
                    "is_allowlist": bool(var.get("is_allowlist", False)),
                    "is_password": bool(var.get("password", False)),
                    "is_set": bool(value),
                    "key": key,
                    "prompt": var.get("prompt") or key,
                    "redacted_value": redact_key(value) if value else None,
                    "required": key == platform.get("token_var"),
                    "url": None,
                }
            )

        setup_status = str(platform.get("status") or "not configured")
        configured = setup_status.lower().startswith("configured")
        runtime_payload = runtime_platforms.get(platform_id) or {}
        runtime_state = runtime_payload.get("state")
        state = runtime_state or ("configured" if configured else "not_configured")
        if not gateway_running and configured:
            state = "gateway_stopped"

        platforms.append(
            {
                "configured": configured,
                "description": "\n".join(platform.get("setup_instructions") or []),
                "docs_url": "",
                "enabled": configured,
                "env_vars": env_vars,
                "error_code": runtime_payload.get("error_code"),
                "error_message": runtime_payload.get("error_message"),
                "gateway_running": gateway_running,
                "has_interactive_setup": bool(platform.get("has_interactive_setup")),
                "home_channel": None,
                "id": platform_id,
                "install_hint": platform.get("install_hint", ""),
                "name": platform.get("label") or platform_id,
                "setup_instructions": list(platform.get("setup_instructions") or []),
                "setup_status": setup_status,
                "source": platform.get("source", "builtin"),
                "state": state,
                "updated_at": runtime_payload.get("updated_at"),
            }
        )

    return {
        "api_server": status.get("api_server") or {},
        "gateway_pids": status.get("gateway_pids") or [],
        "home": status.get("home"),
        "platforms": platforms,
        "runtime_health": status.get("runtime_health") or [],
        "service_installed": bool(status.get("service_installed")),
        "service_running": bool(status.get("service_running")),
        "supports_launchd": bool(status.get("supports_launchd")),
        "supports_systemd": bool(status.get("supports_systemd")),
    }


def _messaging_platform_by_id(platform_id: str) -> dict[str, Any] | None:
    for platform in _messaging_setup_payload()["platforms"]:
        if platform["id"] == platform_id:
            return platform
    return None


@app.get("/api/messaging/platforms")
async def get_messaging_platforms():
    return _messaging_setup_payload()


@app.put("/api/messaging/platforms/{platform_id}")
async def update_messaging_platform(platform_id: str, body: MessagingPlatformUpdate):
    platform = _messaging_platform_by_id(platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail=f"未知消息平台: {platform_id}")

    allowed_env = {field["key"] for field in platform.get("env_vars") or []}
    if body.enabled is not None:
        if body.enabled:
            if not platform.get("configured"):
                raise HTTPException(status_code=400, detail="请先填写该平台的配置项")
        else:
            for field in platform.get("env_vars") or []:
                remove_env_value(field["key"])

    for key in body.clear_env:
        if key not in allowed_env:
            raise HTTPException(status_code=400, detail=f"{key} 不属于 {platform['name']} 配置项")
        remove_env_value(key)

    for key, value in body.env.items():
        if key not in allowed_env:
            raise HTTPException(status_code=400, detail=f"{key} 不属于 {platform['name']} 配置项")
        trimmed = value.strip()
        if trimmed:
            save_env_value(key, trimmed)

    return {"ok": True, "platform": platform_id}


@app.post("/api/messaging/platforms/{platform_id}/test")
async def test_messaging_platform(platform_id: str):
    platform = _messaging_platform_by_id(platform_id)
    if not platform:
        raise HTTPException(status_code=404, detail=f"未知消息平台: {platform_id}")
    if not platform.get("configured"):
        missing = [
            field["key"]
            for field in platform.get("env_vars") or []
            if field.get("required") and not field.get("is_set")
        ]
        message = "缺少配置项: " + ", ".join(missing) if missing else "平台尚未完成配置"
        return {"ok": False, "state": platform.get("state"), "message": message}
    if not platform.get("gateway_running"):
        return {
            "ok": False,
            "state": platform.get("state"),
            "message": "配置已保存，但 Gateway 未运行。请启动或重启 Gateway。",
        }
    return {"ok": True, "state": platform.get("state"), "message": "配置已保存，Gateway 正在运行。"}


# ---------------------------------------------------------------------------
# OAuth provider endpoints — status + disconnect (Phase 1)
# ---------------------------------------------------------------------------
#
# Phase 1 surfaces *which OAuth providers exist* and whether each is
# connected, plus a disconnect button. The actual login flow (PKCE for
# Anthropic, device-code for Nous/Codex) still runs in the CLI for now;
# Phase 2 will add in-browser flows. For unconnected providers we return
# the canonical ``qiqiclaw auth add <provider>`` command so the dashboard
# can surface a one-click copy.


def _truncate_token(value: Optional[str], visible: int = 6) -> str:
    """Return ``...XXXXXX`` (last N chars) for safe display in the UI.

    We never expose more than the trailing ``visible`` characters of an
    OAuth access token. JWT prefixes (the part before the first dot) are
    stripped first when present so the visible suffix is always part of
    the signing region rather than a meaningless header chunk.
    """
    if not value:
        return ""
    s = str(value)
    if "." in s and s.count(".") >= 2:
        # Looks like a JWT — show the trailing piece of the signature only.
        s = s.rsplit(".", 1)[-1]
    if len(s) <= visible:
        return s
    return f"…{s[-visible:]}"


def _anthropic_oauth_status() -> Dict[str, Any]:
    """Combined status across the three Anthropic credential sources we read.

    QiQiClaw resolves Anthropic creds in this order at runtime:
    1. ``~/.qiqiclaw/.anthropic_oauth.json`` — QiQiClaw-managed PKCE flow
    2. ``~/.claude/.credentials.json`` — Claude Code CLI credentials (auto)
    3. ``ANTHROPIC_TOKEN`` / ``ANTHROPIC_API_KEY`` env vars
    The dashboard reports the highest-priority source that's actually present.
    """
    try:
        from agent.anthropic_adapter import (
            read_hermes_oauth_credentials,
            read_claude_code_credentials,
            _HERMES_OAUTH_FILE,
        )
    except ImportError:
        read_claude_code_credentials = None  # type: ignore
        read_hermes_oauth_credentials = None  # type: ignore
        _HERMES_OAUTH_FILE = None  # type: ignore

    hermes_creds = None
    if read_hermes_oauth_credentials:
        try:
            hermes_creds = read_hermes_oauth_credentials()
        except Exception:
            hermes_creds = None
    if hermes_creds and hermes_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "hermes_pkce",
            "source_label": f"QiQi PKCE ({_HERMES_OAUTH_FILE})",
            "token_preview": _truncate_token(hermes_creds.get("accessToken")),
            "expires_at": hermes_creds.get("expiresAt"),
            "has_refresh_token": bool(hermes_creds.get("refreshToken")),
        }

    cc_creds = None
    if read_claude_code_credentials:
        try:
            cc_creds = read_claude_code_credentials()
        except Exception:
            cc_creds = None
    if cc_creds and cc_creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code",
            "source_label": "Claude Code (~/.claude/.credentials.json)",
            "token_preview": _truncate_token(cc_creds.get("accessToken")),
            "expires_at": cc_creds.get("expiresAt"),
            "has_refresh_token": bool(cc_creds.get("refreshToken")),
        }

    env_token = os.getenv("ANTHROPIC_TOKEN") or os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if env_token:
        return {
            "logged_in": True,
            "source": "env_var",
            "source_label": "ANTHROPIC_TOKEN environment variable",
            "token_preview": _truncate_token(env_token),
            "expires_at": None,
            "has_refresh_token": False,
        }
    return {"logged_in": False, "source": None}


def _claude_code_only_status() -> Dict[str, Any]:
    """Surface Claude Code CLI credentials as their own provider entry.

    Independent of the Anthropic entry above so users can see whether their
    Claude Code subscription tokens are actively flowing into QiQiClaw even
    when they also have a separate QiQiClaw-managed PKCE login.
    """
    try:
        from agent.anthropic_adapter import read_claude_code_credentials
        creds = read_claude_code_credentials()
    except Exception:
        creds = None
    if creds and creds.get("accessToken"):
        return {
            "logged_in": True,
            "source": "claude_code_cli",
            "source_label": "~/.claude/.credentials.json",
            "token_preview": _truncate_token(creds.get("accessToken")),
            "expires_at": creds.get("expiresAt"),
            "has_refresh_token": bool(creds.get("refreshToken")),
        }
    return {"logged_in": False, "source": None}


# Provider catalog. The order matters — it's how we render the UI list.
# ``cli_command`` is what the dashboard surfaces as the copy-to-clipboard
# fallback while Phase 2 (in-browser flows) isn't built yet.
# ``flow`` describes the OAuth shape so the future modal can pick the
# right UI: ``pkce`` = open URL + paste callback code, ``device_code`` =
# show code + verification URL + poll, ``external`` = read-only (delegated
# to a third-party CLI like Claude Code or Qwen).
_OAUTH_PROVIDER_CATALOG: tuple[Dict[str, Any], ...] = (
    {
        "id": "anthropic",
        "name": "Anthropic (Claude API)",
        "flow": "pkce",
        "cli_command": "qiqiclaw auth add anthropic",
        "docs_url": "https://docs.claude.com/en/api/getting-started",
        "status_fn": _anthropic_oauth_status,
    },
    {
        "id": "claude-code",
        "name": "Claude Code (subscription)",
        "flow": "external",
        "cli_command": "claude setup-token",
        "docs_url": "https://docs.claude.com/en/docs/claude-code",
        "status_fn": _claude_code_only_status,
    },
    {
        "id": "nous",
        "name": "Nous Portal",
        "flow": "device_code",
        "cli_command": "qiqiclaw auth add nous",
        "docs_url": "https://portal.nousresearch.com",
        "status_fn": None,  # dispatched via auth.get_nous_auth_status
    },
    {
        "id": "openai-codex",
        "name": "OpenAI Codex (ChatGPT)",
        "flow": "device_code",
        "cli_command": "qiqiclaw auth add openai-codex",
        "docs_url": "https://platform.openai.com/docs",
        "status_fn": None,  # dispatched via auth.get_codex_auth_status
    },
    {
        "id": "qwen-oauth",
        "name": "Qwen (via Qwen CLI)",
        "flow": "external",
        "cli_command": "qiqiclaw auth add qwen-oauth",
        "docs_url": "https://github.com/QwenLM/qwen-code",
        "status_fn": None,  # dispatched via auth.get_qwen_auth_status
    },
    {
        "id": "minimax-oauth",
        "name": "MiniMax (OAuth)",
        "flow": "pkce",
        "cli_command": "qiqiclaw auth add minimax-oauth",
        "docs_url": "https://www.minimax.io",
        "status_fn": None,  # dispatched via auth.get_minimax_oauth_auth_status
    },
)


def _resolve_provider_status(provider_id: str, status_fn) -> Dict[str, Any]:
    """Dispatch to the right status helper for an OAuth provider entry."""
    if status_fn is not None:
        try:
            return status_fn()
        except Exception as e:
            return {"logged_in": False, "error": str(e)}
    try:
        from qiqiclaw_cli import auth as hauth
        if provider_id == "nous":
            raw = hauth.get_nous_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "nous_portal",
                "source_label": raw.get("portal_base_url") or "Nous Portal",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("access_expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "openai-codex":
            raw = hauth.get_codex_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": raw.get("source") or "openai_codex",
                "source_label": raw.get("auth_mode") or "OpenAI Codex",
                "token_preview": _truncate_token(raw.get("api_key")),
                "expires_at": None,
                "has_refresh_token": False,
                "last_refresh": raw.get("last_refresh"),
            }
        if provider_id == "qwen-oauth":
            raw = hauth.get_qwen_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "qwen_cli",
                "source_label": raw.get("auth_store_path") or "Qwen CLI",
                "token_preview": _truncate_token(raw.get("access_token")),
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": bool(raw.get("has_refresh_token")),
            }
        if provider_id == "minimax-oauth":
            raw = hauth.get_minimax_oauth_auth_status()
            return {
                "logged_in": bool(raw.get("logged_in")),
                "source": "minimax_oauth",
                "source_label": f"MiniMax ({raw.get('region', 'global')})",
                "token_preview": None,
                "expires_at": raw.get("expires_at"),
                "has_refresh_token": True,
            }
    except Exception as e:
        return {"logged_in": False, "error": str(e)}
    return {"logged_in": False}


@app.get("/api/providers/oauth")
async def list_oauth_providers():
    """Enumerate every OAuth-capable LLM provider with current status.

    Response shape (per provider):
        id              stable identifier (used in DELETE path)
        name            human label
        flow            "pkce" | "device_code" | "external"
        cli_command     fallback CLI command for users to run manually
        docs_url        external docs/portal link for the "Learn more" link
        status:
          logged_in        bool — currently has usable creds
          source           short slug ("hermes_pkce", "claude_code", ...)
          source_label     human-readable origin (file path, env var name)
          token_preview    last N chars of the token, never the full token
          expires_at       ISO timestamp string or null
          has_refresh_token bool
    """
    providers = []
    for p in _OAUTH_PROVIDER_CATALOG:
        status = _resolve_provider_status(p["id"], p.get("status_fn"))
        providers.append({
            "id": p["id"],
            "name": p["name"],
            "flow": p["flow"],
            "cli_command": p["cli_command"],
            "docs_url": p["docs_url"],
            "status": status,
        })
    return {"providers": providers}


@app.delete("/api/providers/oauth/{provider_id}")
async def disconnect_oauth_provider(provider_id: str, request: Request):
    """Disconnect an OAuth provider. Token-protected (matches /env/reveal)."""
    _require_token(request)

    valid_ids = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"未知提供商: {provider_id}。"
                   f"可用: {', '.join(sorted(valid_ids))}",
        )

    # Anthropic and claude-code clear the same QiQiClaw-managed PKCE file
    # AND forget the Claude Code import. We don't touch ~/.claude/* directly
    # — that's owned by the Claude Code CLI; users can re-auth there if they
    # want to undo a disconnect.
    if provider_id in ("anthropic", "claude-code"):
        try:
            from agent.anthropic_adapter import _HERMES_OAUTH_FILE
            if _HERMES_OAUTH_FILE.exists():
                _HERMES_OAUTH_FILE.unlink()
        except Exception:
            pass
        # Also clear the credential pool entry if present.
        try:
            from qiqiclaw_cli.auth import clear_provider_auth
            clear_provider_auth("anthropic")
        except Exception:
            pass
        _log.info("oauth/disconnect: %s", provider_id)
        return {"ok": True, "provider": provider_id}

    try:
        from qiqiclaw_cli.auth import clear_provider_auth
        cleared = clear_provider_auth(provider_id)
        _log.info("oauth/disconnect: %s (cleared=%s)", provider_id, cleared)
        return {"ok": bool(cleared), "provider": provider_id}
    except Exception as e:
        _log.exception("disconnect %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# OAuth Phase 2 — in-browser PKCE & device-code flows
# ---------------------------------------------------------------------------
#
# Two flow shapes are supported:
#
#   PKCE (Anthropic):
#     1. POST /api/providers/oauth/anthropic/start
#          → server generates code_verifier + challenge, builds claude.ai
#            authorize URL, stashes verifier in _oauth_sessions[session_id]
#          → returns { session_id, flow: "pkce", auth_url }
#     2. UI opens auth_url in a new tab. User authorizes, copies code.
#     3. POST /api/providers/oauth/anthropic/submit { session_id, code }
#          → server exchanges (code + verifier) → tokens at console.anthropic.com
#          → persists to ~/.qiqiclaw/.anthropic_oauth.json AND credential pool
#          → returns { ok: true, status: "approved" }
#
#   Device code (Nous, OpenAI Codex):
#     1. POST /api/providers/oauth/{nous|openai-codex}/start
#          → server hits provider's device-auth endpoint
#          → gets { user_code, verification_url, device_code, interval, expires_in }
#          → spawns background poller thread that polls the token endpoint
#            every `interval` seconds until approved/expired
#          → stores poll status in _oauth_sessions[session_id]
#          → returns { session_id, flow: "device_code", user_code,
#                      verification_url, expires_in, poll_interval }
#     2. UI opens verification_url in a new tab and shows user_code.
#     3. UI polls GET /api/providers/oauth/{provider}/poll/{session_id}
#          every 2s until status != "pending".
#     4. On "approved" the background thread has already saved creds; UI
#        refreshes the providers list.
#
# Sessions are kept in-memory only (single-process FastAPI) and time out
# after 15 minutes. A periodic cleanup runs on each /start call to GC
# expired sessions so the dict doesn't grow without bound.

_OAUTH_SESSION_TTL_SECONDS = 15 * 60
_oauth_sessions: Dict[str, Dict[str, Any]] = {}
_oauth_sessions_lock = threading.Lock()

# Import OAuth constants from canonical source instead of duplicating.
# Guarded so qiqiclaw web still starts if anthropic_adapter is unavailable;
# Phase 2 endpoints will return 501 in that case.
try:
    from agent.anthropic_adapter import (
        _OAUTH_CLIENT_ID as _ANTHROPIC_OAUTH_CLIENT_ID,
        _OAUTH_TOKEN_URL as _ANTHROPIC_OAUTH_TOKEN_URL,
        _OAUTH_REDIRECT_URI as _ANTHROPIC_OAUTH_REDIRECT_URI,
        _OAUTH_SCOPES as _ANTHROPIC_OAUTH_SCOPES,
        _generate_pkce as _generate_pkce_pair,
    )
    _ANTHROPIC_OAUTH_AVAILABLE = True
except ImportError:
    _ANTHROPIC_OAUTH_AVAILABLE = False
_ANTHROPIC_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"


def _gc_oauth_sessions() -> None:
    """Drop expired sessions. Called opportunistically on /start."""
    cutoff = time.time() - _OAUTH_SESSION_TTL_SECONDS
    with _oauth_sessions_lock:
        stale = [sid for sid, sess in _oauth_sessions.items() if sess["created_at"] < cutoff]
        for sid in stale:
            _oauth_sessions.pop(sid, None)


def _new_oauth_session(provider_id: str, flow: str) -> tuple[str, Dict[str, Any]]:
    """Create + register a new OAuth session, return (session_id, session_dict)."""
    sid = secrets.token_urlsafe(16)
    sess = {
        "session_id": sid,
        "provider": provider_id,
        "flow": flow,
        "created_at": time.time(),
        "status": "pending",  # pending | approved | denied | expired | error
        "error_message": None,
    }
    with _oauth_sessions_lock:
        _oauth_sessions[sid] = sess
    return sid, sess


def _save_anthropic_oauth_creds(access_token: str, refresh_token: str, expires_at_ms: int) -> None:
    """Persist Anthropic PKCE creds to both QiQiClaw file AND credential pool.

    Mirrors what auth_commands.add_command does so the dashboard flow leaves
    the system in the same state as ``qiqiclaw auth add anthropic``.
    """
    from agent.anthropic_adapter import _HERMES_OAUTH_FILE
    payload = {
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": expires_at_ms,
    }
    _HERMES_OAUTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    _HERMES_OAUTH_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Best-effort credential-pool insert. Failure here doesn't invalidate
    # the file write — pool registration only matters for the rotation
    # strategy, not for runtime credential resolution.
    try:
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid
        pool = load_pool("anthropic")
        # Avoid duplicate entries: delete any prior dashboard-issued OAuth entry
        existing = [e for e in pool.entries() if getattr(e, "source", "").startswith(f"{SOURCE_MANUAL}:dashboard_pkce")]
        for e in existing:
            try:
                pool.remove_entry(getattr(e, "id", ""))
            except Exception:
                pass
        entry = PooledCredential(
            provider="anthropic",
            id=uuid.uuid4().hex[:6],
            label="dashboard PKCE",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_pkce",
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at_ms=expires_at_ms,
        )
        pool.add_entry(entry)
    except Exception as e:
        _log.warning("anthropic pool add (dashboard) failed: %s", e)


def _start_anthropic_pkce() -> Dict[str, Any]:
    """Begin PKCE flow. Returns the auth URL the UI should open."""
    if not _ANTHROPIC_OAUTH_AVAILABLE:
        raise HTTPException(status_code=501, detail="Anthropic OAuth 不可用（缺少适配器）")
    verifier, challenge = _generate_pkce_pair()
    sid, sess = _new_oauth_session("anthropic", "pkce")
    sess["verifier"] = verifier
    sess["state"] = verifier  # Anthropic round-trips verifier as state
    params = {
        "code": "true",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "scope": _ANTHROPIC_OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": verifier,
    }
    auth_url = f"{_ANTHROPIC_OAUTH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {
        "session_id": sid,
        "flow": "pkce",
        "auth_url": auth_url,
        "expires_in": _OAUTH_SESSION_TTL_SECONDS,
    }


def _submit_anthropic_pkce(session_id: str, code_input: str) -> Dict[str, Any]:
    """Exchange authorization code for tokens. Persists on success."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess or sess["provider"] != "anthropic" or sess["flow"] != "pkce":
        raise HTTPException(status_code=404, detail="未知或已过期的会话")
    if sess["status"] != "pending":
        return {"ok": False, "status": sess["status"], "message": sess.get("error_message")}

    # Anthropic's redirect callback page formats the code as `<code>#<state>`.
    # Strip the state suffix if present (we already have the verifier server-side).
    parts = code_input.strip().split("#", 1)
    code = parts[0].strip()
    if not code:
        return {"ok": False, "status": "error", "message": "No code provided"}
    state_from_callback = parts[1] if len(parts) > 1 else ""

    exchange_data = json.dumps({
        "grant_type": "authorization_code",
        "client_id": _ANTHROPIC_OAUTH_CLIENT_ID,
        "code": code,
        "state": state_from_callback or sess["state"],
        "redirect_uri": _ANTHROPIC_OAUTH_REDIRECT_URI,
        "code_verifier": sess["verifier"],
    }).encode()
    req = urllib.request.Request(
        _ANTHROPIC_OAUTH_TOKEN_URL,
        data=exchange_data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "qiqiclaw-dashboard/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Token exchange failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    access_token = result.get("access_token", "")
    refresh_token = result.get("refresh_token", "")
    expires_in = int(result.get("expires_in") or 3600)
    if not access_token:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = "No access token returned"
        return {"ok": False, "status": "error", "message": sess["error_message"]}

    expires_at_ms = int(time.time() * 1000) + (expires_in * 1000)
    try:
        _save_anthropic_oauth_creds(access_token, refresh_token, expires_at_ms)
    except Exception as e:
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = f"Save failed: {e}"
        return {"ok": False, "status": "error", "message": sess["error_message"]}
    with _oauth_sessions_lock:
        sess["status"] = "approved"
    _log.info("oauth/pkce: anthropic login completed (session=%s)", session_id)
    return {"ok": True, "status": "approved"}


async def _start_device_code_flow(provider_id: str) -> Dict[str, Any]:
    """Initiate a device-code flow (Nous or OpenAI Codex).

    Calls the provider's device-auth endpoint via the existing CLI helpers,
    then spawns a background poller. Returns the user-facing display fields
    so the UI can render the verification page link + user code.
    """
    if provider_id == "nous":
        from qiqiclaw_cli.auth import _request_device_code, PROVIDER_REGISTRY
        import httpx
        pconfig = PROVIDER_REGISTRY["nous"]
        portal_base_url = (
            _legacy_env("PORTAL_BASE_URL")
            or os.getenv("NOUS_PORTAL_BASE_URL")
            or pconfig.portal_base_url
        ).rstrip("/")
        client_id = pconfig.client_id
        scope = pconfig.scope
        def _do_nous_device_request():
            with httpx.Client(timeout=httpx.Timeout(15.0), headers={"Accept": "application/json"}) as client:
                return _request_device_code(
                    client=client,
                    portal_base_url=portal_base_url,
                    client_id=client_id,
                    scope=scope,
                )
        device_data = await asyncio.get_event_loop().run_in_executor(None, _do_nous_device_request)
        sid, sess = _new_oauth_session("nous", "device_code")
        sess["device_code"] = str(device_data["device_code"])
        sess["interval"] = int(device_data["interval"])
        sess["expires_at"] = time.time() + int(device_data["expires_in"])
        sess["portal_base_url"] = portal_base_url
        sess["client_id"] = client_id
        threading.Thread(
            target=_nous_poller, args=(sid,), daemon=True, name=f"oauth-poll-{sid[:6]}"
        ).start()
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": str(device_data["user_code"]),
            "verification_url": str(device_data["verification_uri_complete"]),
            "expires_in": int(device_data["expires_in"]),
            "poll_interval": int(device_data["interval"]),
        }

    if provider_id == "openai-codex":
        # Codex uses fixed OpenAI device-auth endpoints; reuse the helper.
        sid, _ = _new_oauth_session("openai-codex", "device_code")
        # Use the helper but in a thread because it polls inline.
        # We can't extract just the start step without refactoring auth.py,
        # so we run the full helper in a worker and proxy the user_code +
        # verification_url back via the session dict. The helper prints
        # to stdout — we capture nothing here, just status.
        threading.Thread(
            target=_codex_full_login_worker, args=(sid,), daemon=True,
            name=f"oauth-codex-{sid[:6]}",
        ).start()
        # Block briefly until the worker has populated the user_code, OR error.
        deadline = time.time() + 10
        while time.time() < deadline:
            with _oauth_sessions_lock:
                s = _oauth_sessions.get(sid)
            if s and (s.get("user_code") or s["status"] != "pending"):
                break
            await asyncio.sleep(0.1)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(sid, {})
        if s.get("status") == "error":
            raise HTTPException(status_code=500, detail=s.get("error_message") or "设备授权失败")
        if not s.get("user_code"):
            raise HTTPException(status_code=504, detail="设备授权超时，未返回用户代码")
        return {
            "session_id": sid,
            "flow": "device_code",
            "user_code": s["user_code"],
            "verification_url": s["verification_url"],
            "expires_in": int(s.get("expires_in") or 900),
            "poll_interval": int(s.get("interval") or 5),
        }

    raise HTTPException(status_code=400, detail=f"提供商 {provider_id} 不支持设备代码流程")


def _nous_poller(session_id: str) -> None:
    """Background poller that drives a Nous device-code flow to completion."""
    from qiqiclaw_cli.auth import _poll_for_token, refresh_nous_oauth_from_state
    from datetime import datetime, timezone
    import httpx
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        return
    portal_base_url = sess["portal_base_url"]
    client_id = sess["client_id"]
    device_code = sess["device_code"]
    interval = sess["interval"]
    expires_in = max(60, int(sess["expires_at"] - time.time()))
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0), headers={"Accept": "application/json"}) as client:
            token_data = _poll_for_token(
                client=client,
                portal_base_url=portal_base_url,
                client_id=client_id,
                device_code=device_code,
                expires_in=expires_in,
                poll_interval=interval,
            )
        # Same post-processing as _nous_device_code_login (mint agent key)
        now = datetime.now(timezone.utc)
        token_ttl = int(token_data.get("expires_in") or 0)
        auth_state = {
            "portal_base_url": portal_base_url,
            "inference_base_url": token_data.get("inference_base_url"),
            "client_id": client_id,
            "scope": token_data.get("scope"),
            "token_type": token_data.get("token_type", "Bearer"),
            "access_token": token_data["access_token"],
            "refresh_token": token_data.get("refresh_token"),
            "obtained_at": now.isoformat(),
            "expires_at": (
                datetime.fromtimestamp(now.timestamp() + token_ttl, tz=timezone.utc).isoformat()
                if token_ttl else None
            ),
            "expires_in": token_ttl,
        }
        full_state = refresh_nous_oauth_from_state(
            auth_state, min_key_ttl_seconds=300, timeout_seconds=15.0,
            force_refresh=False, force_mint=True,
        )
        from qiqiclaw_cli.auth import persist_nous_credentials
        persist_nous_credentials(full_state)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: nous login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("nous device-code poll failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            sess["status"] = "error"
            sess["error_message"] = str(e)


def _codex_full_login_worker(session_id: str) -> None:
    """Run the complete OpenAI Codex device-code flow.

    Codex doesn't use the standard OAuth device-code endpoints; it has its
    own ``/api/accounts/deviceauth/usercode`` (JSON body, returns
    ``device_auth_id``) and ``/api/accounts/deviceauth/token`` (JSON body
    polled until 200). On success the response carries an
    ``authorization_code`` + ``code_verifier`` that get exchanged at
    CODEX_OAUTH_TOKEN_URL with grant_type=authorization_code.

    The flow is replicated inline (rather than calling
    _codex_device_code_login) because that helper prints/blocks/polls in a
    single function — we need to surface the user_code to the dashboard the
    moment we receive it, well before polling completes.
    """
    try:
        import httpx
        from qiqiclaw_cli.auth import (
            CODEX_OAUTH_CLIENT_ID,
            CODEX_OAUTH_TOKEN_URL,
            DEFAULT_CODEX_BASE_URL,
        )
        issuer = "https://auth.openai.com"

        # Step 1: request device code
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.post(
                f"{issuer}/api/accounts/deviceauth/usercode",
                json={"client_id": CODEX_OAUTH_CLIENT_ID},
                headers={"Content-Type": "application/json"},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"deviceauth/usercode returned {resp.status_code}")
        device_data = resp.json()
        user_code = device_data.get("user_code", "")
        device_auth_id = device_data.get("device_auth_id", "")
        poll_interval = max(3, int(device_data.get("interval", "5")))
        if not user_code or not device_auth_id:
            raise RuntimeError("device-code response missing user_code or device_auth_id")
        verification_url = f"{issuer}/codex/device"
        with _oauth_sessions_lock:
            sess = _oauth_sessions.get(session_id)
            if not sess:
                return
            sess["user_code"] = user_code
            sess["verification_url"] = verification_url
            sess["device_auth_id"] = device_auth_id
            sess["interval"] = poll_interval
            sess["expires_in"] = 15 * 60  # OpenAI's effective limit
            sess["expires_at"] = time.time() + sess["expires_in"]

        # Step 2: poll until authorized
        deadline = time.time() + sess["expires_in"]
        code_resp = None
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            while time.time() < deadline:
                time.sleep(poll_interval)
                poll = client.post(
                    f"{issuer}/api/accounts/deviceauth/token",
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers={"Content-Type": "application/json"},
                )
                if poll.status_code == 200:
                    code_resp = poll.json()
                    break
                if poll.status_code in (403, 404):
                    continue  # user hasn't authorized yet
                raise RuntimeError(f"deviceauth/token poll returned {poll.status_code}")

        if code_resp is None:
            with _oauth_sessions_lock:
                sess["status"] = "expired"
                sess["error_message"] = "Device code expired before approval"
            return

        # Step 3: exchange authorization_code for tokens
        authorization_code = code_resp.get("authorization_code", "")
        code_verifier = code_resp.get("code_verifier", "")
        if not authorization_code or not code_verifier:
            raise RuntimeError("device-auth response missing authorization_code/code_verifier")
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            token_resp = client.post(
                CODEX_OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": f"{issuer}/deviceauth/callback",
                    "client_id": CODEX_OAUTH_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if token_resp.status_code != 200:
            raise RuntimeError(f"token exchange returned {token_resp.status_code}")
        tokens = token_resp.json()
        access_token = tokens.get("access_token", "")
        refresh_token = tokens.get("refresh_token", "")
        if not access_token:
            raise RuntimeError("token exchange did not return access_token")

        # Persist via credential pool — same shape as auth_commands.add_command
        from agent.credential_pool import (
            PooledCredential,
            load_pool,
            AUTH_TYPE_OAUTH,
            SOURCE_MANUAL,
        )
        import uuid as _uuid
        pool = load_pool("openai-codex")
        base_url = (
            _legacy_env("CODEX_BASE_URL", "").strip().rstrip("/")
            or DEFAULT_CODEX_BASE_URL
        )
        entry = PooledCredential(
            provider="openai-codex",
            id=_uuid.uuid4().hex[:6],
            label="dashboard device_code",
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:dashboard_device_code",
            access_token=access_token,
            refresh_token=refresh_token,
            base_url=base_url,
        )
        pool.add_entry(entry)
        with _oauth_sessions_lock:
            sess["status"] = "approved"
        _log.info("oauth/device: openai-codex login completed (session=%s)", session_id)
    except Exception as e:
        _log.warning("codex device-code worker failed (session=%s): %s", session_id, e)
        with _oauth_sessions_lock:
            s = _oauth_sessions.get(session_id)
            if s:
                s["status"] = "error"
                s["error_message"] = str(e)


@app.post("/api/providers/oauth/{provider_id}/start")
async def start_oauth_login(provider_id: str, request: Request):
    """Initiate an OAuth login flow. Token-protected."""
    _require_token(request)
    _gc_oauth_sessions()
    valid = {p["id"] for p in _OAUTH_PROVIDER_CATALOG}
    if provider_id not in valid:
        raise HTTPException(status_code=400, detail=f"未知提供商 {provider_id}")
    catalog_entry = next(p for p in _OAUTH_PROVIDER_CATALOG if p["id"] == provider_id)
    if catalog_entry["flow"] == "external":
        raise HTTPException(
            status_code=400,
            detail=f"{provider_id} uses an external CLI; run `{catalog_entry['cli_command']}` manually",
        )
    try:
        if catalog_entry["flow"] == "pkce":
            return _start_anthropic_pkce()
        if catalog_entry["flow"] == "device_code":
            return await _start_device_code_flow(provider_id)
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("oauth/start %s failed", provider_id)
        raise HTTPException(status_code=500, detail=str(e))
    raise HTTPException(status_code=400, detail="不支持的流程")


class OAuthSubmitBody(BaseModel):
    session_id: str
    code: str


@app.post("/api/providers/oauth/{provider_id}/submit")
async def submit_oauth_code(provider_id: str, body: OAuthSubmitBody, request: Request):
    """Submit the auth code for PKCE flows. Token-protected."""
    _require_token(request)
    if provider_id == "anthropic":
        return await asyncio.get_event_loop().run_in_executor(
            None, _submit_anthropic_pkce, body.session_id, body.code,
        )
    raise HTTPException(status_code=400, detail=f"{provider_id} 不支持提交")


@app.get("/api/providers/oauth/{provider_id}/poll/{session_id}")
async def poll_oauth_session(provider_id: str, session_id: str):
    """Poll a device-code session's status (no auth — read-only state)."""
    with _oauth_sessions_lock:
        sess = _oauth_sessions.get(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="会话未找到或已过期")
    if sess["provider"] != provider_id:
        raise HTTPException(status_code=400, detail="会话的提供商不匹配")
    return {
        "session_id": session_id,
        "status": sess["status"],
        "error_message": sess.get("error_message"),
        "expires_at": sess.get("expires_at"),
    }


@app.delete("/api/providers/oauth/sessions/{session_id}")
async def cancel_oauth_session(session_id: str, request: Request):
    """Cancel a pending OAuth session. Token-protected."""
    _require_token(request)
    with _oauth_sessions_lock:
        sess = _oauth_sessions.pop(session_id, None)
    if sess is None:
        return {"ok": False, "message": "session not found"}
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Session detail endpoints
# ---------------------------------------------------------------------------


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str):
    from qiqiclaw_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        session = db.get_session(sid) if sid else None
        if not session:
            raise HTTPException(status_code=404, detail="会话未找到")
        return session
    finally:
        db.close()


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    from qiqiclaw_state import SessionDB
    db = SessionDB()
    try:
        sid = db.resolve_session_id(session_id)
        if not sid:
            raise HTTPException(status_code=404, detail="会话未找到")
        messages = db.get_messages(sid)
        return {"session_id": sid, "messages": messages}
    finally:
        db.close()


@app.delete("/api/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    from qiqiclaw_state import SessionDB
    db = SessionDB()
    try:
        if not db.delete_session(session_id):
            raise HTTPException(status_code=404, detail="会话未找到")
        return {"ok": True}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Log viewer endpoint
# ---------------------------------------------------------------------------


@app.get("/api/logs")
async def get_logs(
    file: str = "agent",
    lines: int = 100,
    level: Optional[str] = None,
    component: Optional[str] = None,
    search: Optional[str] = None,
):
    from qiqiclaw_cli.logs import _read_tail, LOG_FILES

    log_aliases = {
        "gui": "gui.log",
        "desktop": "desktop.log",
    }
    log_name = log_aliases.get(file) or LOG_FILES.get(file)
    if not log_name:
        raise HTTPException(status_code=400, detail=f"未知日志文件: {file}")
    log_path = get_qiqiclaw_home() / "logs" / log_name
    if not log_path.exists():
        return {"file": file, "lines": []}

    try:
        from qiqiclaw_logging import COMPONENT_PREFIXES
    except ImportError:
        COMPONENT_PREFIXES = {}

    # Normalize "ALL" / "all" / empty → no filter. _matches_filters treats an
    # empty tuple as "must match a prefix" (startswith(()) is always False),
    # so passing () instead of None silently drops every line.
    min_level = level if level and level.upper() != "ALL" else None
    if component and component.lower() != "all":
        comp_prefixes = COMPONENT_PREFIXES.get(component)
        if comp_prefixes is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown component: {component}. "
                       f"Available: {', '.join(sorted(COMPONENT_PREFIXES))}",
            )
    else:
        comp_prefixes = None

    has_filters = bool(min_level or comp_prefixes or search)
    result = _read_tail(
        log_path, min(lines, 500) if not search else 2000,
        has_filters=has_filters,
        min_level=min_level,
        component_prefixes=comp_prefixes,
    )
    # Post-filter by search term (case-insensitive substring match).
    # _read_tail doesn't support free-text search, so we filter here and
    # trim to the requested line count afterward.
    if search:
        needle = search.lower()
        result = [l for l in result if needle in l.lower()][-min(lines, 500):]
    return {"file": file, "lines": result}


# ---------------------------------------------------------------------------
# Cron job management endpoints
# ---------------------------------------------------------------------------


class CronJobCreate(BaseModel):
    prompt: str
    schedule: str
    name: str = ""
    deliver: str = "local"


class CronJobUpdate(BaseModel):
    updates: dict


@app.get("/api/cron/jobs")
async def list_cron_jobs():
    from cron.jobs import list_jobs
    return list_jobs(include_disabled=True)


@app.get("/api/cron/jobs/{job_id}")
async def get_cron_job(job_id: str):
    from cron.jobs import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务未找到")
    return job


@app.post("/api/cron/jobs")
async def create_cron_job(body: CronJobCreate):
    from cron.jobs import create_job
    try:
        job = create_job(prompt=body.prompt, schedule=body.schedule,
                         name=body.name, deliver=body.deliver)
        return job
    except Exception as e:
        _log.exception("POST /api/cron/jobs failed")
        raise HTTPException(status_code=400, detail=str(e))


@app.put("/api/cron/jobs/{job_id}")
async def update_cron_job(job_id: str, body: CronJobUpdate):
    from cron.jobs import update_job
    job = update_job(job_id, body.updates)
    if not job:
        raise HTTPException(status_code=404, detail="任务未找到")
    return job


@app.post("/api/cron/jobs/{job_id}/pause")
async def pause_cron_job(job_id: str):
    from cron.jobs import pause_job
    job = pause_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务未找到")
    return job


@app.post("/api/cron/jobs/{job_id}/resume")
async def resume_cron_job(job_id: str):
    from cron.jobs import resume_job
    job = resume_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务未找到")
    return job


@app.post("/api/cron/jobs/{job_id}/trigger")
async def trigger_cron_job(job_id: str):
    from cron.jobs import trigger_job
    job = trigger_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务未找到")
    return job


@app.delete("/api/cron/jobs/{job_id}")
async def delete_cron_job(job_id: str):
    from cron.jobs import remove_job
    if not remove_job(job_id):
        raise HTTPException(status_code=404, detail="任务未找到")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Profile management endpoints (minimal — list/create/rename/delete + SOUL.md)
# ---------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str
    clone_from_default: bool = False


class ProfileRename(BaseModel):
    new_name: str


class ProfileSoulUpdate(BaseModel):
    content: str


class ProfileActiveUpdate(BaseModel):
    name: str


def _profile_attr(info, name: str, default: Any = None) -> Any:
    try:
        return getattr(info, name)
    except Exception:
        return default


def _profile_to_dict(info) -> Dict[str, Any]:
    return {
        "name": _profile_attr(info, "name", ""),
        "path": str(_profile_attr(info, "path", "")),
        "is_default": bool(_profile_attr(info, "is_default", False)),
        "model": _profile_attr(info, "model"),
        "provider": _profile_attr(info, "provider"),
        "has_env": bool(_profile_attr(info, "has_env", False)),
        "skill_count": int(_profile_attr(info, "skill_count", 0) or 0),
    }


def _fallback_profile_dicts(profiles_mod) -> List[Dict[str, Any]]:
    def _safe(callable_, default):
        try:
            return callable_()
        except Exception:
            return default

    profiles: List[Dict[str, Any]] = []
    default_home = profiles_mod._get_default_qiqiclaw_home()
    if default_home.is_dir():
        model, provider = _safe(lambda: profiles_mod._read_config_model(default_home), (None, None))
        profiles.append({
            "name": "default",
            "path": str(default_home),
            "is_default": True,
            "model": model,
            "provider": provider,
            "has_env": (default_home / ".env").exists(),
            "skill_count": _safe(lambda: profiles_mod._count_skills(default_home), 0),
        })

    profiles_root = profiles_mod._get_profiles_root()
    if profiles_root.is_dir():
        for entry in sorted(profiles_root.iterdir()):
            if not entry.is_dir() or not profiles_mod._PROFILE_ID_RE.match(entry.name):
                continue
            model, provider = _safe(lambda entry=entry: profiles_mod._read_config_model(entry), (None, None))
            profiles.append({
                "name": entry.name,
                "path": str(entry),
                "is_default": False,
                "model": model,
                "provider": provider,
                "has_env": (entry / ".env").exists(),
                "skill_count": _safe(lambda entry=entry: profiles_mod._count_skills(entry), 0),
            })

    return profiles


def _resolve_profile_dir(name: str) -> Path:
    """Validate ``name`` and resolve to its directory or raise an HTTPException."""
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        profiles_mod.validate_profile_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not profiles_mod.profile_exists(name):
        raise HTTPException(status_code=404, detail=f"配置文件 '{name}' 不存在。")
    return profiles_mod.get_profile_dir(name)


def _profile_setup_command(name: str) -> str:
    """Return the shell command used to configure a profile in the CLI."""
    _resolve_profile_dir(name)
    return "qiqiclaw setup" if name == "default" else f"{name} setup"


@app.get("/api/profiles")
async def list_profiles_endpoint():
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        return {"profiles": [_profile_to_dict(p) for p in profiles_mod.list_profiles()]}
    except Exception:
        _log.exception("GET /api/profiles failed; falling back to profile directory scan")
        return {"profiles": _fallback_profile_dicts(profiles_mod)}


@app.post("/api/profiles")
async def create_profile_endpoint(body: ProfileCreate):
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        path = profiles_mod.create_profile(
            name=body.name,
            clone_from="default" if body.clone_from_default else None,
            clone_config=body.clone_from_default,
        )
        # Match the CLI's profile-create flow: fresh named profiles get the
        # bundled skills installed. When cloning from default, create_profile()
        # has already copied the source profile's skills, including any
        # user-installed skills.
        if not body.clone_from_default:
            profiles_mod.seed_profile_skills(path, quiet=True)

        # Match the CLI's profile-create flow: named profiles should get a
        # wrapper in ~/.local/bin when the alias is safe to create.
        collision = profiles_mod.check_alias_collision(body.name)
        if not collision:
            profiles_mod.create_wrapper_script(body.name)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("POST /api/profiles failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.name, "path": str(path)}


@app.get("/api/profiles/active")
async def get_active_profile_endpoint():
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        active = profiles_mod.get_active_profile() or "default"
    except Exception:
        active = "default"
    try:
        current = profiles_mod.get_active_profile_name() or "default"
    except Exception:
        current = "default"
    return {"active": active, "current": current}


@app.post("/api/profiles/active")
async def set_active_profile_endpoint(body: ProfileActiveUpdate):
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        profiles_mod.set_active_profile(body.name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("POST /api/profiles/active failed")
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "active": profiles_mod.normalize_profile_name(body.name)}


@app.get("/api/profiles/sessions")
async def get_profiles_sessions(
    limit: int = 20,
    offset: int = 0,
    min_messages: int = 0,
    archived: str = "exclude",
    order: str = "recent",
    profile: str = "all",
    source: Optional[str] = None,
    exclude_sources: Optional[str] = None,
):
    """Aggregate local profile session lists for the desktop sidebar."""
    if archived not in ("exclude", "only", "include"):
        raise HTTPException(status_code=400, detail="archived must be one of: exclude, only, include")
    if order not in ("created", "recent"):
        raise HTTPException(status_code=400, detail="order must be one of: created, recent")

    from qiqiclaw_state import SessionDB
    from qiqiclaw_cli import profiles as profiles_mod

    targets: List[Tuple[str, Path]] = []
    if profile and profile != "all":
        try:
            profiles_mod.validate_profile_name(profile)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if not profiles_mod.profile_exists(profile):
            raise HTTPException(status_code=404, detail=f"配置文件 '{profile}' 不存在。")
        targets.append((profile, profiles_mod.get_profile_dir(profile)))
    else:
        try:
            targets = [(info.name, info.path) for info in profiles_mod.list_profiles()]
        except Exception:
            _log.exception("GET /api/profiles/sessions: list_profiles failed")
            targets = []
        if not targets:
            targets.append(("default", profiles_mod.get_profile_dir("default")))

    request_limit = max(1, limit)
    request_offset = max(0, offset)
    per_profile_limit = min(max(request_limit + request_offset, request_limit, 100), 5000)
    merged: List[Dict[str, Any]] = []
    total = 0
    profile_totals: Dict[str, int] = {}
    errors: List[Dict[str, str]] = []
    now = time.time()

    for name, home in targets:
        db_path = Path(home) / "state.db"
        if not db_path.exists():
            profile_totals[name] = 0
            continue
        try:
            db = SessionDB(db_path=db_path)
        except Exception as exc:
            errors.append({"profile": name, "error": str(exc)})
            continue
        try:
            rows, profile_total = _list_sessions_compat(
                db,
                limit=per_profile_limit,
                offset=0,
                min_messages=min_messages,
                archived=archived,
                order=order,
                source=source,
                exclude_sources=exclude_sources,
            )
            total += profile_total
            profile_totals[name] = profile_total
            for s in rows:
                s["profile"] = name
                s["is_default_profile"] = name == "default"
                s["is_active"] = (
                    s.get("ended_at") is None
                    and (now - s.get("last_active", s.get("started_at", 0))) < 300
                )
                s["archived"] = bool(s.get("archived"))
                merged.append(s)
        except Exception as exc:
            errors.append({"profile": name, "error": str(exc)})
        finally:
            db.close()

    _sort_sessions_for_dashboard(merged, order)
    window = merged[request_offset:request_offset + request_limit]
    return {
        "sessions": window,
        "total": total,
        "profile_totals": profile_totals,
        "limit": request_limit,
        "offset": request_offset,
        "errors": errors,
    }


@app.get("/api/profiles/{name}/setup-command")
async def get_profile_setup_command(name: str):
    return {"command": _profile_setup_command(name)}


@app.post("/api/profiles/{name}/open-terminal")
async def open_profile_terminal_endpoint(name: str):
    try:
        command = _profile_setup_command(name)

        if sys.platform.startswith("win"):
            subprocess.Popen(["cmd.exe", "/c", "start", "", command])
        elif sys.platform == "darwin":
            escaped = command.replace("\\", "\\\\").replace('"', '\\"')
            applescript = (
                'tell application "Terminal"\n'
                "activate\n"
                f'do script "{escaped}"\n'
                "end tell"
            )
            subprocess.Popen(["osascript", "-e", applescript])
        else:
            terminal_commands = [
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", "sh", "-lc", command]),
                ("gnome-terminal", ["gnome-terminal", "--", "sh", "-lc", command]),
                ("konsole", ["konsole", "-e", "sh", "-lc", command]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"sh -lc '{command}'"]),
                ("mate-terminal", ["mate-terminal", "-e", f"sh -lc '{command}'"]),
                ("lxterminal", ["lxterminal", "-e", f"sh -lc '{command}'"]),
                ("tilix", ["tilix", "-e", "sh", "-lc", command]),
                ("alacritty", ["alacritty", "-e", "sh", "-lc", command]),
                ("kitty", ["kitty", "sh", "-lc", command]),
                ("xterm", ["xterm", "-e", "sh", "-lc", command]),
            ]
            for executable, popen_args in terminal_commands:
                if subprocess.call(
                    ["which", executable],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ) == 0:
                    subprocess.Popen(popen_args)
                    break
            else:
                raise HTTPException(
                    status_code=400,
                    detail="No supported terminal emulator found",
                )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        _log.exception("POST /api/profiles/%s/open-terminal failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "command": command}


@app.patch("/api/profiles/{name}")
async def rename_profile_endpoint(name: str, body: ProfileRename):
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        path = profiles_mod.rename_profile(name, body.new_name)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except (ValueError, FileExistsError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("PATCH /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "name": body.new_name, "path": str(path)}


@app.delete("/api/profiles/{name}")
async def delete_profile_endpoint(name: str):
    """Delete a profile. The dashboard collects the user's confirmation in
    its own dialog before this request, so we always pass ``yes=True`` to
    skip the CLI's interactive prompt."""
    from qiqiclaw_cli import profiles as profiles_mod
    try:
        path = profiles_mod.delete_profile(name, yes=True)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _log.exception("DELETE /api/profiles/%s failed", name)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True, "path": str(path)}


@app.get("/api/profiles/{name}/soul")
async def get_profile_soul(name: str):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    if soul_path.exists():
        try:
            return {"content": soul_path.read_text(encoding="utf-8"), "exists": True}
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"无法读取 SOUL.md: {e}")
    return {"content": "", "exists": False}


@app.put("/api/profiles/{name}/soul")
async def update_profile_soul(name: str, body: ProfileSoulUpdate):
    soul_path = _resolve_profile_dir(name) / "SOUL.md"
    try:
        soul_path.write_text(body.content, encoding="utf-8")
    except OSError as e:
        _log.exception("PUT /api/profiles/%s/soul failed", name)
        raise HTTPException(status_code=500, detail=f"无法写入 SOUL.md: {e}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Skills & Tools endpoints
# ---------------------------------------------------------------------------


class SkillToggle(BaseModel):
    name: str
    enabled: bool


@app.get("/api/skills")
async def get_skills():
    from tools.skills_tool import _find_all_skills
    from qiqiclaw_cli.skills_config import get_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    skills = _find_all_skills(skip_disabled=True)
    for s in skills:
        s["enabled"] = s["name"] not in disabled
    return skills


@app.put("/api/skills/toggle")
async def toggle_skill(body: SkillToggle):
    from qiqiclaw_cli.skills_config import get_disabled_skills, save_disabled_skills
    config = load_config()
    disabled = get_disabled_skills(config)
    if body.enabled:
        disabled.discard(body.name)
    else:
        disabled.add(body.name)
    save_disabled_skills(config, disabled)
    return {"ok": True, "name": body.name, "enabled": body.enabled}


@app.get("/api/tools/toolsets")
async def get_toolsets():
    from qiqiclaw_cli.tools_config import (
        _get_effective_configurable_toolsets,
        _get_platform_tools,
        _toolset_has_keys,
    )
    from toolsets import resolve_toolset

    config = load_config()
    enabled_toolsets = _get_platform_tools(
        config,
        "cli",
        include_default_mcp_servers=False,
    )
    result = []
    for name, label, desc in _get_effective_configurable_toolsets():
        try:
            tools = sorted(set(resolve_toolset(name)))
        except Exception:
            tools = []
        is_enabled = name in enabled_toolsets
        result.append({
            "name": name, "label": label, "description": desc,
            "enabled": is_enabled,
            "available": is_enabled,
            "configured": _toolset_has_keys(name, config),
            "tools": tools,
        })
    return result


# ---------------------------------------------------------------------------
# Raw YAML config endpoint
# ---------------------------------------------------------------------------


class RawConfigUpdate(BaseModel):
    yaml_text: str


@app.get("/api/config/raw")
async def get_config_raw():
    path = get_config_path()
    if not path.exists():
        return {"yaml": ""}
    return {"yaml": path.read_text(encoding="utf-8")}


@app.put("/api/config/raw")
async def update_config_raw(body: RawConfigUpdate):
    try:
        parsed = yaml.safe_load(body.yaml_text)
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="YAML 必须是映射类型")
        save_config(parsed)
        return {"ok": True}
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"无效的 YAML: {e}")


# ---------------------------------------------------------------------------
# Token / cost analytics endpoint
# ---------------------------------------------------------------------------


@app.get("/api/analytics/usage")
async def get_usage_analytics(days: int = 30):
    from qiqiclaw_state import SessionDB
    from agent.insights import InsightsEngine

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)
        cur = db._conn.execute("""
            SELECT date(started_at, 'unixepoch') as day,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ?
            GROUP BY day ORDER BY day
        """, (cutoff,))
        daily = [dict(r) for r in cur.fetchall()]

        cur2 = db._conn.execute("""
            SELECT model,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL
            GROUP BY model ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        by_model = [dict(r) for r in cur2.fetchall()]

        cur3 = db._conn.execute("""
            SELECT SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ?
        """, (cutoff,))
        totals = dict(cur3.fetchone())
        insights_report = InsightsEngine(db).generate(days=days)
        skills = insights_report.get("skills", {
            "summary": {
                "total_skill_loads": 0,
                "total_skill_edits": 0,
                "total_skill_actions": 0,
                "distinct_skills_used": 0,
            },
            "top_skills": [],
        })

        return {
            "daily": daily,
            "by_model": by_model,
            "totals": totals,
            "period_days": days,
            "skills": skills,
        }
    finally:
        db.close()


@app.get("/api/analytics/models")
async def get_models_analytics(days: int = 30):
    """Rich per-model analytics for the Models dashboard page.

    Returns token/cost/session breakdown per model plus capability metadata
    from models.dev (context window, vision, tools, reasoning, etc.).
    """
    from qiqiclaw_state import SessionDB

    db = SessionDB()
    try:
        cutoff = time.time() - (days * 86400)

        cur = db._conn.execute("""
            SELECT model,
                   billing_provider,
                   SUM(input_tokens) as input_tokens,
                   SUM(output_tokens) as output_tokens,
                   SUM(cache_read_tokens) as cache_read_tokens,
                   SUM(reasoning_tokens) as reasoning_tokens,
                   COALESCE(SUM(estimated_cost_usd), 0) as estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as actual_cost,
                   COUNT(*) as sessions,
                   SUM(COALESCE(api_call_count, 0)) as api_calls,
                   SUM(tool_call_count) as tool_calls,
                   MAX(started_at) as last_used_at,
                   AVG(input_tokens + output_tokens) as avg_tokens_per_session
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
            GROUP BY model, billing_provider
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]

        models = []
        for row in rows:
            provider = row.get("billing_provider") or ""
            model_name = row["model"]
            caps = {}
            try:
                from agent.models_dev import get_model_capabilities
                mc = get_model_capabilities(provider=provider, model=model_name)
                if mc is not None:
                    caps = {
                        "supports_tools": mc.supports_tools,
                        "supports_vision": mc.supports_vision,
                        "supports_reasoning": mc.supports_reasoning,
                        "context_window": mc.context_window,
                        "max_output_tokens": mc.max_output_tokens,
                        "model_family": mc.model_family,
                    }
            except Exception:
                pass

            models.append({
                "model": model_name,
                "provider": provider,
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "cache_read_tokens": row["cache_read_tokens"],
                "reasoning_tokens": row["reasoning_tokens"],
                "estimated_cost": row["estimated_cost"],
                "actual_cost": row["actual_cost"],
                "sessions": row["sessions"],
                "api_calls": row["api_calls"],
                "tool_calls": row["tool_calls"],
                "last_used_at": row["last_used_at"],
                "avg_tokens_per_session": row["avg_tokens_per_session"],
                "capabilities": caps,
            })

        totals_cur = db._conn.execute("""
            SELECT COUNT(DISTINCT model) as distinct_models,
                   SUM(input_tokens) as total_input,
                   SUM(output_tokens) as total_output,
                   SUM(cache_read_tokens) as total_cache_read,
                   SUM(reasoning_tokens) as total_reasoning,
                   COALESCE(SUM(estimated_cost_usd), 0) as total_estimated_cost,
                   COALESCE(SUM(actual_cost_usd), 0) as total_actual_cost,
                   COUNT(*) as total_sessions,
                   SUM(COALESCE(api_call_count, 0)) as total_api_calls
            FROM sessions WHERE started_at > ? AND model IS NOT NULL AND model != ''
        """, (cutoff,))
        totals = dict(totals_cur.fetchone())

        return {
            "models": models,
            "totals": totals,
            "period_days": days,
        }
    finally:
        db.close()

# ---------------------------------------------------------------------------
# /api/disk — Local filesystem browser for the "Disk" dashboard page
# ---------------------------------------------------------------------------

@app.get("/api/disk/list")
async def disk_list(path: str = "/", request: Request = None):
    """List directory contents. Returns dirs tree + flat entries list."""
    _require_token(request)
    from pathlib import Path as _Path
    try:
        p = _Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {path}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    entries = []
    try:
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                stat = child.stat()
                entries.append({"name": child.name, "path": str(child), "type": "dir" if child.is_dir() else "file", "size": stat.st_size if not child.is_dir() else None, "mtime": stat.st_mtime})
            except OSError:
                continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))

    def build_tree(base, max_depth=3, current_depth=0):
        if current_depth >= max_depth: return []
        dirs = []
        try:
            for child in sorted(base.iterdir(), key=lambda x: x.name.lower()):
                if child.is_dir() and not child.name.startswith('.'):
                    try:
                        stat = child.stat()
                        node = {"name": child.name, "path": str(child), "type": "dir", "mtime": stat.st_mtime}
                        children = build_tree(child, max_depth, current_depth + 1)
                        if children: node["children"] = children
                        dirs.append(node)
                    except (OSError, PermissionError): continue
        except (OSError, PermissionError): pass
        return dirs

    return {"path": str(p), "entries": entries, "dirs": build_tree(p), "parent": str(p.parent) if str(p) != "/" else None}


@app.get("/api/disk/read")
async def disk_read(path: str, raw: bool = False, download: bool = False, request: Request = None):
    """Read file content. Set download=true for force-download, raw=true for raw image. No size limit."""
    _require_token(request)
    from pathlib import Path as _Path
    try:
        p = _Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    if p.is_dir():
        raise HTTPException(status_code=400, detail="Cannot read directory")
    try:
        stat = p.stat()
    except OSError as e:
        raise HTTPException(status_code=403, detail=str(e))
    import mimetypes
    mime, _ = mimetypes.guess_type(str(p))
    is_image = mime and mime.startswith('image/')

    # Download mode: force browser to download the file
    if download:
        from fastapi.responses import FileResponse
        from urllib.parse import quote
        filename = quote(p.name)
        return FileResponse(
            str(p),
            media_type=mime or "application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename*=UTF-8\'\'{filename}'},
        )

    if raw and is_image:
        from fastapi.responses import FileResponse
        return FileResponse(str(p), media_type=mime)
    try:
        content = p.read_text(encoding='utf-8')
        return {"path": str(p), "size": stat.st_size, "mtime": stat.st_mtime, "content": content, "mime": mime or "text/plain"}
    except UnicodeDecodeError:
        if is_image:
            return {"path": str(p), "size": stat.st_size, "mtime": stat.st_mtime, "content": f"[图片: {p.name}]", "mime": mime}
        else:
            return {"path": str(p), "size": stat.st_size, "mtime": stat.st_mtime, "content": "[二进制文件]", "mime": mime or "application/octet-stream"}




@app.get("/api/disk/open")
async def disk_open(path: str, request: Request = None):
    """Open a file with the system default application via xdg-open."""
    _require_token(request)
    from pathlib import Path as _Path
    import subprocess as _subprocess
    try:
        p = _Path(path).resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path")
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        _subprocess.Popen(["xdg-open", str(p)], stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL)
        return {"ok": True, "path": str(p), "name": p.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# /api/pty — PTY-over-WebSocket bridge for the dashboard "Chat" tab.
#
# The endpoint spawns the same ``qiqiclaw --tui`` binary the CLI uses, behind
# a POSIX pseudo-terminal, and forwards bytes + resize escapes across a
# WebSocket.  The browser renders the ANSI through xterm.js (see
# web/src/pages/ChatPage.tsx).
#
# Auth: ``?token=<session_token>`` query param (browsers can't set
# Authorization on the WS upgrade).  Same ephemeral ``_SESSION_TOKEN`` as
# REST.  Localhost-only — we defensively reject non-loopback clients even
# though uvicorn binds to 127.0.0.1.
# ---------------------------------------------------------------------------

import re
import asyncio

from qiqiclaw_cli.pty_bridge import PtyBridge, PtyUnavailableError

_RESIZE_RE = re.compile(rb"\x1b\[RESIZE:(\d+);(\d+)\]")
_PTY_READ_CHUNK_TIMEOUT = 0.2
_VALID_CHANNEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Starlette's TestClient reports the peer as "testclient"; treat it as
# loopback so tests don't need to rewrite request scope.
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "::1",
    "::ffff:127.0.0.1",
    "0:0:0:0:0:ffff:127.0.0.1",
    "localhost",
    "testclient",
    "192.168.0.106",
})

# Per-channel subscriber registry used by /api/pub (PTY-side gateway → dashboard)
# and /api/events (dashboard → browser sidebar).  Keyed by an opaque channel id
# the chat tab generates on mount; entries auto-evict when the last subscriber
# drops AND the publisher has disconnected.
_event_channels: dict[str, set] = {}
_event_lock = asyncio.Lock()


def _resolve_chat_argv(
    resume: Optional[str] = None,
    sidecar_url: Optional[str] = None,
) -> tuple[list[str], Optional[str], Optional[dict]]:
    """Resolve the argv + cwd + env for the chat PTY.

    Default: whatever ``qiqiclaw --tui`` would run.  Tests monkeypatch this
    function to inject a tiny fake command (``cat``, ``sh -c 'printf …'``)
    so nothing has to build Node or the TUI bundle.

    Session resume is propagated via the ``QIQICLAW_TUI_RESUME`` env var —
    matching what ``qiqiclaw_cli.main._launch_tui`` does for the CLI path.
    Appending ``--resume <id>`` to argv doesn't work because ``ui-tui`` does
    not parse its argv.

    `sidecar_url` (when set) is forwarded as ``HERMES_TUI_SIDECAR_URL`` so
    the spawned ``tui_gateway.entry`` can mirror dispatcher emits to the
    dashboard's ``/api/pub`` endpoint (see :func:`pub_ws`).
    """
    from qiqiclaw_cli.main import PROJECT_ROOT, _make_tui_argv

    argv, cwd = _make_tui_argv(PROJECT_ROOT / "ui-tui", tui_dev=False)
    env = os.environ.copy()
    env.setdefault("NODE_ENV", "production")

    if resume:
        env["QIQICLAW_TUI_RESUME"] = resume

    if sidecar_url:
        env["HERMES_TUI_SIDECAR_URL"] = sidecar_url

    return list(argv), str(cwd) if cwd else None, env


def _build_sidecar_url(channel: str) -> Optional[str]:
    """ws:// URL the PTY child should publish events to, or None when unbound."""
    host = getattr(app.state, "bound_host", None)
    port = getattr(app.state, "bound_port", None)

    if not host or not port:
        return None

    netloc = f"[{host}]:{port}" if ":" in host and not host.startswith("[") else f"{host}:{port}"
    qs = urllib.parse.urlencode({"token": _SESSION_TOKEN, "channel": channel})

    return f"ws://{netloc}/api/pub?{qs}"


async def _broadcast_event(channel: str, payload: str) -> None:
    """Fan out one publisher frame to every subscriber on `channel`."""
    async with _event_lock:
        subs = list(_event_channels.get(channel, ()))

    for sub in subs:
        try:
            await sub.send_text(payload)
        except Exception:
            # Subscriber went away mid-send; the /api/events finally clause
            # will remove it from the registry on its next iteration.
            pass


def _channel_or_close_code(ws: WebSocket) -> Optional[str]:
    """Return the channel id from the query string or None if invalid."""
    channel = ws.query_params.get("channel", "")

    return channel if _VALID_CHANNEL_RE.match(channel) else None


def _ws_client_host(ws: WebSocket) -> str:
    return (ws.client.host if ws.client else "") or ""


def _is_loopback_ws_client(ws: WebSocket) -> bool:
    client_host = _ws_client_host(ws)
    return not client_host or client_host in _LOOPBACK_HOSTS


def _log_ws_reject(endpoint: str, reason: str, ws: WebSocket) -> None:
    _log.warning("Rejecting %s WebSocket: %s (client=%s)", endpoint, reason, _ws_client_host(ws) or "unknown")


@app.websocket("/api/pty")
async def pty_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        _log_ws_reject("/api/pty", "embedded chat disabled", ws)
        await ws.close(code=4403)
        return

    # --- auth + loopback check (before accept so we can close cleanly) ---
    token = ws.query_params.get("token", "")
    expected = _SESSION_TOKEN
    if not hmac.compare_digest(token.encode(), expected.encode()):
        _log_ws_reject("/api/pty", "invalid session token", ws)
        await ws.close(code=4401)
        return

    if not _is_loopback_ws_client(ws):
        _log_ws_reject("/api/pty", "non-loopback client", ws)
        await ws.close(code=4403)
        return

    await ws.accept()

    # --- spawn PTY ------------------------------------------------------
    resume = ws.query_params.get("resume") or None
    channel = _channel_or_close_code(ws)
    sidecar_url = _build_sidecar_url(channel) if channel else None

    try:
        argv, cwd, env = _resolve_chat_argv(resume=resume, sidecar_url=sidecar_url)
    except SystemExit as exc:
        # _make_tui_argv calls sys.exit(1) when node/npm is missing.
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return


    try:
        bridge = PtyBridge.spawn(argv, cwd=cwd, env=env)
    except PtyUnavailableError as exc:
        await ws.send_text(f"\r\n\x1b[31mChat unavailable: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return
    except (FileNotFoundError, OSError) as exc:
        await ws.send_text(f"\r\n\x1b[31mChat failed to start: {exc}\x1b[0m\r\n")
        await ws.close(code=1011)
        return

    loop = asyncio.get_running_loop()

    # --- reader task: PTY master → WebSocket ----------------------------
    async def pump_pty_to_ws() -> None:
        while True:
            chunk = await loop.run_in_executor(
                None, bridge.read, _PTY_READ_CHUNK_TIMEOUT
            )
            if chunk is None:  # EOF
                return
            if not chunk:  # no data this tick; yield control and retry
                await asyncio.sleep(0)
                continue
            try:
                await ws.send_bytes(chunk)
            except Exception:
                return

    reader_task = asyncio.create_task(pump_pty_to_ws())

    # --- writer loop: WebSocket → PTY master ----------------------------
    try:
        while True:
            msg = await ws.receive()
            msg_type = msg.get("type")
            if msg_type == "websocket.disconnect":
                break
            raw = msg.get("bytes")
            if raw is None:
                text = msg.get("text")
                raw = text.encode("utf-8") if isinstance(text, str) else b""
            if not raw:
                continue

            # Resize escape is consumed locally, never written to the PTY.
            match = _RESIZE_RE.match(raw)
            if match and match.end() == len(raw):
                cols = int(match.group(1))
                rows = int(match.group(2))
                bridge.resize(cols=cols, rows=rows)
                continue

            bridge.write(raw)
    except WebSocketDisconnect:
        pass
    finally:
        reader_task.cancel()
        try:
            await reader_task
        except (asyncio.CancelledError, Exception):
            pass
        bridge.close()


# ---------------------------------------------------------------------------
# /api/ws — JSON-RPC WebSocket sidecar for the dashboard "Chat" tab.
#
# Drives the same `tui_gateway.dispatch` surface Ink uses over stdio, so the
# dashboard can render structured metadata (model badge, tool-call sidebar,
# slash launcher, session info) alongside the xterm.js terminal that PTY
# already paints. Both transports bind to the same session id when one is
# active, so a tool.start emitted by the agent fans out to both sinks.
# ---------------------------------------------------------------------------


@app.websocket("/api/ws")
async def gateway_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        _log_ws_reject("/api/ws", "embedded chat disabled", ws)
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        _log_ws_reject("/api/ws", "invalid session token", ws)
        await ws.close(code=4401)
        return

    if not _is_loopback_ws_client(ws):
        _log_ws_reject("/api/ws", "non-loopback client", ws)
        await ws.close(code=4403)
        return

    from tui_gateway.ws import handle_ws

    await handle_ws(ws)


# ---------------------------------------------------------------------------
# /api/pub + /api/events — chat-tab event broadcast.
#
# The PTY-side ``tui_gateway.entry`` opens /api/pub at startup (driven by
# HERMES_TUI_SIDECAR_URL set in /api/pty's PTY env) and writes every
# dispatcher emit through it.  The dashboard fans those frames out to any
# subscriber that opened /api/events on the same channel id.  This is what
# gives the React sidebar its tool-call feed without breaking the PTY
# child's stdio handshake with Ink.
# ---------------------------------------------------------------------------


@app.websocket("/api/pub")
async def pub_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await ws.close(code=4401)
        return

    client_host = ws.client.host if ws.client else ""
    if client_host and client_host not in _LOOPBACK_HOSTS:
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    try:
        while True:
            await _broadcast_event(channel, await ws.receive_text())
    except WebSocketDisconnect:
        pass


@app.websocket("/api/events")
async def events_ws(ws: WebSocket) -> None:
    if not _DASHBOARD_EMBEDDED_CHAT_ENABLED:
        await ws.close(code=4403)
        return

    token = ws.query_params.get("token", "")
    if not hmac.compare_digest(token.encode(), _SESSION_TOKEN.encode()):
        await ws.close(code=4401)
        return

    client_host = ws.client.host if ws.client else ""
    if client_host and client_host not in _LOOPBACK_HOSTS:
        await ws.close(code=4403)
        return

    channel = _channel_or_close_code(ws)
    if not channel:
        await ws.close(code=4400)
        return

    await ws.accept()

    async with _event_lock:
        _event_channels.setdefault(channel, set()).add(ws)

    try:
        while True:
            # Subscribers don't speak — the receive() just blocks until
            # disconnect so the connection stays open as long as the
            # browser holds it.
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _event_lock:
            subs = _event_channels.get(channel)

            if subs is not None:
                subs.discard(ws)

                if not subs:
                    _event_channels.pop(channel, None)


def mount_spa(application: FastAPI):
    """Mount the built SPA. Falls back to index.html for client-side routing.

    The session token is injected into index.html via a ``<script>`` tag so
    the SPA can authenticate against protected API endpoints without a
    separate (unauthenticated) token-dispensing endpoint.
    """
    if not WEB_DIST.exists():
        @application.get("/{full_path:path}")
        async def no_frontend(full_path: str):
            chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
            return HTMLResponse(
                "<!doctype html><html><head>"
                f'<script>window.__QIQICLAW_SESSION_TOKEN__="{_SESSION_TOKEN}";'
                f"window.__QIQICLAW_DASHBOARD_EMBEDDED_CHAT__={chat_js};"
                "</script></head><body>QiQiClaw backend is running.</body></html>",
                headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
            )
        return

    _index_path = WEB_DIST / "index.html"
    _NO_STORE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate"}

    def _serve_index():
        """Return index.html with the session token injected."""
        html = _index_path.read_text()
        chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
        token_script = (
            f'<script>window.__QIQICLAW_SESSION_TOKEN__="{_SESSION_TOKEN}";'
            f"window.__QIQICLAW_DASHBOARD_EMBEDDED_CHAT__={chat_js};"
            f"</script>"
        )
        html = html.replace("</head>", f"{token_script}</head>", 1)
        return HTMLResponse(
            html,
            headers=_NO_STORE_HEADERS,
        )

    @application.get("/assets/{filename:path}")
    async def serve_asset(filename: str):
        for base in (
            WEB_DIST / "assets",
            WEB_DIST / "qiqiclaw-spa" / "assets",
            WEB_DIST / "foxel-spa" / "assets",
        ):
            file_path = base / filename
            if (
                filename
                and file_path.resolve().is_relative_to(base.resolve())
                and file_path.exists()
                and file_path.is_file()
            ):
                return FileResponse(file_path, headers=_NO_STORE_HEADERS)
        raise HTTPException(status_code=404, detail="Asset not found")

    @application.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = WEB_DIST / full_path
        # Prevent path traversal via url-encoded sequences (%2e%2e/)
        if (
            full_path
            and file_path.resolve().is_relative_to(WEB_DIST.resolve())
            and file_path.exists()
            and file_path.is_file()
        ):
            return FileResponse(file_path, headers=_NO_STORE_HEADERS)
        return _serve_index()


# ---------------------------------------------------------------------------
# Dashboard theme endpoints
# ---------------------------------------------------------------------------

# Built-in dashboard themes — label + description only.  The actual color
# definitions live in the frontend (web/src/themes/presets.ts).
_BUILTIN_DASHBOARD_THEMES = [
    {"name": "default",   "label": "QiQi Teal",  "description": "Classic dark teal — the canonical QiQiClaw look"},
    {"name": "midnight",  "label": "Midnight",      "description": "Deep blue-violet with cool accents"},
    {"name": "ember",     "label": "Ember",          "description": "Warm crimson and bronze — forge vibes"},
    {"name": "mono",      "label": "Mono",           "description": "Clean grayscale — minimal and focused"},
    {"name": "cyberpunk", "label": "Cyberpunk",      "description": "Neon green on black — matrix terminal"},
    {"name": "rose",      "label": "Rosé",           "description": "Soft pink and warm ivory — easy on the eyes"},
]


def _parse_theme_layer(value: Any, default_hex: str, default_alpha: float = 1.0) -> Optional[Dict[str, Any]]:
    """Normalise a theme layer spec from YAML into `{hex, alpha}` form.

    Accepts shorthand (a bare hex string) or full dict form.  Returns
    ``None`` on garbage input so the caller can fall back to a built-in
    default rather than blowing up.
    """
    if value is None:
        return {"hex": default_hex, "alpha": default_alpha}
    if isinstance(value, str):
        return {"hex": value, "alpha": default_alpha}
    if isinstance(value, dict):
        hex_val = value.get("hex", default_hex)
        alpha_val = value.get("alpha", default_alpha)
        if not isinstance(hex_val, str):
            return None
        try:
            alpha_f = float(alpha_val)
        except (TypeError, ValueError):
            alpha_f = default_alpha
        return {"hex": hex_val, "alpha": max(0.0, min(1.0, alpha_f))}
    return None


_THEME_DEFAULT_TYPOGRAPHY: Dict[str, str] = {
    "fontSans": 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
    "fontMono": 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace',
    "baseSize": "15px",
    "lineHeight": "1.55",
    "letterSpacing": "0",
}

_THEME_DEFAULT_LAYOUT: Dict[str, str] = {
    "radius": "0.5rem",
    "density": "comfortable",
}

_THEME_OVERRIDE_KEYS = {
    "card", "cardForeground", "popover", "popoverForeground",
    "primary", "primaryForeground", "secondary", "secondaryForeground",
    "muted", "mutedForeground", "accent", "accentForeground",
    "destructive", "destructiveForeground", "success", "warning",
    "border", "input", "ring",
}

# Well-known named asset slots themes can populate.  Any other keys under
# ``assets.custom`` are exposed as ``--theme-asset-custom-<key>`` CSS vars
# for plugin/shell use.
_THEME_NAMED_ASSET_KEYS = {"bg", "hero", "logo", "crest", "sidebar", "header"}

# Component-style buckets themes can override.  The value under each bucket
# is a mapping from camelCase property name to CSS string; each pair emits
# ``--component-<bucket>-<kebab-property>`` on :root.  The frontend's shell
# components (Card, App header, Backdrop, etc.) consume these vars so themes
# can restyle chrome (clip-path, border-image, segmented progress, etc.)
# without shipping their own CSS.
_THEME_COMPONENT_BUCKETS = {
    "card", "header", "footer", "sidebar", "tab",
    "progress", "badge", "backdrop", "page",
}

_THEME_LAYOUT_VARIANTS = {"standard", "cockpit", "tiled"}

# Cap on customCSS length so a malformed/oversized theme YAML can't blow up
# the response payload or the <style> tag.  32 KiB is plenty for every
# practical reskin (the Strike Freedom demo is ~2 KiB).
_THEME_CUSTOM_CSS_MAX = 32 * 1024


def _normalise_theme_definition(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise a user theme YAML into the wire format `ThemeProvider`
    expects.  Returns ``None`` if the theme is unusable.

    Accepts both the full schema (palette/typography/layout) and a loose
    form with bare hex strings, so hand-written YAMLs stay friendly.
    """
    if not isinstance(data, dict):
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    # Palette
    palette_src = data.get("palette", {}) if isinstance(data.get("palette"), dict) else {}
    # Allow top-level `colors.background` as a shorthand too.
    colors_src = data.get("colors", {}) if isinstance(data.get("colors"), dict) else {}

    def _layer(key: str, default_hex: str, default_alpha: float = 1.0) -> Dict[str, Any]:
        spec = palette_src.get(key, colors_src.get(key))
        parsed = _parse_theme_layer(spec, default_hex, default_alpha)
        return parsed if parsed is not None else {"hex": default_hex, "alpha": default_alpha}

    palette = {
        "background": _layer("background", "#041c1c", 1.0),
        "midground": _layer("midground", "#ffe6cb", 1.0),
        "foreground": _layer("foreground", "#ffffff", 0.0),
        "warmGlow": palette_src.get("warmGlow") or data.get("warmGlow") or "rgba(255, 189, 56, 0.35)",
        "noiseOpacity": 1.0,
    }
    raw_noise = palette_src.get("noiseOpacity", data.get("noiseOpacity"))
    try:
        palette["noiseOpacity"] = float(raw_noise) if raw_noise is not None else 1.0
    except (TypeError, ValueError):
        palette["noiseOpacity"] = 1.0

    # Typography
    typo_src = data.get("typography", {}) if isinstance(data.get("typography"), dict) else {}
    typography = dict(_THEME_DEFAULT_TYPOGRAPHY)
    for key in ("fontSans", "fontMono", "fontDisplay", "fontUrl", "baseSize", "lineHeight", "letterSpacing"):
        val = typo_src.get(key)
        if isinstance(val, str) and val.strip():
            typography[key] = val

    # Layout
    layout_src = data.get("layout", {}) if isinstance(data.get("layout"), dict) else {}
    layout = dict(_THEME_DEFAULT_LAYOUT)
    radius = layout_src.get("radius")
    if isinstance(radius, str) and radius.strip():
        layout["radius"] = radius
    density = layout_src.get("density")
    if isinstance(density, str) and density in ("compact", "comfortable", "spacious"):
        layout["density"] = density

    # Color overrides — keep only valid keys with string values.
    overrides_src = data.get("colorOverrides", {})
    color_overrides: Dict[str, str] = {}
    if isinstance(overrides_src, dict):
        for key, val in overrides_src.items():
            if key in _THEME_OVERRIDE_KEYS and isinstance(val, str) and val.strip():
                color_overrides[key] = val

    # Assets — named slots + arbitrary user-defined keys.  Values must be
    # strings (URLs or CSS ``url(...)``/``linear-gradient(...)`` expressions).
    # We don't fetch remote assets here; the frontend just injects them as
    # CSS vars.  Empty values are dropped so a theme can explicitly clear a
    # slot by setting ``hero: ""``.
    assets_out: Dict[str, Any] = {}
    assets_src = data.get("assets", {}) if isinstance(data.get("assets"), dict) else {}
    for key in _THEME_NAMED_ASSET_KEYS:
        val = assets_src.get(key)
        if isinstance(val, str) and val.strip():
            assets_out[key] = val
    custom_assets_src = assets_src.get("custom")
    if isinstance(custom_assets_src, dict):
        custom_assets: Dict[str, str] = {}
        for key, val in custom_assets_src.items():
            if (
                isinstance(key, str)
                and key.replace("-", "").replace("_", "").isalnum()
                and isinstance(val, str)
                and val.strip()
            ):
                custom_assets[key] = val
        if custom_assets:
            assets_out["custom"] = custom_assets

    # Custom CSS — raw CSS text the frontend injects as a scoped <style>
    # tag on theme apply.  Clipped to _THEME_CUSTOM_CSS_MAX to keep the
    # payload bounded.  We intentionally do NOT parse/sanitise the CSS
    # here — the dashboard is localhost-only and themes are user-authored
    # YAML in ~/.qiqiclaw/, same trust level as the config file itself.
    custom_css_val = data.get("customCSS")
    custom_css: Optional[str] = None
    if isinstance(custom_css_val, str) and custom_css_val.strip():
        custom_css = custom_css_val[:_THEME_CUSTOM_CSS_MAX]

    # Component style overrides — per-bucket dicts of camelCase CSS
    # property -> CSS string.  The frontend converts these into CSS vars
    # that shell components (Card, App header, Backdrop) consume.
    component_styles_src = data.get("componentStyles", {})
    component_styles: Dict[str, Dict[str, str]] = {}
    if isinstance(component_styles_src, dict):
        for bucket, props in component_styles_src.items():
            if bucket not in _THEME_COMPONENT_BUCKETS or not isinstance(props, dict):
                continue
            clean: Dict[str, str] = {}
            for prop, value in props.items():
                if (
                    isinstance(prop, str)
                    and prop.replace("-", "").replace("_", "").isalnum()
                    and isinstance(value, (str, int, float))
                    and str(value).strip()
                ):
                    clean[prop] = str(value)
            if clean:
                component_styles[bucket] = clean

    layout_variant_src = data.get("layoutVariant")
    layout_variant = (
        layout_variant_src
        if isinstance(layout_variant_src, str) and layout_variant_src in _THEME_LAYOUT_VARIANTS
        else "standard"
    )

    result: Dict[str, Any] = {
        "name": name,
        "label": data.get("label") or name,
        "description": data.get("description", ""),
        "palette": palette,
        "typography": typography,
        "layout": layout,
        "layoutVariant": layout_variant,
    }
    if color_overrides:
        result["colorOverrides"] = color_overrides
    if assets_out:
        result["assets"] = assets_out
    if custom_css is not None:
        result["customCSS"] = custom_css
    if component_styles:
        result["componentStyles"] = component_styles
    return result


def _discover_user_themes() -> list:
    """Scan ~/.qiqiclaw/dashboard-themes/*.yaml for user-created themes.

    Returns a list of fully-normalised theme definitions ready to ship
    to the frontend, so the client can apply them without a secondary
    round-trip or a built-in stub.
    """
    themes_dir = get_qiqiclaw_home() / "dashboard-themes"
    if not themes_dir.is_dir():
        return []
    result = []
    for f in sorted(themes_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        normalised = _normalise_theme_definition(data)
        if normalised is not None:
            result.append(normalised)
    return result


@app.get("/api/dashboard/themes")
async def get_dashboard_themes():
    """Return available themes and the currently active one.

    Built-in entries ship name/label/description only (the frontend owns
    their full definitions in `web/src/themes/presets.ts`).  User themes
    from `~/.qiqiclaw/dashboard-themes/*.yaml` ship with their full
    normalised definition under `definition`, so the client can apply
    them without a stub.
    """
    config = load_config()
    active = cfg_get(config, "dashboard", "theme", default="default")
    user_themes = _discover_user_themes()
    seen = set()
    themes = []
    for t in _BUILTIN_DASHBOARD_THEMES:
        seen.add(t["name"])
        themes.append(t)
    for t in user_themes:
        if t["name"] in seen:
            continue
        themes.append({
            "name": t["name"],
            "label": t["label"],
            "description": t["description"],
            "definition": t,
        })
        seen.add(t["name"])
    return {"themes": themes, "active": active}


class ThemeSetBody(BaseModel):
    name: str


@app.put("/api/dashboard/theme")
async def set_dashboard_theme(body: ThemeSetBody):
    """Set the active dashboard theme (persists to config.yaml)."""
    config = load_config()
    if "dashboard" not in config:
        config["dashboard"] = {}
    config["dashboard"]["theme"] = body.name
    save_config(config)
    return {"ok": True, "theme": body.name}


# ---------------------------------------------------------------------------
# Dashboard plugin system
# ---------------------------------------------------------------------------

def _discover_dashboard_plugins() -> list:
    """Scan plugins/*/dashboard/manifest.json for dashboard extensions.

    Checks three plugin sources (same as qiqiclaw_cli.plugins):
    1. User plugins:    ~/.qiqiclaw/plugins/<name>/dashboard/manifest.json
    2. Bundled plugins: <repo>/plugins/<name>/dashboard/manifest.json  (memory/, etc.)
    3. Project plugins: ./.qiqiclaw/plugins/  (only if QIQICLAW_ENABLE_PROJECT_PLUGINS)
    """
    plugins = []
    seen_names: set = set()

    from qiqiclaw_cli.plugins import get_bundled_plugins_dir
    bundled_root = get_bundled_plugins_dir()
    search_dirs = [
        (get_qiqiclaw_home() / "plugins", "user"),
        (bundled_root / "memory", "bundled"),
        (bundled_root, "bundled"),
    ]
    if _legacy_env("ENABLE_PROJECT_PLUGINS", None):
        search_dirs.append((Path.cwd() / ".qiqiclaw" / "plugins", "project"))

    for plugins_root, source in search_dirs:
        if not plugins_root.is_dir():
            continue
        for child in sorted(plugins_root.iterdir()):
            if not child.is_dir():
                continue
            manifest_file = child / "dashboard" / "manifest.json"
            if not manifest_file.exists():
                continue
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                name = data.get("name", child.name)
                if name in seen_names:
                    continue
                seen_names.add(name)
                # Tab options: ``path`` + ``position`` for a new tab, optional
                # ``override`` to replace a built-in route, and ``hidden`` to
                # register the plugin component/slots without adding a tab
                # (useful for slot-only plugins like a header-crest injector).
                raw_tab = data.get("tab", {}) if isinstance(data.get("tab"), dict) else {}
                tab_info = {
                    "path": raw_tab.get("path", f"/{name}"),
                    "position": raw_tab.get("position", "end"),
                }
                override_path = raw_tab.get("override")
                if isinstance(override_path, str) and override_path.startswith("/"):
                    tab_info["override"] = override_path
                if bool(raw_tab.get("hidden")):
                    tab_info["hidden"] = True
                # Slots: list of named slot locations this plugin populates.
                # The frontend exposes ``registerSlot(pluginName, slotName, Component)``
                # on window; plugins with non-empty slots call it from their JS bundle.
                slots_src = data.get("slots")
                slots: List[str] = []
                if isinstance(slots_src, list):
                    slots = [s for s in slots_src if isinstance(s, str) and s]
                plugins.append({
                    "name": name,
                    "label": data.get("label", name),
                    "description": data.get("description", ""),
                    "icon": data.get("icon", "Puzzle"),
                    "version": data.get("version", "0.0.0"),
                    "tab": tab_info,
                    "slots": slots,
                    "entry": data.get("entry", "dist/index.js"),
                    "css": data.get("css"),
                    "has_api": bool(data.get("api")),
                    "source": source,
                    "_dir": str(child / "dashboard"),
                    "_api_file": data.get("api"),
                })
            except Exception as exc:
                _log.warning("Bad dashboard plugin manifest %s: %s", manifest_file, exc)
                continue
    return plugins


# Cache discovered plugins per-process (refresh on explicit re-scan).
_dashboard_plugins_cache: Optional[list] = None


def _get_dashboard_plugins(force_rescan: bool = False) -> list:
    global _dashboard_plugins_cache
    if _dashboard_plugins_cache is None or force_rescan:
        _dashboard_plugins_cache = _discover_dashboard_plugins()
    return _dashboard_plugins_cache


@app.get("/api/dashboard/plugins")
async def get_dashboard_plugins():
    """Return discovered dashboard plugins."""
    plugins = _get_dashboard_plugins()
    # Strip internal fields before sending to frontend.
    return [
        {k: v for k, v in p.items() if not k.startswith("_")}
        for p in plugins
    ]


@app.get("/api/dashboard/plugins/rescan")
async def rescan_dashboard_plugins():
    """Force re-scan of dashboard plugins."""
    plugins = _get_dashboard_plugins(force_rescan=True)
    return {"ok": True, "count": len(plugins)}


@app.get("/dashboard-plugins/{plugin_name}/{file_path:path}")
async def serve_plugin_asset(plugin_name: str, file_path: str):
    """Serve static assets from a dashboard plugin directory.

    Only serves files from the plugin's ``dashboard/`` subdirectory.
    Path traversal is blocked by checking ``resolve().is_relative_to()``.
    """
    plugins = _get_dashboard_plugins()
    plugin = next((p for p in plugins if p["name"] == plugin_name), None)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件未找到")

    base = Path(plugin["_dir"])
    target = (base / file_path).resolve()

    if not target.is_relative_to(base.resolve()):
        raise HTTPException(status_code=403, detail="路径遍历被阻止")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="文件未找到")

    # Guess content type
    suffix = target.suffix.lower()
    content_types = {
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".html": "text/html",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
    }
    media_type = content_types.get(suffix, "application/octet-stream")
    return FileResponse(target, media_type=media_type)


def _mount_plugin_api_routes():
    """Import and mount backend API routes from plugins that declare them.

    Each plugin's ``api`` field points to a Python file that must expose
    a ``router`` (FastAPI APIRouter).  Routes are mounted under
    ``/api/plugins/<name>/``.
    """
    for plugin in _get_dashboard_plugins():
        api_file_name = plugin.get("_api_file")
        if not api_file_name:
            continue
        api_path = Path(plugin["_dir"]) / api_file_name
        if not api_path.exists():
            _log.warning("Plugin %s declares api=%s but file not found", plugin["name"], api_file_name)
            continue
        try:
            module_name = f"hermes_dashboard_plugin_{plugin['name']}"
            spec = importlib.util.spec_from_file_location(module_name, api_path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            # Register in sys.modules BEFORE exec_module so pydantic/FastAPI
            # can resolve forward references (e.g. models defined in a file
            # that uses `from __future__ import annotations`). Without this,
            # TypeAdapter lazy-build fails at first request with
            # "is not fully defined" because the module namespace isn't
            # reachable by name for string-annotation resolution.
            sys.modules[module_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception:
                sys.modules.pop(module_name, None)
                raise
            router = getattr(mod, "router", None)
            if router is None:
                _log.warning("Plugin %s api file has no 'router' attribute", plugin["name"])
                continue
            app.include_router(router, prefix=f"/api/plugins/{plugin['name']}")
            _log.info("Mounted plugin API routes: /api/plugins/%s/", plugin["name"])
        except Exception as exc:
            _log.warning("Failed to load plugin %s API routes: %s", plugin["name"], exc)


# Mount plugin API routes before the SPA catch-all.
_mount_plugin_api_routes()

# ---------------------------------------------------------------------------
# QiQiClaw file modules (mounted before mount_spa catch-all)
# ---------------------------------------------------------------------------
def _mount_file_modules():
    try:
        from qiqiclaw_cli.file_modules_backend import FileModuleBackend

        FileModuleBackend(
            app=app,
            web_dist=WEB_DIST,
            session_token=_SESSION_TOKEN,
            embedded_chat_enabled=_DASHBOARD_EMBEDDED_CHAT_ENABLED,
            api_prefixes=_FILE_MODULE_API_PREFIXES,
        ).mount()
        return
    except Exception:
        _log.exception("QiQiClaw file-module backend framework failed")
        return

    import os as _os
    import sys as _sys

    _file_module_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _file_module_root not in _sys.path:
        _sys.path.insert(0, _file_module_root)

    try:
        from db.session import init_db as _file_module_init_db, close_db as _file_module_close_db
        from api.routers import include_routers as _file_module_include_routers
        from domain.adapters import runtime_registry as _file_module_runtime_registry
        from domain.config import ConfigService as _FileModuleConfigService, VERSION as _FILE_MODULE_VERSION
        from domain.tasks import (
            task_queue_service as _file_module_task_queue,
            task_scheduler as _file_module_task_scheduler,
        )
        from domain.plugins import init_plugins as _file_module_init_plugins
        from domain.agent.mcp import MCP_HTTP_APP as _file_module_mcp_app
        from middleware.exception_handler import (
            http_exception_handler as _file_module_http_eh,
            httpx_exception_handler as _file_module_httpx_eh,
            validation_exception_handler as _file_module_validation_eh,
        )
        from fastapi import HTTPException as _FileModuleHTTPException
        from fastapi.exceptions import RequestValidationError as _FileModuleReqValErr
        from starlette.exceptions import HTTPException as _StarletteHTTPException
        import httpx as _httpx
    except Exception as exc:
        _log.warning("QiQiClaw file modules disabled (import failed): %s", exc)
        return

    _file_module_routes_before = {id(r) for r in app.routes}
    _file_module_include_routers(app)

    @app.api_route("/api/mcp", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"], include_in_schema=False)
    async def _file_module_mcp_redirect():
        return RedirectResponse(url="/api/mcp/", status_code=307)

    app.mount("/api/mcp", _file_module_mcp_app, name="qiqiclaw_file_modules_mcp")

    # qiqiclaw-owned prefixes that must keep their session-token auth even
    # though the file modules also register routes under the same group (e.g.
    # /api/config and /api/tasks). For these prefixes, we register the exact
    # file-module paths instead of the whole group.
    _QIQICLAW_OWNED_GROUPS = {
        "/api/actions", "/api/analytics", "/api/auth", "/api/config",
        "/api/cron", "/api/dashboard", "/api/disk", "/api/env",
        "/api/events", "/api/gateway", "/api/logs", "/api/me",
        "/api/model", "/api/profiles", "/api/providers", "/api/pty",
        "/api/pub", "/api/qiqiclaw", "/api/s", "/api/sessions",
        "/api/skills", "/api/status", "/api/tasks", "/api/tools",
        "/api/ws",
    }
    _file_module_seen_groups: set = set()
    _file_module_exact_paths: set = set()
    for _r in app.routes:
        if id(_r) in _file_module_routes_before:
            continue
        _rp = getattr(_r, "path", None)
        if not _rp or not _rp.startswith("/api/"):
            continue
        _seg = _rp.split("/")
        if len(_seg) >= 3 and _seg[2]:
            _grp = "/api/" + _seg[2]
            if _grp in _QIQICLAW_OWNED_GROUPS:
                _file_module_exact_paths.add(_rp)
            else:
                _file_module_seen_groups.add(_grp)
    _FILE_MODULE_API_PREFIXES.update(_file_module_seen_groups)
    _FILE_MODULE_API_PREFIXES.update(_file_module_exact_paths)
    _log.info(
        "QiQiClaw file-module API auth-bypass: %d group prefixes, %d exact paths",
        len(_file_module_seen_groups), len(_file_module_exact_paths),
    )

    _file_module_spa_dir = WEB_DIST / "qiqiclaw-spa"
    _legacy_foxel_spa_dir = WEB_DIST / "foxel-spa"
    if _file_module_spa_dir.is_dir() or _legacy_foxel_spa_dir.is_dir():
        from starlette.staticfiles import StaticFiles as _FileModuleStaticFiles

        class _FileModuleSPAStaticFiles(_FileModuleStaticFiles):
            def _serve_index_with_session_token(self, directory: str):
                index_path = Path(directory) / "index.html"
                if not index_path.is_file():
                    return None
                html = index_path.read_text(encoding="utf-8")
                chat_js = "true" if _DASHBOARD_EMBEDDED_CHAT_ENABLED else "false"
                token_script = (
                    f'<script>window.__QIQICLAW_SESSION_TOKEN__="{_SESSION_TOKEN}";'
                    f"window.__QIQICLAW_DASHBOARD_EMBEDDED_CHAT__={chat_js};"
                    f"</script>"
                )
                html = html.replace("</head>", f"{token_script}</head>", 1)
                return HTMLResponse(
                    html,
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
                )

            async def get_response(self, path, scope):
                if scope.get("method") in {"GET", "HEAD"} and path in {"", ".", "index.html"}:
                    response = self._serve_index_with_session_token(self.directory)
                    if response is not None:
                        return response
                try:
                    response = await super().get_response(path, scope)
                except _StarletteHTTPException as exc:
                    if exc.status_code != 404:
                        raise
                    if scope.get("method") in {"GET", "HEAD"}:
                        response = self._serve_index_with_session_token(self.directory)
                        if response is not None:
                            return response
                        return await super().get_response("index.html", scope)
                    raise
                if response.status_code == 404 and scope.get("method") in {"GET", "HEAD"}:
                    token_response = self._serve_index_with_session_token(self.directory)
                    if token_response is not None:
                        return token_response
                    return await super().get_response("index.html", scope)
                return response

    if _file_module_spa_dir.is_dir():
        @app.api_route(
            "/qiqiclaw-spa/plugin-frame",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        @app.api_route(
            "/qiqiclaw-spa/plugin-frame/",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        async def _file_module_plugin_frame_alias():
            plugin_frame = _file_module_spa_dir / "plugin-frame.html"
            if plugin_frame.is_file():
                return FileResponse(plugin_frame)
            raise HTTPException(status_code=404, detail="Plugin frame not found")

        app.mount(
            "/qiqiclaw-spa",
            _FileModuleSPAStaticFiles(directory=str(_file_module_spa_dir), html=True),
            name="qiqiclaw_file_modules_spa",
        )

    if _legacy_foxel_spa_dir.is_dir():
        @app.api_route(
            "/plugin-frame.html",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        @app.api_route(
            "/plugin-frame",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        @app.api_route(
            "/foxel-spa/plugin-frame",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        @app.api_route(
            "/foxel-spa/plugin-frame/",
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        async def _legacy_foxel_plugin_frame_alias():
            plugin_frame = _legacy_foxel_spa_dir / "plugin-frame.html"
            if plugin_frame.is_file():
                return FileResponse(
                    plugin_frame,
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
                )
            raise HTTPException(status_code=404, detail="Legacy plugin frame not found")

        app.mount(
            "/foxel-spa",
            _FileModuleSPAStaticFiles(directory=str(_legacy_foxel_spa_dir), html=True),
            name="qiqiclaw_legacy_foxel_spa",
        )
    elif _file_module_spa_dir.is_dir():
        _legacy_spa_prefix = "/" + "fox" + "el-spa"

        @app.api_route(_legacy_spa_prefix, methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route(_legacy_spa_prefix + "/{_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def _legacy_file_module_spa_redirect(request: Request, _path: str = ""):
            if not _path:
                nav = str(request.query_params.get("nav") or "").strip().lower()
                legacy_nav_routes = {
                    "files": "files/local",
                    "plugins": "plugins",
                    "processors": "processors",
                    "adapters": "adapters",
                }
                _path = legacy_nav_routes.get(nav, "")
            suffix = f"/{_path}" if _path else ""
            return RedirectResponse(url=f"/qiqiclaw-spa{suffix}", status_code=307)

    app.add_exception_handler(_FileModuleHTTPException, _file_module_http_eh)
    app.add_exception_handler(_StarletteHTTPException, _file_module_http_eh)
    app.add_exception_handler(_FileModuleReqValErr, _file_module_validation_eh)
    app.add_exception_handler(_httpx.HTTPStatusError, _file_module_httpx_eh)

    @app.on_event("startup")
    async def _file_module_startup():
        try:
            _data_dir = (
                _os.environ.get("QIQICLAW_FILE_MODULE_DATA_DIR")
                or _os.path.expanduser("~/.qiqiclaw/file-modules")
            )
            _os.makedirs(_os.path.join(_data_dir, "db"), exist_ok=True)
            _os.makedirs(_os.path.join(_data_dir, "plugins"), exist_ok=True)
            _mcp_lifespan_factory = getattr(getattr(_file_module_mcp_app, "router", None), "lifespan_context", None)
            if callable(_mcp_lifespan_factory) and not hasattr(app.state, "qiqiclaw_file_module_mcp_lifespan"):
                _mcp_lifespan_cm = _mcp_lifespan_factory(_file_module_mcp_app)
                await _mcp_lifespan_cm.__aenter__()
                app.state.qiqiclaw_file_module_mcp_lifespan = _mcp_lifespan_cm
            await _file_module_init_db()
            from models import StorageAdapter as _FileModuleStorageAdapter

            _home_dir = _os.path.expanduser("~")
            _local = await _FileModuleStorageAdapter.get_or_none(path="/local")
            if _local is None:
                _local = await _FileModuleStorageAdapter.get_or_none(name="Local Home")
            if _local is None:
                _local = await _FileModuleStorageAdapter.create(
                    name="Local Home",
                    type="local",
                    config={"root": _home_dir},
                    enabled=True,
                    path="/local",
                    sub_path=None,
                )
            else:
                _name_owner = await _FileModuleStorageAdapter.get_or_none(name="Local Home")
                if _name_owner is None or _name_owner.id == _local.id:
                    _local.name = "Local Home"
                _local.type = "local"
                _local.config = {"root": _home_dir}
                _local.enabled = True
                _local.path = "/local"
                _local.sub_path = None
                await _local.save()
            await _file_module_runtime_registry.refresh()
            await _FileModuleConfigService.set("APP_VERSION", _FILE_MODULE_VERSION)
            await _file_module_task_queue.start_worker()
            await _file_module_init_plugins(app)
            await _file_module_task_scheduler.start()
            _log.info("QiQiClaw file modules started (data dir=%s)", _data_dir)
        except Exception:
            _log.exception("QiQiClaw file-module startup failed")

    @app.on_event("shutdown")
    async def _file_module_shutdown():
        try:
            try:
                await _file_module_task_scheduler.stop()
            except Exception:
                pass
            try:
                await _file_module_task_queue.stop_worker()
            except Exception:
                pass
            try:
                await _file_module_close_db()
            except Exception:
                pass
            _mcp_lifespan_cm = getattr(app.state, "qiqiclaw_file_module_mcp_lifespan", None)
            if _mcp_lifespan_cm is not None:
                try:
                    await _mcp_lifespan_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    delattr(app.state, "qiqiclaw_file_module_mcp_lifespan")
                except Exception:
                    pass
        except Exception:
            _log.exception("QiQiClaw file-module shutdown failed")


_mount_file_modules()


@app.api_route(
    "/api/auth/{_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/users/{_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/roles/{_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/permissions/{_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/notices/{_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/notices",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/auth",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/users",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/roles",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
@app.api_route(
    "/api/permissions",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    include_in_schema=False,
)
async def _removed_legacy_user_api(_path: str = ""):
    raise HTTPException(status_code=404, detail="Legacy user, role, permission, and notice APIs are removed")

mount_spa(app)


def start_server(
    host: str = "127.0.0.1",
    port: int = 9119,
    open_browser: bool = True,
    allow_public: bool = False,
    *,
    embedded_chat: bool = False,
):
    """Start the web UI server."""
    import uvicorn

    global _DASHBOARD_EMBEDDED_CHAT_ENABLED
    _DASHBOARD_EMBEDDED_CHAT_ENABLED = embedded_chat

    _LOCALHOST = ("127.0.0.1", "localhost", "::1")
    if host not in _LOCALHOST and not allow_public:
        raise SystemExit(
            f"Refusing to bind to {host} — the dashboard exposes API keys "
            f"and config without robust authentication.\n"
            f"Use --insecure to override (NOT recommended on untrusted networks)."
        )
    if host not in _LOCALHOST:
        _log.warning(
            "Binding to %s with --insecure — the dashboard has no robust "
            "authentication. Only use on trusted networks.", host,
        )

    # Record the bound host so host_header_middleware can validate incoming
    # Host headers against it. Defends against DNS rebinding (GHSA-ppp5-vxwm-4cf7).
    # bound_port is also stashed so /api/pty can build the back-WS URL the
    # PTY child uses to publish events to the dashboard sidebar.
    app.state.bound_host = host
    app.state.bound_port = port

    if open_browser:
        import webbrowser

        def _open():
            time.sleep(1.0)
            webbrowser.open(f"http://{host}:{port}")

        threading.Thread(target=_open, daemon=True).start()

    print(f"  QiQiClaw Web UI → http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
