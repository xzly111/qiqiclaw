"""Resolve QIQICLAW_HOME for standalone skill scripts.

Skill scripts may run outside the QiQi claw process (e.g. system Python,
nix env, CI) where ``qiqiclaw_constants`` is not importable.  This module
provides the same ``get_qiqiclaw_home()`` and ``display_qiqiclaw_home()``
contracts as ``qiqiclaw_constants`` without requiring it on ``sys.path``.

When ``qiqiclaw_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``qiqiclaw_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``QIQICLAW_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from qiqiclaw_constants import display_qiqiclaw_home as display_qiqiclaw_home
    from qiqiclaw_constants import get_qiqiclaw_home as get_qiqiclaw_home
except (ModuleNotFoundError, ImportError):

    def get_qiqiclaw_home() -> Path:
        """Return the QiQi claw home directory (default: ~/.qiqiclaw).

        Mirrors ``qiqiclaw_constants.get_qiqiclaw_home()``."""
        val = os.environ.get("QIQICLAW_HOME", "").strip()
        return Path(val) if val else Path.home() / ".qiqiclaw"

    def display_qiqiclaw_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``qiqiclaw_constants.display_qiqiclaw_home()``."""
        home = get_qiqiclaw_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
