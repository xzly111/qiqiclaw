"""QueryChainTracking — subagent/delegate call-tree tracking.

Direct Python port of CCB's ``QueryChainTracking`` type (src/Tool.ts):

    export type QueryChainTracking = {
      chainId: string
      depth: number
    }

Used by CCB's ``ToolUseContext.queryTracking`` to follow nested agent calls
(AgentTool → subagent → delegate_task → ...). When a subagent is spawned, the
parent forks its chain by keeping the same ``chainId`` but incrementing
``depth``. This lets observability tools (langfuse traces, logs) group related
calls under one logical conversation turn.

Design goals matching CCB
-------------------------
- **chainId** — stable across the whole call tree for one user turn.
- **depth** — 0 at the root (REPL user input), 1 inside a first-level subagent,
  2 inside a nested one, etc.
- **Immutable by default** — both ``root_chain()`` and ``fork_chain()`` return
  fresh values; callers never mutate an existing chain in place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class QueryChainTracking:
    """Identifier + depth for a query chain.

    Mirrors CCB's TypeScript type one-to-one. ``frozen=True`` makes it
    hashable and safe to pass across threads — same invariant CCB gets
    from TypeScript's ``readonly`` + immutable data convention.
    """

    chain_id: str
    depth: int = 0

    def __post_init__(self):
        if self.depth < 0:
            raise ValueError(f"depth must be >= 0, got {self.depth}")
        if not self.chain_id:
            raise ValueError("chain_id must be non-empty")

    def fork(self) -> "QueryChainTracking":
        """Return a fresh chain one level deeper, same chain_id.

        Matches CCB's convention when spawning a subagent: keep the parent's
        chainId, bump depth by 1.
        """
        return replace(self, depth=self.depth + 1)


def root_chain(chain_id: str | None = None) -> QueryChainTracking:
    """Start a new root chain (depth=0).

    When ``chain_id`` is None, a UUID4 is generated — same default CCB uses
    at REPL turn boundaries.
    """
    return QueryChainTracking(
        chain_id=chain_id or str(uuid.uuid4()),
        depth=0,
    )


def fork_chain(parent: QueryChainTracking) -> QueryChainTracking:
    """Fork a chain to spawn a subagent.

    Thin wrapper around ``parent.fork()`` to give callers an explicit
    ``fork_chain(ctx.query_tracking)`` idiom that reads the same as CCB's
    ``{ ...parent.queryTracking, depth: parent.queryTracking.depth + 1 }``.
    """
    return parent.fork()
