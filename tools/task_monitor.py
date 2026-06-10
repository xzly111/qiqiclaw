"""Task-progress monitor — the user-facing loop that keeps a long
command from "going silent" in the CLI.

Design goals (informed by the user brief):

* **Hardcoded thresholds.**  Non-programmer users can't tune knobs, so the
  escalation ladder is a Python constant, not config.
* **Full transparency.**  Every state transition is written back to the
  task panel via task_store.  The user always sees something moving.
* **Auto-diagnose, then ask.**  At 2 minutes of silence we try to classify
  why the process is quiet; at 5 minutes we ask the user what to do.
* **Hard upper bounds.**  30 minutes of continuous silence, or 1 hour of
  total elapsed time, triggers a safe kill so a forgotten job doesn't run
  forever.

The monitor never terminates a job on its own until the hard limits hit —
intermediate stalls always surface to the user first.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from tools import resilient_runner as rr


# ---------------------------------------------------------------------------
# Escalation thresholds — hardcoded intentionally
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL_SEC = 2
SILENT_NOTIFY_SEC      = 30
SILENT_DIAGNOSE_SEC    = 120
SILENT_ASK_USER_SEC    = 300
SILENT_WARN_URGENT_SEC = 900
SILENT_FORCE_KILL_SEC  = 1800
ELAPSED_FORCE_KILL_SEC = 3600


# ---------------------------------------------------------------------------
# Process diagnosis — translate /proc state + wchan into human terms
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Diagnosis:
    kind: str              # one of: healthy / dead / zombie / disk_io /
                           # network_wait / thread_lock / silent_alive / unknown
    human_readable: str    # non-technical description
    details: str           # raw /proc contents for debug


_STATE_HUMAN = {
    "R": "正在运行",
    "S": "正常等待事件（睡眠）",
    "D": "等待磁盘读写",
    "Z": "已结束但未被回收（zombie）",
    "T": "已暂停（stopped）",
    "X": "已终止",
    "I": "空闲等待",
}


def _read_proc_stat(pid: int) -> Optional[dict]:
    try:
        with open(f"/proc/{pid}/stat", "r") as f:
            data = f.read()
    except OSError:
        return None
    # Format: pid (comm) state ppid ...
    # comm can contain spaces — find the last ')'
    try:
        end_comm = data.rindex(")")
        fields = data[end_comm + 2:].split()
        return {
            "state": fields[0],
            "ppid": int(fields[1]),
        }
    except (ValueError, IndexError):
        return None


def _read_wchan(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/wchan", "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def diagnose_process(pid: int, *, silent_for_sec: float = 0.0) -> Diagnosis:
    """Return a human-friendly explanation of why *pid* might be quiet."""
    if pid <= 0:
        return Diagnosis("dead", "进程已结束", f"invalid pid {pid}")
    if not rr._pid_alive(pid):
        return Diagnosis("dead", "进程已结束", "pid not alive")

    stat = _read_proc_stat(pid)
    if stat is None:
        return Diagnosis("unknown", "无法读取进程状态", "/proc/<pid>/stat unavailable")

    state = stat.get("state", "?")
    wchan = _read_wchan(pid)

    # Zombie
    if state == "Z":
        return Diagnosis("zombie", "进程已完成但未被回收", f"state={state}")

    # Stuck in disk I/O
    if state == "D" and silent_for_sec > 60:
        return Diagnosis(
            "disk_io",
            "正在等待磁盘读写（可能是大文件或慢设备）",
            f"state=D wchan={wchan}",
        )

    # Running but silent
    if silent_for_sec > 30:
        wc = wchan.lower()
        if any(k in wc for k in ("tcp", "socket", "sock", "inet")):
            return Diagnosis(
                "network_wait",
                "正在等待网络响应（可能连接慢或对端未回复）",
                f"state={state} wchan={wchan}",
            )
        if any(k in wc for k in ("futex", "mutex", "lock", "rwsem")):
            return Diagnosis(
                "thread_lock",
                "正在等待一个线程锁释放",
                f"state={state} wchan={wchan}",
            )
        if any(k in wc for k in ("pipe", "poll", "read")):
            return Diagnosis(
                "silent_alive",
                "进程活着，但暂时没有产出",
                f"state={state} wchan={wchan}",
            )
        return Diagnosis(
            "silent_alive",
            f"进程在 {_STATE_HUMAN.get(state, state)} 状态，暂无输出",
            f"state={state} wchan={wchan}",
        )

    return Diagnosis(
        "healthy",
        _STATE_HUMAN.get(state, f"state={state}"),
        f"state={state} wchan={wchan}",
    )


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------


@dataclass
class MonitorOutcome:
    exit_code: Optional[int]
    reason: str              # completed / killed_by_user / killed_silent /
                             # killed_elapsed
    elapsed_sec: float
    tail: str
    diagnosed: Optional[Diagnosis]


def _default_heartbeat(run_id: str, tail_line: str, elapsed: int, silent_for: int) -> None:
    """Placeholder — real callers inject a task_store.update-based callback."""
    pass


def _default_notify(run_id: str, message: str) -> None:
    """Placeholder for user-visible messages (e.g. CLI print or task panel)."""
    pass


def _default_ask_user(question: str, options: list[str]) -> str:
    """Placeholder — in a real CLI this calls the clarify tool.

    Without wiring, we conservatively default to "keep waiting".
    """
    return "wait"


def monitor(
    run_id: str,
    *,
    on_heartbeat: Callable[[str, str, int, int], None] = _default_heartbeat,
    on_notify: Callable[[str, str], None] = _default_notify,
    on_ask_user: Callable[[str, list[str]], str] = _default_ask_user,
    extend_budget_sec: int = 600,
) -> MonitorOutcome:
    """Watch *run_id* until it completes, is killed, or hits a hard limit.

    Callers provide three callbacks so this module stays UI-agnostic:

    * ``on_heartbeat(run_id, tail_line, elapsed, silent_for)`` — fires
      every HEARTBEAT_INTERVAL_SEC seconds.  Wire to ``task_update`` to
      refresh the task panel.
    * ``on_notify(run_id, message)`` — fires at milestone seconds
      (30s first notification, 2min diagnose, 15min warn).
    * ``on_ask_user(question, options)`` — invoked exactly once when the
      process goes silent for SILENT_ASK_USER_SEC.  Return one of the
      option strings.  ``"kill"`` escalates, ``"wait"`` extends budget.
    """
    start = time.time()
    last_output_at = start
    last_log_size = 0
    asked_user = False
    warned_urgent = False

    while True:
        time.sleep(HEARTBEAT_INTERVAL_SEC)
        try:
            state = rr.poll(run_id)
        except KeyError:
            return MonitorOutcome(None, "lost", 0.0, "", None)

        # Completed naturally
        if not state.alive:
            return MonitorOutcome(
                exit_code=state.exit_code,
                reason="completed",
                elapsed_sec=state.elapsed_sec,
                tail=rr.tail(run_id, lines=20),
                diagnosed=None,
            )

        # Output progress
        if state.log_size_bytes > last_log_size:
            last_log_size = state.log_size_bytes
            last_output_at = time.time()
            tail_line = rr.tail(run_id, lines=1)
            on_heartbeat(run_id, tail_line.strip(),
                         int(state.elapsed_sec), int(state.silent_for_sec))

        silent_for = time.time() - last_output_at
        elapsed = time.time() - start

        # --- Hard upper bounds ---
        if elapsed > ELAPSED_FORCE_KILL_SEC:
            rr.kill(run_id, grace_sec=2.0)
            return MonitorOutcome(
                exit_code=-3,
                reason="killed_elapsed",
                elapsed_sec=elapsed,
                tail=rr.tail(run_id, lines=20),
                diagnosed=None,
            )
        if silent_for > SILENT_FORCE_KILL_SEC:
            rr.kill(run_id, grace_sec=2.0)
            return MonitorOutcome(
                exit_code=-3,
                reason="killed_silent",
                elapsed_sec=elapsed,
                tail=rr.tail(run_id, lines=20),
                diagnosed=None,
            )

        # --- Ask user at 5-minute silence ---
        if silent_for > SILENT_ASK_USER_SEC and not asked_user:
            pid = state.pid or 0
            diag = diagnose_process(pid, silent_for_sec=silent_for)
            # Auto-handle "understandable" stalls without bothering user
            if diag.kind in ("disk_io",):
                on_notify(
                    run_id,
                    f"任务 {diag.human_readable}，自动继续等待。",
                )
                asked_user = True
                last_output_at = time.time() - 60  # reset silent window by 1 min
                continue

            answer = on_ask_user(
                f"任务静默 5 分钟。{diag.human_readable}",
                ["继续等待", "查看日志", "终止任务"],
            )
            asked_user = True
            if answer in ("终止任务", "kill"):
                rr.kill(run_id, grace_sec=2.0)
                return MonitorOutcome(
                    exit_code=-1,
                    reason="killed_by_user",
                    elapsed_sec=elapsed,
                    tail=rr.tail(run_id, lines=20),
                    diagnosed=diag,
                )
            # Otherwise keep waiting; extend silent clock
            last_output_at = time.time()
            continue

        # --- Urgent warning at 15 minutes silence ---
        if silent_for > SILENT_WARN_URGENT_SEC and not warned_urgent:
            warned_urgent = True
            on_notify(run_id, "任务静默 15 分钟，若再等 15 分钟仍无反应将自动终止。")

        # --- Diagnose silently at 2 minutes ---
        if silent_for > SILENT_DIAGNOSE_SEC and not asked_user:
            pid = state.pid or 0
            diag = diagnose_process(pid, silent_for_sec=silent_for)
            on_notify(run_id, f"诊断：{diag.human_readable}")

        # --- First heads-up at 30 seconds ---
        elif silent_for > SILENT_NOTIFY_SEC:
            on_heartbeat(
                run_id,
                f"（运行 {int(elapsed)}s，暂无输出）",
                int(elapsed), int(silent_for),
            )
