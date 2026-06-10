"""Runtime feature flags — Python port of CCB's ``feature()`` from ``bun:bundle``.

CCB uses Bun's compile-time flag-elimination:

    import { feature } from 'bun:bundle'
    if (feature('BUDDY')) { ... }

At build time, Bun replaces the call with a literal ``true``/``false`` and
tree-shakes the dead branch. The runtime default comes from environment
variables: ``FEATURE_BUDDY=1`` enables it in dev mode.

Python has no equivalent compile-time elimination, but we can give callers
**the exact same read-site idiom**:

    from qiqiclaw_subsystems.features import feature
    if feature('BUDDY'):
        ...

Under the hood the flag state is a ``FeatureFlags`` object — either a
module-global singleton (default, matching CCB's module-level ``feature()``
import) or an explicit instance passed to ``feature('X', flags=my_flags)``
for test isolation.

Flag name catalog is imported verbatim from CCB's ``build.ts`` so any name
documented in CCB's CLAUDE.md works identically here.
"""

from __future__ import annotations

import os
import threading
from typing import Iterable


# ---------------------------------------------------------------------------
# Flag catalog — the exact 19 names enabled in CCB's build.ts + extras
# ---------------------------------------------------------------------------

#: Flag names drawn from CCB's build.ts default-enabled list, grouped as in
#: CCB's CLAUDE.md. Kept as a frozenset so typos fail fast.
KNOWN_FLAGS: frozenset[str] = frozenset({
    # Base
    "BUDDY",
    "TRANSCRIPT_CLASSIFIER",
    "BRIDGE_MODE",
    "AGENT_TRIGGERS_REMOTE",
    "CHICAGO_MCP",
    "VOICE_MODE",
    # Stats / cache
    "SHOT_STATS",
    "PROMPT_CACHE_BREAK_DETECTION",
    "TOKEN_BUDGET",
    # P0 local
    "AGENT_TRIGGERS",
    "ULTRATHINK",
    "BUILTIN_EXPLORE_PLAN_AGENTS",
    "LODESTONE",
    # P1 API-dependent
    "EXTRACT_MEMORIES",
    "VERIFICATION_AGENT",
    "KAIROS_BRIEF",
    "AWAY_SUMMARY",
    "ULTRAPLAN",
    # P2
    "DAEMON",
    # Extras CCB references but doesn't list in build.ts defaults
    "KAIROS",
    "PROACTIVE",
    "MONITOR_TOOL",
    "KAIROS_PUSH_NOTIFICATION",
    "KAIROS_GITHUB_WEBHOOKS",
    "DUMP_SYSTEM_PROMPT",
    "BG_SESSIONS",
})


#: Env-var prefix — CCB enables ``BUDDY`` with ``FEATURE_BUDDY=1``. Kept
#: identical so ops playbooks port cleanly.
ENV_PREFIX: str = "FEATURE_"


def _truthy(value: str | None) -> bool:
    """CCB's env-var truthiness: ``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive).

    CCB's JS treats any non-empty string as truthy; we narrow to the explicit
    tokens humans actually write in shell so typos like ``FEATURE_X=no`` are
    respected.
    """
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


class FeatureFlags:
    """A mutable set of feature-flag overrides.

    Default source of truth is ``os.environ[f"{ENV_PREFIX}{name}"]``. Explicit
    ``enable(...)`` / ``disable(...)`` / ``set(...)`` calls override the env
    for the lifetime of this instance, letting tests flip flags without
    touching the process environment.

    Mirrors the contract of CCB's ``feature()`` call — a boolean lookup by
    name with fail-closed default (unknown flag → False).
    """

    def __init__(self, overrides: dict[str, bool] | None = None,
                 *, env: dict[str, str] | None = None,
                 strict_known: bool = True):
        """Create a flag set.

        Parameters
        ----------
        overrides
            Initial ``{name: enabled}`` map. These win over env.
        env
            Optional dict substituted for ``os.environ`` (test seam).
        strict_known
            When True, raise on an unknown flag name. Set False to mirror
            CCB's lenient-on-unknown behavior (returns False for anything).
        """
        self._overrides: dict[str, bool] = dict(overrides or {})
        self._env = env if env is not None else os.environ
        self._strict_known = strict_known
        self._lock = threading.RLock()

    def _validate(self, name: str) -> None:
        if self._strict_known and name not in KNOWN_FLAGS:
            raise KeyError(
                f"Unknown feature flag {name!r}. Known flags: "
                f"{sorted(KNOWN_FLAGS)}"
            )

    def is_enabled(self, name: str) -> bool:
        """Return True if flag ``name`` is currently on.

        Lookup order (matches CCB):
          1. Explicit override set via ``enable()`` / ``disable()`` / ``set()``
          2. Env var ``FEATURE_<NAME>`` truthiness
          3. Fail-closed: False
        """
        with self._lock:
            if name in self._overrides:
                return self._overrides[name]
        # env lookup — don't hold the lock while reading os.environ
        env_key = f"{ENV_PREFIX}{name}"
        return _truthy(self._env.get(env_key))

    def enable(self, name: str) -> None:
        self._validate(name)
        with self._lock:
            self._overrides[name] = True

    def disable(self, name: str) -> None:
        self._validate(name)
        with self._lock:
            self._overrides[name] = False

    def set(self, name: str, value: bool) -> None:
        self._validate(name)
        with self._lock:
            self._overrides[name] = bool(value)

    def clear_override(self, name: str) -> None:
        """Remove an explicit override so env becomes authoritative again."""
        with self._lock:
            self._overrides.pop(name, None)

    def enabled_flags(self) -> list[str]:
        """Sorted list of currently-enabled flag names (env + overrides)."""
        seen: set[str] = set()
        for name in KNOWN_FLAGS:
            if self.is_enabled(name):
                seen.add(name)
        with self._lock:
            for name, val in self._overrides.items():
                if val:
                    seen.add(name)
        return sorted(seen)

    def enable_all(self, names: Iterable[str]) -> None:
        """Batch-enable the way CCB's dev mode does (full default-on)."""
        for n in names:
            self.enable(n)


# ---------------------------------------------------------------------------
# Module-level singleton — matches CCB's module-level ``feature`` import
# ---------------------------------------------------------------------------

_global_flags: FeatureFlags = FeatureFlags(strict_known=False)
_global_lock = threading.RLock()


def get_global_flags() -> FeatureFlags:
    """Return the process-wide FeatureFlags instance."""
    with _global_lock:
        return _global_flags


def set_global_flags(flags: FeatureFlags) -> None:
    """Swap the global flag store — primarily a test seam."""
    global _global_flags
    with _global_lock:
        _global_flags = flags


def feature(name: str, *, flags: FeatureFlags | None = None) -> bool:
    """Check a feature flag — read-site identical to CCB's ``feature('X')``.

    Passing ``flags=`` targets a specific instance (unit tests); otherwise
    the module-level singleton is used.
    """
    return (flags or get_global_flags()).is_enabled(name)
