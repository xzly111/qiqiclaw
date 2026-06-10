"""Resilient long-command runner — survives qiqiclaw process crashes.

Design constraints the CLI team has hit (see GitHub issues #45717, #49233,
#55893, #57079):

* ``subprocess.run`` with ``timeout=`` can propagate SIGTERM to the parent.
* Claude Code's ``run_in_background`` loses output after task completion.
* Signal handling picks SIGTERM where SIGINT would be cleaner.

This module sidesteps all three by treating each long command as an
artefact on disk:

1. The command is launched through ``setsid nohup bash -c '<cmd>'`` so the
   new process group is detached from qiqiclaw.  qiqiclaw can restart,
   crash, even be uninstalled — the job keeps running.
2. stdout+stderr are merged and redirected to a log file inside
   ``~/.qiqiclaw/long_runs/<run_id>/output.log``.  Readers pull tail-style.
3. The PID and exit code are persisted alongside.  ``poll()`` returns a
   stable snapshot without taking a lock.
4. ``kill()`` sends SIGINT first (#49233), then escalates to SIGTERM,
   finally SIGKILL — giving well-behaved processes a chance to clean up.
5. After exit, a ``.done`` marker is written so a subsequent restart of
   qiqiclaw can enumerate completed runs and reconnect to their output.

Public surface is three small dataclasses + four functions.  No threads
are started here — a separate watcher (P6) consumes the logs.
"""

from __future__ import annotations

import contextlib
import json
import os
import shlex
import signal
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------


def _runs_root() -> Path:
    """Base directory holding every long-running job's artefacts."""
    home = os.environ.get("QIQICLAW_HOME") or os.path.expanduser("~/.qiqiclaw")
    base = Path(home) / "long_runs"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass(frozen=True)
class RunPaths:
    """Where the pieces of a long-running job live on disk."""

    run_id: str
    run_dir: Path
    log_file: Path
    pid_file: Path
    meta_file: Path
    exit_file: Path
    done_marker: Path


def _paths_for(run_id: str) -> RunPaths:
    d = _runs_root() / run_id
    return RunPaths(
        run_id=run_id,
        run_dir=d,
        log_file=d / "output.log",
        pid_file=d / "pid",
        meta_file=d / "meta.json",
        exit_file=d / "exit_code",
        done_marker=d / ".done",
    )


# ---------------------------------------------------------------------------
# State snapshot
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """What the caller sees when polling a run.

    Kept intentionally compact so it survives JSON round-trips and is
    cheap to show in the task panel.
    """

    run_id: str
    alive: bool
    exit_code: Optional[int]
    pid: Optional[int]
    log_size_bytes: int
    started_at: float
    elapsed_sec: float
    last_output_at: float
    silent_for_sec: float
    recent_tail: str

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Return True iff *pid* refers to a running process we can signal."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user / ns — good enough.
        return True
    return True


def start(
    command: str,
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> str:
    """Launch *command* as a detached process group, return its run_id.

    Returns immediately; the process continues to run in the background
    under nohup+setsid.  stdout+stderr land in ``<run_dir>/output.log``.
    """
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")

    run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    paths = _paths_for(run_id)
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.log_file.touch(exist_ok=True)

    meta = {
        "run_id": run_id,
        "command": command,
        "cwd": cwd or os.getcwd(),
        "started_at": time.time(),
        "started_by_pid": os.getpid(),
    }
    paths.meta_file.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    # We use a tiny bash wrapper that:
    #   * runs the user's command under its own process group (setsid)
    #   * appends exit code to the exit_code file
    #   * writes a .done marker so consumers can detect completion
    #     even when they miss the "pid no longer exists" window
    quoted_cmd = shlex.quote(command)
    quoted_log = shlex.quote(str(paths.log_file))
    quoted_exit = shlex.quote(str(paths.exit_file))
    quoted_done = shlex.quote(str(paths.done_marker))

    wrapper = (
        f"setsid bash -c {shlex.quote(f'{command}; ec=$?; echo -n $ec > {quoted_exit}; touch {quoted_done}; exit $ec')}"
        f" >> {quoted_log} 2>&1 &"
        f" echo $!"
    )
    _ = quoted_cmd  # silence unused — kept for debug

    proc = subprocess.run(
        ["bash", "-c", wrapper],
        cwd=cwd or None,
        env=({**os.environ, **env} if env else None),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"failed to launch resilient runner: {proc.stderr.strip()}"
        )
    pid_str = proc.stdout.strip().splitlines()[-1] if proc.stdout else ""
    try:
        pid = int(pid_str)
    except ValueError:
        raise RuntimeError(f"could not parse launcher pid from: {proc.stdout!r}")

    paths.pid_file.write_text(str(pid), encoding="utf-8")
    return run_id


# ---------------------------------------------------------------------------
# Poll
# ---------------------------------------------------------------------------


def _read_pid(paths: RunPaths) -> Optional[int]:
    try:
        return int(paths.pid_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_exit_code(paths: RunPaths) -> Optional[int]:
    try:
        return int(paths.exit_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_meta(paths: RunPaths) -> dict:
    try:
        return json.loads(paths.meta_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_tail(paths: RunPaths, max_bytes: int = 4096) -> str:
    try:
        size = paths.log_file.stat().st_size
    except OSError:
        return ""
    with contextlib.suppress(OSError):
        with open(paths.log_file, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                # Skip partial line at start
                f.readline()
            return f.read().decode("utf-8", errors="replace")
    return ""


def poll(run_id: str) -> RunState:
    """Return the current state of *run_id* without blocking."""
    paths = _paths_for(run_id)
    if not paths.run_dir.exists():
        raise KeyError(f"unknown run_id: {run_id}")

    meta = _read_meta(paths)
    started_at = float(meta.get("started_at") or 0.0)
    pid = _read_pid(paths)

    done = paths.done_marker.exists()
    alive = bool(pid) and _pid_alive(pid) and not done
    exit_code = _read_exit_code(paths) if done else None

    try:
        log_size = paths.log_file.stat().st_size
    except OSError:
        log_size = 0
    try:
        last_mtime = paths.log_file.stat().st_mtime
    except OSError:
        last_mtime = started_at

    now = time.time()
    return RunState(
        run_id=run_id,
        alive=alive,
        exit_code=exit_code,
        pid=pid,
        log_size_bytes=log_size,
        started_at=started_at,
        elapsed_sec=max(0.0, now - started_at),
        last_output_at=last_mtime,
        silent_for_sec=max(0.0, now - last_mtime),
        recent_tail=_read_tail(paths),
    )


# ---------------------------------------------------------------------------
# Tail
# ---------------------------------------------------------------------------


def tail(run_id: str, *, lines: int = 50) -> str:
    """Return the last *lines* lines of output, or "" if no output yet."""
    paths = _paths_for(run_id)
    if not paths.log_file.exists():
        return ""
    # Stream from end cheaply — small N, no mmap needed.
    try:
        with open(paths.log_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            chunk = b""
            while pos > 0 and chunk.count(b"\n") <= lines:
                step = min(4096, pos)
                pos -= step
                f.seek(pos)
                chunk = f.read(step) + chunk
            text = chunk.decode("utf-8", errors="replace")
            return "\n".join(text.splitlines()[-lines:])
    except OSError:
        return ""


def read_incremental(run_id: str, since_bytes: int) -> tuple[str, int]:
    """Return (new_text, new_size) read from log since *since_bytes*."""
    paths = _paths_for(run_id)
    try:
        size = paths.log_file.stat().st_size
    except OSError:
        return "", since_bytes
    if size <= since_bytes:
        return "", since_bytes
    try:
        with open(paths.log_file, "rb") as f:
            f.seek(since_bytes)
            data = f.read(size - since_bytes)
        return data.decode("utf-8", errors="replace"), size
    except OSError:
        return "", since_bytes


# ---------------------------------------------------------------------------
# Kill — SIGINT → SIGTERM → SIGKILL escalation
# ---------------------------------------------------------------------------


def kill(run_id: str, *, grace_sec: float = 3.0) -> bool:
    """Terminate a running job gracefully, escalating signals as needed.

    Returns True iff the process was alive at call time and has
    transitioned to dead by the time we return.
    """
    paths = _paths_for(run_id)
    pid = _read_pid(paths)
    if not pid or not _pid_alive(pid):
        return False

    # Send signals to the process group (via os.killpg, which takes a
    # positive pgid — unlike the negative-pid trick used with os.kill).
    # setsid at launch guarantees a fresh process group, so this reaches
    # the cmd and its children without affecting qiqiclaw.
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return False
    except OSError:
        pgid = pid

    # Safety: refuse to signal our own process group.  If setsid somehow
    # failed and the child shares our pgid, killing that group would take
    # qiqiclaw (or a test runner) down too.  Fall back to per-pid kill.
    use_pgid = True
    try:
        if pgid == os.getpgid(0):
            use_pgid = False
    except OSError:
        use_pgid = False

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            if use_pgid:
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                return True
            except OSError:
                return False
        deadline = time.time() + grace_sec
        while time.time() < deadline:
            time.sleep(0.1)
            if not _pid_alive(pid):
                if not paths.exit_file.exists():
                    paths.exit_file.write_text("-1", encoding="utf-8")
                paths.done_marker.touch(exist_ok=True)
                return True
    return not _pid_alive(pid)


# ---------------------------------------------------------------------------
# Enumerate + cleanup
# ---------------------------------------------------------------------------


def list_runs(*, include_done: bool = True) -> list[dict]:
    """List every run under ``~/.qiqiclaw/long_runs/``.

    Returns a list of summary dicts (run_id, command, alive, elapsed_sec,
    exit_code). Useful for ``/runs`` slash command and startup recovery.
    """
    out: list[dict] = []
    root = _runs_root()
    for entry in sorted(root.iterdir() if root.exists() else []):
        if not entry.is_dir():
            continue
        try:
            state = poll(entry.name)
        except KeyError:
            continue
        if not include_done and not state.alive:
            continue
        meta = _read_meta(_paths_for(entry.name))
        out.append(
            {
                "run_id": state.run_id,
                "command": meta.get("command", ""),
                "alive": state.alive,
                "exit_code": state.exit_code,
                "elapsed_sec": int(state.elapsed_sec),
                "log_size_bytes": state.log_size_bytes,
            }
        )
    return out


def cleanup(run_id: str) -> bool:
    """Remove a finished run's artefacts.  Refuses to delete a live run."""
    paths = _paths_for(run_id)
    if not paths.run_dir.exists():
        return False
    pid = _read_pid(paths)
    if pid and _pid_alive(pid) and not paths.done_marker.exists():
        return False
    # Best-effort rmtree
    import shutil
    try:
        shutil.rmtree(paths.run_dir)
        return True
    except OSError:
        return False


def cleanup_old_runs(*, keep_last: int = 50) -> int:
    """Drop the oldest finished runs beyond *keep_last*. Returns count removed."""
    runs = list_runs(include_done=True)
    done = [r for r in runs if not r["alive"]]
    # Sort by run_id (timestamp prefix) descending — newest first
    done.sort(key=lambda r: r["run_id"], reverse=True)
    removed = 0
    for r in done[keep_last:]:
        if cleanup(r["run_id"]):
            removed += 1
    return removed


# ---------------------------------------------------------------------------
# Startup recovery — resume tracking of long runs that survived a restart
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryReport:
    """Summary of what ``recover_on_startup`` found on disk.

    Surfaces three categories:

    * ``alive`` — background processes still running; caller should rewire
      their progress UI (task panel row, live tail).
    * ``completed`` — finished while qiqiclaw was down; exit code is known,
      the user just needs to see the outcome.
    * ``orphaned`` — pid file says "should be alive" but the process is
      gone and no ``.done`` marker was written (crashes/OOMs).  These are
      the interesting ones to surface because the user expects a result
      they never got.
    """

    alive: list[dict]
    completed: list[dict]
    orphaned: list[dict]

    def as_dict(self) -> dict:
        return {
            "alive": list(self.alive),
            "completed": list(self.completed),
            "orphaned": list(self.orphaned),
        }


def recover_on_startup() -> RecoveryReport:
    """Classify every run directory on disk.

    Call this once, as qiqiclaw boots, before the user has a chance to
    issue new commands.  The caller can surface the three buckets via
    the task panel (alive → register, orphaned → show warning) and
    decide whether to cleanup old completed runs (see ``cleanup_old_runs``).
    """
    alive: list[dict] = []
    completed: list[dict] = []
    orphaned: list[dict] = []

    for summary in list_runs(include_done=True):
        paths = _paths_for(summary["run_id"])
        pid = _read_pid(paths)
        done = paths.done_marker.exists()

        if summary["alive"]:
            alive.append(summary)
            continue

        if done:
            completed.append(summary)
            continue

        # No done marker and pid isn't alive → orphaned.
        # Write a synthetic exit code so future polls see completion.
        if not paths.exit_file.exists():
            try:
                paths.exit_file.write_text("-2", encoding="utf-8")
            except OSError:
                pass
        try:
            paths.done_marker.touch(exist_ok=True)
        except OSError:
            pass
        orphan = dict(summary)
        orphan["reason"] = "process gone with no exit code (likely a crash)"
        orphan["pid_at_record_time"] = pid
        orphaned.append(orphan)

    return RecoveryReport(alive=alive, completed=completed, orphaned=orphaned)
