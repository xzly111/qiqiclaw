"""Git worktree and maintenance helpers for the CLI."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

active_worktree: Optional[Dict[str, str]] = None


def git_repo_root() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def path_is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def setup_worktree(repo_root: str = None) -> Optional[Dict[str, str]]:
    repo_root = repo_root or git_repo_root()
    if not repo_root:
        print("\033[31m✗ --worktree requires being inside a git repository.\033[0m")
        print("  cd into your project repo first, then run qiqiclaw -w")
        return None

    short_id = uuid.uuid4().hex[:8]
    wt_name = f"qiqiclaw-{short_id}"
    branch_name = f"qiqiclaw/{wt_name}"
    worktrees_dir = Path(repo_root) / ".worktrees"
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    wt_path = worktrees_dir / wt_name

    gitignore = Path(repo_root) / ".gitignore"
    ignore_entry = ".worktrees/"
    try:
        existing = gitignore.read_text() if gitignore.exists() else ""
        if ignore_entry not in existing.splitlines():
            with open(gitignore, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"{ignore_entry}\n")
    except Exception as exc:
        logger.debug("Could not update .gitignore: %s", exc)

    try:
        result = subprocess.run(
            ["git", "worktree", "add", str(wt_path), "-b", branch_name, "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
        )
        if result.returncode != 0:
            print(f"\033[31m✗ Failed to create worktree: {result.stderr.strip()}\033[0m")
            return None
    except Exception as exc:
        print(f"\033[31m✗ Failed to create worktree: {exc}\033[0m")
        return None

    include_file = Path(repo_root) / ".worktreeinclude"
    if include_file.exists():
        try:
            repo_root_resolved = Path(repo_root).resolve()
            wt_path_resolved = wt_path.resolve()
            for line in include_file.read_text().splitlines():
                entry = line.strip()
                if not entry or entry.startswith("#"):
                    continue
                src = Path(repo_root) / entry
                dst = wt_path / entry
                try:
                    src_resolved = src.resolve(strict=False)
                    dst_resolved = dst.resolve(strict=False)
                except (OSError, ValueError):
                    logger.debug("Skipping invalid .worktreeinclude entry: %s", entry)
                    continue
                if not path_is_within_root(src_resolved, repo_root_resolved):
                    logger.warning("Skipping .worktreeinclude entry outside repo root: %s", entry)
                    continue
                if not path_is_within_root(dst_resolved, wt_path_resolved):
                    logger.warning("Skipping .worktreeinclude entry that escapes worktree: %s", entry)
                    continue
                if src.is_file():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
                elif src.is_dir() and not dst.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    os.symlink(str(src_resolved), str(dst))
        except Exception as exc:
            logger.debug("Error copying .worktreeinclude entries: %s", exc)

    info = {"path": str(wt_path), "branch": branch_name, "repo_root": repo_root}
    print(f"\033[32m✓ Worktree created:\033[0m {wt_path}")
    print(f"  Branch: {branch_name}")
    return info


def cleanup_worktree(info: Dict[str, str] = None) -> None:
    global active_worktree
    info = info or active_worktree
    if not info:
        return

    wt_path = info["path"]
    branch = info["branch"]
    repo_root = info["repo_root"]
    if not Path(wt_path).exists():
        return

    has_unpushed = False
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=wt_path,
        )
        has_unpushed = bool(result.stdout.strip())
    except Exception:
        has_unpushed = True

    if has_unpushed:
        print(f"\n\033[33m⚠ Worktree has unpushed commits, keeping: {wt_path}\033[0m")
        print(f"  To clean up manually: git worktree remove --force {wt_path}")
        active_worktree = None
        return

    try:
        subprocess.run(
            ["git", "worktree", "remove", wt_path, "--force"],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=repo_root,
        )
    except Exception as exc:
        logger.debug("Failed to remove worktree: %s", exc)

    try:
        subprocess.run(
            ["git", "branch", "-D", branch],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
    except Exception as exc:
        logger.debug("Failed to delete branch %s: %s", branch, exc)

    active_worktree = None
    print(f"\033[32m✓ Worktree cleaned up: {wt_path}\033[0m")


def run_state_db_auto_maintenance(session_db) -> None:
    if session_db is None:
        return
    try:
        from qiqiclaw_cli.config import load_config as load_full_config
        from qiqiclaw_constants import get_qiqiclaw_home

        cfg = (load_full_config().get("sessions") or {})
        if not cfg.get("auto_prune", False):
            return
        session_db.maybe_auto_prune_and_vacuum(
            retention_days=int(cfg.get("retention_days", 90)),
            min_interval_hours=int(cfg.get("min_interval_hours", 24)),
            vacuum=bool(cfg.get("vacuum_after_prune", True)),
            sessions_dir=get_qiqiclaw_home() / "sessions",
        )
    except Exception as exc:
        logger.debug("state.db auto-maintenance skipped: %s", exc)


def run_checkpoint_auto_maintenance() -> None:
    try:
        from qiqiclaw_cli.config import load_config as load_full_config
        from tools.checkpoint_manager import maybe_auto_prune_checkpoints

        cfg = (load_full_config().get("checkpoints") or {})
        if not cfg.get("auto_prune", False):
            return
        maybe_auto_prune_checkpoints(
            retention_days=int(cfg.get("retention_days", 7)),
            min_interval_hours=int(cfg.get("min_interval_hours", 24)),
            delete_orphans=bool(cfg.get("delete_orphans", True)),
        )
    except Exception as exc:
        logger.debug("checkpoint auto-maintenance skipped: %s", exc)


def prune_stale_worktrees(repo_root: str, max_age_hours: int = 24) -> None:
    worktrees_dir = Path(repo_root) / ".worktrees"
    if not worktrees_dir.exists():
        prune_orphaned_branches(repo_root)
        return

    now = time.time()
    soft_cutoff = now - (max_age_hours * 3600)
    hard_cutoff = now - (max_age_hours * 3 * 3600)
    for entry in worktrees_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("qiqiclaw-"):
            continue
        try:
            mtime = entry.stat().st_mtime
            if mtime > soft_cutoff:
                continue
        except Exception:
            continue
        force = mtime <= hard_cutoff
        if not force:
            try:
                result = subprocess.run(
                    ["git", "log", "--oneline", "HEAD", "--not", "--remotes"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    cwd=str(entry),
                )
                if result.stdout.strip():
                    continue
            except Exception:
                continue
        try:
            branch_result = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=str(entry),
            )
            branch = branch_result.stdout.strip()
            subprocess.run(
                ["git", "worktree", "remove", str(entry), "--force"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=repo_root,
            )
            if branch:
                subprocess.run(
                    ["git", "branch", "-D", branch],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=repo_root,
                )
            logger.debug("Pruned stale worktree: %s (force=%s)", entry.name, force)
        except Exception as exc:
            logger.debug("Failed to prune worktree %s: %s", entry.name, exc)
    prune_orphaned_branches(repo_root)


def prune_orphaned_branches(repo_root: str) -> None:
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
        if result.returncode != 0:
            return
        all_branches = [b.strip() for b in result.stdout.strip().split("\n") if b.strip()]
    except Exception:
        return

    active_branches: set[str] = set()
    try:
        wt_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=repo_root,
        )
        for line in wt_result.stdout.split("\n"):
            if line.startswith("branch refs/heads/"):
                active_branches.add(line.split("branch refs/heads/", 1)[-1].strip())
    except Exception:
        return

    try:
        head_result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=repo_root,
        )
        current = head_result.stdout.strip()
        if current:
            active_branches.add(current)
    except Exception:
        pass
    active_branches.add("main")

    orphaned = [
        branch
        for branch in all_branches
        if branch not in active_branches
        and (branch.startswith("qiqiclaw/qiqiclaw-") or branch.startswith("pr-"))
    ]
    for branch in orphaned:
        try:
            subprocess.run(
                ["git", "branch", "-D", branch],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=repo_root,
            )
        except Exception:
            pass


__all__ = [
    "active_worktree",
    "cleanup_worktree",
    "git_repo_root",
    "path_is_within_root",
    "prune_orphaned_branches",
    "prune_stale_worktrees",
    "run_checkpoint_auto_maintenance",
    "run_state_db_auto_maintenance",
    "setup_worktree",
]
