"""ToolUseContext — structured context passed to tool handlers.

Direct port of CCB's ``ToolUseContext`` type (src/Tool.ts) to Python. CCB
threads this object through every tool call, carrying abort signals, file
state caches, Langfuse spans, telemetry buckets, and ~40 other concerns.

Full fidelity isn't realistic in one pass — many of CCB's fields depend on
Ink/React internals (``setToolJSX``, ``appendSystemMessage``) or on
observability libraries (``langfuseTrace``) that aren't wired into
QIQI-claw yet. This module gives the **same shape** and **same field names**
as CCB, with Python-native types, so code can start using the container
today and individual fields light up as each subsystem ports across.

Fields are grouped exactly as in CCB's declaration:

1. Query-turn metadata
2. Cancellation + limits
3. State access
4. Elicitation / UI callbacks (reserved — None in Python-only contexts)
5. Telemetry + observability
6. Sub-agent bookkeeping
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable, Optional

from qiqiclaw_subsystems.chain_tracking import QueryChainTracking


# ---------------------------------------------------------------------------
# Cancellation token — CCB uses AbortController; Python has threading.Event
# ---------------------------------------------------------------------------


class AbortController:
    """Cooperative cancellation token.

    Mirrors the subset of the web AbortController API that CCB actually
    uses: ``signal.aborted`` boolean + ``abort()`` to flip it.
    """

    def __init__(self) -> None:
        self._event = Event()

    @property
    def aborted(self) -> bool:
        return self._event.is_set()

    def abort(self) -> None:
        self._event.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until aborted or timeout expires."""
        return self._event.wait(timeout)


# ---------------------------------------------------------------------------
# Limit bags — CCB has ``fileReadingLimits`` + ``globLimits``
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileReadingLimits:
    """Optional caps on a single file_read call."""
    max_tokens: Optional[int] = None
    max_size_bytes: Optional[int] = None


@dataclass(frozen=True)
class GlobLimits:
    """Optional caps on a single glob/search call."""
    max_results: Optional[int] = None


# ---------------------------------------------------------------------------
# ToolUseContext — the whole container
# ---------------------------------------------------------------------------


@dataclass
class ToolUseContext:
    """Everything a tool handler needs that isn't in its own ``args`` dict.

    Field names + grouping match CCB's ``ToolUseContext`` declaration. Python
    doesn't have CCB's ``DeepImmutable<T>`` helper, so mutation discipline
    is by convention: treat this object as read-mostly, and never reassign
    fields while a tool call is in flight.
    """

    # --- 1. Query-turn metadata --------------------------------------------

    #: Unique id for this tool call. CCB calls this ``toolUseId``.
    tool_use_id: Optional[str] = None

    #: Chain tracking — REPL sets a root chain, subagents fork it.
    query_tracking: Optional[QueryChainTracking] = None

    #: Conversation-level history visible to this tool (read-only snapshot).
    messages: list[Any] = field(default_factory=list)

    # --- 2. Cancellation + limits ------------------------------------------

    #: Signals user interrupt — tool handlers should poll ``.aborted`` between
    #: long-running stages.
    abort_controller: AbortController = field(default_factory=AbortController)

    file_reading_limits: Optional[FileReadingLimits] = None
    glob_limits: Optional[GlobLimits] = None
    max_budget_usd: Optional[float] = None

    # --- 3. State access callbacks -----------------------------------------

    #: Mirrors CCB's ``getAppState()`` — returns the current app state snapshot.
    get_app_state: Optional[Callable[[], Any]] = None

    #: Mirrors CCB's ``setAppState(f)`` — applies a functional update.
    set_app_state: Optional[Callable[[Callable[[Any], Any]], None]] = None

    #: CCB's ``setAppStateForTasks`` — always-shared even for sub-agents.
    set_app_state_for_tasks: Optional[Callable[[Callable[[Any], Any]], None]] = None

    # --- 4. UI / elicitation callbacks (None in Python TUI) ----------------

    #: Mirrors CCB's ``setToolJSX``. In a text-only TUI these are None,
    #: but reserving the slot makes code ported from CCB compile.
    set_tool_jsx: Optional[Callable[[Any], None]] = None
    append_system_message: Optional[Callable[[Any], None]] = None
    send_os_notification: Optional[Callable[[dict], None]] = None
    add_notification: Optional[Callable[[Any], None]] = None
    open_message_selector: Optional[Callable[[], None]] = None
    request_prompt: Optional[Callable[[str, Optional[str]], Callable]] = None
    handle_elicitation: Optional[Callable[..., Any]] = None

    # --- 5. Telemetry + observability --------------------------------------

    #: Root span for this query turn — observability libs hook in here.
    langfuse_trace: Optional[Any] = None
    langfuse_root_trace: Optional[Any] = None
    langfuse_batch_span: Optional[Any] = None
    push_api_metrics_entry: Optional[Callable[[float], None]] = None

    # --- 6. Sub-agent bookkeeping ------------------------------------------

    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    user_modified: bool = False

    #: CCB keeps these Sets to dedupe nested-memory / skill triggers across a
    #: single turn. Defaults to empty sets so handlers can ``.add()`` freely.
    nested_memory_attachment_triggers: set[str] = field(default_factory=set)
    loaded_nested_memory_paths: set[str] = field(default_factory=set)
    dynamic_skill_dir_triggers: set[str] = field(default_factory=set)
    discovered_skill_names: set[str] = field(default_factory=set)

    #: If set, forces the permission system to re-evaluate even when hooks
    #: auto-approve. CCB uses this for speculation with overlay file paths.
    require_can_use_tool: bool = False

    #: Whether to keep toolUseResult on sub-agent messages.
    preserve_tool_use_results: bool = False

    #: Pre-rendered system prompt — shared with subagents to keep prompt
    #: cache warm (CCB's forkSubagent.ts rationale).
    rendered_system_prompt: Optional[Any] = None

    # --- Convenience helpers ----------------------------------------------

    def fork_for_subagent(
        self,
        *,
        agent_id: Optional[str] = None,
        agent_type: Optional[str] = None,
    ) -> "ToolUseContext":
        """Return a fresh context for a spawned subagent.

        Mirrors CCB's ``createSubagentContext``: share the abort controller,
        fork the chain (depth+1), carry over caches, clear turn-specific
        fields (tool_use_id, langfuse_batch_span).
        """
        forked_chain = (
            self.query_tracking.fork() if self.query_tracking is not None else None
        )
        return ToolUseContext(
            tool_use_id=None,  # child gets its own
            query_tracking=forked_chain,
            messages=list(self.messages),  # snapshot
            abort_controller=self.abort_controller,  # shared
            file_reading_limits=self.file_reading_limits,
            glob_limits=self.glob_limits,
            max_budget_usd=self.max_budget_usd,
            get_app_state=self.get_app_state,
            set_app_state=self.set_app_state,
            set_app_state_for_tasks=self.set_app_state_for_tasks,
            set_tool_jsx=self.set_tool_jsx,
            append_system_message=self.append_system_message,
            send_os_notification=self.send_os_notification,
            add_notification=self.add_notification,
            open_message_selector=self.open_message_selector,
            request_prompt=self.request_prompt,
            handle_elicitation=self.handle_elicitation,
            langfuse_trace=None,  # new span for child
            langfuse_root_trace=self.langfuse_root_trace,
            langfuse_batch_span=None,
            push_api_metrics_entry=self.push_api_metrics_entry,
            agent_id=agent_id,
            agent_type=agent_type,
            user_modified=self.user_modified,
            nested_memory_attachment_triggers=set(self.nested_memory_attachment_triggers),
            loaded_nested_memory_paths=set(self.loaded_nested_memory_paths),
            dynamic_skill_dir_triggers=set(self.dynamic_skill_dir_triggers),
            discovered_skill_names=set(self.discovered_skill_names),
            require_can_use_tool=self.require_can_use_tool,
            preserve_tool_use_results=self.preserve_tool_use_results,
            rendered_system_prompt=self.rendered_system_prompt,
        )
