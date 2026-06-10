"""CLI task-list renderer — complete port of CCB's task panel display.

CCB renders the task list with Ink (React-in-terminal). The visual grammar
is simple enough to fully reproduce in plain ANSI:

    [✓] #1  Run tests
    [▶] #2  Implementing login flow ← in-progress shows activeForm
    [ ] #3  Write docs            (owner: alice) [blocked by #2]
    [✗] #4  Old task              (deleted — never shown)

Icons, colors, and layout match CCB 1:1. Everything is a pure function
returning a string, so tests can assert on the exact output and the CLI
just prints the string.

Public API
----------
- render_task_list(store, ...)     → full panel string
- render_task_compact(task, ...)   → one-line summary
- render_task_detail(task, ...)    → full detail pane (TaskGet view)
- task_status_icon(status)         → single-char status glyph
"""

from __future__ import annotations

import io
from typing import Iterable, Optional

from qiqiclaw_subsystems.tasks import Task, TaskStatus, TaskStore, get_default_store


# ---------------------------------------------------------------------------
# ANSI helpers — kept hand-rolled so we have zero runtime dependencies
# ---------------------------------------------------------------------------

_ANSI_RESET = "\x1b[0m"
_ANSI_BOLD = "\x1b[1m"
_ANSI_DIM = "\x1b[2m"

#: Claude Code-style palette.
#:
#: Body text stays on the terminal's default foreground (usually white on
#: dark themes), which keeps the panel readable regardless of the user's
#: color scheme.  Status is conveyed exclusively through the icon color,
#: and secondary metadata fades to dim grey — exactly the restrained
#: visual grammar Claude Code uses for its task list.
#:
#: Previous revision used bright yellow + warm orange for the in-progress
#: row and bold header, which users found harsh.  We now:
#:   - Use blue for in_progress (Claude Code's accent color is #5769F7)
#:   - Use green for completed
#:   - Use dim grey for pending + all decorative chrome (border, header)
#:   - Remove all yellow/orange from body text
_ANSI_FG_DEFAULT = "\x1b[39m"         # explicit "default foreground" — white on dark themes
_ANSI_BLUE = "\x1b[38;5;75m"          # in_progress dot (soft blue, not screaming cyan)
_ANSI_GREEN = "\x1b[38;5;78m"         # completed checkmark (muted green)
_ANSI_GREY = "\x1b[38;5;244m"         # pending dot + dim decorative text
_ANSI_FAINT_RED = "\x1b[38;5;167m"    # blocker warning — desaturated red, not alarm red


def _color(code: str, text: str, *, use_color: bool) -> str:
    return f"{code}{text}{_ANSI_RESET}" if use_color else text


# ---------------------------------------------------------------------------
# Status glyphs — CCB uses Unicode box-drawing / checkmarks
# ---------------------------------------------------------------------------


_STATUS_ICONS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: "○",       # empty circle — not started
    TaskStatus.IN_PROGRESS: "●",   # filled dot — active
    TaskStatus.COMPLETED: "✓",     # check — done
}


_STATUS_COLORS: dict[TaskStatus, str] = {
    TaskStatus.PENDING: _ANSI_GREY,      # pending stays low-contrast
    TaskStatus.IN_PROGRESS: _ANSI_BLUE,  # was orange — now Claude-Code blue
    TaskStatus.COMPLETED: _ANSI_GREEN,
}


def task_status_icon(status: TaskStatus, *, use_color: bool = True) -> str:
    """Return the single-char glyph CCB shows for a given status."""
    icon = _STATUS_ICONS[status]
    return _color(_STATUS_COLORS[status], icon, use_color=use_color)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------


def render_task_compact(task: Task, *, use_color: bool = True,
                        resolved_ids: Optional[set[str]] = None) -> str:
    """One-line summary — the per-row format CCB uses in the task panel.

    Format: ``[ICON] #ID  SUBJECT (owner) [blocked by #X, #Y]``
    The spinner label (activeForm) replaces SUBJECT when status is in_progress.
    ``blockedBy`` excludes already-resolved blockers (matches CCB).
    """
    icon = task_status_icon(task.status, use_color=use_color)
    label = (
        task.active_form
        if task.status is TaskStatus.IN_PROGRESS and task.active_form
        else task.subject
    )

    # Body text (#id + label) stays on the terminal default foreground so
    # dark-theme users see white, light-theme users see black.  No explicit
    # color code here — we deliberately avoid painting the main text.
    parts = [f"{icon} #{task.id}  {label}"]

    if task.owner:
        owner_str = f"({task.owner})"
        parts.append(_color(_ANSI_DIM, owner_str, use_color=use_color))

    if task.blocked_by:
        live = [b for b in task.blocked_by
                if resolved_ids is None or b not in resolved_ids]
        if live:
            blocked_str = f"[blocked by {', '.join(f'#{b}' for b in live)}]"
            parts.append(_color(_ANSI_FAINT_RED, blocked_str, use_color=use_color))

    return "  ".join(parts)


def render_task_list(
    store: Optional[TaskStore] = None,
    *,
    use_color: bool = True,
    title: str = "Tasks",
    show_empty_message: bool = True,
    highlight_in_progress: bool = True,
) -> str:
    """Full task-panel string. Format:

        ┌─ Tasks (2 of 4 done) ───────────────────
        │  ✓ #1  Design schema
        │  ● #2  Implementing API        ← bold when highlighted
        │  ○ #3  Write tests             (alice)
        │  ○ #4  Add docs                [blocked by #3]
        └─────────────────────────────────────────

    ``highlight_in_progress=True`` bolds the in-progress row, matching CCB's
    spinner styling.
    """
    s = store if store is not None else get_default_store()
    tasks = [t for t in s.list() if not t.metadata.get("_internal")]
    resolved = {t.id for t in tasks if t.status is TaskStatus.COMPLETED}

    buf = io.StringIO()

    done = sum(1 for t in tasks if t.status is TaskStatus.COMPLETED)
    header = f"{title} ({done} of {len(tasks)} done)" if tasks else title
    header_line = f"┌─ {header} "
    # Header + border use dim grey — same restraint as Claude Code's panel.
    # No bold, no bright color so the eye lands on the row icons, not the chrome.
    buf.write(_color(_ANSI_GREY, header_line, use_color=use_color))
    buf.write(_color(_ANSI_GREY, "─" * max(0, 60 - len(header_line)),
                      use_color=use_color))
    buf.write("\n")

    if not tasks:
        if show_empty_message:
            buf.write(_color(_ANSI_GREY, "│  ", use_color=use_color))
            buf.write(_color(_ANSI_DIM, "No tasks yet.", use_color=use_color))
            buf.write("\n")
    else:
        for t in tasks:
            row = render_task_compact(t, use_color=use_color, resolved_ids=resolved)
            # Previously we bolded the in-progress row, which looked
            # aggressive against the default white body text.  Claude Code
            # relies solely on the colored status dot for emphasis, so we
            # now follow the same convention and drop the bold entirely.
            _ = highlight_in_progress
            buf.write(_color(_ANSI_GREY, "│  ", use_color=use_color))
            buf.write(row)
            buf.write("\n")

    buf.write(_color(_ANSI_GREY, "└" + "─" * 60, use_color=use_color))
    buf.write("\n")
    return buf.getvalue()


def render_task_detail(task: Task, *, use_color: bool = True) -> str:
    """Full-detail pane for TaskGet — shows description, blockers, metadata."""
    buf = io.StringIO()
    icon = task_status_icon(task.status, use_color=use_color)
    # Title stays in default foreground — only the status icon carries color.
    # (Earlier revision bolded the whole line, which clashed with the status
    # palette we just refined.)
    buf.write(f"{icon} Task #{task.id}: {task.subject}")
    buf.write("\n")
    buf.write(f"  Status: {task.status.value}\n")

    if task.active_form:
        buf.write(f"  Active form: {task.active_form}\n")
    if task.owner:
        buf.write(f"  Owner: {task.owner}\n")

    if task.description:
        buf.write("\n")
        buf.write(_color(_ANSI_DIM, "  Description:", use_color=use_color))
        buf.write("\n")
        for line in task.description.splitlines():
            buf.write(f"    {line}\n")

    if task.blocked_by:
        blocked = f"  Blocked by: {', '.join(f'#{b}' for b in task.blocked_by)}"
        buf.write(_color(_ANSI_FAINT_RED, blocked, use_color=use_color))
        buf.write("\n")
    if task.blocks:
        buf.write(f"  Blocks: {', '.join(f'#{b}' for b in task.blocks)}\n")
    if task.metadata:
        buf.write(f"  Metadata: {task.metadata}\n")

    return buf.getvalue()


def render_task_stream(
    tasks: Iterable[Task],
    *,
    use_color: bool = True,
    resolved_ids: Optional[set[str]] = None,
) -> str:
    """Flat newline-separated list of compact rows — used for embedded views."""
    return "\n".join(
        render_task_compact(t, use_color=use_color, resolved_ids=resolved_ids)
        for t in tasks
    )
