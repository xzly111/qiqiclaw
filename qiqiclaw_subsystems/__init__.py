"""QIQI-claw subsystems package.

Port of the architectural patterns from Claude-Code-Best (CCB), the
reverse-engineered Claude Code CLI. CCB's TypeScript/Bun/React-Ink runtime
can't be copied verbatim into a Python codebase — but the *design patterns*
(feature flags, subsystem registry, query-chain tracking, structured
tool-use context, task list + CLI rendering) map cleanly to stdlib Python.

Modules
-------
- registry        — subsystem registration + health-check surface
- features        — runtime feature flags driven by env vars
- chain_tracking  — QueryChainTracking for subagent/delegate call trees
- tool_context    — structured ToolUseContext for tool handlers
- tasks           — task store (Task / TaskStatus / TaskStore)
- task_tools      — LLM-callable TaskCreate/Update/List/Get handlers + schemas
- task_display    — ANSI CLI rendering of task panel (CCB visual grammar)
"""

from qiqiclaw_subsystems.chain_tracking import (
    QueryChainTracking,
    fork_chain,
    root_chain,
)
from qiqiclaw_subsystems.features import (
    FeatureFlags,
    feature,
    get_global_flags,
    set_global_flags,
)
from qiqiclaw_subsystems.registry import (
    Subsystem,
    SubsystemRegistry,
    SubsystemStatus,
    get_default_registry,
)
from qiqiclaw_subsystems.task_display import (
    render_task_compact,
    render_task_detail,
    render_task_list,
    render_task_stream,
    task_status_icon,
)
from qiqiclaw_subsystems.task_tools import (
    TASK_CREATE_SCHEMA,
    TASK_GET_SCHEMA,
    TASK_LIST_SCHEMA,
    TASK_TOOLS,
    TASK_UPDATE_SCHEMA,
    task_create_tool,
    task_get_tool,
    task_list_tool,
    task_update_tool,
)
from qiqiclaw_subsystems.tasks import (
    Task,
    TaskStatus,
    TaskStore,
    TaskStoreError,
    VALID_UPDATE_STATUSES,
    get_default_store,
    reset_default_store,
    set_default_store,
)
from qiqiclaw_subsystems.tool_context import ToolUseContext

__all__ = [
    # chain tracking
    "QueryChainTracking",
    "fork_chain",
    "root_chain",
    # features
    "FeatureFlags",
    "feature",
    "get_global_flags",
    "set_global_flags",
    # subsystem registry
    "Subsystem",
    "SubsystemRegistry",
    "SubsystemStatus",
    "get_default_registry",
    # tool context
    "ToolUseContext",
    # tasks core
    "Task",
    "TaskStatus",
    "TaskStore",
    "TaskStoreError",
    "VALID_UPDATE_STATUSES",
    "get_default_store",
    "set_default_store",
    "reset_default_store",
    # task tools (LLM surface)
    "TASK_CREATE_SCHEMA",
    "TASK_GET_SCHEMA",
    "TASK_LIST_SCHEMA",
    "TASK_TOOLS",
    "TASK_UPDATE_SCHEMA",
    "task_create_tool",
    "task_get_tool",
    "task_list_tool",
    "task_update_tool",
    # task display (CLI)
    "render_task_compact",
    "render_task_detail",
    "render_task_list",
    "render_task_stream",
    "task_status_icon",
]
