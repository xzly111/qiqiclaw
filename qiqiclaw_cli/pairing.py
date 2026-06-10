"""
CLI commands for the DM pairing system.

Usage:
    qiqiclaw pairing list              # Show all pending + approved users
    qiqiclaw pairing approve <platform> <code>  # Approve a pairing code
    qiqiclaw pairing revoke <platform> <user_id> # Revoke user access
    qiqiclaw pairing clear-pending     # Clear all expired/pending codes
"""

def pairing_command(args):
    """Handle qiqiclaw pairing subcommands."""
    from gateway.pairing import PairingStore

    store = PairingStore()
    action = getattr(args, "pairing_action", None)

    if action == "list":
        _cmd_list(store)
    elif action == "approve":
        _cmd_approve(store, args.platform, args.code)
    elif action == "revoke":
        _cmd_revoke(store, args.platform, args.user_id)
    elif action == "clear-pending":
        _cmd_clear_pending(store)
    else:
        print("用法: qiqiclaw pairing {list|approve|revoke|clear-pending}")
        print("运行 'qiqiclaw pairing --help' 查看详细信息。")


def _cmd_list(store):
    """List all pending and approved users."""
    pending = store.list_pending()
    approved = store.list_approved()

    if not pending and not approved:
        print("未找到配对数据。还没有人尝试配对~")
        return

    if pending:
        print(f"\n  待处理的配对请求 ({len(pending)}):")
        print(f"  {'平台':<12} {'代码':<10} {'用户ID':<20} {'名称':<20} {'时间'}")
        print(f"  {'----':<12} {'----':<10} {'------':<20} {'----':<20} {'----'}")
        for p in pending:
            print(
                f"  {p['platform']:<12} {p['code']:<10} {p['user_id']:<20} "
                f"{(p.get('user_name') or ''):<20} {p['age_minutes']}分钟前"
            )
    else:
        print("\n  没有待处理的配对请求。")

    if approved:
        print(f"\n  已批准的用户 ({len(approved)}):")
        print(f"  {'平台':<12} {'用户ID':<20} {'名称':<20}")
        print(f"  {'----':<12} {'------':<20} {'----':<20}")
        for a in approved:
            print(f"  {a['platform']:<12} {a['user_id']:<20} {(a.get('user_name') or ''):<20}")
    else:
        print("\n  没有已批准的用户。")

    print()


def _cmd_approve(store, platform: str, code: str):
    """Approve a pairing code."""
    platform = platform.lower().strip()
    code = code.upper().strip()

    result = store.approve_code(platform, code)
    if result:
        uid = result["user_id"]
        name = result.get("user_name") or ""
        display = f"{name} ({uid})" if name else uid
        print(f"\n  已批准! 用户 {display} 在 {platform} 平台现在可以使用机器人了~")
        print("  他们在下次发送消息时将自动被识别。\n")
    else:
        print(f"\n  在平台 '{platform}' 上未找到代码 '{code}' 或已过期。")
        print("  运行 'qiqiclaw pairing list' 查看待处理的代码。\n")


def _cmd_revoke(store, platform: str, user_id: str):
    """Revoke a user's access."""
    platform = platform.lower().strip()

    if store.revoke(platform, user_id):
        print(f"\n  已撤销用户 {user_id} 在 {platform} 平台的访问权限。\n")
    else:
        print(f"\n  在 {platform} 平台的已批准列表中未找到用户 {user_id}。\n")


def _cmd_clear_pending(store):
    """Clear all pending pairing codes."""
    count = store.clear_pending()
    if count:
        print(f"\n  已清除 {count} 个待处理的配对请求。\n")
    else:
        print("\n  没有待处理的请求需要清除。\n")
