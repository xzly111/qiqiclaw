"""cli_anything_tool — bridge to HKUDS CLI-Anything harnesses.

This module exposes a small, sandboxed tool surface the LLM can use to drive
CLI-Anything generated CLIs (cli-anything-gimp, cli-anything-mermaid,
cli-anything-ollama, ...) without giving it an unconstrained shell.

Design intent (from the fusion decision doc):
  * Main agent keeps a ~250 token routing snippet in its system prompt and
    delegates software-operation tasks to a cli-anything subagent.
  * The subagent sees only this tool group (``cli_anything_core``) plus the
    detailed subagent SKILL — it never has raw terminal or file access.
  * Every invocation is validated: only names starting with ``cli-anything-``
    are accepted; arguments are quoted; subprocesses run with a timeout.

All tools return JSON strings so the LLM can parse structured results.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from tools.registry import registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety / policy constants
# ---------------------------------------------------------------------------

#: Only binaries matching this pattern can be invoked.  Pinned tightly so a
#: stray ``cli_anything_invoke({"name": "rm", ...})`` can never reach ``rm``.
_HARNESS_NAME_RE = re.compile(r"^cli-anything-[a-z0-9][a-z0-9_-]{0,40}$")

#: Package names allowed for ``cli_anything_install`` — same prefix rule.
_HARNESS_PIP_RE = re.compile(r"^cli-anything-[a-z0-9][a-z0-9_-]{0,40}$")

#: Per-invocation wall clock limit.  Can be overridden per call but capped.
_DEFAULT_TIMEOUT = 180
_MAX_TIMEOUT = 900

#: Characters never allowed in a subcommand / arg token.  Prevents shell
#: metachar injection even though we use ``subprocess`` list-form.
_DISALLOWED_ARG_CHARS = set("`$\n\r")


def _json_error(message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _json_ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _validate_harness_name(name: str) -> Optional[str]:
    if not isinstance(name, str) or not _HARNESS_NAME_RE.match(name):
        return (
            f"invalid harness name {name!r} — must match "
            f"{_HARNESS_NAME_RE.pattern}"
        )
    return None


def _validate_args(args: list[Any]) -> Optional[str]:
    for i, a in enumerate(args):
        if not isinstance(a, str):
            return f"arg[{i}] must be string, got {type(a).__name__}"
        if any(ch in _DISALLOWED_ARG_CHARS for ch in a):
            return f"arg[{i}] contains disallowed control characters"
    return None


def _clamp_timeout(val: Any) -> int:
    try:
        t = int(val) if val is not None else _DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        t = _DEFAULT_TIMEOUT
    return max(1, min(_MAX_TIMEOUT, t))


# ---------------------------------------------------------------------------
# Discovery helpers — pure reads, cheap to call repeatedly
# ---------------------------------------------------------------------------


def _scan_installed_harnesses() -> list[dict[str, Any]]:
    """Find cli-anything-* executables on PATH.

    Also probes the active Python environment for matching installed
    packages so we can report both transport modes (CLI-only vs SDK).
    """
    # 1) Binaries on PATH
    path_env = os.environ.get("PATH", "")
    seen_names: set[str] = set()
    binaries: list[dict[str, Any]] = []
    for d in path_env.split(os.pathsep):
        if not d or not os.path.isdir(d):
            continue
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for e in entries:
            if not e.startswith("cli-anything-"):
                continue
            # Strip typical Windows extensions so the name is canonical
            base = e
            for suf in (".exe", ".cmd", ".bat"):
                if base.lower().endswith(suf):
                    base = base[: -len(suf)]
                    break
            if base in seen_names:
                continue
            seen_names.add(base)
            full = os.path.join(d, e)
            binaries.append({"name": base, "path": full})

    # 2) Installed pip packages (detects SDK-only harnesses even if CLI
    #    isn't on PATH yet — surfaces the gap explicitly).
    pkgs: list[str] = []
    try:
        import importlib.metadata as _md

        for dist in _md.distributions():
            meta_name = (dist.metadata["Name"] or "").lower()
            if meta_name.startswith("cli-anything-") and meta_name not in seen_names:
                pkgs.append(meta_name)
    except Exception:  # noqa: BLE001
        logger.debug("importlib.metadata enumeration failed", exc_info=True)

    return sorted(
        [{"name": b["name"], "binary": b["path"], "source": "path"} for b in binaries]
        + [{"name": n, "binary": None, "source": "pip"} for n in pkgs],
        key=lambda r: r["name"],
    )


def _resolve_binary(name: str) -> Optional[str]:
    """Return absolute path to the harness binary, or None if missing."""
    for candidate in (name, f"{name}.exe", f"{name}.cmd"):
        p = shutil.which(candidate)
        if p:
            return p
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_list(args: dict, **_kw) -> str:
    """List installed CLI-Anything harnesses."""
    try:
        harnesses = _scan_installed_harnesses()
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"scan failed: {type(exc).__name__}: {exc}")
    return _json_ok(count=len(harnesses), harnesses=harnesses)


def _handle_describe(args: dict, **_kw) -> str:
    """Run ``<harness> --help`` to capture its command surface."""
    name = args.get("name", "")
    err = _validate_harness_name(name)
    if err:
        return _json_error(err)
    binary = _resolve_binary(name)
    if not binary:
        return _json_error(
            f"harness {name!r} not on PATH; install it first",
            name=name,
            suggestion=f"cli_anything_install({{'package': {name!r}}})",
        )
    try:
        proc = subprocess.run(
            [binary, "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _json_error(f"{name} --help timed out")
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"{type(exc).__name__}: {exc}")
    return _json_ok(
        name=name,
        binary=binary,
        returncode=proc.returncode,
        stdout=proc.stdout[-4000:],
        stderr=proc.stderr[-1000:],
    )


def _handle_invoke(args: dict, **_kw) -> str:
    """Run a validated cli-anything-* command in a subprocess."""
    name = args.get("name", "")
    err = _validate_harness_name(name)
    if err:
        return _json_error(err)

    sub_args = args.get("args") or []
    if not isinstance(sub_args, list):
        return _json_error("'args' must be a list of strings")
    err = _validate_args(sub_args)
    if err:
        return _json_error(err)

    timeout = _clamp_timeout(args.get("timeout"))

    binary = _resolve_binary(name)
    if not binary:
        return _json_error(
            f"harness {name!r} not on PATH; install first",
            name=name,
            suggestion=f"cli_anything_install({{'package': {name!r}}})",
        )

    # Always auto-append --json when caller didn't — CLI-Anything harnesses
    # all support it, and structured output is what the LLM actually needs.
    cmd = [binary, *sub_args]
    if "--json" not in sub_args:
        cmd.append("--json")

    # Optional working directory — must be a real existing dir.
    cwd = args.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            return _json_error(f"cwd {cwd!r} is not an existing directory")

    # Fixed, predictable env: inherit PATH/HOME, strip secrets.  Matches
    # the same convention that execute_code's sandbox uses.
    safe_env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TZ", "TMPDIR", "TMP"}
        or k.startswith(("LC_", "XDG_", "QIQICLAW_"))
    }

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=safe_env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _json_error(f"{name} timed out after {timeout}s", name=name)
    except FileNotFoundError:
        return _json_error(f"{name} binary disappeared mid-call", name=name)
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"{type(exc).__name__}: {exc}", name=name)

    # Try to parse stdout as JSON (CLI-Anything standard).  Fall back to
    # raw text if the harness didn't honor --json in this version.
    stdout = proc.stdout
    parsed: Any = None
    if stdout.strip():
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None

    return _json_ok(
        name=name,
        returncode=proc.returncode,
        stdout_raw=stdout[-8000:],
        stdout_json=parsed,
        stderr=proc.stderr[-2000:],
    )


def _handle_install(args: dict, **_kw) -> str:
    """``pip install`` a cli-anything-* package in the current env.

    Dangerous by nature — guarded by: (1) name whitelist, (2) --dry-run
    unless caller explicitly opts in, (3) no wheel-url or -r requirements
    file accepted.
    """
    pkg = args.get("package", "")
    err = _validate_harness_name(pkg) if pkg else "missing 'package'"
    if err and err is not None and err is not True:
        return _json_error(err if isinstance(err, str) else "invalid package name")

    version_spec = args.get("version", "")
    if version_spec and not re.match(r"^[<>=!~.0-9*]+$", str(version_spec)):
        return _json_error(
            f"version spec {version_spec!r} contains disallowed characters"
        )

    confirm = bool(args.get("confirm", False))
    spec = f"{pkg}{version_spec}" if version_spec else pkg
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check"]
    if not confirm:
        cmd.append("--dry-run")
    cmd.append(spec)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _json_error("pip install timed out (300s)", package=pkg)
    except Exception as exc:  # noqa: BLE001
        return _json_error(f"{type(exc).__name__}: {exc}")

    return _json_ok(
        package=pkg,
        spec=spec,
        dry_run=not confirm,
        returncode=proc.returncode,
        stdout=proc.stdout[-4000:],
        stderr=proc.stderr[-2000:],
    )


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

_LIST_SCHEMA = {
    "name": "cli_anything_list",
    "description": (
        "List installed CLI-Anything harnesses (cli-anything-gimp, "
        "cli-anything-mermaid, cli-anything-ollama, ...).  No side effects."
    ),
    "parameters": {"type": "object", "properties": {}},
}

_DESCRIBE_SCHEMA = {
    "name": "cli_anything_describe",
    "description": (
        "Return the --help output of an installed CLI-Anything harness, so "
        "the model can discover its subcommands before invoking them."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Harness name, e.g. 'cli-anything-mermaid'.",
            }
        },
        "required": ["name"],
    },
}

_INVOKE_SCHEMA = {
    "name": "cli_anything_invoke",
    "description": (
        "Invoke a CLI-Anything harness with a list of arguments.  --json is "
        "auto-appended when absent so the model gets structured output.  "
        "Only names starting with 'cli-anything-' are accepted."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Harness name."},
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "CLI args passed to the harness.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory (must exist).",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Per-call timeout in seconds "
                    f"(default {_DEFAULT_TIMEOUT}, max {_MAX_TIMEOUT})."
                ),
            },
        },
        "required": ["name", "args"],
    },
}

_INSTALL_SCHEMA = {
    "name": "cli_anything_install",
    "description": (
        "Install a cli-anything-* package via pip.  Defaults to --dry-run; "
        "pass confirm=true to actually install.  Name whitelist enforced."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "package": {
                "type": "string",
                "description": "Package name, e.g. 'cli-anything-mermaid'.",
            },
            "version": {
                "type": "string",
                "description": "Optional PEP 440 version spec, e.g. '>=1.0'.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Set true to perform the install.  Default is dry-run.",
            },
        },
        "required": ["package"],
    },
}


# ---------------------------------------------------------------------------
# Registration — these calls happen at import time via discover_builtin_tools
# ---------------------------------------------------------------------------

_TOOLSET = "cli_anything_core"

registry.register(
    name="cli_anything_list",
    toolset=_TOOLSET,
    schema=_LIST_SCHEMA,
    handler=_handle_list,
    description="List installed cli-anything-* harnesses.",
    emoji="🧰",
    max_result_size_chars=50_000,
)

registry.register(
    name="cli_anything_describe",
    toolset=_TOOLSET,
    schema=_DESCRIBE_SCHEMA,
    handler=_handle_describe,
    description="Show --help for one harness.",
    emoji="📖",
    max_result_size_chars=50_000,
)

registry.register(
    name="cli_anything_invoke",
    toolset=_TOOLSET,
    schema=_INVOKE_SCHEMA,
    handler=_handle_invoke,
    description="Invoke a cli-anything-* harness with sandboxed args.",
    emoji="⚙️",
    max_result_size_chars=100_000,
)

registry.register(
    name="cli_anything_install",
    toolset=_TOOLSET,
    schema=_INSTALL_SCHEMA,
    handler=_handle_install,
    description="pip install a cli-anything-* package (dry-run by default).",
    emoji="📦",
    max_result_size_chars=50_000,
)
