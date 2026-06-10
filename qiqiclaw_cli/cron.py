"""
Cron subcommand for QiQiClaw CLI.

Handles standalone cron management commands like list, create, edit,
pause/resume/run/remove, status, and tick.
"""

import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional
from qiqiclaw_constants import ensure_project_root_on_syspath

PROJECT_ROOT = ensure_project_root_on_syspath()

from qiqiclaw_cli.colors import Colors, color


def _normalize_skills(single_skill=None, skills: Optional[Iterable[str]] = None) -> Optional[List[str]]:
    if skills is None:
        if single_skill is None:
            return None
        raw_items = [single_skill]
    else:
        raw_items = list(skills)

    normalized: List[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _cron_api(**kwargs):
    from tools.cronjob_tools import cronjob as cronjob_tool

    return json.loads(cronjob_tool(**kwargs))


def cron_list(show_all: bool = False):
    """List all scheduled jobs."""
    from cron.jobs import list_jobs

    jobs = list_jobs(include_disabled=show_all)

    if not jobs:
        print(color("无计划任务。", Colors.DIM))
        print(color("使用 'qiqiclaw cron create ...' 或聊天中的 /cron 命令创建一个。", Colors.DIM))
        return

    print()
    print(color("┌─────────────────────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│                           计划任务                                       │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────────────────────┘", Colors.CYAN))
    print()

    for job in jobs:
        job_id = job.get("id", "?")
        name = job.get("name", "(unnamed)")
        schedule = job.get("schedule_display", job.get("schedule", {}).get("value", "?"))
        state = job.get("state", "scheduled" if job.get("enabled", True) else "paused")
        next_run = job.get("next_run_at", "?")

        repeat_info = job.get("repeat", {})
        repeat_times = repeat_info.get("times")
        repeat_completed = repeat_info.get("completed", 0)
        repeat_str = f"{repeat_completed}/{repeat_times}" if repeat_times else "∞"

        deliver = job.get("deliver", ["local"])
        if isinstance(deliver, str):
            deliver = [deliver]
        deliver_str = ", ".join(deliver)

        skills = job.get("skills") or ([job["skill"]] if job.get("skill") else [])
        if state == "paused":
            status = color("[已暂停]", Colors.YELLOW)
        elif state == "completed":
            status = color("[已完成]", Colors.BLUE)
        elif job.get("enabled", True):
            status = color("[活动中]", Colors.GREEN)
        else:
            status = color("[已禁用]", Colors.RED)

        print(f"  {color(job_id, Colors.YELLOW)} {status}")
        print(f"    名称:      {name}")
        print(f"    计划:      {schedule}")
        print(f"    重复:      {repeat_str}")
        print(f"    下次运行:  {next_run}")
        print(f"    交付:      {deliver_str}")
        if skills:
            print(f"    技能:      {', '.join(skills)}")
        script = job.get("script")
        if script:
            print(f"    脚本:      {script}")
        workdir = job.get("workdir")
        if workdir:
            print(f"    工作目录:  {workdir}")

        # Execution history
        last_status = job.get("last_status")
        if last_status:
            last_run = job.get("last_run_at", "?")
            if last_status == "ok":
                status_display = color("成功", Colors.GREEN)
            else:
                status_display = color(f"{last_status}: {job.get('last_error', '?')}", Colors.RED)
            print(f"    上次运行:  {last_run}  {status_display}")

        delivery_err = job.get("last_delivery_error")
        if delivery_err:
            print(f"    {color('⚠ 交付失败:', Colors.YELLOW)} {delivery_err}")

        print()

    from qiqiclaw_cli.gateway import find_gateway_pids
    if not find_gateway_pids():
        print(color("  ⚠  网关未运行 — 任务不会自动触发。", Colors.YELLOW))
        print(color("     启动命令: qiqiclaw gateway install", Colors.DIM))
        print(color("                sudo qiqiclaw gateway install --system  # Linux 服务器", Colors.DIM))
        print()


def cron_tick():
    """Run due jobs once and exit."""
    from cron.scheduler import tick
    tick(verbose=True)


def cron_status():
    """Show cron execution status."""
    from cron.jobs import list_jobs
    from qiqiclaw_cli.gateway import find_gateway_pids

    print()

    pids = find_gateway_pids()
    if pids:
        print(color("✓ 网关正在运行 — cron 任务将自动触发", Colors.GREEN))
        print(f"  PID: {', '.join(map(str, pids))}")
    else:
        print(color("✗ 网关未运行 — cron 任务不会触发", Colors.RED))
        print()
        print("  启用自动执行:")
        print("    qiqiclaw gateway install    # 安装为用户服务")
        print("    sudo qiqiclaw gateway install --system  # Linux 服务器: 开机启动系统服务")
        print("    qiqiclaw gateway            # 或在前台运行")

    print()

    jobs = list_jobs(include_disabled=False)
    if jobs:
        next_runs = [j.get("next_run_at") for j in jobs if j.get("next_run_at")]
        print(f"  {len(jobs)} 个活动任务")
        if next_runs:
            print(f"  下次运行: {min(next_runs)}")
    else:
        print("  无活动任务")

    print()


def cron_create(args):
    result = _cron_api(
        action="create",
        schedule=args.schedule,
        prompt=args.prompt,
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skill=getattr(args, "skill", None),
        skills=_normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None)),
        script=getattr(args, "script", None),
        workdir=getattr(args, "workdir", None),
    )
    if not result.get("success"):
        print(color(f"创建任务失败: {result.get('error', '未知错误')}", Colors.RED))
        return 1
    print(color(f"Created job: {result['job_id']}", Colors.GREEN))
    print(f"  名称: {result['name']}")
    print(f"  计划: {result['schedule']}")
    if result.get("skills"):
        print(f"  技能: {', '.join(result['skills'])}")
    job_data = result.get("job", {})
    if job_data.get("script"):
        print(f"  脚本: {job_data['script']}")
    if job_data.get("workdir"):
        print(f"  工作目录: {job_data['workdir']}")
    print(f"  下次运行: {result['next_run_at']}")
    return 0


def cron_edit(args):
    from cron.jobs import get_job

    job = get_job(args.job_id)
    if not job:
        print(color(f"未找到任务: {args.job_id}", Colors.RED))
        return 1

    existing_skills = list(job.get("skills") or ([] if not job.get("skill") else [job.get("skill")]))
    replacement_skills = _normalize_skills(getattr(args, "skill", None), getattr(args, "skills", None))
    add_skills = _normalize_skills(None, getattr(args, "add_skills", None)) or []
    remove_skills = set(_normalize_skills(None, getattr(args, "remove_skills", None)) or [])

    final_skills = None
    if getattr(args, "clear_skills", False):
        final_skills = []
    elif replacement_skills is not None:
        final_skills = replacement_skills
    elif add_skills or remove_skills:
        final_skills = [skill for skill in existing_skills if skill not in remove_skills]
        for skill in add_skills:
            if skill not in final_skills:
                final_skills.append(skill)

    result = _cron_api(
        action="update",
        job_id=args.job_id,
        schedule=getattr(args, "schedule", None),
        prompt=getattr(args, "prompt", None),
        name=getattr(args, "name", None),
        deliver=getattr(args, "deliver", None),
        repeat=getattr(args, "repeat", None),
        skills=final_skills,
        script=getattr(args, "script", None),
        workdir=getattr(args, "workdir", None),
    )
    if not result.get("success"):
        print(color(f"更新任务失败: {result.get('error', '未知错误')}", Colors.RED))
        return 1

    updated = result["job"]
    print(color(f"Updated job: {updated['job_id']}", Colors.GREEN))
    print(f"  名称: {updated['name']}")
    print(f"  计划: {updated['schedule']}")
    if updated.get("skills"):
        print(f"  技能: {', '.join(updated['skills'])}")
    else:
        print("  技能: 无")
    if updated.get("script"):
        print(f"  脚本: {updated['script']}")
    if updated.get("workdir"):
        print(f"  工作目录: {updated['workdir']}")
    return 0


def _job_action(action: str, job_id: str, success_verb: str) -> int:
    result = _cron_api(action=action, job_id=job_id)
    if not result.get("success"):
        print(color(f"{action} 任务失败: {result.get('error', '未知错误')}", Colors.RED))
        return 1
    job = result.get("job") or result.get("removed_job") or {}
    verb_map = {"已暂停": "Paused job", "已恢复": "Resumed job", "已触发": "Triggered job"}
    label = verb_map.get(success_verb, f"{success_verb} job")
    print(color(f"{label}: {job.get('name', job_id)} ({job_id})", Colors.GREEN))
    if action in {"resume", "run"} and result.get("job", {}).get("next_run_at"):
        print(f"  下次运行: {result['job']['next_run_at']}")
    if action == "run":
        print("  它将在下一个调度器周期运行。")
    return 0


def cron_command(args):
    """Handle cron subcommands."""
    subcmd = getattr(args, 'cron_command', None)

    if subcmd is None or subcmd == "list":
        show_all = getattr(args, 'all', False)
        cron_list(show_all)
        return 0

    if subcmd == "status":
        cron_status()
        return 0

    if subcmd == "tick":
        cron_tick()
        return 0

    if subcmd in {"create", "add"}:
        return cron_create(args)

    if subcmd == "edit":
        return cron_edit(args)

    if subcmd == "pause":
        return _job_action("pause", args.job_id, "已暂停")

    if subcmd == "resume":
        return _job_action("resume", args.job_id, "已恢复")

    if subcmd == "run":
        return _job_action("run", args.job_id, "已触发")

    if subcmd in {"remove", "rm", "delete"}:
        return _job_action("remove", args.job_id, "已删除")

    print(f"未知的 cron 命令: {subcmd}")
    print("用法: qiqiclaw cron [list|create|edit|pause|resume|run|remove|status|tick]")
    sys.exit(1)
