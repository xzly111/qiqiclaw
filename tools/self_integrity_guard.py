"""Self-integrity guard — hardcoded protection against writes that would
corrupt qiqiclaw itself.

Target user group: non-programmers who ask qiqiclaw to "do things" and
should never see their installation broken by the agent's own commands.

Two surfaces:

1. ``is_protected_write(path)`` — called by ``file_tools.write_file``,
   ``file_tools.patch``, and sandbox file transports.  Returns
   ``(blocked, reason)``.

2. ``check_cmd_safety(cmd)`` — called by ``terminal_tool`` before every
   shell invocation.  Scans the command for destructive patterns that
   would hit the project source tree, venv, user-level qiqiclaw
   configuration, or system core directories.

Both surfaces are **deny-only** (no allow-list of user-configurable
overrides) — the whole point is that the average user can't accidentally
green-light a self-destructive command.  If the LLM needs to modify
something under the project tree for a legitimate workflow, that work
belongs outside the guard (e.g. via a worktree).

Implementation is pure stdlib; the module is safe to import at any
bootstrap stage including before the CLI is fully initialised.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Project root discovery
# ---------------------------------------------------------------------------


def _locate_project_root() -> Path:
    """Walk up from this module until we see a qiqiclaw checkout layout.

    We consider a directory the project root iff it contains both
    ``pyproject.toml`` and the ``qiqiclaw_subsystems`` package directory
    — this pair is unique to the qiqiclaw source tree and survives
    ``pip install -e`` variants.

    Falls back to ``<module_dir>/..`` if nothing matches.
    """
    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        try:
            if (
                (candidate / "pyproject.toml").is_file()
                and (candidate / "qiqiclaw_subsystems").is_dir()
            ):
                return candidate
        except OSError:
            continue
    # Best-effort fallback: tools/ sits directly under the repo root.
    return here.parent.parent


PROJECT_ROOT: Path = _locate_project_root()


def _venv_roots() -> tuple[Path, ...]:
    """Return every path we consider "the active venv".

    * ``sys.prefix`` — the running interpreter's venv
    * ``<PROJECT_ROOT>/.venv`` — the canonical location for editable installs
    """
    roots: list[Path] = []
    for p in [Path(sys.prefix), PROJECT_ROOT / ".venv"]:
        try:
            roots.append(p.resolve())
        except OSError:
            roots.append(p)
    # Dedup while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for p in roots:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return tuple(out)


# ---------------------------------------------------------------------------
# Protected paths
# ---------------------------------------------------------------------------


def _protected_subtrees() -> tuple[Path, ...]:
    """Directories inside the project whose contents are read-only to the agent.

    Exactly the packages that the running qiqiclaw process loads from
    disk — editing these while qiqiclaw is running is the fast path to a
    broken install.
    """
    names = (
        "agent",
        "tools",
        "qiqiclaw_cli",
        "qiqiclaw_subsystems",
        "gateway",
        "cron",
        "plugins",
        "tui_gateway",
        "acp_adapter",
        "optional-skills",
        "tests",           # tests drive regressions; not the agent's to edit
    )
    out: list[Path] = []
    for name in names:
        candidate = PROJECT_ROOT / name
        try:
            out.append(candidate.resolve())
        except OSError:
            out.append(candidate)
    return tuple(out)


def _protected_files() -> tuple[Path, ...]:
    """Individual files inside the project root that must not be rewritten."""
    files = (
        "pyproject.toml",
        "setup.py",
        "uv.lock",
        "flake.nix",
        "flake.lock",
        "Dockerfile",
        "docker-compose.yml",
    )
    return tuple(
        (PROJECT_ROOT / f).resolve() if (PROJECT_ROOT / f).exists() else (PROJECT_ROOT / f)
        for f in files
    )


def _user_data_roots() -> tuple[Path, ...]:
    """User data locations that would brick qiqiclaw on next launch if nuked.

    The qiqiclaw home root itself is listed first so command-level scans
    catch ``rm -rf ~/.hermes`` even when no sub-path is spelled out.
    Include explicit QIQICLAW_HOME overrides and the project-local
    .hermes copy that appears in portable/dev checkouts.
    """
    home = Path.home()
    bases: list[Path] = [home / ".qiqiclaw", home / ".hermes"]
    val = os.environ.get("QIQICLAW_HOME", "").strip()
    if val:
        bases.append(Path(val).expanduser())
    bases.append(PROJECT_ROOT / ".qiqiclaw")
    bases.append(PROJECT_ROOT / ".hermes")

    out: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        try:
            key = str(base.resolve())
        except OSError:
            key = str(base)
        if key in seen:
            continue
        seen.add(key)
        out.append(base)
    return tuple(out)


def _user_data_write_targets() -> tuple[Path, ...]:
    """Specific user-data paths protected from file-tool writes.

    Command scanning protects the whole qiqiclaw/hermes home from
    destructive deletes, but write tools must be narrower: this project is
    commonly installed under ``~/.qiqiclaw/qiqiclaw``, so treating the
    whole home as a write-protected subtree would block legitimate source
    and test edits inside the checkout.
    """
    out: list[Path] = []
    for base in _user_data_roots():
        out.extend((
            base / "config.yaml",
            base / ".env",
            base / "SOUL.md",
            base / "sessions",
            base / "skills",
            base / "state.db",
        ))
    return tuple(out)


def _system_roots() -> tuple[Path, ...]:
    """Kernel-level paths that are outside any user workflow."""
    candidates = (
        Path("/etc"),
        Path("/bin"),
        Path("/sbin"),
        Path("/usr"),
        Path("/lib"),
        Path("/lib64"),
        Path("/boot"),
        Path("/root"),
        Path("/proc"),
        Path("/sys"),
    )
    out: list[Path] = []
    for p in candidates:
        try:
            if p.exists():
                out.append(p.resolve())
        except OSError:
            continue
    return tuple(out)


# ---------------------------------------------------------------------------
# Core predicate — is writing to this path going to break qiqiclaw?
# ---------------------------------------------------------------------------


def _is_under(path: Path, root: Path) -> bool:
    """True iff *path* sits inside *root* (after symlink resolution)."""
    try:
        path_r = path.resolve()
    except OSError:
        path_r = path
    try:
        root_r = root.resolve()
    except OSError:
        root_r = root
    try:
        path_r.relative_to(root_r)
        return True
    except ValueError:
        return False


def is_protected_write(path: Path | str) -> tuple[bool, str]:
    """Classify a write target.

    Returns ``(blocked, reason)``.  Callers (file_tools, patch) should
    refuse the operation and surface ``reason`` to the LLM so it can
    choose a different approach.

    This is intentionally conservative — for non-programmer users the
    worst outcome is "the agent politely refuses to edit site-packages"
    which is always the correct outcome.
    """
    try:
        p = Path(path)
    except TypeError:
        return True, "invalid path type"

    # Exact-file matches win first (faster than subtree scans).
    for f in _protected_files():
        try:
            if p.resolve() == f.resolve():
                return True, f"项目关键文件受保护：{f.name}"
        except OSError:
            pass

    # Protected subtrees inside the project
    for root in _protected_subtrees():
        if _is_under(p, root):
            return True, f"项目源码目录受保护：{root.name}/"

    # Active venv — editing site-packages while running = guaranteed crash
    for vroot in _venv_roots():
        if _is_under(p, vroot):
            return True, "虚拟环境受保护（修改 .venv 会破坏当前运行环境）"

    # User data roots
    for u in _user_data_write_targets():
        try:
            if p.resolve() == u.resolve() or _is_under(p, u):
                return True, f"用户配置受保护：{u.name}"
        except OSError:
            pass

    # System core
    for sys_root in _system_roots():
        if _is_under(p, sys_root):
            return True, f"系统目录受保护：{sys_root}"

    return False, ""


# ---------------------------------------------------------------------------
# Command-level safety — detect destructive shell patterns
# ---------------------------------------------------------------------------


# The patterns are intentionally narrow: we only fire on clearly
# destructive verbs targeting clearly protected roots.  False positives
# break legitimate work; false negatives are caught at the tool-write
# layer by is_protected_write.  Pattern text uses placeholders that get
# filled in with the actual project root / home dir at match time so the
# check works no matter where the user installed qiqiclaw.


_DESTRUCTIVE_VERBS = (
    r"rm\s+(?:-[rRfv]+\s+)+",     # rm -rf / rm -Rf
    r"rm\s+--recursive\s+",
    r"rm\s+.*--force\s+",
    r"remove-item\s+(?:-[^\s]+\s+)*",
    r"del\s+(?:/[^\s]+\s+)*",
    r"erase\s+(?:/[^\s]+\s+)*",
    r"rmdir\s+(?:/[^\s]+\s+)*",
    r"shred\s+",
    r"dd\s+.*\bof=",
    r"truncate\s+-s\s*0\s+",
    r"find\s+.*-delete\b",
    r"find\s+.*-exec\s+rm\s+",
    r":\s*>\s*",                   # truncate via redirection (` : > foo `)
    r">\s*",                       # stdout-redirect that overwrites
    r">>\s*",                      # append redirect — still a write
    r"chmod\s+-[Rr]?\s*000\s+",
    r"chown\s+-[Rr]\s+\S+\s+",
    r"mv\s+.*",
)


def _path_pattern(path: Path) -> str:
    """Regex that matches *path* both literally and with ~ / $HOME shorthand."""
    s = str(path)
    home = str(Path.home())
    parts = [re.escape(s)]
    if s.startswith(home):
        tail = s[len(home):]
        tail_win = tail.replace("/", "\\")
        parts.append(re.escape("~") + re.escape(tail))
        parts.append(re.escape("$HOME") + re.escape(tail))
        parts.append(re.escape("${HOME}") + re.escape(tail))
        parts.append(re.escape("%USERPROFILE%") + re.escape(tail_win))
    return "(?:" + "|".join(parts) + ")"


def _build_command_patterns() -> list[tuple[re.Pattern, str]]:
    """Compile the full destructive-verb ∙ protected-path matrix once."""
    targets: list[tuple[Path, str]] = []

    targets.append((PROJECT_ROOT, f"项目根目录 {PROJECT_ROOT.name}/"))
    for sub in _protected_subtrees():
        try:
            targets.append((sub, f"项目源码子目录 {sub.relative_to(PROJECT_ROOT)}/"))
        except ValueError:
            targets.append((sub, f"受保护目录 {sub.name}/"))
    for vroot in _venv_roots():
        try:
            rel = vroot.relative_to(PROJECT_ROOT)
            targets.append((vroot, f"虚拟环境 {rel}/"))
        except ValueError:
            targets.append((vroot, "虚拟环境"))
    for u in _user_data_roots():
        targets.append((u, f"用户配置 {u.name}"))
    for s in _system_roots():
        targets.append((s, f"系统目录 {s}"))

    patterns: list[tuple[re.Pattern, str]] = []
    for verb in _DESTRUCTIVE_VERBS:
        for path, label in targets:
            # Path is followed by either end-of-string, whitespace, or a
            # child separator.  Without this anchor, a bare
            # ``rm -rf ~/.qiqiclaw`` (no trailing slash) silently slipped
            # past the matcher because the pattern kept looking for more
            # characters after the path.
            full = verb + r".*?" + _path_pattern(path) + r"(?:$|[\s/])"
            patterns.append(
                (re.compile(full, re.IGNORECASE), f"危险操作（命中 {label}）")
            )

    # Disasters that don't need a path match — rm -rf / and rm -rf ~
    root_kills = [
        (r"\brm\s+(?:-[rRfv]+\s+)+(?:/|~/?|\$HOME/?)(?:\s|$)", "rm 针对根或家目录"),
        (r"\brm\s+(?:--(?:recursive|force)\s+)+/(?:\s|$)",     "rm --recursive --force /"),
        (r"\bmkfs(\.\w+)?\s+", "文件系统格式化命令"),
        (r"\bdd\s+.*\bof=/dev/[svn]d", "直接写块设备"),
    ]
    for pat, why in root_kills:
        patterns.append((re.compile(pat, re.IGNORECASE), why))

    return patterns


_COMPILED_PATTERNS: Optional[list[tuple[re.Pattern, str]]] = None


def _patterns() -> list[tuple[re.Pattern, str]]:
    """Lazy + cached compile of destructive-verb patterns."""
    global _COMPILED_PATTERNS
    if _COMPILED_PATTERNS is None:
        _COMPILED_PATTERNS = _build_command_patterns()
    return _COMPILED_PATTERNS


def check_cmd_safety(cmd: str) -> tuple[bool, str]:
    """Scan *cmd* for destructive patterns against protected paths.

    Returns ``(blocked, reason)``.  Matches are tried against the raw
    command text with no shell expansion — this is a best-effort
    protection layer, not a sandbox.  Combined with
    ``is_protected_write`` at the file-tool layer, it gives
    defence-in-depth without adding any configuration surface the user
    would have to learn.
    """
    if not isinstance(cmd, str) or not cmd.strip():
        return False, ""
    for pat, reason in _patterns():
        if pat.search(cmd):
            return True, reason
    return False, ""


# ---------------------------------------------------------------------------
# Introspection helpers (mostly for tests)
# ---------------------------------------------------------------------------


def protected_roots_snapshot() -> dict[str, list[str]]:
    """Return the fully resolved guard configuration — useful for tests."""
    return {
        "project_root": [str(PROJECT_ROOT)],
        "subtrees": [str(p) for p in _protected_subtrees()],
        "files": [str(p) for p in _protected_files()],
        "venv": [str(p) for p in _venv_roots()],
        "user_data": [str(p) for p in _user_data_roots()],
        "system": [str(p) for p in _system_roots()],
    }
