"""Task storage layer — Python port of CCB's ``src/utils/tasks.ts``.

Claude Code (CCB) maintains a structured task list per session with:

* Unique task IDs (monotonically-increasing integers as strings: ``"1"``, ``"2"``...)
* Four statuses: ``pending`` → ``in_progress`` → ``completed``, plus ``deleted``
* Dependency graph via ``blocks`` / ``blockedBy`` arrays
* Optional ``owner`` (agent name) for multi-agent coordination
* Optional ``activeForm`` — present-continuous spinner label while in_progress
* Arbitrary ``metadata`` dict for extensions

The semantics mirrored one-to-one from CCB:

* Creating a task returns its integer-string ID.
* Blocking task A on B means: A appears in B's ``blocks``, and B appears in A's ``blockedBy``.
* A task is *claimable* when status is ``pending``, has no owner, and ``blockedBy``
  contains only resolved (completed) IDs.
* ``deleted`` is a special status that removes the task entirely rather than
  storing the status value.

Two storage backends are available (backwards-compatible; chosen at construction):

* ``TaskStore(storage_path=<file.json>)`` — legacy single-file store. All tasks
  live in one JSON doc. Only safe for a single process. Retained so existing
  callers keep working.
* ``TaskStore.open_dir(<dir>)`` — CCB-style directory store. One file per task
  (``<dir>/1.json``, ``<dir>/2.json``), plus ``.highwatermark`` preventing ID
  reuse after deletion and ``.lock`` for OS-level ``fcntl`` locking so multiple
  teammates can share the same task list without stepping on each other.

Thread-safe via RLock; cross-process-safe via the fcntl lock in the directory backend.
"""

from __future__ import annotations

import errno
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Cross-process lock helper (best-effort, fcntl on POSIX, no-op on Windows)
# ---------------------------------------------------------------------------


try:  # POSIX
    import fcntl as _fcntl
    _HAS_FCNTL = True
except ImportError:  # pragma: no cover — Windows falls back to threading.RLock only
    _fcntl = None  # type: ignore[assignment]
    _HAS_FCNTL = False


class _CrossProcessLock:
    """Acquire an advisory exclusive lock on *lock_path*.

    On POSIX we use ``fcntl.flock`` (advisory, automatic release on close).
    On non-POSIX we fall back to a simple retry loop checking the file's
    existence — same spirit as CCB's ``proper-lockfile`` but without its
    exact on-disk marker format. Serializes concurrent callers within ~3s.
    """

    _RETRIES = 30
    _MIN_SLEEP = 0.005
    _MAX_SLEEP = 0.1

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = Path(lock_path)
        self._fd: Optional[int] = None

    def __enter__(self) -> "_CrossProcessLock":
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Touch file if missing (CCB's ensureTaskListLockFile mirror).
        try:
            self._lock_path.touch(exist_ok=True)
        except OSError:
            pass

        if not _HAS_FCNTL:
            # Poor-man fallback: mark ownership by writing our pid and retrying.
            for _ in range(self._RETRIES):
                try:
                    with open(self._lock_path, "x") as f:
                        f.write(str(os.getpid()))
                    return self
                except FileExistsError:
                    time.sleep(self._MIN_SLEEP)
            raise TimeoutError(f"Could not acquire lock {self._lock_path}")

        self._fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        for attempt in range(self._RETRIES):
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in (errno.EWOULDBLOCK, errno.EAGAIN):
                    raise
            time.sleep(min(self._MAX_SLEEP, self._MIN_SLEEP * (1 + attempt)))
        # Final blocking attempt so we don't silently proceed without a lock.
        _fcntl.flock(self._fd, _fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc) -> None:
        if not _HAS_FCNTL:
            try:
                self._lock_path.unlink(missing_ok=True)
            except OSError:
                pass
            return
        if self._fd is not None:
            try:
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


# ---------------------------------------------------------------------------
# Enums + schema
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    """CCB-compatible status enum. ``deleted`` is transient (never persisted)."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# Tokens accepted by TaskUpdate ``status=`` including the delete action.
VALID_UPDATE_STATUSES: frozenset[str] = frozenset(
    {s.value for s in TaskStatus} | {"deleted"}
)


@dataclass
class Task:
    """A single task. Field names match CCB's ``Task`` type in ``src/utils/tasks.ts``."""

    id: str
    subject: str
    description: str = ""
    status: TaskStatus = TaskStatus.PENDING
    active_form: Optional[str] = None
    owner: Optional[str] = None
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Task":
        return cls(
            id=str(d["id"]),
            subject=d.get("subject", ""),
            description=d.get("description", ""),
            status=TaskStatus(d.get("status", "pending")),
            active_form=d.get("active_form"),
            owner=d.get("owner"),
            blocks=list(d.get("blocks", [])),
            blocked_by=list(d.get("blocked_by", [])),
            metadata=dict(d.get("metadata", {})),
        )


@dataclass(frozen=True)
class ClaimResult:
    """Outcome of ``TaskStore.claim_task`` — mirrors CCB's ``ClaimTaskResult``."""

    success: bool
    reason: Optional[str] = None  # task_not_found | already_claimed |
                                  # already_resolved | blocked | agent_busy
    task: Optional[Task] = None
    blocked_by_tasks: tuple[str, ...] = ()
    busy_with_tasks: tuple[str, ...] = ()


class TaskStoreError(Exception):
    """Raised on invalid task operations (unknown ID, bad status, cycle)."""


# ---------------------------------------------------------------------------
# TaskStore — both legacy (single-file) and CCB directory layouts
# ---------------------------------------------------------------------------


_HIGH_WATER_MARK_FILE = ".highwatermark"
_LOCK_FILE = ".lock"


class TaskStore:
    """Thread- and process-safe task collection.

    One store = one CCB "task list" (``taskListId`` in CCB terms). Callers
    typically instantiate one per REPL session, or one per team name.

    Construction forms:

    * ``TaskStore()`` — in-memory only
    * ``TaskStore(storage_path="tasks.json")`` — single-file JSON (legacy,
      still supported; not safe across processes)
    * ``TaskStore.open_dir("<dir>")`` — CCB directory layout: one JSON file
      per task + ``.highwatermark`` + ``.lock`` for cross-process safety.
    """

    def __init__(
        self,
        *,
        storage_path: Optional[Path | str] = None,
        storage_dir: Optional[Path | str] = None,
    ):
        if storage_path and storage_dir:
            raise TaskStoreError(
                "storage_path and storage_dir are mutually exclusive"
            )
        self._tasks: dict[str, Task] = {}
        self._next_id: int = 1
        self._lock = threading.RLock()
        self._storage_path: Optional[Path] = (
            Path(storage_path) if storage_path else None
        )
        self._storage_dir: Optional[Path] = (
            Path(storage_dir) if storage_dir else None
        )
        if self._storage_dir:
            self._storage_dir.mkdir(parents=True, exist_ok=True)
        if self._storage_path and self._storage_path.exists():
            self._load_singlefile()

    # --- Convenience constructor matching CCB's taskListId idiom ---------

    @classmethod
    def open_dir(cls, directory: Path | str) -> "TaskStore":
        """Open (or create) a CCB-style directory-backed task list.

        Equivalent to CCB's ``getTasksDir(taskListId)`` + ``ensureTasksDir``.
        Concurrent processes pointing at the same directory share state
        safely via the embedded ``.lock`` file.
        """
        return cls(storage_dir=directory)

    # --- Legacy single-file persistence ---------------------------------

    def _load_singlefile(self) -> None:
        try:
            raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self._tasks = {
            str(d["id"]): Task.from_dict(d) for d in raw.get("tasks", [])
        }
        self._next_id = int(raw.get("next_id", len(self._tasks) + 1))

    def _save_singlefile(self) -> None:
        if not self._storage_path:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "next_id": self._next_id,
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }
        self._storage_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # --- CCB directory layout helpers ------------------------------------

    def _task_file(self, task_id: str) -> Path:
        assert self._storage_dir is not None
        safe = "".join(c for c in str(task_id) if c.isalnum() or c in "-_")
        return self._storage_dir / f"{safe}.json"

    def _highwater_file(self) -> Path:
        assert self._storage_dir is not None
        return self._storage_dir / _HIGH_WATER_MARK_FILE

    def _lock_file(self) -> Path:
        assert self._storage_dir is not None
        return self._storage_dir / _LOCK_FILE

    def _read_highwatermark(self) -> int:
        try:
            return int(self._highwater_file().read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0

    def _write_highwatermark(self, value: int) -> None:
        self._highwater_file().write_text(str(value), encoding="utf-8")

    def _scan_dir_tasks(self) -> dict[str, Task]:
        assert self._storage_dir is not None
        out: dict[str, Task] = {}
        try:
            entries = os.listdir(self._storage_dir)
        except FileNotFoundError:
            return out
        for name in entries:
            if not name.endswith(".json") or name.startswith("."):
                continue
            path = self._storage_dir / name
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                t = Task.from_dict(data)
                out[t.id] = t
            except (OSError, json.JSONDecodeError, KeyError):
                continue
        return out

    def _highest_id_from_disk(self) -> int:
        assert self._storage_dir is not None
        highest = 0
        try:
            entries = os.listdir(self._storage_dir)
        except FileNotFoundError:
            return 0
        for name in entries:
            if not name.endswith(".json") or name.startswith("."):
                continue
            stem = name[:-5]
            try:
                n = int(stem)
            except ValueError:
                continue
            if n > highest:
                highest = n
        return highest

    def _write_task_file(self, task: Task) -> None:
        # Atomic replace: write to tmp then rename so concurrent readers
        # never see a truncated file.
        path = self._task_file(task.id)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(task.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def _persist(self) -> None:
        """Flush current in-memory view to the configured backend."""
        if self._storage_dir:
            for t in self._tasks.values():
                self._write_task_file(t)
            return
        if self._storage_path:
            self._save_singlefile()

    # ------------------------------------------------------------------
    # CRUD — matches CCB's createTask/getTask/updateTask/deleteTask/listTasks
    # ------------------------------------------------------------------

    def create(
        self,
        subject: str,
        *,
        description: str = "",
        active_form: Optional[str] = None,
        owner: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Create a pending task, return its ID. CCB-compatible."""
        if not subject.strip():
            raise TaskStoreError("subject must be non-empty")

        if self._storage_dir:
            # CCB directory path — use cross-process lock so concurrent
            # teammates don't collide on ID allocation.
            with _CrossProcessLock(self._lock_file()), self._lock:
                self._refresh_from_disk()
                next_id = max(
                    self._highest_id_from_disk(),
                    self._read_highwatermark(),
                    self._next_id - 1,
                ) + 1
                task_id = str(next_id)
                task = Task(
                    id=task_id,
                    subject=subject,
                    description=description,
                    active_form=active_form,
                    owner=owner,
                    metadata=dict(metadata or {}),
                )
                self._tasks[task_id] = task
                self._next_id = next_id + 1
                self._write_task_file(task)
                return task_id

        # In-memory / single-file path (unchanged legacy behaviour).
        with self._lock:
            task_id = str(self._next_id)
            self._next_id += 1
            self._tasks[task_id] = Task(
                id=task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                owner=owner,
                metadata=dict(metadata or {}),
            )
            self._save_singlefile()
            return task_id

    def _refresh_from_disk(self) -> None:
        """Re-scan disk state into memory (directory backend only)."""
        if self._storage_dir:
            self._tasks = self._scan_dir_tasks()

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            if self._storage_dir:
                self._refresh_from_disk()
            task = self._tasks.get(str(task_id))
            return Task.from_dict(task.to_dict()) if task else None

    def list(self) -> list[Task]:
        """Snapshot of all tasks, ordered by numeric ID."""
        with self._lock:
            if self._storage_dir:
                self._refresh_from_disk()
            ordered = sorted(
                self._tasks.values(),
                key=lambda t: int(t.id) if t.id.isdigit() else 10**9,
            )
            return [Task.from_dict(t.to_dict()) for t in ordered]

    def update(
        self,
        task_id: str,
        *,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        active_form: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        owner: Optional[str] = None,
        metadata_merge: Optional[dict[str, Any]] = None,
    ) -> list[str]:
        """Apply partial updates. Returns list of field names that changed.

        ``metadata_merge`` is deep-merged into existing metadata; keys with
        value ``None`` are removed (same semantics as CCB's TaskUpdateTool).
        """
        if self._storage_dir:
            with _CrossProcessLock(self._lock_file()), self._lock:
                return self._apply_update(
                    task_id,
                    subject=subject,
                    description=description,
                    active_form=active_form,
                    status=status,
                    owner=owner,
                    metadata_merge=metadata_merge,
                    persist_after=True,
                )

        with self._lock:
            return self._apply_update(
                task_id,
                subject=subject,
                description=description,
                active_form=active_form,
                status=status,
                owner=owner,
                metadata_merge=metadata_merge,
                persist_after=True,
            )

    def _apply_update(
        self,
        task_id: str,
        *,
        subject: Optional[str],
        description: Optional[str],
        active_form: Optional[str],
        status: Optional[TaskStatus],
        owner: Optional[str],
        metadata_merge: Optional[dict[str, Any]],
        persist_after: bool,
    ) -> list[str]:
        if self._storage_dir:
            self._refresh_from_disk()
        task = self._tasks.get(str(task_id))
        if not task:
            raise TaskStoreError(f"Unknown task id: {task_id}")
        changed: list[str] = []

        if subject is not None and subject != task.subject:
            task.subject = subject
            changed.append("subject")
        if description is not None and description != task.description:
            task.description = description
            changed.append("description")
        if active_form is not None and active_form != task.active_form:
            task.active_form = active_form
            changed.append("active_form")
        if owner is not None and owner != task.owner:
            task.owner = owner
            changed.append("owner")
        if metadata_merge is not None:
            merged = dict(task.metadata)
            for k, v in metadata_merge.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            if merged != task.metadata:
                task.metadata = merged
                changed.append("metadata")
        if status is not None and status != task.status:
            task.status = status
            changed.append("status")

        if changed and persist_after:
            if self._storage_dir:
                self._write_task_file(task)
            else:
                self._save_singlefile()
        return changed

    def delete(self, task_id: str) -> bool:
        if self._storage_dir:
            with _CrossProcessLock(self._lock_file()), self._lock:
                self._refresh_from_disk()
                if str(task_id) not in self._tasks:
                    return False
                # Update high-water mark to prevent ID reuse (CCB parity).
                try:
                    numeric = int(task_id)
                except ValueError:
                    numeric = 0
                if numeric > self._read_highwatermark():
                    self._write_highwatermark(numeric)
                # Delete file
                try:
                    self._task_file(task_id).unlink(missing_ok=True)
                except OSError:
                    return False
                # Clean up references in other tasks
                self._tasks.pop(str(task_id), None)
                for other in list(self._tasks.values()):
                    changed_blocks = [i for i in other.blocks if i != task_id]
                    changed_blocked_by = [i for i in other.blocked_by if i != task_id]
                    if (
                        changed_blocks != other.blocks
                        or changed_blocked_by != other.blocked_by
                    ):
                        other.blocks = changed_blocks
                        other.blocked_by = changed_blocked_by
                        self._write_task_file(other)
                return True

        with self._lock:
            if str(task_id) not in self._tasks:
                return False
            for other in self._tasks.values():
                other.blocks = [i for i in other.blocks if i != task_id]
                other.blocked_by = [i for i in other.blocked_by if i != task_id]
            del self._tasks[str(task_id)]
            self._save_singlefile()
            return True

    # ------------------------------------------------------------------
    # Dependency graph — matches CCB's blockTask
    # ------------------------------------------------------------------

    def add_block(self, blocker_id: str, blocked_id: str) -> None:
        """Declare that ``blocker_id`` must complete before ``blocked_id`` can start.

        CCB semantics: the relationship appears on *both* tasks —
        ``blocker.blocks`` contains ``blocked_id`` and ``blocked.blocked_by``
        contains ``blocker_id``. Cycles are rejected.
        """
        if blocker_id == blocked_id:
            raise TaskStoreError("A task cannot block itself")

        if self._storage_dir:
            with _CrossProcessLock(self._lock_file()), self._lock:
                self._refresh_from_disk()
                blocker = self._tasks.get(str(blocker_id))
                blocked = self._tasks.get(str(blocked_id))
                if not blocker:
                    raise TaskStoreError(f"Unknown blocker id: {blocker_id}")
                if not blocked:
                    raise TaskStoreError(f"Unknown blocked id: {blocked_id}")
                if self._would_create_cycle(blocker_id, blocked_id):
                    raise TaskStoreError(
                        f"Cycle detected: blocking {blocked_id} with {blocker_id}"
                    )
                mutated = False
                if blocked_id not in blocker.blocks:
                    blocker.blocks.append(blocked_id)
                    self._write_task_file(blocker)
                    mutated = True
                if blocker_id not in blocked.blocked_by:
                    blocked.blocked_by.append(blocker_id)
                    self._write_task_file(blocked)
                    mutated = True
                _ = mutated
            return

        with self._lock:
            blocker = self._tasks.get(str(blocker_id))
            blocked = self._tasks.get(str(blocked_id))
            if not blocker:
                raise TaskStoreError(f"Unknown blocker id: {blocker_id}")
            if not blocked:
                raise TaskStoreError(f"Unknown blocked id: {blocked_id}")
            if self._would_create_cycle(blocker_id, blocked_id):
                raise TaskStoreError(
                    f"Cycle detected: blocking {blocked_id} with {blocker_id}"
                )
            if blocked_id not in blocker.blocks:
                blocker.blocks.append(blocked_id)
            if blocker_id not in blocked.blocked_by:
                blocked.blocked_by.append(blocker_id)
            self._save_singlefile()

    def _would_create_cycle(self, new_blocker: str, new_blocked: str) -> bool:
        """Return True iff adding ``new_blocker → new_blocked`` would form a cycle."""
        seen: set[str] = set()
        stack = [new_blocked]
        while stack:
            cur = stack.pop()
            if cur == new_blocker:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            task = self._tasks.get(cur)
            if not task:
                continue
            stack.extend(task.blocks)
        return False

    # ------------------------------------------------------------------
    # claim_task — CCB's atomic "pick up next job" for teammates
    # ------------------------------------------------------------------

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        *,
        check_agent_busy: bool = False,
    ) -> ClaimResult:
        """Atomically claim a task for *agent_id*.

        Port of CCB's ``claimTask`` + ``claimTaskWithBusyCheck`` (``tasks.ts``).
        Returns a ``ClaimResult`` explaining why a claim failed.

        When ``check_agent_busy=True`` (CCB's ``checkAgentBusy`` option), the
        lock scope widens to the whole task list so we can atomically verify
        *agent_id* doesn't already own another unresolved task.
        """
        acquire = (
            _CrossProcessLock(self._lock_file())
            if self._storage_dir
            else _NullContext()
        )
        with acquire, self._lock:
            if self._storage_dir:
                self._refresh_from_disk()

            task = self._tasks.get(str(task_id))
            if not task:
                return ClaimResult(success=False, reason="task_not_found")
            if task.owner and task.owner != agent_id:
                return ClaimResult(
                    success=False, reason="already_claimed",
                    task=Task.from_dict(task.to_dict()),
                )
            if task.status is TaskStatus.COMPLETED:
                return ClaimResult(
                    success=False, reason="already_resolved",
                    task=Task.from_dict(task.to_dict()),
                )

            unresolved_ids = {
                t.id for t in self._tasks.values()
                if t.status is not TaskStatus.COMPLETED
            }
            blocked_by = [
                b for b in task.blocked_by if b in unresolved_ids
            ]
            if blocked_by:
                return ClaimResult(
                    success=False, reason="blocked",
                    task=Task.from_dict(task.to_dict()),
                    blocked_by_tasks=tuple(blocked_by),
                )

            if check_agent_busy:
                busy = [
                    t.id for t in self._tasks.values()
                    if t.status is not TaskStatus.COMPLETED
                    and t.owner == agent_id
                    and t.id != task.id
                ]
                if busy:
                    return ClaimResult(
                        success=False, reason="agent_busy",
                        task=Task.from_dict(task.to_dict()),
                        busy_with_tasks=tuple(busy),
                    )

            task.owner = agent_id
            if self._storage_dir:
                self._write_task_file(task)
            else:
                self._save_singlefile()
            return ClaimResult(success=True, task=Task.from_dict(task.to_dict()))

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def resolved_ids(self) -> set[str]:
        with self._lock:
            if self._storage_dir:
                self._refresh_from_disk()
            return {
                t.id for t in self._tasks.values()
                if t.status is TaskStatus.COMPLETED
            }

    def claimable(self) -> list[Task]:
        """Tasks that are pending, unowned, and have no unresolved blockers.

        Matches CCB's "available work" definition used by teammates to pick up
        the next task.
        """
        resolved = self.resolved_ids()
        with self._lock:
            if self._storage_dir:
                self._refresh_from_disk()
            claimable = [
                Task.from_dict(t.to_dict())
                for t in self._tasks.values()
                if t.status is TaskStatus.PENDING
                and not t.owner
                and all(b in resolved for b in t.blocked_by)
            ]
            claimable.sort(
                key=lambda t: int(t.id) if t.id.isdigit() else 10**9
            )
            return claimable

    def clear(self) -> None:
        """Wipe everything — used by tests."""
        with self._lock:
            # Bump high-water mark to prevent ID reuse after a reset.
            if self._storage_dir:
                current = self._highest_id_from_disk()
                if current > self._read_highwatermark():
                    self._write_highwatermark(current)
                for name in os.listdir(self._storage_dir):
                    if name.endswith(".json") and not name.startswith("."):
                        try:
                            (self._storage_dir / name).unlink()
                        except OSError:
                            pass
            self._tasks.clear()
            self._next_id = 1
            if self._storage_path:
                self._save_singlefile()


class _NullContext:
    """A no-op context manager used when there's no file lock to take."""

    def __enter__(self) -> "_NullContext":
        return self

    def __exit__(self, *_exc) -> None:
        return None


# ---------------------------------------------------------------------------
# Process-wide default store
#
# CCB's ``getTaskListId()`` uses one of:
#   1. CLAUDE_CODE_TASK_LIST_ID env
#   2. teammate/team name
#   3. session id
#
# We mirror that decision here via QIQICLAW_TASK_LIST_ID and fall back to
# ``~/.qiqiclaw/tasks/<id>/`` — CCB-compatible directory layout that
# multiple teammate processes can share (thanks to the fcntl lock).
# ---------------------------------------------------------------------------


_default_store: Optional[TaskStore] = None
_default_lock = threading.RLock()


def _sanitize_list_id(raw: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in raw).strip("-") or "default"


def _resolve_default_task_dir() -> Optional[Path]:
    """Return the tasks dir CCB would pick for this process, or None.

    Honors ``QIQICLAW_TASK_LIST_ID`` (our equivalent of CCB's
    ``CLAUDE_CODE_TASK_LIST_ID``), then ``QIQICLAW_TEAM_NAME``, then
    ``QIQICLAW_SESSION_ID``. Returns None if QIQICLAW_HOME isn't set and
    we can't pick a sensible default without polluting disk.
    """
    list_id = (
        os.environ.get("QIQICLAW_TASK_LIST_ID")
        or os.environ.get("QIQICLAW_TEAM_NAME")
        or os.environ.get("QIQICLAW_SESSION_ID")
    )
    if not list_id:
        return None
    home = os.environ.get("QIQICLAW_HOME") or os.path.expanduser("~/.qiqiclaw")
    return Path(home) / "tasks" / _sanitize_list_id(list_id)


def get_default_store() -> TaskStore:
    """Lazily build and return the process-wide TaskStore.

    Prefers a CCB-style directory store (so teammates can see each other's
    tasks) when ``QIQICLAW_TASK_LIST_ID`` / ``QIQICLAW_TEAM_NAME`` /
    ``QIQICLAW_SESSION_ID`` is set.  Falls back to an in-memory store for
    tests and one-shot scripts.
    """
    global _default_store
    with _default_lock:
        if _default_store is None:
            dir_ = _resolve_default_task_dir()
            _default_store = (
                TaskStore.open_dir(dir_) if dir_ else TaskStore()
            )
        return _default_store


def set_default_store(store: TaskStore) -> None:
    """Override the default store (tests + persistent sessions)."""
    global _default_store
    with _default_lock:
        _default_store = store


def reset_default_store() -> None:
    """Test helper."""
    global _default_store
    with _default_lock:
        _default_store = None
