"""qiqiclaw claw — OpenClaw 迁移命令。

用法:
    qiqiclaw claw migrate              # 预览后迁移（始终先显示预览）
    qiqiclaw claw migrate --dry-run    # 仅预览，不做更改
    qiqiclaw claw migrate --yes        # 跳过确认提示
    qiqiclaw claw migrate --preset full --overwrite --migrate-secrets  # 完整运行（包含密钥）
    qiqiclaw claw migrate --no-backup  # 跳过迁移前快照
    qiqiclaw claw cleanup              # 归档剩余的 OpenClaw 目录
    qiqiclaw claw cleanup --dry-run    # 预览将要归档的内容
"""

import importlib.util
import logging
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from qiqiclaw_cli.config import get_qiqiclaw_home, get_config_path, load_config, save_config
from qiqiclaw_constants import get_optional_skills_dir
from qiqiclaw_cli.setup import (
    Colors,
    color,
    print_header,
    print_info,
    print_success,
    print_error,
    prompt_yes_no,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.resolve()

_OPENCLAW_SCRIPT = (
    get_optional_skills_dir(PROJECT_ROOT / "optional-skills")
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_qiqiclaw.py"
)

# Fallback: user may have installed the skill from the Hub
_OPENCLAW_SCRIPT_INSTALLED = (
    get_qiqiclaw_home()
    / "skills"
    / "migration"
    / "openclaw-migration"
    / "scripts"
    / "openclaw_to_qiqiclaw.py"
)

# Known OpenClaw directory names (current + legacy)
_OPENCLAW_DIR_NAMES = (".openclaw", ".clawdbot", ".moltbot")

def _detect_openclaw_processes() -> list[str]:
    """Detect running OpenClaw processes and services.

    Returns a list of human-readable descriptions of what was found.
    An empty list means nothing was detected.
    """
    found: list[str] = []

    # -- systemd service (Linux) ------------------------------------------
    if sys.platform != "win32":
        try:
            result = subprocess.run(
                ["systemctl", "--user", "is-active", "openclaw-gateway.service"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip() == "active":
                found.append("systemd 服务: openclaw-gateway.service")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # -- process scan ------------------------------------------------------
    if sys.platform == "win32":
        try:
            for exe in ("openclaw.exe", "clawd.exe"):
                result = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {exe}"],
                    capture_output=True, text=True, timeout=5,
                )
                if exe in result.stdout.lower():
                    found.append(f"进程: {exe}")

            # Node.js-hosted OpenClaw — tasklist doesn't show command lines,
            # so fall back to PowerShell.
            ps_cmd = (
                'Get-CimInstance Win32_Process -Filter "Name = \'node.exe\'" | '
                'Where-Object { $_.CommandLine -match "openclaw|clawd" } | '
                'Select-Object -First 1 ProcessId'
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                found.append(f"node.exe 进程，命令行中包含 openclaw（PID {result.stdout.strip()}）")
        except Exception:
            pass
    else:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "openclaw"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split()
                found.append(f"openclaw 进程（PID: {', '.join(pids)}）")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return found


def _warn_if_openclaw_running(auto_yes: bool) -> None:
    """Warn if OpenClaw is still running before migration.

    Telegram, Discord, and Slack only allow one active connection per bot
    token. Migrating while OpenClaw is running causes both to fight for the
    same token.
    """
    running = _detect_openclaw_processes()
    if not running:
        return

    print()
    print_error("OpenClaw appears to be running:")
    for detail in running:
        print_info(f"  * {detail}")
    print_info(
        "消息平台（Telegram、Discord、Slack）每个机器人令牌只允许一个活动会话。"
        "如果继续，OpenClaw 和 QiQiClaw 可能会尝试使用相同的令牌，导致连接断开。"
    )
    print_info("建议: 在迁移前停止 OpenClaw。")
    print()
    if auto_yes:
        return
    if not sys.stdin.isatty():
        print_info("Non-interactive session — continuing with preview only.")
        return
    if not prompt_yes_no("仍要继续吗?", default=False):
        print_info("Migration cancelled. Please stop OpenClaw and retry.")
        sys.exit(0)


def _warn_if_gateway_running(auto_yes: bool) -> None:
    """Check if a QiQiClaw gateway is running with connected platforms.

    Migrating bot tokens while the gateway is polling will cause conflicts
    (e.g. Telegram 409 "terminated by other getUpdates request"). Warn the
    user and let them decide whether to continue.
    """
    from gateway.status import get_running_pid, read_runtime_status

    if not get_running_pid():
        return

    data = read_runtime_status() or {}
    platforms = data.get("platforms") or {}
    connected = [name for name, info in platforms.items()
                 if isinstance(info, dict) and info.get("state") == "connected"]
    if not connected:
        return

    print()
    print_error(
        "QiQi 网关正在运行，并有活动连接: "
        + ", ".join(connected)
    )
    print_info(
        "在网关活动时迁移机器人令牌会导致冲突"
        "（Telegram、Discord 和 Slack 每个令牌只允许一个活动会话）。"
    )
    print_info("建议: 先使用 'qiqiclaw stop' 停止网关。")
    print()
    if not auto_yes and not prompt_yes_no("仍要继续吗?", default=False):
        print_info("迁移已取消。请停止网关后重试。")
        sys.exit(0)

# State files commonly found in OpenClaw workspace directories — listed
# during cleanup to help the user decide whether to archive
_WORKSPACE_STATE_GLOBS = (
    "*/todo.json",
    "*/sessions/*",
    "*/memory/*.json",
    "*/logs/*",
)


def _find_migration_script() -> Path | None:
    """Find the openclaw_to_qiqiclaw.py script in known locations."""
    for candidate in [_OPENCLAW_SCRIPT, _OPENCLAW_SCRIPT_INSTALLED]:
        if candidate.exists():
            return candidate
    return None


def _load_migration_module(script_path: Path):
    """Dynamically load the migration script as a module."""
    spec = importlib.util.spec_from_file_location("openclaw_to_qiqiclaw", script_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules so @dataclass can resolve the module
    # (Python 3.11+ requires this for dynamically loaded modules)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return mod


def _find_openclaw_dirs() -> list[Path]:
    """Find all OpenClaw directories on disk."""
    found = []
    for name in _OPENCLAW_DIR_NAMES:
        candidate = Path.home() / name
        if candidate.is_dir():
            found.append(candidate)
    return found


def _scan_workspace_state(source_dir: Path) -> list[tuple[Path, str]]:
    """Scan an OpenClaw directory for workspace state files.

    Returns a list of (path, description) tuples.
    """
    findings: list[tuple[Path, str]] = []

    # Direct state files in the root
    for name in ("todo.json", "sessions", "logs"):
        candidate = source_dir / name
        if candidate.exists():
            kind = "directory" if candidate.is_dir() else "file"
            findings.append((candidate, f"Root {kind}: {name}"))

    # State files inside workspace directories
    for child in sorted(source_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # Check for workspace-like subdirectories
        for state_name in ("todo.json", "sessions", "logs", "memory"):
            state_path = child / state_name
            if state_path.exists():
                kind = "directory" if state_path.is_dir() else "file"
                rel = state_path.relative_to(source_dir).as_posix()
                findings.append((state_path, f"Workspace {kind}: {rel}"))

    return findings


def _archive_directory(source_dir: Path, dry_run: bool = False) -> Path:
    """Rename an OpenClaw directory to .pre-migration.

    Returns the archive path.
    """
    timestamp = datetime.now().strftime("%Y%m%d")
    archive_name = f"{source_dir.name}.pre-migration"
    archive_path = source_dir.parent / archive_name

    # If archive already exists, add timestamp
    if archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}"
        archive_path = source_dir.parent / archive_name

    # If still exists (multiple runs same day), add counter
    counter = 2
    while archive_path.exists():
        archive_name = f"{source_dir.name}.pre-migration-{timestamp}-{counter}"
        archive_path = source_dir.parent / archive_name
        counter += 1

    if not dry_run:
        source_dir.rename(archive_path)

    return archive_path


def claw_command(args):
    """Route qiqiclaw claw subcommands."""
    action = getattr(args, "claw_action", None)

    if action == "migrate":
        _cmd_migrate(args)
    elif action in ("cleanup", "clean"):
        _cmd_cleanup(args)
    else:
        print("用法: qiqiclaw claw <命令> [选项]")
        print()
        print("命令:")
        print("  migrate          从 OpenClaw 迁移设置到 QiQiClaw")
        print("  cleanup          归档迁移后剩余的 OpenClaw 目录")
        print()
        print("运行 'qiqiclaw claw <命令> --help' 查看选项。")


def _cmd_migrate(args):
    """Run the OpenClaw → QiQiClaw migration."""
    # Check current and legacy OpenClaw directories
    explicit_source = getattr(args, "source", None)
    if explicit_source:
        source_dir = Path(explicit_source)
    else:
        source_dir = Path.home() / ".openclaw"
        if not source_dir.is_dir():
            # Try legacy directory names
            for legacy in (".clawdbot", ".moltbot"):
                candidate = Path.home() / legacy
                if candidate.is_dir():
                    source_dir = candidate
                    break
    dry_run = getattr(args, "dry_run", False)
    preset = getattr(args, "preset", "full")
    overwrite = getattr(args, "overwrite", False)
    migrate_secrets = getattr(args, "migrate_secrets", False)
    workspace_target = getattr(args, "workspace_target", None)
    skill_conflict = getattr(args, "skill_conflict", "skip")
    no_backup = getattr(args, "no_backup", False)

    # Secrets are never included implicitly — they must be explicitly requested
    # via --migrate-secrets, even under --preset full.  This mirrors OpenClaw's
    # migrate-qiqiclaw posture (two-phase: run once without secrets, rerun with
    # --include-secrets) and prevents a --preset full invocation from silently
    # importing API keys that the user may not have intended to copy.

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          Q QiQiClaw — OpenClaw Migration             │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # Check source directory
    if not source_dir.is_dir():
        print()
        print_error(f"OpenClaw directory not found: {source_dir}")
        print_info("请确保您的 OpenClaw 安装在预期路径。")
        print_info("您可以指定自定义路径: qiqiclaw claw migrate --source /path/to/.openclaw")
        return

    # Find the migration script
    script_path = _find_migration_script()
    if not script_path:
        print()
        print_error("Migration script not found.")
        print_info("预期位置:")
        print_info(f"  {_OPENCLAW_SCRIPT}")
        print_info(f"  {_OPENCLAW_SCRIPT_INSTALLED}")
        print_info("请确保已安装 openclaw-migration 技能。")
        return

    # Show what we're doing
    qiqiclaw_home = get_qiqiclaw_home()
    auto_yes = getattr(args, "yes", False)
    print()
    print_header("迁移设置")
    print_info(f"源目录:      {source_dir}")
    print_info(f"目标目录:    {qiqiclaw_home}")
    print_info(f"预设:        {preset}")
    print_info(f"覆盖:        {'是' if overwrite else '否（跳过冲突）'}")
    print_info(f"密钥:        {'是（仅允许列表）' if migrate_secrets else '否'}")
    if skill_conflict != "skip":
        print_info(f"技能冲突:    {skill_conflict}")
    if workspace_target:
        print_info(f"工作区:      {workspace_target}")
    print()

    # Check if OpenClaw is still running — migrating tokens while both are
    # active will cause conflicts (e.g. Telegram 409).
    _warn_if_openclaw_running(auto_yes)

    # Check if a QiQiClaw gateway is running with connected platforms.
    _warn_if_gateway_running(auto_yes)

    # Ensure config.yaml exists before migration tries to read it
    config_path = get_config_path()
    if not config_path.exists():
        save_config(load_config())

    # Load the migration module
    try:
        mod = _load_migration_module(script_path)
        if mod is None:
            print_error("Could not load migration script.")
            return
    except Exception as e:
        print()
        print_error(f"Could not load migration script: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        return

    selected = mod.resolve_selected_options(None, None, preset=preset)
    ws_target = Path(workspace_target).resolve() if workspace_target else None

    # ── Phase 1: Always preview first ──────────────────────────
    try:
        preview = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=qiqiclaw_home.resolve(),
            execute=False,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        preview_report = preview.migrate()
    except Exception as e:
        print()
        print_error(f"迁移预览失败: {e}")
        logger.debug("OpenClaw migration preview error", exc_info=True)
        return

    preview_summary = preview_report.get("summary", {})
    preview_count = preview_summary.get("migrated", 0)
    preview_conflicts = preview_summary.get("conflict", 0)

    # "Nothing to migrate" means nothing migrated AND nothing blocked by
    # conflicts.  If there are conflicts, we still want to show the plan and
    # surface the refusal/--overwrite guidance instead of silently bailing.
    if preview_count == 0 and preview_conflicts == 0:
        print()
        print_info("没有需要从 OpenClaw 迁移的内容。")
        _print_migration_report(preview_report, dry_run=True)
        return

    print()
    if preview_count > 0:
        print_header(f"迁移预览 — 将导入 {preview_count} 项")
    else:
        print_header(
            f"迁移预览 — {preview_conflicts} 个冲突，不会导入任何内容"
        )
    print_info("尚未进行任何更改。请查看以下列表:")
    _print_migration_report(preview_report, dry_run=True)

    # If --dry-run, stop here
    if dry_run:
        return

    # ── Phase 1b: Refuse if the plan has conflicts and --overwrite is not set ─
    # Modelled on OpenClaw's assertConflictFreePlan() — apply is a safe no-op
    # on conflicts unless the user explicitly opts in to overwriting.  Without
    # this guard, the user would answer "yes, proceed" and silently end up
    # with a migration that skipped every conflicting item.
    if preview_conflicts > 0 and not overwrite:
        print()
        print_error(
            f"计划有 {preview_conflicts} 个冲突。拒绝应用。"
        )
        print_info(
            "每个冲突都是目标已存在于 ~/.qiqiclaw/ 中的项。"
            "使用 --overwrite 重新运行以替换冲突的目标（项级别的"
            "备份将写入迁移报告目录）。"
        )
        print_info("或使用 --dry-run 重新运行以查看完整计划。")
        return

    # ── Phase 2: Confirm and execute ───────────────────────────
    print()
    if not auto_yes:
        if not sys.stdin.isatty():
            print_info("非交互式会话 — 仅预览。")
            print_info("要执行，请使用以下命令重新运行: qiqiclaw claw migrate --yes")
            return
        if not prompt_yes_no("继续迁移吗?", default=True):
            print_info("Migration cancelled.")
            return

    # ── Phase 2b: Pre-apply backup of the QiQiClaw home ─────────
    # Delegates to qiqiclaw_cli.backup.create_pre_migration_backup(), which
    # shares implementation with the pre-update backup (same exclusion
    # rules, same SQLite safe-copy, zip format) so the archive is
    # restorable with `qiqiclaw import`.  Mirrors OpenClaw's
    # createPreMigrationBackup posture — one atomic restore point before
    # any mutation, auto-pruned to the last 5 pre-migration zips.
    backup_archive: Optional[Path] = None
    if not no_backup:
        try:
            from qiqiclaw_cli.backup import create_pre_migration_backup, _format_size
            backup_archive = create_pre_migration_backup(qiqiclaw_home=qiqiclaw_home)
            if backup_archive:
                size_str = _format_size(backup_archive.stat().st_size)
                print()
                print_success(f"迁移前备份: {backup_archive} ({size_str})")
                print_info(f"使用以下命令恢复: qiqiclaw import {backup_archive.name}")
        except Exception as e:
            print()
            print_error(f"无法创建迁移前备份: {e}")
            print_info(
                "使用 --no-backup 重新运行以跳过，或释放 QiQiClaw 主目录下的磁盘空间。"
            )
            logger.debug("Pre-migration backup error", exc_info=True)
            return

    try:
        migrator = mod.Migrator(
            source_root=source_dir.resolve(),
            target_root=qiqiclaw_home.resolve(),
            execute=True,
            workspace_target=ws_target,
            overwrite=overwrite,
            migrate_secrets=migrate_secrets,
            output_dir=None,
            selected_options=selected,
            preset_name=preset,
            skill_conflict_mode=skill_conflict,
        )
        report = migrator.migrate()
    except Exception as e:
        print()
        print_error(f"迁移失败: {e}")
        logger.debug("OpenClaw migration error", exc_info=True)
        if backup_archive:
            print_info(f"迁移前备份位于: {backup_archive}")
            print_info(f"使用以下命令恢复: qiqiclaw import {backup_archive.name}")
        return

    # Print results
    _print_migration_report(report, dry_run=False)

    # Source directory is left untouched — archiving is not the migration
    # tool's responsibility.  Users who want to clean up can run
    # 'qiqiclaw claw cleanup' separately.


def _cmd_cleanup(args):
    """Archive leftover OpenClaw directories after migration.

    Scans for OpenClaw directories that still exist after migration and offers
    to rename them to .pre-migration to free disk space.
    """
    dry_run = getattr(args, "dry_run", False)
    auto_yes = getattr(args, "yes", False)
    explicit_source = getattr(args, "source", None)

    print()
    print(
        color(
            "┌─────────────────────────────────────────────────────────┐",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "│          Q QiQiClaw — OpenClaw Cleanup               │",
            Colors.MAGENTA,
        )
    )
    print(
        color(
            "└─────────────────────────────────────────────────────────┘",
            Colors.MAGENTA,
        )
    )

    # Find OpenClaw directories
    if explicit_source:
        dirs_to_check = [Path(explicit_source)]
    else:
        dirs_to_check = _find_openclaw_dirs()

    if not dirs_to_check:
        print()
        print_success("No OpenClaw directories found. Nothing to clean up.")
        return

    # Warn if OpenClaw is still running — archiving while the service is
    # active causes it to recreate an empty skeleton directory (#8502).
    running = _detect_openclaw_processes()
    if running:
        print()
        print_error("OpenClaw 似乎仍在运行:")
        for detail in running:
            print_info(f"  * {detail}")
        print_info(
            "在服务活动时归档 .openclaw/ 可能会导致它"
            "立即重新创建一个空的骨架目录，破坏您的配置。"
        )
        print_info("请先停止 OpenClaw: systemctl --user stop openclaw-gateway.service")
        print()
        if not auto_yes:
            if not sys.stdin.isatty():
                print_info("非交互式会话 — 中止。请停止 OpenClaw 后重新运行。")
                return
            if not prompt_yes_no("仍要继续吗?", default=False):
                print_info("已中止。请先停止 OpenClaw，然后重新运行: qiqiclaw claw cleanup")
                return

    total_archived = 0

    for source_dir in dirs_to_check:
        print()
        print_header(f"发现: {source_dir}")

        # Scan for state files
        state_files = _scan_workspace_state(source_dir)

        # Show directory stats
        try:
            workspace_dirs = [
                d for d in source_dir.iterdir()
                if d.is_dir() and not d.name.startswith(".")
                and any((d / name).exists() for name in ("todo.json", "SOUL.md", "MEMORY.md", "USER.md"))
            ]
        except OSError:
            workspace_dirs = []

        if workspace_dirs:
            print_info(f"工作区目录: {len(workspace_dirs)}")
            for ws in workspace_dirs[:5]:
                items = []
                if (ws / "todo.json").exists():
                    items.append("todo.json")
                if (ws / "sessions").is_dir():
                    items.append("sessions/")
                if (ws / "SOUL.md").exists():
                    items.append("SOUL.md")
                if (ws / "MEMORY.md").exists():
                    items.append("MEMORY.md")
                detail = ", ".join(items) if items else "空"
                print(f"      {ws.name}/  ({detail})")
            if len(workspace_dirs) > 5:
                print(f"      ... 以及另外 {len(workspace_dirs) - 5} 个")

        if state_files:
            print()
            print(color(f"  发现 {len(state_files)} 个状态文件:", Colors.YELLOW))
            for path, desc in state_files[:8]:
                print(f"      {desc}")
            if len(state_files) > 8:
                print(f"      ... 以及另外 {len(state_files) - 8} 个")

        print()

        if dry_run:
            archive_path = _archive_directory(source_dir, dry_run=True)
            print_info(f"Would archive: {source_dir} → {archive_path}")
        elif not auto_yes and not sys.stdin.isatty():
            print_info(f"非交互式会话 — 将归档: {source_dir}")
            print_info("要执行，请使用以下命令重新运行: qiqiclaw claw cleanup --yes")
        else:
            if auto_yes or prompt_yes_no(f"归档 {source_dir}?", default=True):
                try:
                    archive_path = _archive_directory(source_dir)
                    print_success(f"Archived: {source_dir} → {archive_path}")
                    total_archived += 1
                except OSError as e:
                    print_error(f"无法归档: {e}")
                    print_info(f"请尝试手动操作: mv {source_dir} {source_dir}.pre-migration")
            else:
                print_info("Skipped.")

    # Summary
    print()
    if dry_run:
        print_info(f"Dry run complete. Would archive {len(dirs_to_check)} directorie(s).")
        print_info("不使用 --dry-run 运行以归档它们。")
    elif total_archived:
        print_success(f"Cleaned up {total_archived} OpenClaw directorie(s).")
        print_info("目录已重命名，未删除。您可以通过重命名回来撤销操作。")
    else:
        print_info("未归档任何目录。")


def _print_migration_report(report: dict, dry_run: bool):
    """Print a formatted migration report."""
    summary = report.get("summary", {})
    migrated = summary.get("migrated", 0)
    skipped = summary.get("skipped", 0)
    conflicts = summary.get("conflict", 0)
    errors = summary.get("error", 0)

    print()
    if dry_run:
        print_header("Dry Run Results")
        print_info("未修改任何文件。这是将要发生的操作的预览。")
    else:
        print_header("Migration Results")

    print()

    # Detailed items
    items = report.get("items", [])
    if items:
        # Group by status
        migrated_items = [i for i in items if i.get("status") == "migrated"]
        skipped_items = [i for i in items if i.get("status") == "skipped"]
        conflict_items = [i for i in items if i.get("status") == "conflict"]
        error_items = [i for i in items if i.get("status") == "error"]

        if migrated_items:
            label = "Would migrate" if dry_run else "Migrated"
            print(color(f"  ✓ {label}:", Colors.GREEN))
            for item in migrated_items:
                kind = item.get("kind", "unknown")
                dest = item.get("destination", "")
                if dest:
                    dest_short = str(dest).replace(str(Path.home()), "~")
                    print(f"      {kind:<22s} → {dest_short}")
                else:
                    print(f"      {kind}")
            print()

        if conflict_items:
            print(color("  ⚠ 冲突（已跳过 — 使用 --overwrite 强制）:", Colors.YELLOW))
            for item in conflict_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "已存在")
                print(f"      {kind:<22s}  {reason}")
            print()

        if skipped_items:
            print(color("  ─ 已跳过:", Colors.DIM))
            for item in skipped_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "")
                print(f"      {kind:<22s}  {reason}")
            print()

        if error_items:
            print(color("  ✗ 错误:", Colors.RED))
            for item in error_items:
                kind = item.get("kind", "unknown")
                reason = item.get("reason", "未知错误")
                print(f"      {kind:<22s}  {reason}")
            print()

    # Summary line
    parts = []
    if migrated:
        action = "would migrate" if dry_run else "migrated"
        parts.append(f"{migrated} {action}")
    if conflicts:
        parts.append(f"{conflicts} 个冲突")
    if skipped:
        parts.append(f"{skipped} skipped")
    if errors:
        parts.append(f"{errors} 个错误")

    if parts:
        print_info(f"摘要: {', '.join(parts)}")
    else:
        print_info("Nothing to migrate.")

    # Output directory
    output_dir = report.get("output_dir")
    if output_dir:
        print_info(f"Full report saved to: {output_dir}")

    if dry_run:
        print()
        print_info("要执行迁移，请不使用 --dry-run 运行:")
        print_info(f"  qiqiclaw claw migrate --preset {report.get('preset', 'full')}")
    elif migrated:
        print()
        print_success("Migration complete!")
        # Warn if API keys were skipped (migrate_secrets not enabled)
        skipped_keys = [
            i for i in report.get("items", [])
            if i.get("kind") == "provider-keys" and i.get("status") == "skipped"
        ]
        if skipped_keys:
            print()
            print(color("  ⚠ API 密钥未迁移（默认禁用密钥迁移）。", Colors.YELLOW))
            print(color("  您的 OPENROUTER_API_KEY 和其他提供商密钥必须手动添加。", Colors.YELLOW))
            print()
            print_info("要迁移 API 密钥，请使用以下命令重新运行:")
            print_info("  qiqiclaw claw migrate --migrate-secrets")
            print()
            print_info("或手动添加您的密钥:")
            print_info("  qiqiclaw config set OPENROUTER_API_KEY sk-or-v1-...")
