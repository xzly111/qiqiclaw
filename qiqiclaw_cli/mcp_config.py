"""
MCP Server Management CLI — ``qiqiclaw mcp`` subcommand.

Implements ``qiqiclaw mcp add/remove/list/test/configure`` for interactive
MCP server lifecycle management (issue #690 Phase 2).

Relies on tools/mcp_tool.py for connection/discovery and keeps
configuration in ~/.qiqiclaw/config.yaml under the ``mcp_servers`` key.
"""

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from qiqiclaw_cli.config import (
    cfg_get,
    load_config,
    save_config,
    get_env_value,
    save_env_value,
    get_qiqiclaw_home,  # noqa: F401 — used by test mocks
)
from qiqiclaw_cli.colors import Colors, color
from qiqiclaw_constants import display_qiqiclaw_home

logger = logging.getLogger(__name__)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


_MCP_PRESETS: Dict[str, Dict[str, Any]] = {}


# ─── UI Helpers ───────────────────────────────────────────────────────────────

def _info(text: str):
    print(color(f"  {text}", Colors.DIM))

def _success(text: str):
    print(color(f"  ✓ {text}", Colors.GREEN))

def _warning(text: str):
    print(color(f"  ⚠ {text}", Colors.YELLOW))

def _error(text: str):
    print(color(f"  ✗ {text}", Colors.RED))


def _confirm(question: str, default: bool = True) -> bool:
    default_str = "Y/n" if default else "y/N"
    try:
        val = input(color(f"  {question} [{default_str}]: ", Colors.YELLOW)).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if not val:
        return default
    return val in ("y", "yes")


def _prompt(question: str, *, password: bool = False, default: str = "") -> str:
    from qiqiclaw_cli.cli_output import prompt as _shared_prompt
    return _shared_prompt(question, default=default, password=password)


# ─── Config Helpers ───────────────────────────────────────────────────────────

def _get_mcp_servers(config: Optional[dict] = None) -> Dict[str, dict]:
    """Return the ``mcp_servers`` dict from config, or empty dict."""
    if config is None:
        config = load_config()
    servers = config.get("mcp_servers")
    if not servers or not isinstance(servers, dict):
        return {}
    return servers


def _save_mcp_server(name: str, server_config: dict):
    """Add or update a server entry in config.yaml."""
    config = load_config()
    config.setdefault("mcp_servers", {})[name] = server_config
    save_config(config)


def _remove_mcp_server(name: str) -> bool:
    """Remove a server from config.yaml.  Returns True if it existed."""
    config = load_config()
    servers = config.get("mcp_servers", {})
    if name not in servers:
        return False
    del servers[name]
    if not servers:
        config.pop("mcp_servers", None)
    save_config(config)
    return True


def _env_key_for_server(name: str) -> str:
    """Convert server name to an env-var key like ``MCP_MYSERVER_API_KEY``."""
    return f"MCP_{name.upper().replace('-', '_')}_API_KEY"


def _parse_env_assignments(raw_env: Optional[List[str]]) -> Dict[str, str]:
    """Parse ``KEY=VALUE`` strings from CLI args into an env dict."""
    parsed: Dict[str, str] = {}
    for item in raw_env or []:
        text = str(item or "").strip()
        if not text:
            continue
        if "=" not in text:
            raise ValueError(f"Invalid --env value '{text}' (expected KEY=VALUE)")
        key, value = text.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid --env value '{text}' (missing variable name)")
        if not _ENV_VAR_NAME_RE.match(key):
            raise ValueError(f"Invalid --env variable name '{key}'")
        parsed[key] = value
    return parsed


def _apply_mcp_preset(
    name: str,
    *,
    preset_name: Optional[str],
    url: Optional[str],
    command: Optional[str],
    cmd_args: List[str],
    server_config: Dict[str, Any],
) -> tuple[Optional[str], Optional[str], List[str], bool]:
    """Apply a known MCP preset when transport details were omitted."""
    if not preset_name:
        return url, command, cmd_args, False

    preset = _MCP_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown MCP preset: {preset_name}")

    if url or command:
        return url, command, cmd_args, False

    url = preset.get("url")
    command = preset.get("command")
    cmd_args = list(preset.get("args") or [])

    if url:
        server_config["url"] = url
    if command:
        server_config["command"] = command
    if cmd_args:
        server_config["args"] = cmd_args

    return url, command, cmd_args, True


# ─── Discovery (temporary connect) ───────────────────────────────────────────

def _probe_single_server(
    name: str, config: dict, connect_timeout: float = 30
) -> List[Tuple[str, str]]:
    """Temporarily connect to one MCP server, list its tools, disconnect.

    Returns list of ``(tool_name, description)`` tuples.
    Raises on connection failure.
    """
    from tools.mcp_tool import (
        _ensure_mcp_loop,
        _run_on_mcp_loop,
        _connect_server,
        _stop_mcp_loop,
    )

    _ensure_mcp_loop()

    tools_found: List[Tuple[str, str]] = []

    async def _probe():
        server = await asyncio.wait_for(
            _connect_server(name, config), timeout=connect_timeout
        )
        for t in server._tools:
            desc = getattr(t, "description", "") or ""
            # Truncate long descriptions for display
            if len(desc) > 80:
                desc = desc[:77] + "..."
            tools_found.append((t.name, desc))
        await server.shutdown()

    try:
        _run_on_mcp_loop(_probe(), timeout=connect_timeout + 10)
    except BaseException as exc:
        raise _unwrap_exception_group(exc) from None
    finally:
        _stop_mcp_loop()

    return tools_found


def _unwrap_exception_group(exc: BaseException) -> Exception:
    """Extract the root-cause exception from anyio TaskGroup wrappers.

    The MCP SDK uses anyio task groups, which wrap errors in
    ``BaseExceptionGroup`` / ``ExceptionGroup``.  This makes error
    messages opaque ("unhandled errors in a TaskGroup").  We unwrap
    to surface the real cause (e.g. "401 Unauthorized").
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    # Return a plain Exception so callers can catch normally
    if isinstance(exc, Exception):
        return exc
    return RuntimeError(str(exc))


# ─── qiqiclaw mcp add ──────────────────────────────────────────────────────────

def cmd_mcp_add(args):
    """Add a new MCP server with discovery-first tool selection."""
    name = args.name
    url = getattr(args, "url", None)
    command = getattr(args, "command", None)
    cmd_args = getattr(args, "args", None) or []
    auth_type = getattr(args, "auth", None)
    preset_name = getattr(args, "preset", None)
    raw_env = getattr(args, "env", None)

    server_config: Dict[str, Any] = {}
    try:
        explicit_env = _parse_env_assignments(raw_env)
        url, command, cmd_args, _preset_applied = _apply_mcp_preset(
            name,
            preset_name=preset_name,
            url=url,
            command=command,
            cmd_args=list(cmd_args),
            server_config=server_config,
        )
    except ValueError as exc:
        _error(str(exc))
        return

    if url and explicit_env:
        _error("--env is only supported for stdio MCP servers (--command or stdio preset)")
        return

    # Validate transport
    if not url and not command:
        _error("Must specify --url <endpoint>, --command <command>, or --preset <name>")
        _info("示例:")
        _info('  qiqiclaw mcp add ink --url "https://mcp.ml.ink/mcp"')
        _info('  qiqiclaw mcp add github --command npx --args @modelcontextprotocol/server-github')
        _info('  qiqiclaw mcp add myserver --preset mypreset')
        return

    # Check if server already exists
    existing = _get_mcp_servers()
    if name in existing:
        if not _confirm(f"服务器 '{name}' 已存在。是否覆盖?", default=False):
            _info("已取消。")
            return

    # Build initial config
    if url:
        server_config["url"] = url
    else:
        server_config["command"] = command
        if cmd_args:
            server_config["args"] = cmd_args
        if explicit_env:
            server_config["env"] = explicit_env


    # ── Authentication ────────────────────────────────────────────────

    if url and auth_type == "oauth":
        print()
        _info(f"正在为 '{name}' 启动 OAuth 流程...")
        oauth_ok = False
        try:
            from tools.mcp_oauth_manager import get_manager
            oauth_auth = get_manager().get_or_build_provider(name, url, None)
            if oauth_auth:
                server_config["auth"] = "oauth"
                _success("OAuth 已配置 (令牌将在首次连接时获取)")
                oauth_ok=True
            else:
                _warning("OAuth 设置失败 — MCP SDK 认证模块不可用")
        except Exception as exc:
            _warning(f"OAuth 错误: {exc}")

        if not oauth_ok:
            _info("此服务器可能不支持 OAuth。")
            if _confirm("是否继续而不进行身份验证?", default=True):
                # Don't store auth: oauth — server doesn't support it
                pass
            else:
                _info("已取消。")
                return

    elif url:
        # Prompt for API key / Bearer token for HTTP servers
        print()
        _info(f"正在连接到 {url}")
        needs_auth = _confirm("此服务器是否需要身份验证?", default=True)
        if needs_auth:
            if auth_type == "header" or not auth_type:
                env_key = _env_key_for_server(name)
                existing_key = get_env_value(env_key)
                if existing_key:
                    _success(f"{env_key}: 已配置")
                    api_key = existing_key
                else:
                    api_key = _prompt("API 密钥 / Bearer 令牌", password=True)
                    if api_key:
                        save_env_value(env_key, api_key)
                        _success(f"已保存到 {display_qiqiclaw_home()}/.env 作为 {env_key}")

                # Set header with env var interpolation
                if api_key or existing_key:
                    server_config["headers"] = {
                        "Authorization": f"Bearer ${{{env_key}}}"
                    }

    # ── Discovery: connect and list tools ─────────────────────────────

    print()
    print(color(f"  正在连接到 '{name}'...", Colors.CYAN))

    try:
        tools = _probe_single_server(name, server_config)
    except Exception as exc:
        _error(f"连接失败: {exc}")
        if _confirm("是否仍然保存配置 (稍后可以测试)?", default=False):
            server_config["enabled"] = False
            _save_mcp_server(name, server_config)
            _success(f"Saved '{name}' to config (disabled)")
            _info("修复问题后，运行: qiqiclaw mcp test " + name)
        return

    if not tools:
        _warning("服务器已连接但未报告任何工具。")
        if _confirm("是否仍然保存配置?", default=True):
            _save_mcp_server(name, server_config)
            _success(f"Saved '{name}' to config")
        return

    # ── Tool selection ────────────────────────────────────────────────

    print()
    _success(f"已连接! 从 '{name}' 发现 {len(tools)} 个工具:")
    print()
    for tool_name, desc in tools:
        short = desc[:60] + "..." if len(desc) > 60 else desc
        print(f"    {color(tool_name, Colors.GREEN):40s} {short}")
    print()

    # Ask: enable all, select, or cancel
    try:
        choice = input(
            color(f"  是否启用全部 {len(tools)} 个工具? [Y/n/select]: ", Colors.YELLOW)
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        _info("已取消。")
        return

    if choice in ("n", "no"):
        _info("已取消 — 服务器未保存。")
        return

    if choice in ("s", "select"):
        # Interactive tool selection
        from qiqiclaw_cli.curses_ui import curses_checklist

        labels = [f"{t[0]}  —  {t[1]}" for t in tools]
        pre_selected = set(range(len(tools)))

        chosen = curses_checklist(
            f"选择 '{name}' 的工具",
            labels,
            pre_selected,
        )

        if not chosen:
            _info("未选择工具 — 服务器未保存。")
            return

        chosen_names = [tools[i][0] for i in sorted(chosen)]
        server_config.setdefault("tools", {})["include"] = chosen_names

        tool_count = len(chosen_names)
        total = len(tools)
    else:
        # Enable all (no filter needed — default behaviour)
        tool_count = len(tools)
        total = len(tools)

    # ── Save ──────────────────────────────────────────────────────────

    server_config["enabled"] = True
    _save_mcp_server(name, server_config)

    print()
    _success(f"Saved '{name}' to {display_qiqiclaw_home()}/config.yaml ({tool_count}/{total} tools enabled)")
    _info("启动新会话以使用这些工具。")


# ─── qiqiclaw mcp remove ───────────────────────────────────────────────────────

def cmd_mcp_remove(args):
    """Remove an MCP server from config."""
    name = args.name
    existing = _get_mcp_servers()

    if name not in existing:
        _error(f"Server '{name}' not found in config.")
        servers = list(existing.keys())
        if servers:
            _info(f"可用的服务器: {', '.join(servers)}")
        return

    if not _confirm(f"是否移除服务器 '{name}'?", default=True):
        _info("已取消。")
        return

    _remove_mcp_server(name)
    _success(f"Removed '{name}' from config")

    # Clean up OAuth tokens if they exist — route through MCPOAuthManager so
    # any provider instance cached in the current process (e.g. from an
    # earlier `qiqiclaw mcp test` in the same session) is evicted too.
    try:
        from tools.mcp_oauth_manager import get_manager
        get_manager().remove(name)
        _success("已清理 OAuth 令牌")
    except Exception:
        pass
    try:
        token_dir = get_qiqiclaw_home() / "mcp-tokens"
        for path in (
            token_dir / f"{name}.json",
            token_dir / f"{name}.client.json",
        ):
            path.unlink(missing_ok=True)
    except Exception:
        pass


# ─── qiqiclaw mcp list ──────────────────────────────────────────────────────────

def cmd_mcp_list(args=None):
    """List all configured MCP servers."""
    servers = _get_mcp_servers()

    if not servers:
        print()
        _info("No MCP servers configured.")
        print()
        _info("使用以下命令添加:")
        _info('  qiqiclaw mcp add <名称> --url <端点>')
        _info('  qiqiclaw mcp add <名称> --command <命令> --args <参数...>')
        print()
        return

    print()
    print(color("  MCP 服务器:", Colors.CYAN + Colors.BOLD))
    print()

    # Table header
    print(f"  {'名称':<16} {'传输方式':<30} {'工具':<12} {'状态':<10}")
    print(f"  {'─' * 16} {'─' * 30} {'─' * 12} {'─' * 10}")

    for name, cfg in servers.items():
        # Transport info
        if "url" in cfg:
            url = cfg["url"]
            # Truncate long URLs
            if len(url) > 28:
                url = url[:25] + "..."
            transport = url
        elif "command" in cfg:
            cmd = cfg["command"]
            cmd_args = cfg.get("args", [])
            if isinstance(cmd_args, list) and cmd_args:
                transport = f"{cmd} {' '.join(str(a) for a in cmd_args[:2])}"
            else:
                transport = cmd
            if len(transport) > 28:
                transport = transport[:25] + "..."
        else:
            transport = "?"

        # Tool count
        tools_cfg = cfg.get("tools", {})
        if isinstance(tools_cfg, dict):
            include = tools_cfg.get("include")
            exclude = tools_cfg.get("exclude")
            if include and isinstance(include, list):
                tools_str = f"{len(include)} selected"
            elif exclude and isinstance(exclude, list):
                tools_str = f"-{len(exclude)} excluded"
            else:
                tools_str = "all"
        else:
            tools_str = "all"

        # Enabled status
        enabled = cfg.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.lower() in ("true", "1", "yes")
        status = color("✓ enabled", Colors.GREEN) if enabled else color("✗ disabled", Colors.DIM)

        print(f"  {name:<16} {transport:<30} {tools_str:<12} {status}")

    print()


# ─── qiqiclaw mcp test ──────────────────────────────────────────────────────────

def cmd_mcp_test(args):
    """Test connection to an MCP server."""
    name = args.name
    servers = _get_mcp_servers()

    if name not in servers:
        _error(f"Server '{name}' not found in config.")
        available = list(servers.keys())
        if available:
            _info(f"可用的服务器: {', '.join(available)}")
        return

    cfg = servers[name]
    print()
    print(color(f"  正在测试 '{name}'...", Colors.CYAN))

    # Show transport info
    if "url" in cfg:
        _info(f"传输方式: HTTP → {cfg['url']}")
    else:
        cmd = cfg.get("command", "?")
        _info(f"传输方式: stdio → {cmd}")

    # Show auth info (masked)
    auth_type = cfg.get("auth", "")
    headers = cfg.get("headers", {})
    if auth_type == "oauth":
        _info("认证: OAuth 2.1 PKCE")
    elif headers:
        for k, v in headers.items():
            if isinstance(v, str) and ("key" in k.lower() or "auth" in k.lower()):
                # Mask the value
                resolved = _interpolate_value(v)
                if len(resolved) > 8:
                    masked = resolved[:4] + "***" + resolved[-4:]
                else:
                    masked = "***"
                print(f"    {k}: {masked}")
    else:
        _info("认证: 无")

    # Attempt connection
    start = time.monotonic()
    try:
        tools = _probe_single_server(name, cfg)
        elapsed_ms = (time.monotonic() - start) * 1000
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        _error(f"连接失败 ({elapsed_ms:.0f}ms): {exc}")
        return

    _success(f"Connected ({elapsed_ms:.0f}ms)")
    _success(f"Tools discovered: {len(tools)}")

    if tools:
        print()
        for tool_name, desc in tools:
            short = desc[:55] + "..." if len(desc) > 55 else desc
            print(f"    {color(tool_name, Colors.GREEN):36s} {short}")
    print()


def _interpolate_value(value: str) -> str:
    """Resolve ``${ENV_VAR}`` references in a string."""
    def _replace(m):
        return os.getenv(m.group(1), "")
    return re.sub(r"\$\{(\w+)\}", _replace, value)


# ─── qiqiclaw mcp login ────────────────────────────────────────────────────────

def cmd_mcp_login(args):
    """Force re-authentication for an OAuth-based MCP server.

    Deletes cached tokens (both on disk and in the running process's
    MCPOAuthManager cache) and triggers a fresh OAuth flow via the
    existing probe path.

    Use this when:
      - Tokens are stuck in a bad state (server revoked, refresh token
        consumed by an external process, etc.)
      - You want to re-authenticate to change scopes or account
      - A tool call returned ``needs_reauth: true``
    """
    name = args.name
    servers = _get_mcp_servers()

    if name not in servers:
        _error(f"Server '{name}' not found in config.")
        if servers:
            _info(f"可用的服务器: {', '.join(servers)}")
        return

    server_config = servers[name]
    url = server_config.get("url")
    if not url:
        _error(f"Server '{name}' has no URL — not an OAuth-capable server")
        return
    if server_config.get("auth") != "oauth":
        _error(f"Server '{name}' is not configured for OAuth (auth={server_config.get('auth')})")
        _info("使用 `qiqiclaw mcp remove` + `qiqiclaw mcp add` 重新配置认证。")
        return

    # Wipe both disk and in-memory cache so the next probe forces a fresh
    # OAuth flow.
    try:
        from tools.mcp_oauth_manager import get_manager
        mgr = get_manager()
        mgr.remove(name)
    except Exception as exc:
        _warning(f"无法清除现有的 OAuth 状态: {exc}")

    print()
    _info(f"正在为 '{name}' 启动 OAuth 流程...")

    # Probe triggers the OAuth flow (browser redirect + callback capture).
    try:
        tools = _probe_single_server(name, server_config)
        if tools:
            _success(f"已认证 — {len(tools)} 个工具可用")
        else:
            _success("已认证 (服务器未报告任何工具)")
    except Exception as exc:
        _error(f"认证失败: {exc}")


# ─── qiqiclaw mcp configure ────────────────────────────────────────────────────

def cmd_mcp_configure(args):
    """Reconfigure which tools are enabled for an existing MCP server."""
    import sys as _sys
    if not _sys.stdin.isatty():
        print("错误: 'qiqiclaw mcp configure' 需要交互式终端。", file=_sys.stderr)
        _sys.exit(1)
    name = args.name
    servers = _get_mcp_servers()

    if name not in servers:
        _error(f"配置中未找到服务器 '{name}'。")
        available = list(servers.keys())
        if available:
            _info(f"可用的服务器: {', '.join(available)}")
        return

    cfg = servers[name]

    # Discover all available tools
    print()
    print(color(f"  正在连接到 '{name}' 以发现工具...", Colors.CYAN))

    try:
        all_tools = _probe_single_server(name, cfg)
    except Exception as exc:
        _error(f"连接失败: {exc}")
        return

    if not all_tools:
        _warning("服务器未报告任何工具。")
        return

    # Determine which are currently enabled
    tools_cfg = cfg.get("tools", {})
    if isinstance(tools_cfg, dict):
        include = tools_cfg.get("include")
        exclude = tools_cfg.get("exclude")
    else:
        include = None
        exclude = None

    tool_names = [t[0] for t in all_tools]

    if include and isinstance(include, list):
        include_set = set(include)
        pre_selected = {
            i for i, tn in enumerate(tool_names) if tn in include_set
        }
    elif exclude and isinstance(exclude, list):
        exclude_set = set(exclude)
        pre_selected = {
            i for i, tn in enumerate(tool_names) if tn not in exclude_set
        }
    else:
        pre_selected = set(range(len(all_tools)))

    currently = len(pre_selected)
    total = len(all_tools)
    _info(f"当前为 '{name}' 启用了 {currently}/{total} 个工具。")
    print()

    # Interactive checklist
    from qiqiclaw_cli.curses_ui import curses_checklist

    labels = [f"{t[0]}  —  {t[1]}" for t in all_tools]

    chosen = curses_checklist(
        f"选择 '{name}' 的工具",
        labels,
        pre_selected,
    )

    if chosen == pre_selected:
        _info("未做任何更改。")
        return

    # Update config
    config = load_config()
    server_entry = cfg_get(config, "mcp_servers", name, default={})

    if len(chosen) == total:
        # All selected → remove include/exclude (register all)
        server_entry.pop("tools", None)
    else:
        chosen_names = [tool_names[i] for i in sorted(chosen)]
        server_entry.setdefault("tools", {})
        server_entry["tools"]["include"] = chosen_names
        server_entry["tools"].pop("exclude", None)

    config.setdefault("mcp_servers", {})[name] = server_entry
    save_config(config)

    new_count = len(chosen)
    _success(f"已更新配置: {new_count}/{total} 个工具已启用")
    _info("启动新会话以使更改生效。")


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def mcp_command(args):
    """Main dispatcher for ``qiqiclaw mcp`` subcommands."""
    action = getattr(args, "mcp_action", None)

    if action == "serve":
        from mcp_serve import run_mcp_server
        run_mcp_server(verbose=getattr(args, "verbose", False))
        return

    handlers = {
        "add": cmd_mcp_add,
        "remove": cmd_mcp_remove,
        "rm": cmd_mcp_remove,
        "list": cmd_mcp_list,
        "ls": cmd_mcp_list,
        "test": cmd_mcp_test,
        "configure": cmd_mcp_configure,
        "config": cmd_mcp_configure,
        "login": cmd_mcp_login,
    }

    handler = handlers.get(action)
    if handler:
        handler(args)
    else:
        # No subcommand — show list
        cmd_mcp_list()
        print(color("  命令:", Colors.CYAN))
        _info("qiqiclaw mcp serve                              作为 MCP 服务器运行")
        _info("qiqiclaw mcp add <名称> --url <端点>            添加 MCP 服务器")
        _info("qiqiclaw mcp add <名称> --command <命令>        添加 stdio 服务器")
        _info("qiqiclaw mcp add <名称> --preset <预设>         从已知预设添加")
        _info("qiqiclaw mcp remove <名称>                      移除服务器")
        _info("qiqiclaw mcp list                               列出服务器")
        _info("qiqiclaw mcp test <名称>                        测试连接")
        _info("qiqiclaw mcp configure <名称>                   切换工具")
        _info("qiqiclaw mcp login <名称>                       重新进行 OAuth 认证")
        print()
