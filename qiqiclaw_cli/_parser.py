"""
Top-level argparse construction for the QiQiClaw CLI.

Lives in its own module so other modules (e.g. ``relaunch.py``) can
introspect the parser to discover which flags exist without running the
``main`` fn.

Only the top-level parser and the ``chat`` subparser live here. Every other
subparser (model, gateway, sessions, …) is built inline in ``main.py``
because its dispatch is tightly coupled to module-level ``cmd_*`` functions.
"""

import argparse


# `--profile` / `-p` is consumed by ``main._apply_profile_override`` before
# argparse runs (it sets ``QIQICLAW_HOME`` and strips itself from ``sys.argv``),
# so it isn't on the parser. Listed here so all "carry over on relaunch"
# metadata lives in one file.
PRE_ARGPARSE_INHERITED_FLAGS: list[tuple[str, bool]] = [
    ("--profile", True),
    ("-p", True),
]


def _inherited_flag(parser, *args, **kwargs):
    """Register a flag that ``qiqiclaw_cli.relaunch`` should carry over when
    the CLI re-execs itself (e.g. after ``sessions browse`` picks a session,
    or after the setup wizard launches chat).

    Equivalent to ``parser.add_argument(...)`` plus tagging the resulting
    Action with ``inherit_on_relaunch = True`` so the relaunch table builder
    can find it via introspection.
    """
    action = parser.add_argument(*args, **kwargs)
    action.inherit_on_relaunch = True
    return action


_EPILOGUE = """
示例:
    qiqiclaw                        启动交互式聊天
    qiqiclaw chat -q "Hello"        单次查询模式
    qiqiclaw -c                     恢复最近的会话
    qiqiclaw -c "my project"        按名称恢复会话（最新的分支）
    qiqiclaw --resume <session_id>  按 ID 恢复特定会话
    qiqiclaw setup                  运行设置向导
    qiqiclaw logout                 清除已存储的身份验证
    qiqiclaw auth add <provider>    添加池化凭据
    qiqiclaw auth list              列出池化凭据
    qiqiclaw auth remove <p> <t>    按索引、ID 或标签删除池化凭据
    qiqiclaw auth reset <provider>  清除提供商的耗尽状态
    qiqiclaw model                  选择默认模型
    qiqiclaw fallback [list]        显示后备提供商链
    qiqiclaw fallback add           添加后备提供商（与 `qiqiclaw model` 相同的选择器）
    qiqiclaw fallback remove        从链中删除后备提供商
    qiqiclaw config                 查看配置
    qiqiclaw config edit            在 $EDITOR 中编辑配置
    qiqiclaw config set model gpt-4 设置配置值
    qiqiclaw gateway                运行消息网关
    qiqiclaw -s qiqiclaw-dev,github-auth
    qiqiclaw -w                     在隔离的 git 工作树中启动
    qiqiclaw gateway install        安装网关后台服务
    qiqiclaw sessions list          列出过去的会话
    qiqiclaw sessions browse        交互式会话选择器
    qiqiclaw sessions rename ID T   重命名/标题会话
    qiqiclaw langgraph --dry-run "规划一个任务"
                                  使用 LangGraph 编排 QiQiClaw 节点
    qiqiclaw logs                   查看 agent.log（最后 50 行）
    qiqiclaw logs -f                实时跟踪 agent.log
    qiqiclaw logs errors            查看 errors.log
    qiqiclaw logs --since 1h        最后一小时的日志行
    qiqiclaw debug share            上传调试报告以获取支持
    qiqiclaw update                 更新到最新版本

获取命令的更多帮助:
    qiqiclaw <command> --help
"""


def build_top_level_parser():
    """Build the top-level parser, the subparsers action, and the ``chat`` subparser.

    Returns ``(parser, subparsers, chat_parser)``. The caller wires
    ``chat_parser.set_defaults(func=cmd_chat)`` and continues registering
    other subparsers via ``subparsers.add_parser(...)``.
    """
    parser = argparse.ArgumentParser(
        prog="qiqiclaw",
        description="QiQiClaw - 具有工具调用能力的 AI 助手",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOGUE,
    )

    parser.add_argument(
        "--version", "-V", action="store_true", help="显示版本并退出"
    )
    parser.add_argument(
        "-z",
        "--oneshot",
        metavar="PROMPT",
        default=None,
        help=(
            "单次模式：发送单个提示并仅将最终响应文本打印到标准输出。"
            "无横幅、无旋转器、无工具预览、无 session_id 行。"
            "工具、内存、规则和 CWD 中的 AGENTS.md 正常加载；"
            "审批自动绕过。适用于脚本/管道。"
        ),
    )
    # --model / --provider are accepted at the top level so they can pair
    # with -z without needing the `chat` subcommand.  If neither -z nor a
    # subcommand consumes them, they fall through harmlessly as None.
    # Mirrors `qiqiclaw chat --model ... --provider ...` semantics.
    _inherited_flag(
        parser,
        "-m",
        "--model",
        default=None,
        help=(
            "此次调用的模型覆盖（例如 anthropic/claude-sonnet-4.6）。"
            "适用于 -z/--oneshot 和 --tui。也可通过 QIQICLAW_INFERENCE_MODEL 环境变量设置。"
        ),
    )
    _inherited_flag(
        parser,
        "--provider",
        default=None,
        help=(
            "此次调用的提供商覆盖（例如 openrouter、anthropic）。"
            "适用于 -z/--oneshot 和 --tui。也可通过 QIQICLAW_INFERENCE_PROVIDER 环境变量设置。"
        ),
    )
    parser.add_argument(
        "-t",
        "--toolsets",
        default=None,
        help="此次调用要启用的逗号分隔工具集。适用于 -z/--oneshot 和 --tui。",
    )
    parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION",
        default=None,
        help="按 ID 或标题恢复先前的会话",
    )
    parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=None,
        metavar="SESSION_NAME",
        help="按名称恢复会话，如果未提供名称则恢复最近的会话",
    )
    parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=False,
        help="在隔离的 git 工作树中运行（用于并行代理）",
    )
    parser.add_argument(
        "--new",
        "--no-picker",
        dest="no_session_picker",
        action="store_true",
        default=False,
        help="跳过会话选择器，直接开始新会话",
    )
    _inherited_flag(
        parser,
        "--accept-hooks",
        action="store_true",
        default=False,
        help=(
            "自动批准 config.yaml 中声明的任何未见过的 shell 钩子，"
            "无需 TTY 提示。等同于 QIQICLAW_ACCEPT_HOOKS=1 或 "
            "config.yaml 中的 hooks_auto_accept: true。用于无法提示的 CI/无头运行。"
        ),
    )
    _inherited_flag(
        parser,
        "--skills",
        "-s",
        action="append",
        default=None,
        help="为会话预加载一个或多个技能（重复标志或逗号分隔）",
    )
    _inherited_flag(
        parser,
        "--yolo",
        action="store_true",
        default=False,
        help="绕过所有危险命令批准提示（使用风险自负）",
    )
    _inherited_flag(
        parser,
        "--pass-session-id",
        action="store_true",
        default=False,
        help="在代理的系统提示中包含会话 ID",
    )
    _inherited_flag(
        parser,
        "--ignore-user-config",
        action="store_true",
        default=False,
        help="忽略 ~/.qiqiclaw/config.yaml 并回退到内置默认值（.env 中的凭据仍会加载）",
    )
    _inherited_flag(
        parser,
        "--ignore-rules",
        action="store_true",
        default=False,
        help="跳过 AGENTS.md、SOUL.md、.cursorrules、内存和预加载技能的自动注入",
    )
    _inherited_flag(
        parser,
        "--tui",
        action="store_true",
        default=False,
        help="启动现代 TUI 而不是经典 REPL",
    )
    _inherited_flag(
        parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=False,
        help="与 --tui 一起使用：通过 tsx 运行 TypeScript 源代码（跳过 dist 构建）",
    )

    subparsers = parser.add_subparsers(dest="command", help="要运行的命令")

    # =========================================================================
    # chat 命令
    # =========================================================================
    chat_parser = subparsers.add_parser(
        "chat",
        help="与代理进行交互式聊天",
        description="与 QiQiClaw 开始交互式聊天会话",
    )
    chat_parser.add_argument(
        "-q", "--query", help="单次查询（非交互模式）"
    )
    chat_parser.add_argument(
        "--image", help="附加到单次查询的可选本地图像路径"
    )
    _inherited_flag(
        chat_parser,
        "-m", "--model", help="要使用的模型（例如 anthropic/claude-sonnet-4）",
    )
    chat_parser.add_argument(
        "-t", "--toolsets", help="要启用的逗号分隔工具集"
    )
    _inherited_flag(
        chat_parser,
        "-s",
        "--skills",
        action="append",
        default=argparse.SUPPRESS,
        help="为会话预加载一个或多个技能（重复标志或逗号分隔）",
    )
    _inherited_flag(
        chat_parser,
        "--provider",
        # No `choices=` here: user-defined providers from config.yaml `providers:`
        # are also valid values, and runtime resolution (resolve_runtime_provider)
        # handles validation/error reporting consistently with the top-level
        # `--provider` flag.
        default=None,
        help="推理提供商（默认：自动）。内置或来自 config.yaml 中 `providers:` 的用户定义名称。",
    )
    chat_parser.add_argument(
        "-v", "--verbose", action="store_true", help="详细输出"
    )
    chat_parser.add_argument(
        "-Q",
        "--quiet",
        action="store_true",
        help="安静模式用于程序化使用：抑制横幅、旋转器和工具预览。仅输出最终响应和会话信息。",
    )
    chat_parser.add_argument(
        "--resume",
        "-r",
        metavar="SESSION_ID",
        default=argparse.SUPPRESS,
        help="按 ID 恢复之前的会话（退出时显示）",
    )
    chat_parser.add_argument(
        "--continue",
        "-c",
        dest="continue_last",
        nargs="?",
        const=True,
        default=argparse.SUPPRESS,
        metavar="SESSION_NAME",
        help="按名称恢复会话，如果未给出名称则恢复最近的会话",
    )
    chat_parser.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        default=argparse.SUPPRESS,
        help="在隔离的 git 工作树中运行（用于同一仓库上的并行代理）",
    )
    _inherited_flag(
        chat_parser,
        "--accept-hooks",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "自动批准 config.yaml 中声明的任何未见过的 shell 钩子，"
            "无需 TTY 提示（另请参阅 QIQICLAW_ACCEPT_HOOKS 环境变量和 "
            "config.yaml 中的 hooks_auto_accept:）。"
        ),
    )
    chat_parser.add_argument(
        "--checkpoints",
        action="store_true",
        default=False,
        help="在破坏性文件操作之前启用文件系统检查点（使用 /rollback 恢复）",
    )
    chat_parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help="每个对话轮次的最大工具调用迭代次数（默认：90，或 config 中的 agent.max_turns）",
    )
    _inherited_flag(
        chat_parser,
        "--yolo",
        action="store_true",
        default=argparse.SUPPRESS,
        help="绕过所有危险命令批准提示（使用风险自负）",
    )
    _inherited_flag(
        chat_parser,
        "--pass-session-id",
        action="store_true",
        default=argparse.SUPPRESS,
        help="在代理的系统提示中包含会话 ID",
    )
    _inherited_flag(
        chat_parser,
        "--ignore-user-config",
        action="store_true",
        default=argparse.SUPPRESS,
        help="忽略 ~/.qiqiclaw/config.yaml 并回退到内置默认值（.env 中的凭据仍会加载）。对于隔离的 CI 运行、重现和第三方集成很有用。",
    )
    _inherited_flag(
        chat_parser,
        "--ignore-rules",
        action="store_true",
        default=argparse.SUPPRESS,
        help="跳过 AGENTS.md、SOUL.md、.cursorrules、内存和预加载技能的自动注入。与 --ignore-user-config 结合使用可实现完全隔离运行。",
    )
    chat_parser.add_argument(
        "--source",
        default=None,
        help="用于过滤的会话源标签（默认：cli）。对于不应出现在用户会话列表中的第三方集成，使用 'tool'。",
    )
    _inherited_flag(
        chat_parser,
        "--tui",
        action="store_true",
        default=False,
        help="启动现代 TUI 而不是经典 REPL",
    )
    _inherited_flag(
        chat_parser,
        "--dev",
        dest="tui_dev",
        action="store_true",
        default=False,
        help="与 --tui 一起使用：通过 tsx 运行 TypeScript 源代码（跳过 dist 构建）",
    )

    return parser, subparsers, chat_parser
