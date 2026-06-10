"""Task tools — Python port of CCB's TaskCreate / TaskUpdate / TaskList / TaskGet tools.

These are the **LLM-callable** surface: each function accepts a JSON-shaped
dict (matching the Zod schema in CCB) and returns a JSON-serializable dict
result. The return format matches CCB's ``mapToolResultToToolResultBlockParam``
output so a model that learned CCB's phrasing sees the same text back.

Tool name mapping (CCB → here):

    TaskCreate   → task_create_tool
    TaskUpdate   → task_update_tool
    TaskList     → task_list_tool
    TaskGet      → task_get_tool

Each tool also has a matching ``*_SCHEMA`` dict in OpenAI function-call
format so they can plug into QIQI-claw's existing ``tools/registry.py``
or be passed directly to any LLM with tool-calling support.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from qiqiclaw_subsystems.tasks import (
    Task,
    TaskStatus,
    TaskStore,
    TaskStoreError,
    VALID_UPDATE_STATUSES,
    get_default_store,
)


# ---------------------------------------------------------------------------
# Prompts — the exact user-facing text CCB emits. Ported verbatim.
# ---------------------------------------------------------------------------

TASK_CREATE_DESCRIPTION = "Create a new task in the task list"
# Verbatim port of CCB's packages/builtin-tools/src/tools/TaskCreateTool/prompt.ts
# (the non-agent-swarms branch — single-agent variant).  Same text the model
# saw when it learned the tool, so porting is behaviourally identical.
TASK_CREATE_PROMPT = """Use this tool to create a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

- Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
- Non-trivial and complex tasks - Tasks that require careful planning or multiple operations
- Plan mode - When using plan mode, create a task list to track the work
- User explicitly requests todo list - When the user directly asks you to use the todo list
- User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions - Immediately capture user requirements as tasks
- When you start working on a task - Mark it as in_progress BEFORE beginning work
- After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Fields

- **subject**: A brief, actionable title in imperative form (e.g., "Fix authentication bug in login flow")
- **description**: What needs to be done
- **activeForm** (optional): Present continuous form shown in the spinner when the task is in_progress (e.g., "Fixing authentication bug"). If omitted, the spinner shows the subject instead.

All tasks are created with status `pending`.

## Tips

- Create tasks with clear, specific subjects that describe the outcome
- After creating tasks, use TaskUpdate to set up dependencies (blocks/blockedBy) if needed
- Check TaskList first to avoid creating duplicate tasks
"""

TASK_UPDATE_DESCRIPTION = "Update a task in the task list"
# Verbatim port of CCB's TaskUpdateTool/prompt.ts.
TASK_UPDATE_PROMPT = """Use this tool to update a task in the task list.

## When to Use This Tool

**Mark tasks as resolved:**
- When you have completed the work described in a task
- When a task is no longer needed or has been superseded
- IMPORTANT: Always mark your assigned tasks as resolved when you finish them
- After resolving, call TaskList to find your next task

- ONLY mark a task as completed when you have FULLY accomplished it
- If you encounter errors, blockers, or cannot finish, keep the task as in_progress
- When blocked, create a new task describing what needs to be resolved
- Never mark a task as completed if:
  - Tests are failing
  - Implementation is partial
  - You encountered unresolved errors
  - You couldn't find necessary files or dependencies

**Delete tasks:**
- When a task is no longer relevant or was created in error
- Setting status to `deleted` permanently removes the task

**Update task details:**
- When requirements change or become clearer
- When establishing dependencies between tasks

## Fields You Can Update

- **status**: The task status (see Status Workflow below)
- **subject**: Change the task title (imperative form, e.g., "Run tests")
- **description**: Change the task description
- **activeForm**: Present continuous form shown in spinner when in_progress (e.g., "Running tests")
- **owner**: Change the task owner (agent name)
- **metadata**: Merge metadata keys into the task (set a key to null to delete it)
- **addBlocks**: Mark tasks that cannot start until this one completes
- **addBlockedBy**: Mark tasks that must complete before this one can start

## Status Workflow

Status progresses: `pending` → `in_progress` → `completed`

Use `deleted` to permanently remove a task.

## Staleness

Make sure to read a task's latest state using `TaskGet` before updating it.

## Examples

Mark task as in progress when starting work:
```json
{"taskId": "1", "status": "in_progress"}
```

Mark task as completed after finishing work:
```json
{"taskId": "1", "status": "completed"}
```

Delete a task:
```json
{"taskId": "1", "status": "deleted"}
```

Claim a task by setting owner:
```json
{"taskId": "1", "owner": "my-name"}
```

Set up task dependencies:
```json
{"taskId": "2", "addBlockedBy": ["1"]}
```
"""

TASK_LIST_DESCRIPTION = "List all tasks in the task list"
# Verbatim port of CCB's TaskListTool/prompt.ts.
TASK_LIST_PROMPT = """Use this tool to list all tasks in the task list.

## When to Use This Tool

- To see what tasks are available to work on (status: 'pending', no owner, not blocked)
- To check overall progress on the project
- To find tasks that are blocked and need dependencies resolved
- After completing a task, to check for newly unblocked work or claim the next available task
- **Prefer working on tasks in ID order** (lowest ID first) when multiple tasks are available, as earlier tasks often set up context for later ones

## Output

Returns a summary of each task:
- **id**: Task identifier (use with TaskGet, TaskUpdate)
- **subject**: Brief description of the task
- **status**: 'pending', 'in_progress', or 'completed'
- **owner**: Agent ID if assigned, empty if available
- **blockedBy**: List of open task IDs that must be resolved first (tasks with blockedBy cannot be claimed until dependencies resolve)

Use TaskGet with a specific task ID to view full details including description and comments.
"""

TASK_GET_DESCRIPTION = "Retrieve a task by its ID from the task list"
# Inspired by CCB's TaskGetTool/prompt.ts (short DESCRIPTION in source; we
# expand slightly so single-shot SDK callers know what's returned).
TASK_GET_PROMPT = """Use this tool to retrieve a task by its ID from the task list.

## When to Use This Tool

- When you need the full description and context before starting work on a task
- To understand task dependencies (what it blocks, what blocks it)
- After being assigned a task, to get complete requirements

## Output

Returns full task details:
- **subject**: Task title
- **description**: Detailed requirements and context
- **status**: 'pending', 'in_progress', or 'completed'
- **blocks**: Tasks waiting on this one to complete
- **blockedBy**: Tasks that must complete before this one can start

## Tips

- After fetching a task, verify its blockedBy list is empty before beginning work.
- Use TaskList to see all tasks in summary form.
"""


# ---------------------------------------------------------------------------
# OpenAI function-call schemas (also Anthropic-compatible)
# ---------------------------------------------------------------------------

TASK_CREATE_SCHEMA: dict[str, Any] = {
    "name": "TaskCreate",
    "description": TASK_CREATE_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "subject": {"type": "string", "description": "A brief title for the task"},
            "description": {"type": "string", "description": "What needs to be done"},
            "activeForm": {
                "type": "string",
                "description": "Present continuous form shown in spinner when in_progress",
            },
            "metadata": {
                "type": "object",
                "description": "Arbitrary metadata dict attached to the task",
                "additionalProperties": True,
            },
        },
        "required": ["subject", "description"],
    },
}

TASK_UPDATE_SCHEMA: dict[str, Any] = {
    "name": "TaskUpdate",
    "description": TASK_UPDATE_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "taskId": {"type": "string"},
            "subject": {"type": "string"},
            "description": {"type": "string"},
            "activeForm": {"type": "string"},
            "status": {
                "type": "string",
                "enum": sorted(VALID_UPDATE_STATUSES),
            },
            "owner": {"type": "string"},
            "addBlocks": {"type": "array", "items": {"type": "string"}},
            "addBlockedBy": {"type": "array", "items": {"type": "string"}},
            "metadata": {"type": "object", "additionalProperties": True},
        },
        "required": ["taskId"],
    },
}

TASK_LIST_SCHEMA: dict[str, Any] = {
    "name": "TaskList",
    "description": TASK_LIST_DESCRIPTION,
    "parameters": {"type": "object", "properties": {}},
}

TASK_GET_SCHEMA: dict[str, Any] = {
    "name": "TaskGet",
    "description": TASK_GET_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {"taskId": {"type": "string"}},
        "required": ["taskId"],
    },
}


# ---------------------------------------------------------------------------
# Handlers — each accepts a dict (LLM args) and returns a dict (result)
# ---------------------------------------------------------------------------


def _store(override: Optional[TaskStore]) -> TaskStore:
    return override if override is not None else get_default_store()


def _append_panel(
    result: dict[str, Any],
    store: TaskStore,
    *,
    when: str,
) -> dict[str, Any]:
    """Append a live task-panel snapshot to ``result["message"]``.

    Mirrors the way Claude Code renders the task list inline after each
    TaskCreate / TaskUpdate — gives the human operator an always-fresh view
    of progress without having to run a separate command.

    Suppressed automatically when:
      * the tool reported an error (``ok=False`` or ``error`` key set) —
        we don't want to drown the error in a panel;
      * ``QIQICLAW_TASK_PANEL_INLINE=0`` is set — opt-out for headless
        tests / piped non-TTY runs where the panel would be noise;
      * the store is empty (nothing useful to show);
      * rendering raises for any reason (defensive: never break the
        caller because of a cosmetic helper).
    """
    if not result.get("message"):
        return result
    if "error" in result:
        return result
    if result.get("success") is False:
        return result
    if os.environ.get("QIQICLAW_TASK_PANEL_INLINE", "1") in {"0", "false", "no"}:
        return result
    try:
        # Imported lazily so import cycles + missing terminals don't crash
        # pure task-storage tests.
        from qiqiclaw_subsystems.task_display import render_task_list

        panel = render_task_list(store, use_color=False)
        if not panel or "No tasks yet" in panel and when in {"list", "get"}:
            # Nothing to show, keep the message clean.
            return result
        result["message"] = result["message"].rstrip() + "\n\n" + panel.rstrip()
    except Exception:  # noqa: BLE001 — decorative only, never propagate
        pass
    return result


def task_create_tool(
    args: dict[str, Any],
    *,
    store: Optional[TaskStore] = None,
) -> dict[str, Any]:
    """CCB TaskCreateTool.call equivalent.

    Expected args: ``subject``, ``description``, ``activeForm?``, ``metadata?``.
    Returns: ``{"task": {"id", "subject"}, "message": <LLM-facing text>}``.
    """
    s = _store(store)
    subject = args.get("subject", "")
    if not subject:
        return {
            "error": "subject is required",
            "message": "Error: subject is required",
        }
    try:
        task_id = s.create(
            subject=subject,
            description=args.get("description", ""),
            active_form=args.get("activeForm"),
            metadata=args.get("metadata"),
        )
    except TaskStoreError as exc:
        return {"error": str(exc), "message": f"Error: {exc}"}
    return _append_panel(
        {
            "task": {"id": task_id, "subject": subject},
            "message": f"Task #{task_id} created successfully: {subject}",
        },
        s,
        when="create",
    )


def task_update_tool(
    args: dict[str, Any],
    *,
    store: Optional[TaskStore] = None,
) -> dict[str, Any]:
    """CCB TaskUpdateTool.call equivalent.

    Supports the full set of fields plus ``addBlocks`` / ``addBlockedBy``.
    Returns the changed-fields list and optional ``statusChange``.
    """
    s = _store(store)
    task_id = args.get("taskId")
    if not task_id:
        return {"success": False, "error": "taskId is required",
                "message": "Error: taskId is required"}

    task_id = str(task_id)
    existing = s.get(task_id)
    if not existing:
        return {
            "success": False,
            "taskId": task_id,
            "updatedFields": [],
            "error": "Task not found",
            "message": f"Task #{task_id} not found",
        }

    # Handle deletion specially — matches CCB's early-return path.
    if args.get("status") == "deleted":
        deleted = s.delete(task_id)
        return {
            "success": deleted,
            "taskId": task_id,
            "updatedFields": ["deleted"] if deleted else [],
            "statusChange": (
                {"from": existing.status.value, "to": "deleted"} if deleted else None
            ),
            "message": f"Task #{task_id} deleted" if deleted
                       else f"Failed to delete task #{task_id}",
        }

    # Status validation
    status_arg = args.get("status")
    new_status: Optional[TaskStatus] = None
    if status_arg is not None:
        if status_arg not in {s.value for s in TaskStatus}:
            return {
                "success": False,
                "taskId": task_id,
                "updatedFields": [],
                "error": f"Invalid status {status_arg!r}",
                "message": f"Error: invalid status {status_arg!r}",
            }
        new_status = TaskStatus(status_arg)

    updated_fields: list[str] = []
    try:
        updated_fields = s.update(
            task_id,
            subject=args.get("subject"),
            description=args.get("description"),
            active_form=args.get("activeForm"),
            status=new_status,
            owner=args.get("owner"),
            metadata_merge=args.get("metadata"),
        )
    except TaskStoreError as exc:
        return {
            "success": False,
            "taskId": task_id,
            "updatedFields": [],
            "error": str(exc),
            "message": f"Error: {exc}",
        }

    # addBlocks / addBlockedBy — these never error-out silently because a
    # cycle should surface to the model.
    add_blocks = args.get("addBlocks") or []
    add_blocked_by = args.get("addBlockedBy") or []
    for blocked_id in add_blocks:
        if blocked_id not in existing.blocks:
            try:
                s.add_block(blocker_id=task_id, blocked_id=str(blocked_id))
                if "blocks" not in updated_fields:
                    updated_fields.append("blocks")
            except TaskStoreError as exc:
                return {
                    "success": False,
                    "taskId": task_id,
                    "updatedFields": updated_fields,
                    "error": str(exc),
                    "message": f"Error: {exc}",
                }
    for blocker_id in add_blocked_by:
        if blocker_id not in existing.blocked_by:
            try:
                s.add_block(blocker_id=str(blocker_id), blocked_id=task_id)
                if "blockedBy" not in updated_fields:
                    updated_fields.append("blockedBy")
            except TaskStoreError as exc:
                return {
                    "success": False,
                    "taskId": task_id,
                    "updatedFields": updated_fields,
                    "error": str(exc),
                    "message": f"Error: {exc}",
                }

    status_change = None
    if new_status and new_status != existing.status:
        status_change = {"from": existing.status.value, "to": new_status.value}

    message = (
        f"Updated task #{task_id} {', '.join(updated_fields)}"
        if updated_fields
        else f"Task #{task_id} unchanged"
    )
    return _append_panel(
        {
            "success": True,
            "taskId": task_id,
            "updatedFields": updated_fields,
            "statusChange": status_change,
            "message": message,
        },
        s,
        when="update",
    )


def task_list_tool(
    args: Optional[dict[str, Any]] = None,
    *,
    store: Optional[TaskStore] = None,
) -> dict[str, Any]:
    """CCB TaskListTool.call equivalent.

    Filters out internal tasks (``metadata._internal``) to match CCB.
    Each returned task's ``blockedBy`` is filtered to exclude already-resolved
    blockers — also CCB semantics (`tasks.ts` line 74-82).
    """
    s = _store(store)
    all_tasks = [t for t in s.list() if not t.metadata.get("_internal")]
    resolved = {t.id for t in all_tasks if t.status is TaskStatus.COMPLETED}

    tasks_out = [
        {
            "id": t.id,
            "subject": t.subject,
            "status": t.status.value,
            "owner": t.owner,
            "blockedBy": [b for b in t.blocked_by if b not in resolved],
        }
        for t in all_tasks
    ]

    if not tasks_out:
        return {"tasks": [], "message": "No tasks found"}

    lines = []
    for t in tasks_out:
        owner_part = f" ({t['owner']})" if t["owner"] else ""
        blocked_part = (
            f" [blocked by {', '.join(f'#{b}' for b in t['blockedBy'])}]"
            if t["blockedBy"]
            else ""
        )
        lines.append(f"#{t['id']} [{t['status']}] {t['subject']}{owner_part}{blocked_part}")

    return _append_panel(
        {"tasks": tasks_out, "message": "\n".join(lines)},
        s,
        when="list",
    )


def task_get_tool(
    args: dict[str, Any],
    *,
    store: Optional[TaskStore] = None,
) -> dict[str, Any]:
    """CCB TaskGetTool.call equivalent."""
    s = _store(store)
    task_id = args.get("taskId")
    if not task_id:
        return {"task": None, "error": "taskId is required",
                "message": "Error: taskId is required"}
    task = s.get(str(task_id))
    if not task:
        return {"task": None, "message": "Task not found"}

    lines = [
        f"Task #{task.id}: {task.subject}",
        f"Status: {task.status.value}",
        f"Description: {task.description}",
    ]
    if task.owner:
        lines.append(f"Owner: {task.owner}")
    if task.active_form:
        lines.append(f"Active form: {task.active_form}")
    if task.blocked_by:
        lines.append(f"Blocked by: {', '.join(f'#{b}' for b in task.blocked_by)}")
    if task.blocks:
        lines.append(f"Blocks: {', '.join(f'#{b}' for b in task.blocks)}")

    return _append_panel(
        {
            "task": {
                "id": task.id,
                "subject": task.subject,
                "description": task.description,
                "status": task.status.value,
                "owner": task.owner,
                "activeForm": task.active_form,
                "blocks": list(task.blocks),
                "blockedBy": list(task.blocked_by),
                "metadata": dict(task.metadata),
            },
            "message": "\n".join(lines),
        },
        s,
        when="get",
    )


# ---------------------------------------------------------------------------
# Convenience bundle — register with tools/registry.py in one shot
# ---------------------------------------------------------------------------

TASK_TOOLS: dict[str, tuple[dict[str, Any], Any, str]] = {
    "TaskCreate": (TASK_CREATE_SCHEMA, task_create_tool, TASK_CREATE_PROMPT),
    "TaskUpdate": (TASK_UPDATE_SCHEMA, task_update_tool, TASK_UPDATE_PROMPT),
    "TaskList": (TASK_LIST_SCHEMA, task_list_tool, TASK_LIST_PROMPT),
    "TaskGet": (TASK_GET_SCHEMA, task_get_tool, TASK_GET_PROMPT),
}
