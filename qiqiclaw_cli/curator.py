"""CLI subcommand: `qiqiclaw curator <subcommand>`.

Thin shell around agent/curator.py and tools/skill_usage.py. Renders a status
table, triggers a run, pauses/resumes, and pins/unpins skills.

This module intentionally has no side effects at import time — main.py wires
the argparse subparsers on demand.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional


def _fmt_ts(ts: Optional[str]) -> str:
    if not ts:
        return "从未"
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return str(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}秒前"
    if secs < 3600:
        return f"{secs // 60}分钟前"
    if secs < 86400:
        return f"{secs // 3600}小时前"
    return f"{secs // 86400}天前"


def _cmd_status(args) -> int:
    from agent import curator
    from tools import skill_usage

    state = curator.load_state()
    enabled = curator.is_enabled()
    paused = state.get("paused", False)
    last_run = state.get("last_run_at")
    summary = state.get("last_run_summary") or "(无)"
    runs = state.get("run_count", 0)

    status_line = (
        "已启用" if enabled and not paused else
        "已暂停" if paused else
        "已禁用"
    )
    print(f"策展器: {status_line}")
    print(f"  运行次数:       {runs}")
    print(f"  上次运行:       {_fmt_ts(last_run)}")
    print(f"  上次摘要:       {summary}")
    _report = state.get("last_report_path")
    if _report:
        print(f"  上次报告:       {_report}")
    _ih = curator.get_interval_hours()
    _interval_label = (
        f"{_ih // 24}天" if _ih % 24 == 0 and _ih >= 24
        else f"{_ih}小时"
    )
    print(f"  运行间隔:       每 {_interval_label}")
    print(f"  过期时间:       {curator.get_stale_after_days()}天未使用")
    print(f"  归档时间:       {curator.get_archive_after_days()}天未使用")

    rows = skill_usage.agent_created_report()
    if not rows:
        print("\nno agent-created skills")
        return 0

    by_state = {"active": [], "stale": [], "archived": []}
    pinned = []
    for r in rows:
        state_name = r.get("state", "active")
        by_state.setdefault(state_name, []).append(r)
        if r.get("pinned"):
            pinned.append(r["name"])

    print(f"\n代理创建的技能: 共 {len(rows)} 个")
    state_name_map = {"active": "活跃", "stale": "过期", "archived": "已归档"}
    for state_name in ("active", "stale", "archived"):
        bucket = by_state.get(state_name, [])
        print(f"  {state_name_map[state_name]:10s} {len(bucket)}")

    if pinned:
        print(f"\n已固定 ({len(pinned)}): {', '.join(pinned)}")

    # Show top 5 least-recently-active skills. Views and edits are activity too:
    # curator should not report a skill as "never used" right after skill_view()
    # or skill_manage() touched it.
    active = sorted(
        by_state.get("active", []),
        key=lambda r: r.get("last_activity_at") or r.get("created_at") or "",
    )[:5]
    if active:
        print("\nleast recently active (top 5):")
        for r in active:
            last = _fmt_ts(r.get("last_activity_at"))
            print(
                f"  {r['name']:40s}  "
                f"activity={r.get('activity_count', 0):3d}  "
                f"使用={r.get('use_count', 0):3d}  "
                f"查看={r.get('view_count', 0):3d}  "
                f"补丁={r.get('patch_count', 0):3d}  "
                f"上次活动={last}"
            )

    # Show top 5 most-active and least-active skills by activity_count
    # (use + view + patch). This is a different signal from
    # least-recently-active: activity_count reflects frequency,
    # last_activity_at reflects recency. A skill touched 30 times a year
    # ago is high-frequency but stale; a skill touched once yesterday is
    # recent but low-frequency. Both can matter.
    active_all = by_state.get("active", [])
    if active_all:
        most_active = sorted(
            active_all,
            key=lambda r: (r.get("activity_count") or 0, r.get("last_activity_at") or ""),
            reverse=True,
        )[:5]
        if most_active and (most_active[0].get("activity_count") or 0) > 0:
            print("\nmost active (top 5):")
            for r in most_active:
                last = _fmt_ts(r.get("last_activity_at"))
                print(
                    f"  {r['name']:40s}  "
                    f"activity={r.get('activity_count', 0):3d}  "
                    f"使用={r.get('use_count', 0):3d}  "
                    f"查看={r.get('view_count', 0):3d}  "
                    f"补丁={r.get('patch_count', 0):3d}  "
                    f"上次活动={last}"
                )

        least_active = sorted(
            active_all,
            key=lambda r: (r.get("activity_count") or 0, r.get("last_activity_at") or ""),
        )[:5]
        if least_active:
            print("\nleast active (top 5):")
            for r in least_active:
                last = _fmt_ts(r.get("last_activity_at"))
                print(
                    f"  {r['name']:40s}  "
                    f"activity={r.get('activity_count', 0):3d}  "
                    f"使用={r.get('use_count', 0):3d}  "
                    f"查看={r.get('view_count', 0):3d}  "
                    f"补丁={r.get('patch_count', 0):3d}  "
                    f"上次活动={last}"
                )

    return 0


def _cmd_run(args) -> int:
    from agent import curator
    if not curator.is_enabled():
        print("策展器: 已通过配置禁用; 使用 `curator.enabled: true` 启用")
        return 1

    print("策展器: 正在运行审查流程...")

    def _on_summary(msg: str) -> None:
        print(msg)

    result = curator.run_curator_review(
        on_summary=_on_summary,
        synchronous=bool(args.synchronous),
    )
    auto = result.get("auto_transitions", {})
    if auto:
        print(
            f"自动: 已检查={auto.get('checked', 0)} "
            f"已标记过期={auto.get('marked_stale', 0)} "
            f"已归档={auto.get('archived', 0)} "
            f"已重新激活={auto.get('reactivated', 0)}"
        )
    if not args.synchronous:
        print("LLM 流程正在后台运行 — 稍后检查 `qiqiclaw curator status`")
    return 0


def _cmd_pause(args) -> int:
    from agent import curator
    curator.set_paused(True)
    print("策展器: 已暂停")
    return 0


def _cmd_resume(args) -> int:
    from agent import curator
    curator.set_paused(False)
    print("策展器: 已恢复")
    return 0


def _cmd_pin(args) -> int:
    from tools import skill_usage
    if not skill_usage.is_agent_created(args.skill):
        print(
            f"策展器: '{args.skill}' 是捆绑(bundled)或从中心(hub)安装的 — 无法固定 "
            "(只有代理创建的技能参与策展)"
        )
        return 1
    skill_usage.set_pinned(args.skill, True)
    print(f"策展器: 已固定 '{args.skill}' (将绕过自动转换)")
    return 0


def _cmd_unpin(args) -> int:
    from tools import skill_usage
    if not skill_usage.is_agent_created(args.skill):
        print(
            f"策展器: '{args.skill}' 是捆绑(bundled)或从中心(hub)安装的 — "
            "没有需要取消固定的内容 (策展器只跟踪代理创建的技能)"
        )
        return 1
    skill_usage.set_pinned(args.skill, False)
    print(f"策展器: 已取消固定 '{args.skill}'")
    return 0


def _cmd_restore(args) -> int:
    from tools import skill_usage
    ok, msg = skill_usage.restore_skill(args.skill)
    print(f"策展器: {msg}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# argparse wiring (called from qiqiclaw_cli.main)
# ---------------------------------------------------------------------------

def register_cli(parent: argparse.ArgumentParser) -> None:
    """Attach `curator` subcommands to *parent*.

    main.py calls this with the ArgumentParser returned by
    ``subparsers.add_parser("curator", ...)``.
    """
    parent.set_defaults(func=lambda a: (parent.print_help(), 0)[1])
    subs = parent.add_subparsers(dest="curator_command")

    p_status = subs.add_parser("status", help="Show curator status and skill stats")
    p_status.set_defaults(func=_cmd_status)

    p_run = subs.add_parser("run", help="Trigger a curator review now")
    p_run.add_argument(
        "--sync", "--synchronous", dest="synchronous", action="store_true",
        help="Wait for the LLM review pass to finish (default: background thread)",
    )
    p_run.set_defaults(func=_cmd_run)

    p_pause = subs.add_parser("pause", help="Pause the curator until resumed")
    p_pause.set_defaults(func=_cmd_pause)

    p_resume = subs.add_parser("resume", help="Resume a paused curator")
    p_resume.set_defaults(func=_cmd_resume)

    p_pin = subs.add_parser("pin", help="Pin a skill so the curator never auto-transitions it")
    p_pin.add_argument("skill", help="Skill name")
    p_pin.set_defaults(func=_cmd_pin)

    p_unpin = subs.add_parser("unpin", help="Unpin a skill")
    p_unpin.add_argument("skill", help="Skill name")
    p_unpin.set_defaults(func=_cmd_unpin)

    p_restore = subs.add_parser("restore", help="Restore an archived skill")
    p_restore.add_argument("skill", help="Skill name")
    p_restore.set_defaults(func=_cmd_restore)


def cli_main(argv=None) -> int:
    """Standalone entry (also usable by qiqiclaw_cli.main fallthrough)."""
    parser = argparse.ArgumentParser(prog="qiqiclaw curator")
    register_cli(parser)
    args = parser.parse_args(argv)
    fn = getattr(args, "func", None)
    if fn is None:
        parser.print_help()
        return 0
    return int(fn(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(cli_main())
