#!/usr/bin/env python3
"""
QiQiClaw CLI 卡片渲染器
吸收 Reasonix ApprovalCard 设计 — 每种状态渲染为带语义色边框的卡片块

用法:
    from qiqiclaw_cli.card_renderer import StatusCard, ToolCallCard
    card = ToolCallCard("read_file", "/path/to/file", duration="0.3s")
    print(card.render())

视觉结构:
    ┌─ ● 工具名称 ───────────────── [0.3s] ────┐
    │                                            │
    │   内容区域（动态）                            │
    │                                            │
    └─ dim -> Esc to dismiss ────────────────────┘
"""

from typing import Optional
from qiqiclaw_cli.design_tokens import tokens


class StatusCard:
    """通用状态卡片 — 对应 Reasonix ApprovalCard"""

    def __init__(
        self,
        tone: str = "tool",
        title: str = "",
        content: str = "",
        meta_right: str = "",
        footer: str = "",
        collapsed: bool = False,
    ):
        self.tone = tone
        self.title = title
        self.content = content
        self.meta_right = meta_right
        self.footer = footer
        self.collapsed = collapsed

    def render(self) -> str:
        """渲染为带 ANSI 颜色边框的文本块"""
        card = tokens.CARD.get(self.tone, tokens.CARD["tool"])
        color = card["color"]
        glyph = card["glyph"]

        lines = []
        # 顶部边框
        header = f" {glyph} {self.title}"
        if self.meta_right:
            header += f"  [{self.meta_right}]"
        top_border = f"[{color}]┌─[/][dim]{header}[/]"
        # 补齐到合适宽度
        width = max(len(self.content.strip().split('\n')[0]) if self.content else 40, 60)
        top_border += "─" * max(0, width - len(header) - 4)
        top_border += f"[{color}]─┐[/]"
        lines.append(top_border)

        # 内容区域
        if self.collapsed:
            lines.append(f"[{color}]│[/] [dim]... ({len(self.content)} chars collapsed)[/]")
        elif self.content:
            for line in self.content.strip().split('\n')[:20]:  # 最多20行
                lines.append(f"[{color}]│[/]  {line}")
        else:
            lines.append(f"[{color}]│[/]")

        # 底部边框
        if self.footer:
            bottom = f"[{color}]└─[/] [dim]{self.footer}[/]"
            bottom += "─" * max(0, width - len(self.footer) - 6)
        else:
            bottom = f"[{color}]└[/]"
            bottom += "─" * max(0, width - 2)
        bottom += f"[{color}]─┘[/]"
        lines.append(bottom)

        return '\n'.join(lines)

    def render_compact(self) -> str:
        """紧凑模式：单行渲染（用于状态栏上方工具活动指示）"""
        card = tokens.CARD.get(self.tone, tokens.CARD["tool"])
        color = card["color"]
        glyph = card["glyph"]
        return f"[{color}]{glyph}[/] [{color}]{self.title}[/]"


class ToolCallCard(StatusCard):
    """工具调用卡片 — 带状态的语义卡片

    status: "running" | "success" | "error" | "pending"
    运行中用蓝色，成功变绿，失败变红
    """

    TONE_MAP = {
        "running": "tool",
        "success": "ok",
        "error":   "error",
        "pending": "tool",
    }

    def __init__(
        self,
        tool_name: str,
        args_summary: str = "",
        duration: str = "",
        status: str = "running",
    ):
        tone = self.TONE_MAP.get(status, "tool")
        footer = ""
        if status == "success":
            footer = "Esc to dismiss"
        elif status == "running":
            footer = "Running..."
        elif status == "error":
            footer = "Execution failed"

        super().__init__(
            tone=tone,
            title=f"Tool: {tool_name}",
            content=args_summary,
            meta_right=duration,
            footer=footer,
        )


class ThinkingCard(StatusCard):
    """思考/推理卡片 — 流式显示模型推理过程"""

    def __init__(self, content: str = ""):
        super().__init__(
            tone="thinking",
            title="Thinking...",
            content=content,
            footer="Streaming...",
        )


class SessionCard(StatusCard):
    """会话卡片 — 用于会话选择器中的每条记录"""

    def __init__(
        self,
        session_name: str,
        summary: str = "",
        branch: str = "main",
        turns: int = 0,
        cost_label: str = "",
        relative_time: str = "",
        focused: bool = False,
    ):
        self.session_name = session_name
        self.summary = summary
        self.branch = branch
        self.turns = turns
        self.cost_label = cost_label
        self.relative_time = relative_time
        self.focused = focused

        meta = f"{relative_time}"
        if self.turns:
            meta += f" · {turns} turns"
        if self.cost_label:
            meta += f" · {self.cost_label}"

        prefix = "▸ " if focused else "  "
        card = tokens.CARD["session"]
        title = f"{prefix}{session_name} · {branch}"
        content = summary[:80] if summary else ""

        super().__init__(
            tone="session",
            title=title,
            content=content,
            meta_right=meta,
        )
