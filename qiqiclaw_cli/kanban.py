"""CLI for the QiQiClaw Kanban board — ``qiqiclaw kanban …`` subcommand.

Exposes the full 15-verb surface documented in the design spec
(``docs/qiqiclaw-kanban-v1-spec.pdf``).  All DB work is delegated to
``kanban_db``.  This module adds:

  * Argparse subcommand construction (``build_parser``).
  * Argument dispatch (``kanban_command``).
  * Output formatting (plain text + ``--json``).
  * A short shared helper that parses a single slash-style string
    (used by ``/kanban …`` in CLI and gateway) and forwards it to the
    argparse surface.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Optional

from qiqiclaw_cli import kanban_db as kb


# ---------------------------------------------------------------------------
# Small formatting helpers
# ---------------------------------------------------------------------------

_STATUS_ICONS = {
    "todo":     "◻",
    "ready":    "▶",
    "running":  "●",
    "blocked":  "⊘",
    "done":     "✓",
    "archived": "—",
}


def _fmt_ts(ts: Optional[int]) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _fmt_task_line(t: kb.Task) -> str:
    icon = _STATUS_ICONS.get(t.status, "?")
    assignee = t.assignee or "(未分配)"
    tenant = f" [{t.tenant}]" if t.tenant else ""
    return f"{icon} {t.id}  {t.status:8s}  {assignee:20s}{tenant}  {t.title}"


def _task_to_dict(t: kb.Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "title": t.title,
        "body": t.body,
        "assignee": t.assignee,
        "status": t.status,
        "priority": t.priority,
        "tenant": t.tenant,
        "workspace_kind": t.workspace_kind,
        "workspace_path": t.workspace_path,
        "created_by": t.created_by,
        "created_at": t.created_at,
        "started_at": t.started_at,
        "completed_at": t.completed_at,
        "result": t.result,
        "skills": list(t.skills) if t.skills else [],
    }


def _parse_workspace_flag(value: str) -> tuple[str, Optional[str]]:
    """Parse ``--workspace`` into ``(kind, path|None)``.

    Accepts: ``scratch``, ``worktree``, ``worktree:<path>``, ``dir``, ``dir:<path>``.
    """
    if not value:
        return ("scratch", None)
    v = value.strip()
    if v == "scratch":
        return (v, None)
    if v in ("worktree", "dir"):
        return (v, None)
    if v.startswith("dir:") or v.startswith("worktree:"):
        kind, raw_path = v.split(":", 1)
        path = raw_path.strip()
        if not path:
            raise argparse.ArgumentTypeError(
                f"--workspace {kind}: 需要在冒号后提供路径"
            )
        return (kind, os.path.expanduser(path))
    raise argparse.ArgumentTypeError(
        f"未知的 --workspace 值 {value!r}: 请使用 scratch, worktree, worktree:<path>, dir, 或 dir:<path>"
    )


def _check_dispatcher_presence() -> tuple[bool, str]:
    """Return ``(running, message)``.

    - ``running=True``: a gateway is alive for this QIQICLAW_HOME and its
      config has ``kanban.dispatch_in_gateway`` on (default). Message
      is a short status line.
    - ``running=False``: either no gateway is running, or the gateway
      is running but the config flag is off. Message is human guidance
      explaining the next step.

    Used by ``qiqiclaw kanban create`` (and callers) to warn when a task
    will sit in ``ready`` because nothing is there to pick it up.
    Defensive against import failures and config-read errors — if the
    probe itself errors, we return ``(True, "")`` so we don't spam
    false warnings (better to miss a warning than to cry wolf).
    """
    try:
        from gateway.status import get_running_pid  # type: ignore
    except Exception:
        return (True, "")  # can't probe — silent
    try:
        pid = get_running_pid()
    except Exception:
        return (True, "")  # probe errored — silent

    # Even if the gateway is up, dispatch_in_gateway may be off.
    try:
        from qiqiclaw_cli.config import load_config
        cfg = load_config()
        dispatch_on = bool(cfg.get("kanban", {}).get("dispatch_in_gateway", True))
    except Exception:
        dispatch_on = True  # can't tell — assume default

    if pid and dispatch_on:
        return (True, f"gateway pid={pid}, dispatch enabled")
    if pid and not dispatch_on:
        return (
            False,
            "网关正在运行，但配置文件 config.yaml 中 kanban.dispatch_in_gateway=false "
            "— 任务将保持在 'ready' 状态，直到您将其改回 true 并重启网关，"
            "或者运行旧版独立守护进程 (`qiqiclaw kanban daemon --force`)。"
        )
    return (
        False,
        "没有网关正在运行 — 任务将保持在 'ready' 状态，直到您启动网关。运行:\n"
        "    qiqiclaw gateway start\n"
        "网关托管了一个嵌入式调度器（默认每 60 秒执行一次）；您的任务将在网关启动后的下一次执行时被处理。"
    )


# ---------------------------------------------------------------------------
# Argparse builder
# ---------------------------------------------------------------------------

def build_parser(parent_subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Attach the ``kanban`` subcommand tree under an existing subparsers.

    Returns the top-level ``kanban`` parser so caller can ``set_defaults``.
    """
    kanban_parser = parent_subparsers.add_parser(
        "kanban",
        help="Multi-profile collaboration board (tasks, links, comments)",
        description=(
            "Durable SQLite-backed task board shared across QiQiClaw profiles. "
            "Tasks are claimed atomically, can depend on other tasks, and "
            "are executed by a named profile in an isolated workspace. "
            "See https://qiqiclaw.nousresearch.com/docs/user-guide/features/kanban "
            "or docs/qiqiclaw-kanban-v1-spec.pdf for the full design."
        ),
    )
    kanban_parser.add_argument(
        "--board",
        default=None,
        metavar="<slug>",
        help=(
            "Board slug to operate on. Defaults to the current board "
            "(set with `qiqiclaw kanban boards switch <slug>` or "
            "QIQICLAW_KANBAN_BOARD)."
        ),
    )
    sub = kanban_parser.add_subparsers(dest="kanban_action")

    # --- init ---
    sub.add_parser("init", help="Create kanban.db if missing (idempotent)")

    # --- boards ---
    p_boards = sub.add_parser(
        "boards",
        help="Manage kanban boards (one board per project / workstream)",
    )
    boards_sub = p_boards.add_subparsers(dest="boards_action")

    b_list = boards_sub.add_parser("list", aliases=["ls"], help="List boards")
    b_list.add_argument("--json", action="store_true")
    b_list.add_argument("--all", action="store_true", help="Include archived boards")

    b_create = boards_sub.add_parser("create", aliases=["new"], help="Create a board")
    b_create.add_argument("slug")
    b_create.add_argument("--name", default=None)
    b_create.add_argument("--description", default=None)
    b_create.add_argument("--icon", default=None)
    b_create.add_argument("--color", default=None)
    b_create.add_argument("--switch", action="store_true")
    b_create.add_argument("--default-workdir", default=None)

    b_rm = boards_sub.add_parser(
        "rm",
        aliases=["remove", "delete"],
        help="Archive or delete a board",
    )
    b_rm.add_argument("slug")
    b_rm.add_argument("--delete", action="store_true")

    b_switch = boards_sub.add_parser("switch", aliases=["use"], help="Set active board")
    b_switch.add_argument("slug")

    b_show = boards_sub.add_parser("show", aliases=["current"], help="Show active board")
    b_show.add_argument("--json", action="store_true")

    b_rename = boards_sub.add_parser("rename", help="Rename board display name")
    b_rename.add_argument("slug")
    b_rename.add_argument("name")

    b_set_wd = boards_sub.add_parser(
        "set-default-workdir",
        help="Set or clear a board default workdir",
    )
    b_set_wd.add_argument("slug")
    b_set_wd.add_argument("path", nargs="?", default=None)

    # --- create ---
    p_create = sub.add_parser("create", help="Create a new task")
    p_create.add_argument("title", help="Task title")
    p_create.add_argument("--body", default=None, help="Optional opening post")
    p_create.add_argument("--assignee", default=None, help="Profile name to assign")
    p_create.add_argument("--parent", action="append", default=[],
                          help="Parent task id (repeatable)")
    p_create.add_argument("--workspace", default="scratch",
                          help="scratch | worktree | dir:<path> (default: scratch)")
    p_create.add_argument("--tenant", default=None, help="Tenant namespace")
    p_create.add_argument("--priority", type=int, default=0, help="Priority tiebreaker")
    p_create.add_argument("--triage", action="store_true",
                          help="Park in triage — a specifier will flesh out the spec and promote to todo")
    p_create.add_argument("--idempotency-key", default=None,
                          help="Dedup key. If a non-archived task with this key exists, "
                               "its id is returned instead of creating a duplicate.")
    p_create.add_argument("--max-runtime", default=None,
                          help="Per-task runtime cap. Accepts seconds (300) or "
                               "durations (90s, 30m, 2h, 1d). When exceeded, "
                               "the dispatcher SIGTERMs (then SIGKILLs) the worker "
                               "and re-queues the task.")
    p_create.add_argument("--created-by", default="user",
                          help="Author name recorded on the task (default: user)")
    p_create.add_argument("--skill", action="append", default=[], dest="skills",
                          help="Skill to force-load into the worker "
                               "(repeatable). Appended to the built-in "
                               "kanban-worker skill. Example: "
                               "--skill translation --skill github-code-review")
    p_create.add_argument("--json", action="store_true", help="Emit JSON output")

    # --- list ---
    p_list = sub.add_parser("list", aliases=["ls"], help="List tasks")
    p_list.add_argument("--mine", action="store_true",
                        help="Filter by $QIQICLAW_PROFILE as assignee")
    p_list.add_argument("--assignee", default=None)
    p_list.add_argument("--status", default=None,
                        choices=sorted(kb.VALID_STATUSES))
    p_list.add_argument("--tenant", default=None)
    p_list.add_argument("--archived", action="store_true",
                        help="Include archived tasks")
    p_list.add_argument("--json", action="store_true")

    # --- show ---
    p_show = sub.add_parser("show", help="Show a task with comments + events")
    p_show.add_argument("task_id")
    p_show.add_argument("--json", action="store_true")

    # --- assign ---
    p_assign = sub.add_parser("assign", help="Assign or reassign a task")
    p_assign.add_argument("task_id")
    p_assign.add_argument("profile", help="Profile name (or 'none' to unassign)")

    # --- link / unlink ---
    p_link = sub.add_parser("link", help="Add a parent->child dependency")
    p_link.add_argument("parent_id")
    p_link.add_argument("child_id")
    p_unlink = sub.add_parser("unlink", help="Remove a parent->child dependency")
    p_unlink.add_argument("parent_id")
    p_unlink.add_argument("child_id")

    # --- claim ---
    p_claim = sub.add_parser(
        "claim",
        help="Atomically claim a ready task (prints resolved workspace path)",
    )
    p_claim.add_argument("task_id")
    p_claim.add_argument("--ttl", type=int, default=kb.DEFAULT_CLAIM_TTL_SECONDS,
                         help="Claim TTL in seconds (default: 900)")

    # --- comment / complete / block / unblock / archive ---
    p_comment = sub.add_parser("comment", help="Append a comment")
    p_comment.add_argument("task_id")
    p_comment.add_argument("text", nargs="+", help="Comment body")
    p_comment.add_argument("--author", default=None,
                           help="Author name (default: $QIQICLAW_PROFILE or 'user')")

    p_complete = sub.add_parser("complete", help="Mark one or more tasks done")
    p_complete.add_argument("task_ids", nargs="+",
                            help="One or more task ids (only --result applies to all of them)")
    p_complete.add_argument("--result", default=None, help="Result summary")
    p_complete.add_argument("--summary", default=None,
                            help="Structured handoff summary for downstream tasks. "
                                 "Falls back to --result if omitted.")
    p_complete.add_argument("--metadata", default=None,
                            help='JSON dict of structured facts (e.g. \'{"changed_files": [...], '
                                 '"tests_run": 12}\'). Stored on the closing run.')

    p_block = sub.add_parser("block", help="Mark one or more tasks blocked")
    p_block.add_argument("task_id")
    p_block.add_argument("reason", nargs="*", help="Reason (also appended as a comment)")
    p_block.add_argument("--ids", nargs="+", default=None,
                         help="Additional task ids to block with the same reason (bulk mode)")

    p_unblock = sub.add_parser("unblock", help="Return one or more blocked tasks to ready")
    p_unblock.add_argument("task_ids", nargs="+")

    p_archive = sub.add_parser("archive", help="Archive one or more tasks")
    p_archive.add_argument("task_ids", nargs="+")

    # --- tail ---
    p_tail = sub.add_parser("tail", help="Follow a task's event stream")
    p_tail.add_argument("task_id")
    p_tail.add_argument("--interval", type=float, default=1.0)

    # --- dispatch ---
    p_disp = sub.add_parser(
        "dispatch",
        help="One dispatcher pass: reclaim stale, promote ready, spawn workers",
    )
    p_disp.add_argument("--dry-run", action="store_true",
                        help="Don't actually spawn processes; just print what would happen")
    p_disp.add_argument("--max", type=int, default=None,
                        help="Cap number of spawns this pass")
    p_disp.add_argument("--failure-limit", type=int,
                        default=kb.DEFAULT_SPAWN_FAILURE_LIMIT,
                        help=f"Auto-block a task after this many consecutive spawn failures "
                             f"(default: {kb.DEFAULT_SPAWN_FAILURE_LIMIT})")
    p_disp.add_argument("--json", action="store_true")

    # --- daemon (deprecated) ---
    p_daemon = sub.add_parser(
        "daemon",
        help="DEPRECATED — dispatcher now runs in the gateway. Use `qiqiclaw gateway start`.",
    )
    p_daemon.add_argument("--interval", type=float, default=60.0,
                          help="Seconds between dispatch ticks (default: 60)")
    p_daemon.add_argument("--max", type=int, default=None,
                          help="Cap number of spawns per tick")
    p_daemon.add_argument("--failure-limit", type=int,
                          default=kb.DEFAULT_SPAWN_FAILURE_LIMIT)
    p_daemon.add_argument("--pidfile", default=None,
                          help="Write the daemon's PID to this file on start")
    p_daemon.add_argument("--verbose", "-v", action="store_true",
                          help="Log each tick's outcome to stdout")
    # Undocumented escape hatch for users who truly cannot run the gateway.
    # Intentionally excluded from --help so nobody discovers it casually and
    # keeps the old double-dispatcher pattern alive.
    p_daemon.add_argument("--force", action="store_true",
                          help=argparse.SUPPRESS)

    # --- watch ---
    p_watch = sub.add_parser(
        "watch",
        help="Live-stream task_events to the terminal (Ctrl+C to exit)",
    )
    p_watch.add_argument("--assignee", default=None,
                         help="Only show events for tasks assigned to this profile")
    p_watch.add_argument("--tenant", default=None,
                         help="Only show events from tasks in this tenant")
    p_watch.add_argument("--kinds", default=None,
                         help="Comma-separated event kinds to include "
                              "(e.g. 'completed,blocked,gave_up,crashed,timed_out')")
    p_watch.add_argument("--interval", type=float, default=0.5,
                         help="Poll interval in seconds (default: 0.5)")

    # --- stats ---
    p_stats = sub.add_parser(
        "stats", help="Per-status + per-assignee counts + oldest-ready age",
    )
    p_stats.add_argument("--json", action="store_true")

    # --- notify subscribe / list / remove ---
    p_nsub = sub.add_parser(
        "notify-subscribe",
        help="Subscribe a gateway source to a task's terminal events "
             "(used by /kanban subscribe in the gateway adapter)",
    )
    p_nsub.add_argument("task_id")
    p_nsub.add_argument("--platform", required=True)
    p_nsub.add_argument("--chat-id", required=True)
    p_nsub.add_argument("--thread-id", default=None)
    p_nsub.add_argument("--user-id", default=None)

    p_nlist = sub.add_parser(
        "notify-list",
        help="List notification subscriptions (optionally for a single task)",
    )
    p_nlist.add_argument("task_id", nargs="?", default=None)
    p_nlist.add_argument("--json", action="store_true")

    p_nrm = sub.add_parser(
        "notify-unsubscribe",
        help="Remove a gateway subscription from a task",
    )
    p_nrm.add_argument("task_id")
    p_nrm.add_argument("--platform", required=True)
    p_nrm.add_argument("--chat-id", required=True)
    p_nrm.add_argument("--thread-id", default=None)

    # --- log ---
    p_log = sub.add_parser(
        "log",
        help="Print the worker log for a task (from $QIQICLAW_HOME/kanban/logs/)",
    )
    p_log.add_argument("task_id")
    p_log.add_argument("--tail", type=int, default=None,
                       help="Only print the last N bytes")

    # --- runs (per-attempt history for a task) ---
    p_runs = sub.add_parser(
        "runs",
        help="Show attempt history for a task (one row per run: profile, "
             "outcome, elapsed, summary)",
    )
    p_runs.add_argument("task_id")
    p_runs.add_argument("--json", action="store_true")

    # --- heartbeat (worker liveness signal) ---
    p_hb = sub.add_parser(
        "heartbeat",
        help="Emit a heartbeat event for a running task (worker liveness signal)",
    )
    p_hb.add_argument("task_id")
    p_hb.add_argument("--note", default=None,
                      help="Optional short note attached to the heartbeat event")

    # --- assignees ---
    p_asg = sub.add_parser(
        "assignees",
        help="List known profiles + per-profile task counts "
             "(union of ~/.qiqiclaw/profiles/ and current assignees on the board)",
    )
    p_asg.add_argument("--json", action="store_true")

    # --- context --- (for spawned workers)
    p_ctx = sub.add_parser(
        "context",
        help="Print the full context a worker sees for a task "
             "(title + body + parent results + comments).",
    )
    p_ctx.add_argument("task_id")

    # --- gc ---
    p_gc = sub.add_parser(
        "gc", help="Garbage-collect archived-task workspaces, old events, and old logs",
    )
    p_gc.add_argument("--event-retention-days", type=int, default=30,
                      help="Delete task_events older than N days for terminal tasks (default: 30)")
    p_gc.add_argument("--log-retention-days", type=int, default=30,
                      help="Delete worker log files older than N days (default: 30)")

    kanban_parser.set_defaults(_kanban_parser=kanban_parser)
    return kanban_parser


# ---------------------------------------------------------------------------
# Command dispatch
# ---------------------------------------------------------------------------

def kanban_command(args: argparse.Namespace) -> int:
    """Entry point from ``qiqiclaw kanban …`` argparse dispatch.

    Returns a shell-style exit code (0 on success, non-zero on error).
    """
    action = getattr(args, "kanban_action", None)
    if not action:
        # No subaction given: print help via the stored parser reference.
        parser = getattr(args, "_kanban_parser", None)
        if parser is not None:
            parser.print_help()
        else:
            print(
                "usage: qiqiclaw kanban <action> [options]\n"
                "Run 'qiqiclaw kanban --help' for the full list of actions.",
                file=sys.stderr,
            )
        return 0

    if action == "boards":
        return _dispatch_boards(args)

    board_override = getattr(args, "board", None)
    prev_board_env = os.environ.get("QIQICLAW_KANBAN_BOARD")
    restore_board_env = False
    if board_override:
        try:
            normed = kb._normalize_board_slug(board_override)
        except ValueError as exc:
            print(f"kanban: {exc}", file=sys.stderr)
            return 2
        if not normed:
            print("kanban: --board requires a slug", file=sys.stderr)
            return 2
        if normed != kb.DEFAULT_BOARD and not kb.board_exists(normed):
            print(
                f"kanban: board {normed!r} does not exist. "
                f"Create it with `qiqiclaw kanban boards create {normed}`.",
                file=sys.stderr,
            )
            return 1
        os.environ["QIQICLAW_KANBAN_BOARD"] = normed
        restore_board_env = True

    # Auto-initialize the DB before dispatching any subcommand. init_db
    # is idempotent, so running it every invocation is cheap (one
    # SELECT against sqlite_master when tables already exist) and
    # prevents "no such table: tasks" on first use from a fresh
    # QIQICLAW_HOME. Previously only `init` and `daemon` triggered
    # schema creation; `create` / `list` / every other command would
    # error out on a fresh install.
    try:
        kb.init_db()
    except Exception as exc:
        if restore_board_env:
            if prev_board_env is None:
                os.environ.pop("QIQICLAW_KANBAN_BOARD", None)
            else:
                os.environ["QIQICLAW_KANBAN_BOARD"] = prev_board_env
        print(f"kanban: could not initialize database: {exc}", file=sys.stderr)
        return 1

    handlers = {
        "init":     _cmd_init,
        "create":   _cmd_create,
        "list":     _cmd_list,
        "ls":       _cmd_list,
        "show":     _cmd_show,
        "assign":   _cmd_assign,
        "link":     _cmd_link,
        "unlink":   _cmd_unlink,
        "claim":    _cmd_claim,
        "comment":  _cmd_comment,
        "complete": _cmd_complete,
        "block":    _cmd_block,
        "unblock":  _cmd_unblock,
        "archive":  _cmd_archive,
        "tail":     _cmd_tail,
        "dispatch": _cmd_dispatch,
        "daemon":   _cmd_daemon,
        "watch":    _cmd_watch,
        "stats":    _cmd_stats,
        "log":      _cmd_log,
        "runs":     _cmd_runs,
        "heartbeat": _cmd_heartbeat,
        "assignees": _cmd_assignees,
        "notify-subscribe":   _cmd_notify_subscribe,
        "notify-list":        _cmd_notify_list,
        "notify-unsubscribe": _cmd_notify_unsubscribe,
        "context":  _cmd_context,
        "gc":       _cmd_gc,
    }
    handler = handlers.get(action)
    if not handler:
        print(f"kanban: unknown action {action!r}", file=sys.stderr)
        return 2
    try:
        return int(handler(args) or 0)
    except (ValueError, RuntimeError) as exc:
        print(f"kanban: {exc}", file=sys.stderr)
        return 1
    finally:
        if restore_board_env:
            if prev_board_env is None:
                os.environ.pop("QIQICLAW_KANBAN_BOARD", None)
            else:
                os.environ["QIQICLAW_KANBAN_BOARD"] = prev_board_env


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _board_task_counts(slug: str, db_path: Optional[str] = None) -> dict[str, int]:
    try:
        path = Path(db_path) if db_path else kb.kanban_db_path(board=slug)
        if not path.exists():
            return {}
        with kb.connect(path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}
    except Exception:
        return {}


def _dispatch_boards(args: argparse.Namespace) -> int:
    sub = getattr(args, "boards_action", None) or "list"
    if sub in {"list", "ls"}:
        return _cmd_boards_list(args)
    if sub in {"create", "new"}:
        return _cmd_boards_create(args)
    if sub in {"rm", "remove", "delete"}:
        return _cmd_boards_rm(args)
    if sub in {"switch", "use"}:
        return _cmd_boards_switch(args)
    if sub in {"show", "current"}:
        return _cmd_boards_show(args)
    if sub == "rename":
        return _cmd_boards_rename(args)
    if sub == "set-default-workdir":
        return _cmd_boards_set_default_workdir(args)
    print(f"kanban boards: unknown action {sub!r}", file=sys.stderr)
    return 2


def _cmd_boards_list(args: argparse.Namespace) -> int:
    boards = kb.list_boards(include_archived=bool(getattr(args, "all", False)))
    current = kb.get_current_board()
    for board in boards:
        board["is_current"] = board["slug"] == current
        board["counts"] = _board_task_counts(board["slug"], board.get("db_path"))
        board["total"] = sum(board["counts"].values())
    if getattr(args, "json", False):
        print(json.dumps(boards, indent=2, ensure_ascii=False))
        return 0
    if not boards:
        print("(no boards)")
        return 0
    print(f"{'':2s}  {'SLUG':24s}  {'NAME':28s}  COUNTS")
    for board in boards:
        marker = "*" if board["is_current"] else " "
        counts = board.get("counts") or {}
        counts_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(empty)"
        name = board.get("name") or ""
        if board.get("archived"):
            name += " [archived]"
        print(f"{marker:2s}  {board['slug']:24s}  {name:28s}  {counts_str}")
    print(f"\nCurrent board: {current}")
    return 0


def _cmd_boards_create(args: argparse.Namespace) -> int:
    try:
        meta = kb.create_board(
            args.slug,
            name=args.name,
            description=args.description,
            icon=args.icon,
            color=args.color,
            default_workdir=args.default_workdir,
        )
    except ValueError as exc:
        print(f"kanban boards create: {exc}", file=sys.stderr)
        return 2
    print(f"Board {meta['slug']!r} created.")
    print(f"  Display name: {meta.get('name', '')}")
    print(f"  DB path:      {meta['db_path']}")
    if getattr(args, "switch", False):
        kb.set_current_board(meta["slug"])
        print(f"  Switched to {meta['slug']!r}.")
    else:
        print(f"  Use `qiqiclaw kanban boards switch {meta['slug']}` to make it current.")
    return 0


def _cmd_boards_rm(args: argparse.Namespace) -> int:
    force_delete = getattr(args, "delete", False) or getattr(args, "boards_action", "") == "delete"
    try:
        res = kb.remove_board(args.slug, archive=not force_delete)
    except ValueError as exc:
        print(f"kanban boards rm: {exc}", file=sys.stderr)
        return 1
    if res["action"] == "archived":
        print(f"Board {res['slug']!r} archived -> {res['new_path']}")
    else:
        print(f"Board {res['slug']!r} deleted.")
    return 0


def _cmd_boards_switch(args: argparse.Namespace) -> int:
    try:
        normed = kb._normalize_board_slug(args.slug)
    except ValueError as exc:
        print(f"kanban boards switch: {exc}", file=sys.stderr)
        return 2
    if not normed or not kb.board_exists(normed):
        print(f"kanban boards switch: board {args.slug!r} does not exist", file=sys.stderr)
        return 1
    kb.set_current_board(normed)
    print(f"Active board is now {normed!r}.")
    return 0


def _cmd_boards_show(args: argparse.Namespace) -> int:
    current = kb.get_current_board()
    meta = kb.read_board_metadata(current)
    counts = _board_task_counts(current)
    meta["is_current"] = True
    meta["counts"] = counts
    meta["total"] = sum(counts.values())
    if getattr(args, "json", False):
        print(json.dumps(meta, indent=2, ensure_ascii=False))
        return 0
    print(f"Current board: {current}")
    print(f"  Display name: {meta.get('name', '')}")
    if meta.get("description"):
        print(f"  Description:  {meta['description']}")
    print(f"  DB path:      {meta['db_path']}")
    print(f"  Tasks:        {meta['total']} total")
    return 0


def _cmd_boards_rename(args: argparse.Namespace) -> int:
    try:
        normed = kb._normalize_board_slug(args.slug)
    except ValueError as exc:
        print(f"kanban boards rename: {exc}", file=sys.stderr)
        return 2
    if not normed or not kb.board_exists(normed):
        print(f"kanban boards rename: board {args.slug!r} does not exist", file=sys.stderr)
        return 1
    meta = kb.write_board_metadata(normed, name=args.name)
    print(f"Board {normed!r} renamed to {meta['name']!r}.")
    return 0


def _cmd_boards_set_default_workdir(args: argparse.Namespace) -> int:
    try:
        normed = kb._normalize_board_slug(args.slug)
    except ValueError as exc:
        print(f"kanban boards set-default-workdir: {exc}", file=sys.stderr)
        return 2
    if not normed or not kb.board_exists(normed):
        print(f"kanban boards set-default-workdir: board {args.slug!r} does not exist", file=sys.stderr)
        return 1
    meta = kb.write_board_metadata(normed, default_workdir=args.path)
    val = meta.get("default_workdir")
    print(f"Board {normed!r} default workdir {'set to ' + repr(val) if val else 'cleared'}.")
    return 0

def _profile_author() -> str:
    """Best-effort author name for an interactive CLI call."""
    for env in ("QIQICLAW_PROFILE_NAME", "QIQICLAW_PROFILE"):
        v = os.environ.get(env)
        if v:
            return v
    try:
        from qiqiclaw_cli.profiles import get_active_profile_name
        return get_active_profile_name() or "user"
    except Exception:
        return "user"


def _parse_duration(val) -> Optional[int]:
    """Parse ``30s`` / ``5m`` / ``2h`` / ``1d`` or a raw integer → seconds.

    Returns None for empty input. Raises ValueError on malformed input so
    the CLI can surface a usage error cleanly.
    """
    if val is None or val == "":
        return None
    s = str(val).strip().lower()
    # Bare integer → seconds.
    try:
        return int(s)
    except ValueError:
        pass
    # Suffixed form.
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if s and s[-1] in units:
        try:
            n = float(s[:-1])
        except ValueError as exc:
            raise ValueError(f"malformed duration {val!r}") from exc
        return int(n * units[s[-1]])
    raise ValueError(f"malformed duration {val!r} (expected 30s, 5m, 2h, 1d, or a number)")


def _cmd_init(args: argparse.Namespace) -> int:
    path = kb.init_db()
    print(f"看板数据库已初始化于 {path}")
    print()
    # Enumerate profiles on disk so the user knows what assignees are
    # already addressable. Multica does this auto-detection on its
    # daemon start; we do it here at init time instead because our
    # dispatcher doesn't need to enumerate — we just pass the name
    # through to `qiqiclaw -p <name>`.
    try:
        profiles = kb.list_profiles_on_disk()
    except Exception:
        profiles = []
    if profiles:
        print(f"发现 {len(profiles)} 个配置文件；以下任何一个都可以作为 --assignee:")
        for name in profiles:
            print(f"  {name}")
    else:
        print("在 ~/.qiqiclaw/profiles/ 下未找到配置文件。")
        print("在分配任务之前，请使用 `qiqiclaw -p <name> setup` 创建一个配置文件。")
    print()
    print("下一步：启动网关，以便实际处理就绪的任务。")
    print("  qiqiclaw gateway start")
    print()
    print(
        "网关托管了一个嵌入式调度器，默认每 60 秒执行一次\n"
        "（配置项：kanban.dispatch_interval_seconds）。如果没有运行中的网关，\n"
        "任务将永远保持在 'ready' 状态。"
    )
    return 0


def _cmd_heartbeat(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        ok = kb.heartbeat_worker(conn, args.task_id, note=getattr(args, "note", None))
    if not ok:
        print(f"无法为 {args.task_id} 记录心跳（未在运行中？）", file=sys.stderr)
        return 1
    print(f"Heartbeat recorded for {args.task_id}")
    return 0


def _cmd_assignees(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        data = kb.known_assignees(conn)
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if not data:
        print("(无受让人 — 使用 `qiqiclaw -p <name> setup` 创建配置文件)")
        return 0
    # Header
    print(f"{'名称':20s}  {'磁盘上':8s}  计数")
    for entry in data:
        on_disk = "是" if entry["on_disk"] else "否"
        counts = entry["counts"] or {}
        count_str = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "(空闲)"
        print(f"{entry['name']:20s}  {on_disk:8s}  {count_str}")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    ws_kind, ws_path = _parse_workspace_flag(args.workspace)
    try:
        max_runtime = _parse_duration(getattr(args, "max_runtime", None))
    except ValueError as exc:
        print(f"kanban: --max-runtime: {exc}", file=sys.stderr)
        return 2
    with kb.connect() as conn:
        task_id = kb.create_task(
            conn,
            title=args.title,
            body=args.body,
            assignee=args.assignee,
            created_by=args.created_by or _profile_author(),
            workspace_kind=ws_kind,
            workspace_path=ws_path,
            tenant=args.tenant,
            priority=args.priority,
            parents=tuple(args.parent or ()),
            triage=bool(getattr(args, "triage", False)),
            idempotency_key=getattr(args, "idempotency_key", None),
            max_runtime_seconds=max_runtime,
            skills=getattr(args, "skills", None) or None,
            board=kb.get_current_board(),
        )
        task = kb.get_task(conn, task_id)
    if getattr(args, "json", False):
        print(json.dumps(_task_to_dict(task), indent=2, ensure_ascii=False))
    else:
        print(f"Created {task_id}  ({task.status}, assignee={task.assignee or '-'})")

        # Warn when the task would sit in `ready` because no dispatcher is
        # present. Only warn on ready+assigned tasks — triage/todo are
        # expected to sit idle until promoted, and unassigned tasks
        # can't be dispatched. Skipped in --json mode so the stdout
        # stream stays strictly machine-parseable for callers (the JSON
        # response itself carries enough info for them to decide if
        # they want to check dispatcher presence separately).
        if task.status == "ready" and task.assignee:
            running, message = _check_dispatcher_presence()
            if not running and message:
                print(f"\n⚠  {message}", file=sys.stderr)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    assignee = args.assignee
    if args.mine and not assignee:
        assignee = _profile_author()
    with kb.connect() as conn:
        # Cheap "mini-dispatch": recompute ready so list output reflects
        # dependencies that may have cleared since the last dispatcher tick.
        kb.recompute_ready(conn)
        tasks = kb.list_tasks(
            conn,
            assignee=assignee,
            status=args.status,
            tenant=args.tenant,
            include_archived=args.archived,
        )
    if getattr(args, "json", False):
        print(json.dumps([_task_to_dict(t) for t in tasks], indent=2, ensure_ascii=False))
        return 0
    if not tasks:
        print("(无匹配的任务)")
        return 0
    for t in tasks:
        print(_fmt_task_line(t))
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        task = kb.get_task(conn, args.task_id)
        if not task:
            print(f"无此任务: {args.task_id}", file=sys.stderr)
            return 1
        comments = kb.list_comments(conn, args.task_id)
        events = kb.list_events(conn, args.task_id)
        parents = kb.parent_ids(conn, args.task_id)
        children = kb.child_ids(conn, args.task_id)
        runs = kb.list_runs(conn, args.task_id)

    if getattr(args, "json", False):
        payload = {
            "task": _task_to_dict(task),
            "parents": parents,
            "children": children,
            "comments": [
                {"author": c.author, "body": c.body, "created_at": c.created_at}
                for c in comments
            ],
            "events": [
                {
                    "kind": e.kind,
                    "payload": e.payload,
                    "created_at": e.created_at,
                    "run_id": e.run_id,
                }
                for e in events
            ],
            "runs": [
                {
                    "id": r.id,
                    "profile": r.profile,
                    "step_key": r.step_key,
                    "status": r.status,
                    "outcome": r.outcome,
                    "summary": r.summary,
                    "error": r.error,
                    "metadata": r.metadata,
                    "worker_pid": r.worker_pid,
                    "started_at": r.started_at,
                    "ended_at": r.ended_at,
                }
                for r in runs
            ],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"任务 {task.id}: {task.title}")
    print(f"  状态:      {task.status}")
    print(f"  受让人:    {task.assignee or '-'}")
    if task.tenant:
        print(f"  租户:      {task.tenant}")
    print(f"  工作区:    {task.workspace_kind}" +
          (f" @ {task.workspace_path}" if task.workspace_path else ""))
    if task.skills:
        print(f"  skills:    {', '.join(task.skills)}")
    print(f"  创建时间:  {_fmt_ts(task.created_at)} 由 {task.created_by or '-'}")
    if task.started_at:
        print(f"  开始时间:  {_fmt_ts(task.started_at)}")
    if task.completed_at:
        print(f"  完成时间:  {_fmt_ts(task.completed_at)}")
    if parents:
        print(f"  父任务:    {', '.join(parents)}")
    if children:
        print(f"  子任务:    {', '.join(children)}")
    if task.body:
        print()
        print("内容:")
        print(task.body)
    if task.result:
        print()
        print("结果:")
        print(task.result)
    if comments:
        print()
        print(f"评论 ({len(comments)}):")
        for c in comments:
            print(f"  [{_fmt_ts(c.created_at)}] {c.author}: {c.body}")
    if events:
        print()
        print(f"事件 ({len(events)}):")
        for e in events[-20:]:
            pl = f" {e.payload}" if e.payload else ""
            run_tag = f" [运行 {e.run_id}]" if e.run_id else ""
            print(f"  [{_fmt_ts(e.created_at)}]{run_tag} {e.kind}{pl}")
    if runs:
        print()
        print(f"运行记录 ({len(runs)}):")
        for r in runs:
            # Clamp to 0 so NTP backward-jumps don't print negative seconds.
            elapsed = (max(0, r.ended_at - r.started_at)
                       if r.ended_at else None)
            el = f"{elapsed}s" if elapsed is not None else "活动中"
            outcome = r.outcome or r.status or "活动中"
            print(f"  #{r.id:<3} {outcome:<12} @{r.profile or '-'}  {el}  "
                  f"{_fmt_ts(r.started_at)}")
            if r.summary:
                print(f"        → {r.summary.splitlines()[0][:160]}")
            if r.error:
                print(f"        ! {r.error.splitlines()[0][:160]}")
    return 0


def _cmd_assign(args: argparse.Namespace) -> int:
    profile = None if args.profile.lower() in ("none", "-", "null") else args.profile
    with kb.connect() as conn:
        ok = kb.assign_task(conn, args.task_id, profile)
    if not ok:
        print(f"无此任务: {args.task_id}", file=sys.stderr)
        return 1
    print(f"Assigned {args.task_id} to {profile or '(unassigned)'}")
    return 0


def _cmd_link(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        kb.link_tasks(conn, args.parent_id, args.child_id)
    print(f"Linked {args.parent_id} -> {args.child_id}")
    return 0


def _cmd_unlink(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        ok = kb.unlink_tasks(conn, args.parent_id, args.child_id)
    if not ok:
        print(f"无此链接: {args.parent_id} -> {args.child_id}", file=sys.stderr)
        return 1
    print(f"Unlinked {args.parent_id} -> {args.child_id}")
    return 0


def _cmd_claim(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        task = kb.claim_task(conn, args.task_id, ttl_seconds=args.ttl)
        if task is None:
            # Report why
            existing = kb.get_task(conn, args.task_id)
            if existing is None:
                print(f"无此任务: {args.task_id}", file=sys.stderr)
                return 1
            print(
                f"无法认领 {args.task_id}: 状态={existing.status} "
                f"锁={existing.claim_lock or '(无)'}",
                file=sys.stderr,
            )
            return 1
        workspace = kb.resolve_workspace(task)
        kb.set_workspace_path(conn, task.id, str(workspace))
    print(f"已认领 {task.id}")
    print(f"工作区: {workspace}")
    return 0


def _cmd_comment(args: argparse.Namespace) -> int:
    body = " ".join(args.text).strip()
    author = args.author or _profile_author()
    with kb.connect() as conn:
        kb.add_comment(conn, args.task_id, author, body)
    print(f"已为 {args.task_id} 添加评论")
    return 0


def _cmd_complete(args: argparse.Namespace) -> int:
    """Mark one or more tasks done. Supports a single id or a list."""
    ids = list(args.task_ids or [])
    if not ids:
        print("至少需要一个 task_id", file=sys.stderr)
        return 1
    summary = getattr(args, "summary", None)
    raw_meta = getattr(args, "metadata", None)
    # Guard: structured handoff fields are per-run, so they'd be
    # copy-pasted identically across N runs — almost always a footgun.
    # Refuse instead of silently doing the wrong thing.
    if len(ids) > 1 and (summary or raw_meta):
        print(
            "kanban: --summary / --metadata are per-task options and cannot be used with multiple ids "
            "（会将相同的交接应用于每个任务）。请逐个完成任务，或删除这些标志进行批量关闭。",
            file=sys.stderr,
        )
        return 2
    metadata = None
    if raw_meta:
        try:
            metadata = json.loads(raw_meta)
            if not isinstance(metadata, dict):
                raise ValueError("必须是 JSON 对象")
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"kanban: --metadata: {exc}", file=sys.stderr)
            return 2
    failed: list[str] = []
    with kb.connect() as conn:
        for tid in ids:
            if not kb.complete_task(
                conn, tid,
                result=args.result,
                summary=summary,
                metadata=metadata,
            ):
                failed.append(tid)
                print(f"无法完成 {tid}（未知 id 或终止状态）", file=sys.stderr)
            else:
                print(f"Completed {tid}")
    return 0 if not failed else 1


def _cmd_block(args: argparse.Namespace) -> int:
    reason = " ".join(args.reason).strip() if args.reason else None
    author = _profile_author()
    ids = [args.task_id] + list(getattr(args, "ids", None) or [])
    failed: list[str] = []
    with kb.connect() as conn:
        for tid in ids:
            if reason:
                kb.add_comment(conn, tid, author, f"已阻止: {reason}")
            if not kb.block_task(conn, tid, reason=reason):
                failed.append(tid)
                print(f"无法阻止 {tid}", file=sys.stderr)
            else:
                print(f"Blocked {tid}" + (f": {reason}" if reason else ""))
    return 0 if not failed else 1


def _cmd_unblock(args: argparse.Namespace) -> int:
    ids = list(args.task_ids or [])
    if not ids:
        print("至少需要一个 task_id", file=sys.stderr)
        return 1
    failed: list[str] = []
    with kb.connect() as conn:
        for tid in ids:
            if not kb.unblock_task(conn, tid):
                failed.append(tid)
                print(f"无法解除阻止 {tid}（未被阻止？）", file=sys.stderr)
            else:
                print(f"Unblocked {tid}")
    return 0 if not failed else 1


def _cmd_archive(args: argparse.Namespace) -> int:
    ids = list(args.task_ids or [])
    if not ids:
        print("至少需要一个 task_id", file=sys.stderr)
        return 1
    failed: list[str] = []
    with kb.connect() as conn:
        for tid in ids:
            if not kb.archive_task(conn, tid):
                failed.append(tid)
                print(f"无法归档 {tid}", file=sys.stderr)
            else:
                print(f"Archived {tid}")
    return 0 if not failed else 1


def _cmd_tail(args: argparse.Namespace) -> int:
    last_id = 0
    print(f"正在跟踪 {args.task_id} 的事件。按 Ctrl-C 停止。")
    try:
        while True:
            with kb.connect() as conn:
                events = kb.list_events(conn, args.task_id)
            for e in events:
                if e.id > last_id:
                    pl = f" {e.payload}" if e.payload else ""
                    print(f"[{_fmt_ts(e.created_at)}] {e.kind}{pl}", flush=True)
                    last_id = e.id
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("\n(已停止)")
        return 0


def _cmd_dispatch(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        res = kb.dispatch_once(
            conn,
            dry_run=args.dry_run,
            max_spawn=args.max,
            failure_limit=getattr(args, "failure_limit", kb.DEFAULT_SPAWN_FAILURE_LIMIT),
        )
    if getattr(args, "json", False):
        print(json.dumps({
            "reclaimed": res.reclaimed,
            "crashed": res.crashed,
            "timed_out": res.timed_out,
            "auto_blocked": res.auto_blocked,
            "promoted": res.promoted,
            "spawned": [
                {"task_id": tid, "assignee": who, "workspace": ws}
                for (tid, who, ws) in res.spawned
            ],
            "skipped_unassigned": res.skipped_unassigned,
        }, indent=2))
        return 0
    print(f"Reclaimed:      {res.reclaimed}")
    print(f"Crashed:        {len(res.crashed)}")
    if res.crashed:
        print(f"  {', '.join(res.crashed)}")
    print(f"Timed out:      {len(res.timed_out)}")
    if res.timed_out:
        print(f"  {', '.join(res.timed_out)}")
    print(f"Auto-blocked:   {len(res.auto_blocked)}")
    if res.auto_blocked:
        print(f"  {', '.join(res.auto_blocked)}")
    print(f"Promoted:       {res.promoted}")
    print(f"Spawned:        {len(res.spawned)}")
    for tid, who, ws in res.spawned:
        tag = " (试运行)" if args.dry_run else ""
        print(f"  - {tid}  ->  {who}  @ {ws or '-'}{tag}")
    if res.skipped_unassigned:
        print(f"已跳过（未分配）: {', '.join(res.skipped_unassigned)}")
    return 0


def _cmd_daemon(args: argparse.Namespace) -> int:
    """Deprecated — the dispatcher now runs inside the gateway.

    Left in as a stub so users with the old command in scripts/systemd
    units get a clear migration message instead of a cryptic
    "no such command" error. A ``--force`` escape hatch keeps the old
    standalone daemon alive for the rare edge case where someone truly
    cannot run the gateway (e.g. running on a host that forbids
    long-lived background services), but the default path exits 2
    with guidance so nobody accidentally keeps running two dispatchers
    against the same kanban.db.
    """
    # --force lets power users keep the standalone loop for one more
    # release cycle. Undocumented in `--help` so nobody discovers it
    # casually — intentional.
    if not getattr(args, "force", False):
        print(
            "qiqiclaw kanban daemon: DEPRECATED — dispatcher now runs inside the gateway.\n"
            "要使用看板功能:\n"
            "\n"
            "    qiqiclaw gateway start       # 启动网关 + 嵌入式调度器\n"
            "\n"
            "就绪的任务将在下一次调度器执行时被处理\n"
            "（默认：每 60 秒）。通过 config.yaml 配置:\n"
            "\n"
            "    kanban:\n"
            "      dispatch_in_gateway: true      # 默认值\n"
            "      dispatch_interval_seconds: 60\n"
            "\n"
            "同时运行网关和此独立守护进程将导致竞争认领。\n"
            "如果您确实需要旧的独立守护进程（没有可用的网关），\n"
            "请使用 --force 重新运行。",
            file=sys.stderr,
        )
        return 2

    # Legacy path — same logic as before, kept behind --force.
    # Make sure the DB exists before printing "started" so the user sees the
    # correct DB path and any init error surfaces immediately.
    kb.init_db()

    pidfile = getattr(args, "pidfile", None)
    if pidfile:
        try:
            Path(pidfile).parent.mkdir(parents=True, exist_ok=True)
            Path(pidfile).write_text(str(os.getpid()), encoding="utf-8")
        except OSError as exc:
            print(f"warning: could not write pidfile {pidfile}: {exc}", file=sys.stderr)

    verbose = bool(getattr(args, "verbose", False))
    print(
        f"看板调度器以独立模式运行（通过 --force）"
        f"（间隔={args.interval}秒, pid={os.getpid()}）。"
        f"按 Ctrl-C 停止。注意：如果网关也在运行且 "
        f"dispatch_in_gateway=true（默认），您将有两个调度器竞争认领。",
        file=sys.stderr,
    )

    # Health telemetry: warn when every tick finds ready work but fails to
    # spawn any worker. Catches broken profiles, PATH drift, missing venv,
    # credential loss — cases where the per-task circuit breaker auto-blocks
    # each task quietly but the operator has no signal that the dispatcher
    # itself is dysfunctional.
    HEALTH_WINDOW = 6  # ticks (default 30s at interval=5)
    health_state = {"bad_ticks": 0, "last_warn_at": 0}

    def _on_tick(res):
        ready_pending = bool(res.skipped_unassigned) or _ready_queue_nonempty()
        spawned_any = bool(res.spawned)
        if ready_pending and not spawned_any:
            health_state["bad_ticks"] += 1
        else:
            health_state["bad_ticks"] = 0
        # Emit a warning once per HEALTH_WINDOW bad ticks (not every tick)
        # so log volume stays bounded while the problem persists.
        if health_state["bad_ticks"] >= HEALTH_WINDOW:
            now = int(time.time())
            # Rate-limit repeats: at most one warning per 5 minutes.
            if now - health_state["last_warn_at"] >= 300:
                print(
                    f"[{_fmt_ts(now)}] 警告：调度器卡住了："
                    f"就绪队列在连续 {health_state['bad_ticks']} 次执行中非空，"
                    f"但成功生成的工作进程为 0。"
                    f"请检查配置文件健康状况（venv、PATH、凭据）以及 "
                    f"`qiqiclaw kanban list --status ready` / "
                    f"`qiqiclaw kanban list --status blocked` 中最近的 "
                    f"spawn_failed 任务。",
                    file=sys.stderr, flush=True,
                )
                health_state["last_warn_at"] = now
        if not verbose:
            return
        did_work = (
            res.reclaimed or res.crashed or res.timed_out or res.promoted
            or res.spawned or res.auto_blocked
        )
        if did_work:
            print(
                f"[{_fmt_ts(int(time.time()))}] "
                f"reclaimed={res.reclaimed} crashed={len(res.crashed)} "
                f"timed_out={len(res.timed_out)} "
                f"promoted={res.promoted} spawned={len(res.spawned)} "
                f"auto_blocked={len(res.auto_blocked)}",
                flush=True,
            )

    def _ready_queue_nonempty() -> bool:
        """简单的 SELECT — 仅检查是否至少有一个就绪任务
        且有受让人，调度器可以处理它。"""
        try:
            with kb.connect() as conn:
                row = conn.execute(
                    "SELECT 1 FROM tasks "
                    "WHERE status = 'ready' AND assignee IS NOT NULL "
                    "    AND claim_lock IS NULL LIMIT 1"
                ).fetchone()
                return row is not None
        except Exception:
            return False

    try:
        kb.run_daemon(
            interval=args.interval,
            max_spawn=args.max,
            failure_limit=getattr(args, "failure_limit", kb.DEFAULT_SPAWN_FAILURE_LIMIT),
            on_tick=_on_tick,
        )
    finally:
        if pidfile:
            try:
                Path(pidfile).unlink()
            except OSError:
                pass
    print("(调度器已停止)")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    """将 task_events 实时流式传输到终端。"""
    kinds = (
        {k.strip() for k in args.kinds.split(",") if k.strip()}
        if args.kinds else None
    )
    cursor = 0
    print("正在监视看板事件。按 Ctrl-C 停止。", flush=True)
    # Seed cursor at the latest id so we don't replay history.
    with kb.connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS m FROM task_events"
        ).fetchone()
        cursor = int(row["m"])

    try:
        while True:
            with kb.connect() as conn:
                rows = conn.execute(
                    "SELECT e.id, e.task_id, e.kind, e.payload, e.created_at, "
                    "       t.assignee, t.tenant "
                    "FROM task_events e LEFT JOIN tasks t ON t.id = e.task_id "
                    "WHERE e.id > ? ORDER BY e.id ASC LIMIT 200",
                    (cursor,),
                ).fetchall()
            for r in rows:
                cursor = max(cursor, int(r["id"]))
                if kinds and r["kind"] not in kinds:
                    continue
                if args.assignee and r["assignee"] != args.assignee:
                    continue
                if args.tenant and r["tenant"] != args.tenant:
                    continue
                try:
                    payload = json.loads(r["payload"]) if r["payload"] else None
                except Exception:
                    payload = None
                pl = f" {payload}" if payload else ""
                print(
                    f"[{_fmt_ts(r['created_at'])}] {r['task_id']:10s} "
                    f"{r['kind']:18s} (@{r['assignee'] or '-'}){pl}",
                    flush=True,
                )
            time.sleep(max(0.1, args.interval))
    except KeyboardInterrupt:
        print("\n(已停止)")
        return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        stats = kb.board_stats(conn)
    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 0
    print("按状态统计:")
    for k in ("triage", "todo", "ready", "running", "blocked", "done"):
        print(f"  {k:8s}  {stats['by_status'].get(k, 0)}")
    if stats["by_assignee"]:
        print("\n按受让人统计:")
        for who, counts in sorted(stats["by_assignee"].items()):
            parts = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            print(f"  {who:20s}  {parts}")
    age = stats["oldest_ready_age_seconds"]
    if age is not None:
        print(f"\n最旧的就绪任务年龄: {int(age)}秒")
    return 0


def _cmd_notify_subscribe(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        if kb.get_task(conn, args.task_id) is None:
            print(f"无此任务: {args.task_id}", file=sys.stderr)
            return 1
        kb.add_notify_sub(
            conn, task_id=args.task_id,
            platform=args.platform, chat_id=args.chat_id,
            thread_id=args.thread_id, user_id=args.user_id,
        )
    print(f"Subscribed {args.platform}:{args.chat_id}"
          + (f":{args.thread_id}" if args.thread_id else "")
          + f" to {args.task_id}")
    return 0


def _cmd_notify_list(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        subs = kb.list_notify_subs(conn, args.task_id)
    if getattr(args, "json", False):
        print(json.dumps(subs, indent=2, ensure_ascii=False))
        return 0
    if not subs:
        print("(无订阅)")
        return 0
    for s in subs:
        thr = f":{s['thread_id']}" if s.get("thread_id") else ""
        print(f"  {s['task_id']:10s}  {s['platform']}:{s['chat_id']}{thr}"
              f"  (自事件 {s['last_event_id']} 起)")
    return 0


def _cmd_notify_unsubscribe(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        ok = kb.remove_notify_sub(
            conn, task_id=args.task_id,
            platform=args.platform, chat_id=args.chat_id,
            thread_id=args.thread_id,
        )
    if not ok:
        print("(无此订阅)", file=sys.stderr)
        return 1
    print(f"Unsubscribed {args.task_id}")
    return 0


def _cmd_log(args: argparse.Namespace) -> int:
    content = kb.read_worker_log(args.task_id, tail_bytes=args.tail)
    if content is None:
        print(f"({args.task_id} no log — task may not have spawned yet)",
              file=sys.stderr)
        return 1
    sys.stdout.write(content)
    if not content.endswith("\n"):
        sys.stdout.write("\n")
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    """显示任务的尝试历史。"""
    with kb.connect() as conn:
        runs = kb.list_runs(conn, args.task_id)
    if getattr(args, "json", False):
        print(json.dumps([
            {
                "id": r.id, "profile": r.profile, "status": r.status,
                "outcome": r.outcome, "started_at": r.started_at,
                "ended_at": r.ended_at, "summary": r.summary,
                "error": r.error, "metadata": r.metadata,
                "worker_pid": r.worker_pid, "step_key": r.step_key,
            } for r in runs
        ], indent=2, ensure_ascii=False))
        return 0
    if not runs:
        print(f"({args.task_id} 尚无运行记录)")
        return 0
    print(f"{'#':3s}  {'结果':12s}  {'配置文件':16s}  {'耗时':>8s}  开始时间")
    for i, r in enumerate(runs, 1):
        end = r.ended_at or int(time.time())
        # Clamp to 0 so NTP backward-jumps don't print negative durations.
        elapsed = max(0, end - r.started_at)
        if elapsed < 60:
            el = f"{elapsed}秒"
        elif elapsed < 3600:
            el = f"{elapsed // 60}分"
        else:
            el = f"{elapsed / 3600:.1f}时"
        outcome = r.outcome or ("(运行中)" if not r.ended_at else r.status)
        print(f"{i:3d}  {outcome:12s}  {(r.profile or '-'):16s}  {el:>8s}  {_fmt_ts(r.started_at)}")
        if r.summary:
            # Indent and truncate long summaries to keep the table readable.
            summary = r.summary.splitlines()[0][:100]
            print(f"     → {summary}")
        if r.error:
            print(f"     ✖ {r.error[:100]}")
    return 0


def _cmd_context(args: argparse.Namespace) -> int:
    with kb.connect() as conn:
        text = kb.build_worker_context(conn, args.task_id)
    print(text)
    return 0


def _cmd_gc(args: argparse.Namespace) -> int:
    """Remove scratch workspaces of archived tasks, prune old events, and
    delete old worker logs."""
    import shutil
    scratch_root = kb.workspaces_root()
    removed_ws = 0
    with kb.connect() as conn:
        rows = conn.execute(
            "SELECT id, workspace_kind, workspace_path FROM tasks WHERE status = 'archived'"
        ).fetchall()
    for row in rows:
        if row["workspace_kind"] != "scratch":
            continue
        path = Path(row["workspace_path"] or (scratch_root / row["id"]))
        try:
            path = path.resolve()
        except OSError:
            continue
        try:
            path.relative_to(scratch_root.resolve())
        except ValueError:
            # Safety: never delete outside the scratch root.
            continue
        if path.exists() and path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed_ws += 1

    event_days = getattr(args, "event_retention_days", 30)
    log_days = getattr(args, "log_retention_days", 30)
    with kb.connect() as conn:
        removed_events = kb.gc_events(
            conn, older_than_seconds=event_days * 24 * 3600,
        )
    removed_logs = kb.gc_worker_logs(
        older_than_seconds=log_days * 24 * 3600,
    )
    print(f"GC complete: {removed_ws} workspace(s), "
          f"{removed_events} event row(s), {removed_logs} log file(s) removed")
    return 0


# ---------------------------------------------------------------------------
# Slash-command entry point (used by /kanban from CLI and gateway)
# ---------------------------------------------------------------------------

def run_slash(rest: str) -> str:
    """Execute a ``/kanban …`` string and return captured stdout/stderr.

    ``rest`` is everything after ``/kanban`` (may be empty).  Used from
    both the interactive CLI (``self._handle_kanban_command``) and the
    gateway (``_handle_kanban_command``) so formatting is identical.
    """
    import io
    import contextlib

    tokens = shlex.split(rest) if rest and rest.strip() else []

    parser = argparse.ArgumentParser(prog="/kanban", add_help=False)
    parser.exit_on_error = False  # type: ignore[attr-defined]
    sub = parser.add_subparsers(dest="kanban_action")
    # Reuse the argparse builder -- call it with a throwaway parent
    # subparsers via a wrapping top-level parser.
    wrap = argparse.ArgumentParser(prog="/", add_help=False)
    wrap.exit_on_error = False  # type: ignore[attr-defined]
    wrap_sub = wrap.add_subparsers(dest="_top")
    build_parser(wrap_sub)

    buf_out = io.StringIO()
    buf_err = io.StringIO()
    try:
        # Prepend the "kanban" token so our top-level subparser routes here.
        argv = ["kanban", *tokens] if tokens else ["kanban"]
        args = wrap.parse_args(argv)
    except SystemExit as exc:
        return f"(usage error: {exc})"
    except argparse.ArgumentError as exc:
        return f"(usage error: {exc})"

    with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
        try:
            kanban_command(args)
        except SystemExit:
            pass
        except Exception as exc:
            print(f"error: {exc}", file=sys.stderr)

    out = buf_out.getvalue().rstrip()
    err = buf_err.getvalue().rstrip()
    if err and out:
        return f"{out}\n{err}"
    return err if err else (out or "(no output)")
