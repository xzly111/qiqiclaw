"""Backward-compatible SessionDB exports.

The canonical implementation now lives in ``agent.state.session_db``.
This module remains as a compatibility shim so existing imports and tests
continue to work unchanged.
"""

from agent.state.session_db import DEFAULT_DB_PATH, SCHEMA_SQL, SCHEMA_VERSION, SessionDB

__all__ = [
    "DEFAULT_DB_PATH",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "SessionDB",
]
