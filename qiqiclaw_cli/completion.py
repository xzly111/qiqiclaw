"""Shell completion script generation for QiQiClaw CLI.

Walks the live argparse parser tree to generate accurate, always-up-to-date
completion scripts — no hardcoded subcommand lists, no extra dependencies.

Supports bash, zsh, and fish.
"""

from __future__ import annotations

import argparse
from typing import Any


def _walk(parser: argparse.ArgumentParser) -> dict[str, Any]:
    """Recursively extract subcommands and flags from a parser.

    Uses _SubParsersAction._choices_actions to get canonical names (no aliases)
    along with their help text.
    """
    flags: list[str] = []
    subcommands: dict[str, Any] = {}

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            # _choices_actions has one entry per canonical name; aliases are
            # omitted, which keeps completion lists clean.
            seen: set[str] = set()
            for pseudo in action._choices_actions:
                name = pseudo.dest
                if name in seen:
                    continue
                seen.add(name)
                subparser = action.choices.get(name)
                if subparser is None:
                    continue
                info = _walk(subparser)
                info["help"] = _clean(pseudo.help or "")
                subcommands[name] = info
        elif action.option_strings:
            flags.extend(o for o in action.option_strings if o.startswith("-"))

    return {"flags": flags, "subcommands": subcommands}


def _clean(text: str, maxlen: int = 60) -> str:
    """Strip shell-unsafe characters and truncate."""
    return text.replace("'", "").replace('"', "").replace("\\", "")[:maxlen]


# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------

def generate_bash(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = " ".join(sorted(tree["subcommands"]))

    cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if cmd == "profile" and info["subcommands"]:
            # Profile subcommand: complete actions, then profile names for
            # actions that accept a profile argument.
            subcmds = " ".join(sorted(info["subcommands"]))
            profile_actions = "use delete show alias rename export"
            cases.append(
                f"        profile)\n"
                f"            case \"$prev\" in\n"
                f"                profile)\n"
                f"                    COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"                {profile_actions.replace(' ', '|')})\n"
                f"                    COMPREPLY=($(compgen -W \"$(_hermes_profiles)\" -- \"$cur\"))\n"
                f"                    return\n"
                f"                    ;;\n"
                f"            esac\n"
                f"            ;;"
            )
        elif info["subcommands"]:
            subcmds = " ".join(sorted(info["subcommands"]))
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{subcmds}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )
        elif info["flags"]:
            flags = " ".join(info["flags"])
            cases.append(
                f"        {cmd})\n"
                f"            COMPREPLY=($(compgen -W \"{flags}\" -- \"$cur\"))\n"
                f"            return\n"
                f"            ;;"
            )

    cases_str = "\n".join(cases)

    return f"""# QiQiClaw bash 自动补全
# 添加到 ~/.bashrc:
#   eval "$(qiqiclaw completion bash)"

_hermes_profiles() {{
    local profiles_dir="$HOME/.qiqiclaw/profiles"
    local profiles="default"
    if [ -d "$profiles_dir" ]; then
        profiles="$profiles $(ls "$profiles_dir" 2>/dev/null)"
    fi
    echo "$profiles"
}}

_hermes_completion() {{
    local cur prev
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"

    # Complete profile names after -p / --profile
    if [[ "$prev" == "-p" || "$prev" == "--profile" ]]; then
        COMPREPLY=($(compgen -W "$(_hermes_profiles)" -- "$cur"))
        return
    fi

    if [[ $COMP_CWORD -ge 2 ]]; then
        case "${{COMP_WORDS[1]}}" in
{cases_str}
        esac
    fi

    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{top_cmds}" -- "$cur"))
    fi
}}

complete -F _hermes_completion qiqiclaw
"""


# ---------------------------------------------------------------------------
# Zsh
# ---------------------------------------------------------------------------

def generate_zsh(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)

    top_cmds_lines: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        help_text = _clean(tree["subcommands"][cmd].get("help", ""))
        top_cmds_lines.append(f"                '{cmd}:{help_text}'")
    top_cmds_str = "\n".join(top_cmds_lines)

    sub_cases: list[str] = []
    for cmd in sorted(tree["subcommands"]):
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        if cmd == "profile":
            # Profile subcommand: complete actions, then profile names for
            # actions that accept a profile argument.
            sub_lines: list[str] = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                        '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            sub_cases.append(
                f"                profile)\n"
                f"                    case ${{line[2]}} in\n"
                f"                        use|delete|show|alias|rename|export)\n"
                f"                            _hermes_profiles\n"
                f"                            ;;\n"
                f"                        *)\n"
                f"                            local -a profile_cmds\n"
                f"                            profile_cmds=(\n"
                f"{sub_str}\n"
                f"                            )\n"
                f"                            _describe 'profile command' profile_cmds\n"
                f"                            ;;\n"
                f"                    esac\n"
                f"                    ;;"
            )
        else:
            sub_lines = []
            for sc in sorted(info["subcommands"]):
                sh = _clean(info["subcommands"][sc].get("help", ""))
                sub_lines.append(f"                    '{sc}:{sh}'")
            sub_str = "\n".join(sub_lines)
            safe = cmd.replace("-", "_")
            sub_cases.append(
                f"                {cmd})\n"
                f"                    local -a {safe}_cmds\n"
                f"                    {safe}_cmds=(\n"
                f"{sub_str}\n"
                f"                    )\n"
                f"                    _describe '{cmd} command' {safe}_cmds\n"
                f"                    ;;"
            )
    sub_cases_str = "\n".join(sub_cases)

    return f"""#compdef qiqiclaw
# QiQiClaw zsh 自动补全
# 添加到 ~/.zshrc:
#   eval "$(qiqiclaw completion zsh)"

_hermes_profiles() {{
    local -a profiles
    profiles=(default)
    if [[ -d "$HOME/.qiqiclaw/profiles" ]]; then
        profiles+=("${{(@f)$(ls $HOME/.qiqiclaw/profiles 2>/dev/null)}}")
    fi
    _describe 'profile' profiles
}}

_hermes() {{
    local context state line
    typeset -A opt_args

    _arguments -C \\
        '(-h --help){{-h,--help}}[Show help and exit]' \\
        '(-V --version){{-V,--version}}[Show version and exit]' \\
        '(-p --profile){{-p,--profile}}[Profile name]:profile:_hermes_profiles' \\
        '1:command:->commands' \\
        '*::arg:->args'

    case $state in
        commands)
            local -a subcmds
            subcmds=(
{top_cmds_str}
            )
            _describe 'qiqiclaw command' subcmds
            ;;
        args)
            case ${{line[1]}} in
{sub_cases_str}
            esac
            ;;
    esac
}}

_hermes "$@"
"""


# ---------------------------------------------------------------------------
# Fish
# ---------------------------------------------------------------------------

def generate_fish(parser: argparse.ArgumentParser) -> str:
    tree = _walk(parser)
    top_cmds = sorted(tree["subcommands"])
    top_cmds_str = " ".join(top_cmds)

    lines: list[str] = [
        "# QiQiClaw fish 自动补全",
        "# 添加到您的配置:",
        "#   qiqiclaw completion fish | source",
        "",
        "# 辅助函数: 列出可用的配置文件",
        "function __hermes_profiles",
        "    echo default",
        "    if test -d $HOME/.qiqiclaw/profiles",
        "        ls $HOME/.qiqiclaw/profiles 2>/dev/null",
        "    end",
        "end",
        "",
        "# 默认禁用文件补全",
        "complete -c qiqiclaw -f",
        "",
        "# 在 -p / --profile 后补全配置文件名",
        "complete -c qiqiclaw -f -s p -l profile"
        " -d '配置文件名' -xa '(__hermes_profiles)'",
        "",
        "# 顶层子命令",
    ]

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        help_text = _clean(info.get("help", ""))
        lines.append(
            f"complete -c qiqiclaw -f "
            f"-n 'not __fish_seen_subcommand_from {top_cmds_str}' "
            f"-a {cmd} -d '{help_text}'"
        )

    lines.append("")
    lines.append("# 子命令补全")

    profile_name_actions = {"use", "delete", "show", "alias", "rename", "export"}

    for cmd in top_cmds:
        info = tree["subcommands"][cmd]
        if not info["subcommands"]:
            continue
        lines.append(f"# {cmd}")
        for sc in sorted(info["subcommands"]):
            sinfo = info["subcommands"][sc]
            sh = _clean(sinfo.get("help", ""))
            lines.append(
                f"complete -c qiqiclaw -f "
                f"-n '__fish_seen_subcommand_from {cmd}' "
                f"-a {sc} -d '{sh}'"
            )
        # For profile subcommand, complete profile names for relevant actions
        if cmd == "profile":
            for action in sorted(profile_name_actions):
                lines.append(
                    f"complete -c qiqiclaw -f "
                    f"-n '__fish_seen_subcommand_from {action}; "
                    f"and __fish_seen_subcommand_from profile' "
                    f"-a '(__hermes_profiles)' -d '配置文件名'"
                )

    lines.append("")
    return "\n".join(lines)
