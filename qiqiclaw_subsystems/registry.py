"""SubsystemRegistry — unified subsystem declaration + health checks.

CCB organizes its codebase into 20+ Bun workspace packages
(``packages/@ant/*``, ``packages/builtin-tools``, ``packages/mcp-client``, ...)
and relies on ``bun run health`` / ``doctor.ts`` to probe each for readiness.
The workspace membership and the health check are two sides of the same thing:
a named subsystem with a liveness probe.

This module exposes the same contract in Python:

* ``Subsystem`` — one named unit (Gateway, Cron, Agent, Tools, Skills, ...).
* ``SubsystemStatus`` — result of a health probe (OK / DEGRADED / UNAVAILABLE).
* ``SubsystemRegistry`` — thread-safe collection of subsystems + batch probe.

Exact behaviour mirrored from CCB:

* A subsystem is ``available`` iff its ``check_fn()`` returns truthy; if it
  raises, the registry catches the exception and marks it UNAVAILABLE with
  the error message attached (CCB's ``doctor.ts`` does the same — see
  ``_evaluate_toolset_check`` in ``tools/registry.py``).
* Results are TTL-cached for 30 s so a ``health`` call from the REPL doesn't
  re-probe Docker / Playwright / API-key validity on every render
  (identical TTL + rationale as ``tools/registry.py::_check_fn_cached``).
* Registering a subsystem with an existing name overwrites silently only
  when metadata matches; otherwise it raises — same shadow-protection
  contract CCB applies to tool registration.
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class HealthState(str, Enum):
    """Three-state health readout — the exact set CCB's doctor uses."""
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class SubsystemStatus:
    """Snapshot of one subsystem's health at probe time."""
    name: str
    state: HealthState
    message: str = ""
    probed_at: float = field(default_factory=time.monotonic)

    @property
    def ok(self) -> bool:
        return self.state is HealthState.OK


@dataclass(frozen=True)
class Subsystem:
    """Declaration of a named subsystem.

    ``category`` mirrors CCB's grouping (``agent``, ``tooling``, ``ui``,
    ``transport``, ``observability``, ``platform``) so UIs can render a
    tree view the same way CCB's doctor screen does.
    """
    name: str
    category: str
    description: str = ""
    check_fn: Optional[Callable[[], bool]] = None
    requires_env: tuple[str, ...] = ()
    optional: bool = False


#: Cache TTL — same 30-second window CCB uses in tools/registry.py. Long
#: enough to amortize Docker/Playwright probes, short enough that
#: env-var flips propagate within a turn or two.
_HEALTH_TTL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SubsystemRegistry:
    """Singleton-capable registry for declared subsystems.

    Thread-safe. Health results are TTL-cached. Callers can ``probe_all()``
    for a doctor-style view or ``probe(name)`` for a single system.
    """

    def __init__(self) -> None:
        self._systems: dict[str, Subsystem] = {}
        self._cache: dict[str, tuple[float, SubsystemStatus]] = {}
        self._lock = threading.RLock()

    # --- Registration -----------------------------------------------------

    def register(self, system: Subsystem) -> None:
        """Add or update a subsystem. Raises on shadow conflict."""
        with self._lock:
            existing = self._systems.get(system.name)
            if existing and existing != system:
                raise ValueError(
                    f"Subsystem {system.name!r} already registered with "
                    f"different metadata. Deregister first if intentional."
                )
            self._systems[system.name] = system
            # A registration change invalidates any cached probe for it.
            self._cache.pop(system.name, None)

    def deregister(self, name: str) -> None:
        with self._lock:
            self._systems.pop(name, None)
            self._cache.pop(name, None)

    def all_names(self) -> list[str]:
        with self._lock:
            return sorted(self._systems.keys())

    def get(self, name: str) -> Optional[Subsystem]:
        with self._lock:
            return self._systems.get(name)

    def categories(self) -> dict[str, list[str]]:
        """Group registered subsystems by ``category`` — powers doctor UIs."""
        groups: dict[str, list[str]] = {}
        with self._lock:
            for s in self._systems.values():
                groups.setdefault(s.category, []).append(s.name)
        for cat in groups:
            groups[cat].sort()
        return dict(sorted(groups.items()))

    # --- Health probes ----------------------------------------------------

    def _probe_now(self, system: Subsystem) -> SubsystemStatus:
        """Run the check_fn with CCB's fail-closed error handling."""
        if system.check_fn is None:
            return SubsystemStatus(
                name=system.name,
                state=HealthState.OK,
                message="no check registered",
            )
        try:
            ok = bool(system.check_fn())
        except Exception as exc:  # noqa: BLE001 — we want to surface any error
            return SubsystemStatus(
                name=system.name,
                state=HealthState.UNAVAILABLE,
                message=f"{type(exc).__name__}: {exc}",
            )
        return SubsystemStatus(
            name=system.name,
            state=HealthState.OK if ok else HealthState.UNAVAILABLE,
            message="" if ok else "check_fn returned falsy",
        )

    def probe(self, name: str, *, use_cache: bool = True) -> SubsystemStatus:
        """Check one subsystem. Cached for ``_HEALTH_TTL_SECONDS``."""
        with self._lock:
            system = self._systems.get(name)
            if system is None:
                raise KeyError(f"Unknown subsystem {name!r}")
            if use_cache:
                cached = self._cache.get(name)
                if cached and (time.monotonic() - cached[0]) < _HEALTH_TTL_SECONDS:
                    return cached[1]
        # Probe outside the lock — check_fn may be slow (Docker / network).
        status = self._probe_now(system)
        with self._lock:
            self._cache[name] = (time.monotonic(), status)
        return status

    def probe_all(self, *, use_cache: bool = True) -> dict[str, SubsystemStatus]:
        """Probe every registered subsystem — doctor-style batch check."""
        # Take a snapshot so concurrent register/deregister doesn't break us.
        with self._lock:
            names = list(self._systems.keys())
        return {n: self.probe(n, use_cache=use_cache) for n in names}

    def invalidate_cache(self, name: str | None = None) -> None:
        """Drop cached probe(s). Call after config changes that change readiness."""
        with self._lock:
            if name is None:
                self._cache.clear()
            else:
                self._cache.pop(name, None)


# ---------------------------------------------------------------------------
# Default registry preloaded with QIQI-claw's known subsystems
# ---------------------------------------------------------------------------


def _build_default_registry() -> SubsystemRegistry:
    """Register the 9 subsystems documented in QIQI-claw's architecture.

    Categories mirror CCB's grouping so a doctor-style UI renders the same
    shape regardless of which codebase it's pointed at.
    """
    reg = SubsystemRegistry()

    subsystems = [
        Subsystem(
            name="cli",
            category="ui",
            description="Interactive TUI REPL (qiqiclaw_cli + cli.py)",
        ),
        Subsystem(
            name="agent",
            category="agent",
            description="run_agent.AIAgent + prompt/context/compression",
        ),
        Subsystem(
            name="tools",
            category="tooling",
            description="Registered tool implementations (tools/*.py)",
        ),
        Subsystem(
            name="tool_environments",
            category="tooling",
            description="Terminal backends: local/docker/modal/ssh/daytona/vercel/singularity",
        ),
        Subsystem(
            name="gateway",
            category="transport",
            description="Cross-platform messaging gateway (telegram/discord/slack/wecom/feishu/...)",
        ),
        Subsystem(
            name="cron",
            category="scheduling",
            description="croniter-backed scheduled-task runner",
        ),
        Subsystem(
            name="plugins",
            category="tooling",
            description="Third-party plugin loader with shadow protection",
        ),
        Subsystem(
            name="tui_gateway",
            category="transport",
            description="Bridge between REPL and gateway messages",
        ),
        Subsystem(
            name="acp_adapter",
            category="transport",
            description="Agent Client Protocol adapter for IDE integrations",
        ),
    ]
    for s in subsystems:
        reg.register(s)
    return reg


_default_registry: Optional[SubsystemRegistry] = None
_default_lock = threading.RLock()


def get_default_registry() -> SubsystemRegistry:
    """Lazily build and return the process-wide default registry."""
    global _default_registry
    with _default_lock:
        if _default_registry is None:
            _default_registry = _build_default_registry()
        return _default_registry


def reset_default_registry() -> None:
    """Test-only helper: nuke the singleton so the next call rebuilds it."""
    global _default_registry
    with _default_lock:
        _default_registry = None
