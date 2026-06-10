"""Outcome strategies library + the ``ensure_outcome`` high-level tool.

User-facing contract: the LLM calls ``ensure_outcome({...})`` once with
a goal description.  Everything else — running commands, watching their
output, retrying with fallback methods, verifying success — happens
internally via :mod:`tools.resilient_runner` and :mod:`tools.task_monitor`.

If every strategy fails, the return payload includes the attempt log
so the LLM (and, in turn, the user) sees exactly what was tried.

This module is safe to import at any bootstrap stage; it doesn't touch
the resilient_runner storage until ``ensure_outcome`` is actually invoked.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional, Sequence

from tools import resilient_runner as rr
from tools import task_monitor as tm


# ---------------------------------------------------------------------------
# Strategy records
# ---------------------------------------------------------------------------


@dataclass
class Strategy:
    """One concrete attempt at achieving a goal.

    Fields:
        name:        Short label shown to the user (e.g. "apt install").
        cmd:         Shell command to run (may reference Python-format
                     placeholders like ``{pkg}`` that caller fills in).
        verify:      Shell command that must exit 0 to prove success.
                     May be empty → assume success if ``cmd`` exited 0.
        requires:    Optional capability name resolved via
                     ``probe_capabilities`` (e.g. ``"sudo_nopasswd"``).
                     Missing capabilities make us skip this strategy.
        timeout_sec: Upper bound for this strategy; None → no hard cap,
                     let the monitor watch it.
    """

    name: str
    cmd: str
    verify: str = ""
    requires: Optional[str] = None
    timeout_sec: Optional[int] = None


@dataclass
class AttemptResult:
    strategy: str
    cmd: str
    exit_code: Optional[int]
    verified: bool
    elapsed_sec: float
    reason: str
    tail: str


@dataclass
class OutcomeResult:
    ok: bool
    goal: str
    method: Optional[str]
    attempts: list[AttemptResult] = field(default_factory=list)
    fatal_error: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "goal": self.goal,
            "method": self.method,
            "attempts": [asdict(a) for a in self.attempts],
            "fatal_error": self.fatal_error,
        }


# ---------------------------------------------------------------------------
# Capability probes — run once, cached
# ---------------------------------------------------------------------------


_cap_cache: dict[str, bool] = {}


def _shell_check(cmd: str) -> bool:
    try:
        out = subprocess.run(
            cmd, shell=True, timeout=5, capture_output=True,
        )
        return out.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def has_capability(name: str) -> bool:
    """Return True if a capability is present.  Cached for the process lifetime."""
    if name in _cap_cache:
        return _cap_cache[name]
    cmd = _CAPABILITIES.get(name)
    if cmd is None:
        _cap_cache[name] = False
        return False
    ok = _shell_check(cmd)
    _cap_cache[name] = ok
    return ok


_CAPABILITIES = {
    # Python package managers
    "uv":    "command -v uv",
    "pip":   "command -v pip",
    "pip3":  "command -v pip3",
    "pipx":  "command -v pipx",
    # JS package managers
    "npm":   "command -v npm",
    "pnpm":  "command -v pnpm",
    "yarn":  "command -v yarn",
    # System
    "root":           "[ \"$(id -u)\" -eq 0 ]",
    "sudo_nopasswd":  "sudo -n true 2>/dev/null",
    "apt":   "command -v apt-get",
    "curl":  "command -v curl",
    "wget":  "command -v wget",
}


def probe_capabilities() -> dict[str, bool]:
    """Snapshot every known capability.  Useful for diagnostics."""
    return {k: has_capability(k) for k in _CAPABILITIES}


# ---------------------------------------------------------------------------
# Built-in strategies — hardcoded, not user-configurable
# ---------------------------------------------------------------------------


def _install_python(pkg: str, importable: Optional[str] = None,
                    cli: Optional[str] = None) -> list[Strategy]:
    """Return ordered strategies to install a Python package."""
    importable = importable or pkg.replace("-", "_")
    strategies: list[Strategy] = []
    verify_import = f'python3 -c "import {importable}"'
    if has_capability("uv"):
        strategies.append(Strategy(
            name="uv pip install",
            cmd=f"uv pip install {shlex.quote(pkg)}",
            verify=verify_import,
            requires="uv",
            timeout_sec=300,
        ))
    if has_capability("pip"):
        strategies.append(Strategy(
            name="pip install --user",
            cmd=f"pip install --user {shlex.quote(pkg)}",
            verify=verify_import,
            requires="pip",
            timeout_sec=300,
        ))
    if has_capability("pip3"):
        strategies.append(Strategy(
            name="pip3 install --user",
            cmd=f"pip3 install --user {shlex.quote(pkg)}",
            verify=verify_import,
            requires="pip3",
            timeout_sec=300,
        ))
    if cli and has_capability("pipx"):
        strategies.append(Strategy(
            name="pipx install",
            cmd=f"pipx install {shlex.quote(pkg)}",
            verify=f"command -v {shlex.quote(cli)}",
            requires="pipx",
            timeout_sec=300,
        ))
    return strategies


def _install_apt(pkg: str, cli: Optional[str] = None) -> list[Strategy]:
    """Strategies for installing a system package via apt or fallbacks."""
    strategies: list[Strategy] = []
    verify = f"command -v {shlex.quote(cli)}" if cli else f"dpkg -s {shlex.quote(pkg)}"
    if has_capability("apt") and has_capability("root"):
        strategies.append(Strategy(
            name="apt-get install",
            cmd=f"apt-get install -y {shlex.quote(pkg)}",
            verify=verify,
            requires="root",
            timeout_sec=300,
        ))
    if has_capability("apt") and has_capability("sudo_nopasswd"):
        strategies.append(Strategy(
            name="sudo apt-get install",
            cmd=f"sudo -n apt-get install -y {shlex.quote(pkg)}",
            verify=verify,
            requires="sudo_nopasswd",
            timeout_sec=300,
        ))
    return strategies


def _download(url: str, dst: str) -> list[Strategy]:
    strategies: list[Strategy] = []
    verify = f'test -f {shlex.quote(dst)} && test -s {shlex.quote(dst)}'
    if has_capability("curl"):
        strategies.append(Strategy(
            name="curl",
            cmd=f"curl -fsSL -o {shlex.quote(dst)} {shlex.quote(url)}",
            verify=verify,
            requires="curl",
            timeout_sec=600,
        ))
    if has_capability("wget"):
        strategies.append(Strategy(
            name="wget",
            cmd=f"wget -q -O {shlex.quote(dst)} {shlex.quote(url)}",
            verify=verify,
            requires="wget",
            timeout_sec=600,
        ))
    return strategies


# ---------------------------------------------------------------------------
# Strategy picker
# ---------------------------------------------------------------------------


def pick_strategies(goal: dict) -> list[Strategy]:
    """Build an ordered strategy list for *goal*.

    *goal* schema (all fields optional unless noted):

        kind        — one of: install_python / install_apt / download / custom
        pkg         — package name (install_*)
        import_name — Python import name (install_python)
        cli         — CLI binary name (install_*)
        url, dst    — for download
        strategies  — explicit list[dict] with name/cmd/verify/timeout_sec
                      fields, used when kind=custom
    """
    kind = goal.get("kind", "")

    if kind == "install_python":
        return _install_python(
            goal["pkg"],
            importable=goal.get("import_name"),
            cli=goal.get("cli"),
        )
    if kind == "install_apt":
        return _install_apt(goal["pkg"], cli=goal.get("cli"))
    if kind == "download":
        return _download(goal["url"], goal["dst"])

    # Custom: caller supplied an explicit list.
    if kind == "custom" and isinstance(goal.get("strategies"), list):
        out: list[Strategy] = []
        for s in goal["strategies"]:
            if not isinstance(s, dict) or "cmd" not in s:
                continue
            out.append(
                Strategy(
                    name=s.get("name") or s["cmd"][:40],
                    cmd=s["cmd"],
                    verify=s.get("verify", ""),
                    requires=s.get("requires"),
                    timeout_sec=s.get("timeout_sec"),
                )
            )
        return out

    return []


# ---------------------------------------------------------------------------
# Runner — one strategy attempt
# ---------------------------------------------------------------------------


def _run_strategy(
    strat: Strategy,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
) -> AttemptResult:
    """Run one strategy to completion; return its AttemptResult."""
    import time as _time

    t0 = _time.time()
    run_id = rr.start(strat.cmd)

    def _hb(run_id, tail, elapsed, silent):
        if on_progress:
            on_progress(f"{strat.name}: {tail[:80]}" if tail else f"{strat.name}: 运行中 ({elapsed}s)")

    outcome = tm.monitor(run_id, on_heartbeat=_hb)

    verified = False
    if outcome.reason == "completed" and outcome.exit_code == 0:
        if strat.verify:
            verified = _shell_check(strat.verify)
        else:
            verified = True

    return AttemptResult(
        strategy=strat.name,
        cmd=strat.cmd,
        exit_code=outcome.exit_code,
        verified=verified,
        elapsed_sec=_time.time() - t0,
        reason=outcome.reason,
        tail=outcome.tail,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def ensure_outcome(
    goal: dict,
    *,
    on_progress: Optional[Callable[[str], None]] = None,
    on_ask_user: Optional[Callable[[str, list[str]], str]] = None,
) -> OutcomeResult:
    """Attempt *goal* until verified success or every strategy fails.

    Returns an OutcomeResult.  The caller (LLM tool handler) serialises
    this back to JSON for the model.
    """
    summary = goal.get("summary") or goal.get("kind", "task")

    strategies = pick_strategies(goal)
    if not strategies:
        return OutcomeResult(
            ok=False,
            goal=summary,
            method=None,
            fatal_error=(
                f"没有匹配的策略可用（kind={goal.get('kind')!r}）。"
                f"可能是环境缺失或 goal 配置不完整。"
            ),
        )

    attempts: list[AttemptResult] = []
    for idx, strat in enumerate(strategies, start=1):
        if on_progress:
            on_progress(f"尝试方法 {idx}/{len(strategies)}：{strat.name}")

        # Capability gate
        if strat.requires and not has_capability(strat.requires):
            attempts.append(AttemptResult(
                strategy=strat.name,
                cmd=strat.cmd,
                exit_code=None,
                verified=False,
                elapsed_sec=0.0,
                reason=f"skipped:missing_capability_{strat.requires}",
                tail="",
            ))
            continue

        result = _run_strategy(strat, on_progress=on_progress)
        attempts.append(result)

        if result.verified:
            return OutcomeResult(
                ok=True,
                goal=summary,
                method=strat.name,
                attempts=attempts,
            )

    # Every strategy failed
    return OutcomeResult(
        ok=False,
        goal=summary,
        method=None,
        attempts=attempts,
        fatal_error="所有可用方法均失败，请查看 attempts 里的尾部输出以确认原因。",
    )
