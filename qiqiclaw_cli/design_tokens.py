#!/usr/bin/env python3
"""
QiQiClaw CLI 设计令牌 — 三层颜色语义系统
吸收 Reasonix 的 FG/TONE/CARD 设计语言
基于 Rich 样式系统，与 skin_engine 独立叠加
"""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class DesignTokens:
    """三层语义颜色令牌 — FG(内容层级) / TONE(语义色彩) / CARD(卡片边框)

    用法:
        from qiqiclaw_cli.design_tokens import tokens
        console.print("hello", style=tokens.FG["brand"])
    """

    # ── FG：内容层级 (6级) ──
    FG: Dict[str, str] = field(default_factory=lambda: {
        "brand":      "bright_cyan",
        "body":       "bright_white",
        "strong":     "white bold",
        "sub":        "grey70",
        "meta":       "grey50",
        "faint":      "grey35",
    })

    # ── TONE：语义色彩 (6色) ──
    TONE: Dict[str, str] = field(default_factory=lambda: {
        "brand":      "bright_cyan",
        "accent":     "magenta",
        "warn":       "yellow",
        "err":        "red",
        "ok":         "green",
        "info":       "blue",
    })

    # ── CARD：8种语义卡片映射 ──
    CARD: Dict[str, Dict[str, str]] = field(default_factory=lambda: {
        "tool":       {"color": "blue",          "glyph": "●"},
        "thinking":   {"color": "bright_cyan",   "glyph": "◉"},
        "warn":       {"color": "yellow",        "glyph": "⚠"},
        "error":      {"color": "red",           "glyph": "✗"},
        "ok":         {"color": "green",         "glyph": "✓"},
        "memory":     {"color": "green",         "glyph": "●"},
        "diff":       {"color": "cyan",          "glyph": "±"},
        "plan":       {"color": "magenta",       "glyph": "●"},
        "user":       {"color": "bright_white",  "glyph": "●"},
        "mcp":        {"color": "bright_yellow", "glyph": "⌁"},
        "session":    {"color": "bright_cyan",   "glyph": "▸"},
        "approval":   {"color": "yellow",        "glyph": "●"},
    })

    # ── 上下文使用率颜色 ──
    def ctx_color(self, ratio: float) -> str:
        """返回上下文使用率对应的颜色"""
        if ratio >= 0.85:
            return self.TONE["err"]
        elif ratio >= 0.75:
            return self.TONE["warn"]
        elif ratio >= 0.50:
            return self.TONE["ok"]
        return self.TONE["info"]

    def merge_skin(self, skin_colors: Dict[str, str]) -> None:
        """从 skin_engine 加载的颜色合并到令牌中"""
        mapping = {
            "ui_accent":      ("FG", "brand"),
            "ui_label":       ("FG", "sub"),
            "ui_ok":          ("TONE", "ok"),
            "ui_error":       ("TONE", "err"),
            "ui_warn":        ("TONE", "warn"),
            "response_border":("CARD", "tool"),
        }
        for skin_key, (target_dict, token_key) in mapping.items():
            if skin_key in skin_colors:
                getattr(self, target_dict)[token_key] = skin_colors[skin_key]


# 全局令牌实例
tokens = DesignTokens()
