"""Rendering helpers extracted from ``cli.py``."""

from __future__ import annotations

import re

from rich.text import Text as _RichText

_ACCENT_ANSI_DEFAULT = "\033[1;38;2;92;154;230m"


def _hex_to_ansi(hex_color: str, *, bold: bool = False) -> str:
    """Convert a hex color like '#268bd2' to a true-color ANSI escape."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        prefix = "1;" if bold else ""
        return f"\033[{prefix}38;2;{r};{g};{b}m"
    except (ValueError, IndexError):
        return _ACCENT_ANSI_DEFAULT if bold else "\033[38;2;125;133;144m"


class _SkinAwareAnsi:
    """Lazy ANSI escape that resolves from the skin engine on first use."""

    def __init__(self, skin_key: str, fallback_hex: str = "#5C9AE6", *, bold: bool = False):
        self._skin_key = skin_key
        self._fallback_hex = fallback_hex
        self._bold = bold
        self._cached: str | None = None

    def __str__(self) -> str:
        if self._cached is None:
            try:
                from qiqiclaw_cli.skin_engine import get_active_skin

                self._cached = _hex_to_ansi(
                    get_active_skin().get_color(self._skin_key, self._fallback_hex),
                    bold=self._bold,
                )
            except Exception:
                self._cached = _hex_to_ansi(self._fallback_hex, bold=self._bold)
        return self._cached

    def __add__(self, other: str) -> str:
        return str(self) + other

    def __radd__(self, other: str) -> str:
        return other + str(self)

    def reset(self) -> None:
        self._cached = None


def _accent_hex() -> str:
    """Return the active skin accent color for legacy CLI output lines."""
    try:
        from qiqiclaw_cli.skin_engine import get_active_skin

        return get_active_skin().get_color("ui_accent", "#5C9AE6")
    except Exception:
        return "#5C9AE6"


def _rich_text_from_ansi(text: str) -> _RichText:
    """Safely render assistant/tool output that may contain ANSI escapes."""
    return _RichText.from_ansi(text or "")


def _strip_markdown_syntax(text: str) -> str:
    """Best-effort markdown marker removal for plain-text display."""
    plain = _rich_text_from_ansi(text or "").plain
    plain = re.sub(r"^\s{0,3}(?:[-*_]\s*){3,}$", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"^\s{0,3}#{1,6}\s+", "", plain, flags=re.MULTILINE)
    plain = re.sub(r"(```+|~~~+)", "", plain)
    plain = re.sub(r"`([^`]*)`", r"\1", plain)
    plain = re.sub(r"!\[([^\]]*)\]\([^\)]*\)", r"\1", plain)
    plain = re.sub(r"\[([^\]]+)\]\([^\)]*\)", r"\1", plain)
    plain = re.sub(r"\*\*\*([^*]+)\*\*\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)___([^_]+)___(?!\w)", r"\1", plain)
    plain = re.sub(r"\*\*([^*]+)\*\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)__([^_]+)__(?!\w)", r"\1", plain)
    plain = re.sub(r"\*([^*]+)\*", r"\1", plain)
    plain = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", plain)
    plain = re.sub(r"~~([^~]+)~~", r"\1", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain.strip("\n")


def _render_final_assistant_content(text: str, mode: str = "render", semantic_highlight: bool = False):
    """Render final assistant content as markdown, stripped text, or raw text."""
    from rich.markdown import Markdown

    normalized_mode = str(mode or "render").strip().lower()
    if normalized_mode == "strip":
        plain = _strip_markdown_syntax(text)
        if semantic_highlight:
            from qiqiclaw_cli.semantic_highlight import highlight_markup

            return _RichText.from_markup(highlight_markup(plain))
        return _RichText(plain)
    if normalized_mode == "raw":
        return _rich_text_from_ansi(text or "")

    plain = _rich_text_from_ansi(text or "").plain
    return Markdown(plain)


__all__ = [
    "_SkinAwareAnsi",
    "_accent_hex",
    "_hex_to_ansi",
    "_render_final_assistant_content",
    "_rich_text_from_ansi",
    "_strip_markdown_syntax",
]
