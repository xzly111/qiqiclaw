"""Small startup animation for the interactive QiQiClaw TUI."""

from __future__ import annotations

import os
import sys
import time


def should_show_startup_animation(config: dict | None = None) -> bool:
    """Return True when startup animation should run in this process."""
    display = (config or {}).get("display", {}) if isinstance(config, dict) else {}
    if not isinstance(display, dict):
        display = {}
    if not display.get("startup_animation", False):
        return False
    if os.environ.get("CI"):
        return False
    if os.environ.get("QIQICLAW_NO_ANIMATION"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def play_startup_animation(console, config: dict | None = None) -> None:
    """Render a short Reasonix-inspired startup sequence."""
    if not should_show_startup_animation(config):
        return

    display = (config or {}).get("display", {}) if isinstance(config, dict) else {}
    speed = str(display.get("startup_animation_speed", "fast")).strip().lower()
    delay = {"fast": 0.055, "normal": 0.085, "slow": 0.13}.get(speed, 0.055)

    try:
        from qiqiclaw_cli.skin_engine import get_active_skin

        skin = get_active_skin()
        brand = skin.get_color("banner_title", "#5CC8FF")
        dim = skin.get_color("banner_dim", "#7D8590")
        ok = skin.get_color("ui_ok", "#7EE787")
    except Exception:
        brand, dim, ok = "#5CC8FF", "#7D8590", "#7EE787"

    lines = [
        "",
        f"[bold {brand}]        QiQiClaw[/]",
        "",
        f"[{dim}]   ◈ scanning workspace[/]",
        f"[{dim}]   ◈ loading tools[/]",
        f"[{dim}]   ◈ restoring session[/]",
        f"[bold {ok}]   ◈ ready[/]",
        "",
    ]
    for line in lines:
        console.print(line)
        time.sleep(delay)


__all__ = ["play_startup_animation", "should_show_startup_animation"]
