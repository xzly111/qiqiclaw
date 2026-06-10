"""LangGraph orchestration entrypoint for QiQiClaw.

This module keeps LangGraph at the workflow layer and QiQiClaw at the
intelligence layer: LangGraph owns state and graph execution, while the graph
node delegates the actual reasoning/tool/memory run to QiQiClaw's oneshot
agent path.
"""

from __future__ import annotations

import json
import os
import sys
from importlib import import_module
from pathlib import Path
from typing import Callable, Iterable, NotRequired, TypedDict

from qiqiclaw_constants import _set_legacy_env


QIQICLAW_ROOT = Path(__file__).resolve().parents[1]
BUNDLED_LANGGRAPH_SOURCE = QIQICLAW_ROOT / "vendor" / "langgraph-main"
# External source override. Previously this was a hardcoded developer-machine
# path ("/home/szd/下载/langgraph-main") which is a dead path on every other
# machine. It is now driven by the QIQICLAW_LANGGRAPH_SOURCE env var so a
# developer can point at a local langgraph checkout without editing source;
# unset (the normal case) means "no external source — use the bundled vendor
# copy", which the resolution chain below already prefers.
_EXTERNAL_LANGGRAPH_ENV = os.environ.get("QIQICLAW_LANGGRAPH_SOURCE", "").strip()
EXTERNAL_LANGGRAPH_SOURCE = (
    Path(_EXTERNAL_LANGGRAPH_ENV).expanduser() if _EXTERNAL_LANGGRAPH_ENV else None
)
DEFAULT_LANGGRAPH_SOURCE = BUNDLED_LANGGRAPH_SOURCE


class QiQiClawGraphState(TypedDict):
    """State passed through the QiQiClaw LangGraph workflow."""

    prompt: str
    response: NotRequired[str]
    model: NotRequired[str | None]
    provider: NotRequired[str | None]
    toolsets: NotRequired[list[str] | None]
    status: NotRequired[str]
    error: NotRequired[str]
    langgraph_source: NotRequired[str]


QiQiClawRunner = Callable[[str, str | None, str | None, list[str] | None], str]


def _resolve_langgraph_source(source_path: str | os.PathLike[str] | None = None) -> Path:
    if source_path:
        return Path(source_path).expanduser()
    if BUNDLED_LANGGRAPH_SOURCE.exists():
        return BUNDLED_LANGGRAPH_SOURCE
    if EXTERNAL_LANGGRAPH_SOURCE is not None:
        return EXTERNAL_LANGGRAPH_SOURCE
    # No bundled vendor copy and no external override configured: return the
    # bundled path anyway so downstream .exists() checks fail cleanly with a
    # meaningful path rather than crashing on None.
    return BUNDLED_LANGGRAPH_SOURCE


def _langgraph_source_paths(source_path: str | os.PathLike[str] | None = None) -> list[Path]:
    root = _resolve_langgraph_source(source_path)
    libs = root / "libs"
    candidates = [
        libs / "langgraph",
        libs / "checkpoint",
        libs / "prebuilt",
        libs / "sdk-py",
    ]
    return [path.resolve() for path in candidates if path.exists()]


def activate_local_langgraph_source(source_path: str | os.PathLike[str] | None = None) -> str:
    """Prepend local langgraph-main library paths so QiQiClaw can drive that checkout."""
    paths = _langgraph_source_paths(source_path)
    if not paths:
        return ""
    root = _resolve_langgraph_source(source_path).resolve()
    for path in reversed(paths):
        text = str(path)
        if text in sys.path:
            sys.path.remove(text)
        sys.path.insert(0, text)
    should_clear = False
    for name, module in list(sys.modules.items()):
        if name != "langgraph" and not name.startswith("langgraph."):
            continue
        loaded_file = getattr(module, "__file__", None)
        if loaded_file and not str(Path(loaded_file).resolve()).startswith(str(root)):
            should_clear = True
            break
    if should_clear:
        for name in [name for name in sys.modules if name == "langgraph" or name.startswith("langgraph.")]:
            sys.modules.pop(name, None)
    return str(root)


def _load_langgraph(source_path: str | os.PathLike[str] | None = None, *, prefer_local: bool = True):
    if prefer_local:
        activate_local_langgraph_source(source_path)
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:  # pragma: no cover - exercised by CLI environment
        raise RuntimeError(
            "LangGraph is not installed. Install project dependencies with "
            "`python -m pip install -e .` or `uv pip install -e .`."
        ) from exc
    return StateGraph, START, END


def get_langgraph_runtime_status(source_path: str | os.PathLike[str] | None = None) -> dict[str, object]:
    """Return import/runtime metadata for the LangGraph integration."""
    requested_root = _resolve_langgraph_source(source_path)
    local_paths = _langgraph_source_paths(requested_root)
    activated_source = activate_local_langgraph_source(requested_root) if local_paths else ""
    available = False
    error = ""
    module_file = ""
    graph_module_file = ""
    package_paths: list[str] = []
    version = ""
    try:
        from importlib import metadata

        try:
            version = metadata.version("langgraph")
        except Exception:
            version = ""
        import langgraph
        graph_module = import_module("langgraph.graph")
        getattr(graph_module, "StateGraph")

        module_file_raw = getattr(langgraph, "__file__", None)
        module_file = str(Path(module_file_raw).resolve()) if module_file_raw else ""
        graph_module_raw = getattr(graph_module, "__file__", None)
        graph_module_file = str(Path(graph_module_raw).resolve()) if graph_module_raw else ""
        package_paths = [
            str(Path(path).resolve()) for path in list(getattr(langgraph, "__path__", []) or [])
        ]
        available = True
    except Exception as exc:
        error = str(exc)
    local_root = str(Path(activated_source).resolve()) if activated_source else ""
    using_local_source = bool(
        local_root
        and (
            graph_module_file.startswith(local_root)
            or any(path.startswith(local_root) for path in package_paths)
        )
    )
    return {
        "available": available,
        "version": version,
        "error": error,
        "source_path": activated_source,
        "bundled_source_path": str(BUNDLED_LANGGRAPH_SOURCE.resolve()),
        "external_source_path": (
            str(EXTERNAL_LANGGRAPH_SOURCE.resolve()) if EXTERNAL_LANGGRAPH_SOURCE else ""
        ),
        "module_file": module_file,
        "graph_module_file": graph_module_file,
        "package_paths": package_paths,
        "local_paths": [str(path) for path in local_paths],
        "using_local_source": using_local_source,
    }


def normalize_toolsets(toolsets: object = None) -> list[str] | None:
    if not toolsets:
        return None
    raw_items: Iterable[object]
    if isinstance(toolsets, str):
        raw_items = toolsets.split(",")
    elif isinstance(toolsets, Iterable):
        raw_items = toolsets
    else:
        raw_items = [toolsets]
    normalized = [str(item).strip() for item in raw_items]
    return [item for item in normalized if item] or None


def qiqiclaw_oneshot_runner(
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    toolsets: list[str] | None = None,
) -> str:
    """Run the existing QiQiClaw oneshot agent as a LangGraph node."""
    from qiqiclaw_cli.oneshot import _run_agent

    os.environ["QIQICLAW_YOLO_MODE"] = "1"
    _set_legacy_env("ACCEPT_HOOKS", "1")
    return _run_agent(prompt, model=model, provider=provider, toolsets=toolsets) or ""


def build_qiqiclaw_graph(
    runner: QiQiClawRunner | None = None,
    *,
    source_path: str | os.PathLike[str] | None = None,
    prefer_local_source: bool = True,
):
    """Build and compile the minimal QiQiClaw LangGraph workflow."""
    StateGraph, START, END = _load_langgraph(source_path, prefer_local=prefer_local_source)
    active_runner = runner or qiqiclaw_oneshot_runner

    def run_qiqiclaw(state: QiQiClawGraphState) -> QiQiClawGraphState:
        prompt = (state.get("prompt") or "").strip()
        if not prompt:
            return {"status": "error", "error": "prompt is required"}
        try:
            response = active_runner(
                prompt,
                state.get("model"),
                state.get("provider"),
                state.get("toolsets"),
            )
        except Exception as exc:  # noqa: BLE001 - surface graph node failure
            return {"status": "error", "error": str(exc)}
        return {"status": "ok", "response": response}

    graph = StateGraph(QiQiClawGraphState)
    graph.add_node("qiqiclaw", run_qiqiclaw)
    graph.add_edge(START, "qiqiclaw")
    graph.add_edge("qiqiclaw", END)
    return graph.compile()


def invoke_qiqiclaw_graph(
    prompt: str,
    *,
    model: str | None = None,
    provider: str | None = None,
    toolsets: object = None,
    runner: QiQiClawRunner | None = None,
    source_path: str | os.PathLike[str] | None = None,
    prefer_local_source: bool = True,
) -> QiQiClawGraphState:
    """Invoke the QiQiClaw LangGraph workflow and return final state."""
    source_used = activate_local_langgraph_source(source_path) if prefer_local_source else ""
    app = build_qiqiclaw_graph(
        runner=runner,
        source_path=source_path,
        prefer_local_source=prefer_local_source,
    )
    initial: QiQiClawGraphState = {
        "prompt": prompt,
        "model": model,
        "provider": provider,
        "toolsets": normalize_toolsets(toolsets),
        "langgraph_source": source_used,
    }
    return app.invoke(initial)


def _dry_run_runner(
    prompt: str,
    model: str | None = None,
    provider: str | None = None,
    toolsets: list[str] | None = None,
) -> str:
    details = []
    if model:
        details.append(f"model={model}")
    if provider:
        details.append(f"provider={provider}")
    if toolsets:
        details.append(f"toolsets={','.join(toolsets)}")
    suffix = f" ({'; '.join(details)})" if details else ""
    return f"LangGraph dry-run routed prompt to QiQiClaw{suffix}: {prompt}"


dry_run_runner = _dry_run_runner


def run_cli(args) -> int:
    prompt = " ".join(getattr(args, "prompt", []) or []).strip()
    runner = _dry_run_runner if getattr(args, "dry_run", False) else None
    state = invoke_qiqiclaw_graph(
        prompt,
        model=getattr(args, "model", None),
        provider=getattr(args, "provider", None),
        toolsets=getattr(args, "toolsets", None),
        runner=runner,
        source_path=getattr(args, "source_path", None),
        prefer_local_source=not getattr(args, "no_local_source", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(state, ensure_ascii=False, sort_keys=True))
    elif state.get("status") == "ok":
        print(state.get("response", ""))
    else:
        print(state.get("error", "LangGraph workflow failed"), file=sys.stderr)
    return 0 if state.get("status") == "ok" else 1
