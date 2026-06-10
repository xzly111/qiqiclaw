"""
ContextManager — 基于 token 使用率的分级上下文管理
移植自 Reasonix 的分级折叠策略
"""

import logging

logger = logging.getLogger(__name__)

THRESHOLD_NORMAL = 0.75
THRESHOLD_AGGRESSIVE = 0.78
THRESHOLD_FORCE_SUMMARY = 0.85
THRESHOLD_TURN_START = 0.90


class ContextDecision:
    __slots__ = ("kind", "tail_fraction", "aggressive")
    def __init__(self, kind: str, tail_fraction: float = 0.20, aggressive: bool = False):
        self.kind = kind
        self.tail_fraction = tail_fraction
        self.aggressive = aggressive


class ContextManager:
    """分级上下文管理器。"""

    def __init__(
        self,
        ctx_max: int = 128_000,
        threshold_normal: float = THRESHOLD_NORMAL,
        threshold_aggressive: float = THRESHOLD_AGGRESSIVE,
        threshold_summary: float = THRESHOLD_FORCE_SUMMARY,
        threshold_turn_start: float = THRESHOLD_TURN_START,
    ):
        self.ctx_max = ctx_max
        self.threshold_normal = threshold_normal
        self.threshold_aggressive = threshold_aggressive
        self.threshold_summary = threshold_summary
        self.threshold_turn_start = threshold_turn_start
        self._already_folded_this_turn = False

    def start_turn(self):
        self._already_folded_this_turn = False

    def update_context_limit(self, ctx_max: int) -> None:
        if ctx_max:
            self.ctx_max = int(ctx_max)

    def decide_before_turn(self, estimated_tokens: int) -> ContextDecision:
        ratio = estimated_tokens / self.ctx_max
        if ratio > self.threshold_turn_start:
            return ContextDecision(kind="turn-start-fold", tail_fraction=0.15,
                                   aggressive=True)
        return ContextDecision(kind="none")

    def decide_after_usage(self, prompt_tokens: int) -> ContextDecision:
        ratio = prompt_tokens / self.ctx_max

        if ratio > self.threshold_summary:
            return ContextDecision(kind="exit-with-summary")

        if self._already_folded_this_turn:
            return ContextDecision(kind="none")

        if ratio > self.threshold_aggressive:
            self._already_folded_this_turn = True
            return ContextDecision(kind="fold", tail_fraction=0.10,
                                   aggressive=True)

        if ratio > self.threshold_normal:
            self._already_folded_this_turn = True
            return ContextDecision(kind="fold", tail_fraction=0.20,
                                   aggressive=False)

        return ContextDecision(kind="none")
