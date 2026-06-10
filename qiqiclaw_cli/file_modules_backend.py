"""
QiQiClaw desktop file-module backend framework.

This module adapts the Foxel file/app/processor/storage backend into the
QiQiClaw dashboard process.  It keeps the integration points explicit:
database/runtime lifecycle, route registration, auth-bypass discovery and SPA
serving.  The goal is to make the four migrated modules behave like a coherent
QiQiClaw-owned service instead of a loose collection of mounted routes.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles

_LOG = logging.getLogger(__name__)


@dataclass
class FileModuleBackend:
    app: FastAPI
    web_dist: Path
    session_token: str
    embedded_chat_enabled: bool = True
    api_prefixes: set[str] = field(default_factory=set)
    project_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])

    def ensure_import_path(self) -> None:
        root = str(self.project_root)
        if root not in sys.path:
            sys.path.insert(0, root)

    @property
    def data_dir(self) -> Path:
        configured = os.environ.get("QIQICLAW_FILE_MODULE_DATA_DIR")
        return Path(configured).expanduser() if configured else Path.home() / ".qiqiclaw" / "file-modules"

    def prepare_environment(self) -> None:
        self.ensure_import_path()
        self.data_dir.joinpath("db").mkdir(parents=True, exist_ok=True)
        self.data_dir.joinpath("plugins").mkdir(parents=True, exist_ok=True)

    def mount(self) -> bool:
        self.prepare_environment()
        try:
            imports = self._import_runtime()
        except Exception as exc:
            _LOG.warning("QiQiClaw file modules disabled (import failed): %s", exc)
            return False

        routes_before = {id(r) for r in self.app.routes}
        imports["include_routers"](self.app)
        self._collect_auth_bypass_prefixes(routes_before)
        self._mount_mcp(imports["mcp_app"])
        self._mount_spas()
        self._install_exception_handlers(imports)
        self._install_lifecycle(imports)
        return True

    def _import_runtime(self) -> dict[str, Any]:
        from db.session import close_db, init_db
        from api.routers import include_routers
        from domain.adapters import runtime_registry
        from domain.config import ConfigService, VERSION
        from domain.plugins import init_plugins
        from domain.tasks import task_queue_service, task_scheduler
        from domain.agent.mcp import MCP_HTTP_APP
        from middleware.exception_handler import (
            http_exception_handler,
            httpx_exception_handler,
            validation_exception_handler,
        )
        from fastapi.exceptions import RequestValidationError
        import httpx

        return {
            "close_db": close_db,
            "init_db": init_db,
            "include_routers": include_routers,
            "runtime_registry": runtime_registry,
            "ConfigService": ConfigService,
            "VERSION": VERSION,
            "init_plugins": init_plugins,
            "task_queue_service": task_queue_service,
            "task_scheduler": task_scheduler,
            "mcp_app": MCP_HTTP_APP,
            "http_exception_handler": http_exception_handler,
            "httpx_exception_handler": httpx_exception_handler,
            "validation_exception_handler": validation_exception_handler,
            "RequestValidationError": RequestValidationError,
            "httpx": httpx,
        }

    def _collect_auth_bypass_prefixes(self, routes_before: set[int]) -> None:
        owned_groups = {
            "/api/actions", "/api/analytics", "/api/auth", "/api/config",
            "/api/cron", "/api/dashboard", "/api/disk", "/api/env",
            "/api/events", "/api/gateway", "/api/logs", "/api/me",
            "/api/model", "/api/profiles", "/api/providers", "/api/pty",
            "/api/pub", "/api/qiqiclaw", "/api/s", "/api/sessions",
            "/api/skills", "/api/status", "/api/tasks", "/api/tools",
            "/api/ws",
        }
        group_prefixes: set[str] = set()
        exact_paths: set[str] = set()

        for route in self.app.routes:
            if id(route) in routes_before:
                continue
            path = getattr(route, "path", None)
            if not path or not path.startswith("/api/"):
                continue
            parts = path.split("/")
            if len(parts) < 3 or not parts[2]:
                continue
            group = "/api/" + parts[2]
            if group in owned_groups:
                exact_paths.add(path)
            else:
                group_prefixes.add(group)

        self.api_prefixes.update(group_prefixes)
        self.api_prefixes.update(exact_paths)
        _LOG.info(
            "QiQiClaw file-module API auth-bypass: %d group prefixes, %d exact paths",
            len(group_prefixes),
            len(exact_paths),
        )

    def _mount_mcp(self, mcp_app: Any) -> None:
        self.api_prefixes.add("/api/mcp")

        @self.app.api_route(
            "/api/mcp",
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
            include_in_schema=False,
        )
        async def _file_module_mcp_redirect():
            return RedirectResponse(url="/api/mcp/", status_code=307)

        self.app.mount("/api/mcp", mcp_app, name="qiqiclaw_file_modules_mcp")

    def _mount_spas(self) -> None:
        qiqiclaw_spa_dir = self.web_dist / "qiqiclaw-spa"
        legacy_foxel_spa_dir = self.web_dist / "foxel-spa"

        spa_cls = self._spa_static_class()

        if qiqiclaw_spa_dir.is_dir():
            self._mount_plugin_frame_aliases(
                qiqiclaw_spa_dir,
                "/qiqiclaw-spa/plugin-frame",
                "/qiqiclaw-spa/plugin-frame/",
                missing="Plugin frame not found",
            )
            self.app.mount(
                "/qiqiclaw-spa",
                spa_cls(directory=str(qiqiclaw_spa_dir), html=True),
                name="qiqiclaw_file_modules_spa",
            )

        if legacy_foxel_spa_dir.is_dir():
            self._mount_plugin_frame_aliases(
                legacy_foxel_spa_dir,
                "/plugin-frame.html",
                "/plugin-frame",
                "/foxel-spa/plugin-frame",
                "/foxel-spa/plugin-frame/",
                missing="Legacy plugin frame not found",
            )
            self.app.mount(
                "/foxel-spa",
                spa_cls(directory=str(legacy_foxel_spa_dir), html=True),
                name="qiqiclaw_legacy_foxel_spa",
            )
        elif qiqiclaw_spa_dir.is_dir():
            self._mount_legacy_spa_redirect()

    def _spa_static_class(self):
        session_token = self.session_token
        chat_js = "true" if self.embedded_chat_enabled else "false"

        class FileModuleSPAStaticFiles(StaticFiles):
            def _serve_index_with_session_token(self, directory: str):
                index_path = Path(directory) / "index.html"
                if not index_path.is_file():
                    return None
                html = index_path.read_text(encoding="utf-8")
                token_script = (
                    f'<script>window.__QIQICLAW_SESSION_TOKEN__="{session_token}";'
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
                except StarletteHTTPException as exc:
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

        return FileModuleSPAStaticFiles

    def _mount_plugin_frame_aliases(self, spa_dir: Path, *paths: str, missing: str) -> None:
        async def _plugin_frame_alias():
            plugin_frame = spa_dir / "plugin-frame.html"
            if plugin_frame.is_file():
                return FileResponse(
                    plugin_frame,
                    headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
                )
            raise HTTPException(status_code=404, detail=missing)

        for path in paths:
            self.app.add_api_route(
                path,
                _plugin_frame_alias,
                methods=["GET", "HEAD"],
                include_in_schema=False,
            )

    def _mount_legacy_spa_redirect(self) -> None:
        legacy_spa_prefix = "/foxel-spa"

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

        self.app.add_api_route(
            legacy_spa_prefix,
            _legacy_file_module_spa_redirect,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )
        self.app.add_api_route(
            legacy_spa_prefix + "/{_path:path}",
            _legacy_file_module_spa_redirect,
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    def _install_exception_handlers(self, imports: dict[str, Any]) -> None:
        self.app.add_exception_handler(HTTPException, imports["http_exception_handler"])
        self.app.add_exception_handler(StarletteHTTPException, imports["http_exception_handler"])
        self.app.add_exception_handler(imports["RequestValidationError"], imports["validation_exception_handler"])
        self.app.add_exception_handler(imports["httpx"].HTTPStatusError, imports["httpx_exception_handler"])

    def _install_lifecycle(self, imports: dict[str, Any]) -> None:
        @self.app.on_event("startup")
        async def _file_module_startup():
            try:
                self.prepare_environment()
                mcp_app = imports["mcp_app"]
                lifespan_factory = getattr(getattr(mcp_app, "router", None), "lifespan_context", None)
                if callable(lifespan_factory) and not hasattr(self.app.state, "qiqiclaw_file_module_mcp_lifespan"):
                    lifespan_cm = lifespan_factory(mcp_app)
                    await lifespan_cm.__aenter__()
                    self.app.state.qiqiclaw_file_module_mcp_lifespan = lifespan_cm

                await imports["init_db"]()
                await self._ensure_local_home_adapter()
                await imports["runtime_registry"].refresh()
                await imports["ConfigService"].set("APP_VERSION", imports["VERSION"])
                await imports["task_queue_service"].start_worker()
                await imports["init_plugins"](self.app)
                await imports["task_scheduler"].start()
                _LOG.info("QiQiClaw file modules started (data dir=%s)", self.data_dir)
            except Exception:
                _LOG.exception("QiQiClaw file-module startup failed")

        @self.app.on_event("shutdown")
        async def _file_module_shutdown():
            try:
                await imports["task_scheduler"].stop()
            except Exception:
                pass
            try:
                await imports["task_queue_service"].stop_worker()
            except Exception:
                pass
            try:
                await imports["close_db"]()
            except Exception:
                pass
            lifespan_cm = getattr(self.app.state, "qiqiclaw_file_module_mcp_lifespan", None)
            if lifespan_cm is not None:
                try:
                    await lifespan_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    delattr(self.app.state, "qiqiclaw_file_module_mcp_lifespan")
                except Exception:
                    pass

    async def _ensure_local_home_adapter(self) -> None:
        from models import StorageAdapter

        home_dir = os.path.expanduser("~")
        local = await StorageAdapter.get_or_none(path="/local")
        if local is None:
            local = await StorageAdapter.get_or_none(name="Local Home")
        if local is None:
            await StorageAdapter.create(
                name="Local Home",
                type="local",
                config={"root": home_dir},
                enabled=True,
                path="/local",
                sub_path=None,
            )
            return

        name_owner = await StorageAdapter.get_or_none(name="Local Home")
        if name_owner is None or name_owner.id == local.id:
            local.name = "Local Home"
        local.type = "local"
        local.config = {"root": home_dir}
        local.enabled = True
        local.path = "/local"
        local.sub_path = None
        await local.save()
