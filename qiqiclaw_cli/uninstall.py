"""
QiQiClaw Uninstaller.

Provides options for:
- Full uninstall: Remove everything including configs and data
- Keep data: Remove code but keep ~/.qiqiclaw/ (configs, sessions, logs)
"""

import os
import shutil
import subprocess
from pathlib import Path

from qiqiclaw_constants import get_qiqiclaw_home, get_project_root

from qiqiclaw_cli.colors import Colors, color

def log_info(msg: str):
    print(f"{color('→', Colors.CYAN)} {msg}")

def log_success(msg: str):
    print(f"{color('✓', Colors.GREEN)} {msg}")

def log_warn(msg: str):
    print(f"{color('⚠', Colors.YELLOW)} {msg}")



def find_shell_configs() -> list:
    """Find shell configuration files that might have PATH entries."""
    home = Path.home()
    configs = []
    
    candidates = [
        home / ".bashrc",
        home / ".bash_profile",
        home / ".profile",
        home / ".zshrc",
        home / ".zprofile",
    ]
    
    for config in candidates:
        if config.exists():
            configs.append(config)
    
    return configs


def remove_path_from_shell_configs():
    """Remove QiQiClaw PATH entries from shell configuration files."""
    configs = find_shell_configs()
    removed_from = []
    
    for config_path in configs:
        try:
            content = config_path.read_text()
            original_content = content
            
            # Remove lines containing qiqiclaw or qiqiclaw PATH entries
            new_lines = []
            skip_next = False
            
            for line in content.split('\n'):
                # Skip the "# QiQiClaw" comment and following line
                if '# QiQiClaw' in line or '# qiqiclaw' in line:
                    skip_next = True
                    continue
                if skip_next and ('qiqiclaw' in line.lower() and 'PATH' in line):
                    skip_next = False
                    continue
                skip_next = False
                
                # Remove any PATH line containing qiqiclaw
                if 'qiqiclaw' in line.lower() and ('PATH=' in line or 'path=' in line.lower()):
                    continue
                    
                new_lines.append(line)
            
            new_content = '\n'.join(new_lines)
            
            # Clean up multiple blank lines
            while '\n\n\n' in new_content:
                new_content = new_content.replace('\n\n\n', '\n\n')
            
            if new_content != original_content:
                config_path.write_text(new_content)
                removed_from.append(config_path)
                
        except Exception as e:
            log_warn(f"无法更新 {config_path}: {e}")
    
    return removed_from


def remove_wrapper_script():
    """Remove the qiqiclaw wrapper script if it exists."""
    wrapper_paths = [
        Path.home() / ".local" / "bin" / "qiqiclaw",
        Path("/usr/local/bin/qiqiclaw"),
    ]
    
    removed = []
    for wrapper in wrapper_paths:
        if wrapper.exists():
            try:
                # Check if it's our wrapper (contains hermes_cli reference)
                content = wrapper.read_text()
                if 'qiqiclaw_cli' in content or 'qiqiclaw' in content:
                    wrapper.unlink()
                    removed.append(wrapper)
            except Exception as e:
                log_warn(f"无法删除 {wrapper}: {e}")
    
    return removed


def uninstall_gateway_service():
    """Stop and uninstall the gateway service (systemd, launchd) and kill any
    standalone gateway processes.

    Delegates to the gateway module which handles:
    - Linux: user + system systemd services (with proper DBUS env setup)
    - macOS: launchd plists
    - All platforms: standalone ``qiqiclaw gateway run`` processes
    - Termux/Android: skips systemd (no systemd on Android), still kills standalone processes
    """
    import platform
    stopped_something = False

    # 1. Kill any standalone gateway processes (all platforms, including Termux)
    try:
        from qiqiclaw_cli.gateway import kill_gateway_processes, find_gateway_pids
        pids = find_gateway_pids()
        if pids:
            killed = kill_gateway_processes()
            if killed:
                log_success(f"已终止 {killed} 个运行中的网关进程")
                stopped_something = True
    except Exception as e:
        log_warn(f"无法检查网关进程: {e}")

    system = platform.system()

    # Termux/Android has no systemd and no launchd — nothing left to do.
    prefix = os.getenv("PREFIX", "")
    is_termux = bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)
    if is_termux:
        return stopped_something

    # 2. Linux: uninstall systemd services (both user and system scopes)
    if system == "Linux":
        try:
            from qiqiclaw_cli.gateway import (
                get_systemd_unit_path,
                get_service_name,
                _systemctl_cmd,
            )
            svc_name = get_service_name()

            for is_system in (False, True):
                unit_path = get_systemd_unit_path(system=is_system)
                if not unit_path.exists():
                    continue

                scope = "system" if is_system else "user"
                try:
                    if is_system and os.geteuid() != 0:
                        log_warn(f"系统网关服务存在于 {unit_path} "
                                 f"但需要 sudo 权限才能删除")
                        continue

                    cmd = _systemctl_cmd(is_system)
                    subprocess.run(cmd + ["stop", svc_name],
                                   capture_output=True, check=False)
                    subprocess.run(cmd + ["disable", svc_name],
                                   capture_output=True, check=False)
                    unit_path.unlink()
                    subprocess.run(cmd + ["daemon-reload"],
                                   capture_output=True, check=False)
                    log_success(f"已删除 {scope} 网关服务 ({unit_path})")
                    stopped_something = True
                except Exception as e:
                    log_warn(f"无法删除 {scope} 网关服务: {e}")
        except Exception as e:
            log_warn(f"无法检查 systemd 网关服务: {e}")

    # 3. macOS: uninstall launchd plist
    elif system == "Darwin":
        try:
            from qiqiclaw_cli.gateway import get_launchd_plist_path
            plist_path = get_launchd_plist_path()
            if plist_path.exists():
                subprocess.run(["launchctl", "unload", str(plist_path)],
                               capture_output=True, check=False)
                plist_path.unlink()
                log_success(f"已删除 macOS 网关服务 ({plist_path})")
                stopped_something = True
        except Exception as e:
            log_warn(f"无法删除 launchd 网关服务: {e}")

    return stopped_something


def _is_default_qiqiclaw_home(qiqiclaw_home: Path) -> bool:
    """Return True when ``qiqiclaw_home`` points at the default (non-profile) root."""
    try:
        from qiqiclaw_constants import get_default_qiqiclaw_root
        return qiqiclaw_home.resolve() == get_default_qiqiclaw_root().resolve()
    except Exception:
        return False


def _discover_named_profiles():
    """Return a list of ``ProfileInfo`` for every non-default profile, or ``[]``
    if profile support is unavailable or nothing is installed beyond the
    default root."""
    try:
        from qiqiclaw_cli.profiles import list_profiles
    except Exception:
        return []
    try:
        return [p for p in list_profiles() if not getattr(p, "is_default", False)]
    except Exception as e:
        log_warn(f"无法枚举配置文件: {e}")
        return []


def _uninstall_profile(profile) -> None:
    """Fully uninstall a single named profile: stop its gateway service,
    remove its alias wrapper, and wipe its QIQICLAW_HOME directory.

    We shell out to ``qiqiclaw -p <name> gateway stop|uninstall`` because
    service names, unit paths, and plist paths are all derived from the
    current QIQICLAW_HOME and can't be easily switched in-process.
    """
    import sys as _sys
    name = profile.name
    profile_home = profile.path

    log_info(f"正在卸载配置文件 '{name}'...")

    # 1. Stop and remove this profile's gateway service.
    #    Use `python -m qiqiclaw_cli.main` so we don't depend on a `qiqiclaw`
    #    wrapper that may be half-removed mid-uninstall.
    qiqiclaw_invocation = [_sys.executable, "-m", "qiqiclaw_cli.main", "--profile", name]
    for subcmd in ("stop", "uninstall"):
        try:
            subprocess.run(
                qiqiclaw_invocation + ["gateway", subcmd],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log_warn(f"  配置文件 '{name}' 的网关 {subcmd} 操作超时")
        except Exception as e:
            log_warn(f"  无法为配置文件 '{name}' 运行网关 {subcmd} 操作: {e}")

    # 2. Remove the wrapper alias script at ~/.local/bin/<name> (if any).
    alias_path = getattr(profile, "alias_path", None)
    if alias_path and alias_path.exists():
        try:
            alias_path.unlink()
            log_success(f"  已删除别名 {alias_path}")
        except Exception as e:
            log_warn(f"  无法删除别名 {alias_path}: {e}")

    # 3. Wipe the profile's QIQICLAW_HOME directory.
    try:
        if profile_home.exists():
            shutil.rmtree(profile_home)
            log_success(f"  已删除 {profile_home}")
    except Exception as e:
        log_warn(f"  无法删除 {profile_home}: {e}")


def run_uninstall(args):
    """
    Run the uninstall process.
    
    Options:
    - Full uninstall: removes code + ~/.qiqiclaw/ (configs, data, logs)
    - Keep data: removes code but keeps ~/.qiqiclaw/ for future reinstall
    """
    project_root = get_project_root()
    qiqiclaw_home = get_qiqiclaw_home()

    # Detect named profiles when uninstalling from the default root —
    # offer to clean them up too instead of leaving zombie QIQICLAW_HOMEs
    # and systemd units behind.
    is_default_profile = _is_default_qiqiclaw_home(qiqiclaw_home)
    named_profiles = _discover_named_profiles() if is_default_profile else []

    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.MAGENTA, Colors.BOLD))
    print(color("│            Q QiQiClaw 卸载程序                     │", Colors.MAGENTA, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.MAGENTA, Colors.BOLD))
    print()

    # Show what will be affected
    print(color("当前安装：", Colors.CYAN, Colors.BOLD))
    print(f"  代码：    {project_root}")
    print(f"  配置：    {qiqiclaw_home / 'config.yaml'}")
    print(f"  密钥：    {qiqiclaw_home / '.env'}")
    print(f"  数据：    {qiqiclaw_home / 'cron/'}, {qiqiclaw_home / 'sessions/'}, {qiqiclaw_home / 'logs/'}")
    print()

    if named_profiles:
        print(color("检测到其他配置文件：", Colors.CYAN, Colors.BOLD))
        for p in named_profiles:
            running = " (网关运行中)" if getattr(p, "gateway_running", False) else ""
            print(f"  • {p.name}{running}: {p.path}")
        print()
    
    # Ask for confirmation
    print(color("卸载选项：", Colors.YELLOW, Colors.BOLD))
    print()
    print("  1) " + color("保留数据", Colors.GREEN) + " - 仅删除代码，保留配置/会话/日志")
    print("     (推荐 - 您可以稍后重新安装并保留您的设置)")
    print()
    print("  2) " + color("完全卸载", Colors.RED) + " - 删除所有内容，包括所有数据")
    print("     (警告：这将永久删除所有配置、会话和日志)")
    print()
    print("  3) " + color("取消", Colors.CYAN) + " - 不卸载")
    print()
    
    try:
        choice = input(color("选择选项 [1/2/3]: ", Colors.BOLD)).strip()
    except (KeyboardInterrupt, EOFError):
        print()
        print("已取消。")
        return

    if choice == "3" or choice.lower() in ("c", "cancel", "q", "quit", "n", "no"):
        print()
        print("卸载已取消。")
        return
    
    full_uninstall = (choice == "2")

    # When doing a full uninstall from the default profile, also offer to
    # remove any named profiles — stopping their gateway services, unlinking
    # their alias wrappers, and wiping their QIQICLAW_HOME dirs. Otherwise
    # those leave zombie services and data behind.
    remove_profiles = False
    if full_uninstall and named_profiles:
        print()
        print(color("默认情况下不会删除其他配置文件。", Colors.YELLOW))
        print(f"找到 {len(named_profiles)} 个命名配置文件: " +
              ", ".join(p.name for p in named_profiles))
        print()
        try:
            resp = input(color(
                f"是否也停止并删除这 {len(named_profiles)} 个配置文件? [y/N]: ",
                Colors.BOLD
            )).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            print("已取消。")
            return
        remove_profiles = resp in ("y", "yes")

    # Final confirmation
    print()
    if full_uninstall:
        print(color("⚠️  警告：这将永久删除所有 QiQiClaw 数据！", Colors.RED, Colors.BOLD))
        print(color("   包括：配置、API 密钥、会话、计划任务、日志", Colors.RED))
        if remove_profiles:
            print(color(
                f"   以及 {len(named_profiles)} 个配置文件: " +
                ", ".join(p.name for p in named_profiles),
                Colors.RED
            ))
    else:
        print("这将删除 QiQiClaw 代码，但保留您的配置和数据。")

    print()
    try:
        confirm = input(f"输入 '{color('yes', Colors.YELLOW)}' 以确认: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        print("已取消。")
        return

    if confirm != "yes":
        print()
        print("卸载已取消。")
        return
    
    print()
    print(color("正在卸载...", Colors.CYAN, Colors.BOLD))
    print()

    # 1. Stop and uninstall gateway service + kill standalone processes
    log_info("正在检查运行中的网关...")
    if not uninstall_gateway_service():
        log_info("未找到网关服务或进程")
    
    # 2. Remove PATH entries from shell configs
    log_info("正在从 shell 配置中删除 PATH 条目...")
    removed_configs = remove_path_from_shell_configs()
    if removed_configs:
        for config in removed_configs:
            log_success(f"已更新 {config}")
    else:
        log_info("未找到需要删除的 PATH 条目")
    
    # 3. Remove wrapper script
    log_info("正在删除 qiqiclaw 命令...")
    removed_wrappers = remove_wrapper_script()
    if removed_wrappers:
        for wrapper in removed_wrappers:
            log_success(f"已删除 {wrapper}")
    else:
        log_info("未找到包装脚本")
    
    # 4. Remove installation directory (code)
    log_info("正在删除安装目录...")

    # Check if we're running from within the install dir
    # We need to be careful here
    try:
        if project_root.exists():
            # If the install is inside ~/.qiqiclaw/, just remove the qiqiclaw subdir
            if qiqiclaw_home in project_root.parents or project_root.parent == qiqiclaw_home:
                shutil.rmtree(project_root)
                log_success(f"已删除 {project_root}")
            else:
                # Installation is somewhere else entirely
                shutil.rmtree(project_root)
                log_success(f"已删除 {project_root}")
    except Exception as e:
        log_warn(f"无法完全删除 {project_root}: {e}")
        log_info("您可能需要手动删除它")
    
    # 5. Optionally remove ~/.qiqiclaw/ data directory (and named profiles)
    if full_uninstall:
        # 5a. Stop and remove each named profile's gateway service and
        #     alias wrapper. The profile QIQICLAW_HOME dirs live under
        #     ``<default>/profiles/<name>/`` and will be swept away by the
        #     rmtree below, but services + alias scripts live OUTSIDE the
        #     default root and have to be cleaned up explicitly.
        if remove_profiles and named_profiles:
            for prof in named_profiles:
                _uninstall_profile(prof)

        log_info("正在删除配置和数据...")
        try:
            if qiqiclaw_home.exists():
                shutil.rmtree(qiqiclaw_home)
                log_success(f"已删除 {qiqiclaw_home}")
        except Exception as e:
            log_warn(f"无法完全删除 {qiqiclaw_home}: {e}")
            log_info("您可能需要手动删除它")
    else:
        log_info(f"保留配置和数据在 {qiqiclaw_home}")
    
    # Done
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.GREEN, Colors.BOLD))
    print(color("│              ✓ 卸载完成！                               │", Colors.GREEN, Colors.BOLD))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.GREEN, Colors.BOLD))
    print()

    if not full_uninstall:
        print(color("您的配置和数据已保留：", Colors.CYAN))
        print(f"  {qiqiclaw_home}/")
        print()
        print("稍后使用现有设置重新安装：")
        print(color("  curl -fsSL https://raw.githubusercontent.com/xzly111/qiqiclaw/main/scripts/install.sh | bash", Colors.DIM))
        print()

    print(color("重新加载您的 shell 以完成该过程：", Colors.YELLOW))
    print("  source ~/.bashrc  # 或 ~/.zshrc")
    print()
    print("感谢您使用 QiQiClaw！")
    print()
