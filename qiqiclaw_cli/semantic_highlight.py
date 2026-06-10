"""Display-only semantic highlighting for QiQiClaw CLI text.

The helpers in this module never mutate history/session data.  They only
return Rich-safe markup for terminal display.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from rich.markup import escape


@dataclass(frozen=True)
class SemanticPalette:
    path: str = "#7DD3FC"
    command: str = "#C084FC"
    slash: str = "#67E8F9"
    url: str = "#60A5FA"
    ok: str = "#7EE787"
    warn: str = "#F2CC60"
    err: str = "#FF7B72"


def palette_from_skin() -> SemanticPalette:
    """Build a semantic palette from the active skin, with Reasonix-like fallbacks."""
    try:
        from qiqiclaw_cli.skin_engine import get_active_skin

        skin = get_active_skin()
        return SemanticPalette(
            path=skin.get_color("semantic_path", "#7DD3FC"),
            command=skin.get_color("semantic_command", "#C084FC"),
            slash=skin.get_color("semantic_slash", "#67E8F9"),
            url=skin.get_color("semantic_url", "#60A5FA"),
            ok=skin.get_color("ui_ok", "#7EE787"),
            warn=skin.get_color("ui_warn", "#F2CC60"),
            err=skin.get_color("ui_error", "#FF7B72"),
        )
    except Exception:
        return SemanticPalette()


_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"']+")
_ABS_PATH_RE = re.compile(r"(?<![\w@])(?:~|/[\w .@%+=:,()#-]+)(?:/[\w .@%+=:,()#-]+)+(?:[:#]\d+)?")
_REL_PATH_RE = re.compile(
    r"(?<![\w/-])(?:\.{1,2}/)?(?:[\w.@%+=#-]+/)+[\w.@%+=#-]+(?:\.[A-Za-z0-9_+-]{1,12})?(?:[:#]\d+)?"
)
_SLASH_RE = re.compile(r"(?<!\S)/[A-Za-z][A-Za-z0-9_-]*(?:\b|(?=\s))")
_COMMAND_RE = re.compile(
    r"(?<![\w/-])(?:"
    r"git\s+(?:status|diff|log|show|add|commit|push|pull|checkout|switch|branch|stash|restore|reset|merge|rebase)"
    r"(?:\s+(?:-{1,2}[\w=-]+|[~./\w@%+=#-]*[/.][^\s,.;:]+)){0,4}"
    r"|npm\s+(?:run\s+[\w:-]+|install|test|start|build|dev)"
    r"|pnpm\s+(?:run\s+[\w:-]+|install|test|start|build|dev)"
    r"|yarn\s+(?:run\s+[\w:-]+|install|test|start|build|dev)"
    r"|python3?\s+[~./\w@%+=#-]*[/.][^\s,.;:]+(?:\s+-{1,2}[\w=-]+){0,3}"
    r"|pytest(?:\s+(?:-{1,2}[\w=-]+|[~./\w@%+=#-]*[/.][^\s,.;:]+)){0,4}"
    r"|ruff\s+(?:check|format)(?:\s+(?:-{1,2}[\w=-]+|[~./\w@%+=#-]*[/.][^\s,.;:]+)){0,4}"
    r"|docker(?:-compose)?\s+(?:build|run|compose|ps|logs|up|down)(?:\s+-{1,2}[\w=-]+){0,4}"
    r"|make(?:\s+[\w:-]+)?"
    r")"
)
_STATUS_RE = re.compile(
    r"\b(success|successful|succeeded|done|passed|ok|warning|warn|skipped|error|failed|failure|traceback)\b",
    re.IGNORECASE,
)


def _style_for_status(value: str, palette: SemanticPalette) -> str:
    lower = value.lower()
    if lower in {"success", "successful", "succeeded", "done", "passed", "ok"}:
        return palette.ok
    if lower in {"warning", "warn", "skipped"}:
        return palette.warn
    return palette.err


def _collect_matches(text: str, palette: SemanticPalette) -> list[tuple[int, int, str]]:
    matches: list[tuple[int, int, str]] = []

    for regex, style in (
        (_URL_RE, palette.url),
        (_ABS_PATH_RE, palette.path),
        (_REL_PATH_RE, palette.path),
        (_SLASH_RE, palette.slash),
        (_COMMAND_RE, palette.command),
    ):
        for match in regex.finditer(text):
            start, end = match.span()
            if start == end:
                continue
            matches.append((start, end, style))

    for match in _STATUS_RE.finditer(text):
        matches.append((match.start(), match.end(), _style_for_status(match.group(0), palette)))

    return _dedupe_overlaps(matches)


def _dedupe_overlaps(matches: Iterable[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    ordered = sorted(matches, key=lambda item: (item[0], -(item[1] - item[0])))
    result: list[tuple[int, int, str]] = []
    occupied_until = -1
    for start, end, style in ordered:
        if start < occupied_until:
            continue
        result.append((start, end, style))
        occupied_until = end
    return result


def highlight_markup(text: str, palette: SemanticPalette | None = None) -> str:
    """Return Rich markup with paths, commands, URLs, slash commands and states highlighted."""
    if not text:
        return ""
    palette = palette or palette_from_skin()
    matches = _collect_matches(text, palette)
    if not matches:
        return escape(text)

    out: list[str] = []
    pos = 0
    for start, end, style in matches:
        if start > pos:
            out.append(escape(text[pos:start]))
        out.append(f"[{style}]{escape(text[start:end])}[/]")
        pos = end
    if pos < len(text):
        out.append(escape(text[pos:]))
    return "".join(out)


__all__ = ["SemanticPalette", "highlight_markup", "palette_from_skin"]
