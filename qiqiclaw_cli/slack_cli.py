"""``qiqiclaw slack ...`` CLI subcommands.

Today only ``qiqiclaw slack manifest`` is implemented — it generates the
Slack app manifest JSON for registering every gateway command as a native
Slack slash (``/btw``, ``/stop``, ``/model``, …) so users get the same
first-class slash UX Discord and Telegram already have.

Typical workflow::

    $ qiqiclaw slack manifest > slack-manifest.json
    # or:
    $ qiqiclaw slack manifest --write

Then paste the printed JSON into the Slack app config (Features → App
Manifest → Edit) and click Save. Slack diffs the manifest and prompts
for reinstall when scopes/commands change.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _build_full_manifest(bot_name: str, bot_description: str) -> dict:
    """Build a full Slack manifest merging display info + our slash list.

    The slash-command list is always generated from ``COMMAND_REGISTRY`` so
    it stays in sync with the rest of QiQiClaw. Other manifest sections
    (display info, OAuth scopes, socket mode) are set to sensible defaults
    for a QiQiClaw deployment — users can tweak them in the Slack UI after
    pasting.
    """
    from qiqiclaw_cli.commands import slack_app_manifest

    partial = slack_app_manifest()
    slashes = partial["features"]["slash_commands"]

    return {
        "_metadata": {
            "major_version": 1,
            "minor_version": 1,
        },
        "display_information": {
            "name": bot_name[:35],
            "description": (bot_description or "您在 Slack 上的 QiQiClaw 代理")[:140],
            "background_color": "#1a1a2e",
        },
        "features": {
            "bot_user": {
                "display_name": bot_name[:80],
                "always_online": True,
            },
            "slash_commands": slashes,
            "assistant_view": {
                "assistant_description": "在话题和私信中与 QiQiClaw 聊天。",
            },
        },
        "oauth_config": {
            "scopes": {
                "bot": [
                    "app_mentions:read",
                    "assistant:write",
                    "channels:history",
                    "channels:read",
                    "chat:write",
                    "commands",
                    "files:read",
                    "files:write",
                    "groups:history",
                    "im:history",
                    "im:read",
                    "im:write",
                    "users:read",
                ],
            },
        },
        "settings": {
            "event_subscriptions": {
                "bot_events": [
                    "app_mention",
                    "assistant_thread_context_changed",
                    "assistant_thread_started",
                    "message.channels",
                    "message.groups",
                    "message.im",
                ],
            },
            "interactivity": {
                "is_enabled": True,
            },
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def slack_manifest_command(args) -> int:
    """Print or write a Slack app manifest JSON.

    Flags (all parsed in ``qiqiclaw_cli/main.py``):
      --write [PATH]  Write to file instead of stdout (default path:
                      ``$QIQICLAW_HOME/slack-manifest.json``)
      --name NAME     Override the bot display name (default: "QiQiClaw")
      --description DESC  Override the bot description
      --slashes-only  Emit only the ``features.slash_commands`` array (for
                      merging into an existing manifest manually)
    """
    name = getattr(args, "name", None) or "QiQiClaw"
    description = getattr(args, "description", None) or "您在 Slack 上的 QiQiClaw 代理"

    if getattr(args, "slashes_only", False):
        from qiqiclaw_cli.commands import slack_app_manifest

        manifest = slack_app_manifest()["features"]["slash_commands"]
    else:
        manifest = _build_full_manifest(name, description)

    payload = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"

    write_target = getattr(args, "write", None)
    if write_target is not None:
        if isinstance(write_target, bool) and write_target:
            # --write with no value → default location
            try:
                from qiqiclaw_constants import get_qiqiclaw_home

                target = Path(get_qiqiclaw_home()) / "slack-manifest.json"
            except Exception:
                target = Path(os.environ.get("QIQICLAW_HOME") or str(Path.home() / ".qiqiclaw")) / "slack-manifest.json"
        else:
            target = Path(write_target).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(payload, encoding="utf-8")
        print(f"Slack 清单已写入: {target}", file=sys.stderr)
        print(
            "\n后续步骤:\n"
            "  1. 打开 https://api.slack.com/apps 并选择您的 QiQiClaw 应用\n"
            "     (或创建新应用: Create New App → From an app manifest)。\n"
            f"  2. Features → App Manifest → 粘贴以下文件的内容\n"
            f"     {target}\n"
            "  3. 保存; 如果作用域或斜杠命令发生变化，Slack 会提示重新安装应用。\n"
            "  4. 确保已启用 Socket Mode，并且已通过 `qiqiclaw setup` 配置了\n"
            "     机器人令牌 (xoxb-...) 和应用令牌 (xapp-...)。\n",
            file=sys.stderr,
        )
    else:
        sys.stdout.write(payload)
    return 0
