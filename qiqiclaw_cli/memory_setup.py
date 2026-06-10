"""qiqiclaw memory setup|status — configure memory provider plugins.

Auto-detects installed memory providers via the plugin system.
Interactive curses-based UI for provider selection, then walks through
the provider's config schema. Writes config to config.yaml + .env.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path

from qiqiclaw_constants import get_qiqiclaw_home


# ---------------------------------------------------------------------------
# Curses-based interactive picker (same pattern as qiqiclaw tools)
# ---------------------------------------------------------------------------

def _curses_select(title: str, items: list[tuple[str, str]], default: int = 0) -> int:
    """Interactive single-select with arrow keys.

    items: list of (label, description) tuples.
    Returns selected index, or default on escape/quit.
    """
    from qiqiclaw_cli.curses_ui import curses_radiolist
    # Format (label, desc) tuples into display strings
    display_items = [
        f"{label}  {desc}" if desc else label
        for label, desc in items
    ]
    return curses_radiolist(title, display_items, selected=default, cancel_returns=default)


def _prompt(label: str, default: str | None = None, secret: bool = False) -> str:
    """Prompt for a value with optional default and secret masking."""
    suffix = f" [{default}]" if default else ""
    if secret:
        # Visible input — supports paste and shows typed characters
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        if sys.stdin.isatty():
            val = input("")
        else:
            val = sys.stdin.readline().strip()
    else:
        sys.stdout.write(f"  {label}{suffix}: ")
        sys.stdout.flush()
        val = sys.stdin.readline().strip()
    return val or (default or "")


# ---------------------------------------------------------------------------
# Provider discovery
# ---------------------------------------------------------------------------

def _install_dependencies(provider_name: str) -> None:
    """Install pip dependencies declared in plugin.yaml."""
    import subprocess
    from plugins.memory import find_provider_dir

    plugin_dir = find_provider_dir(provider_name)
    if not plugin_dir:
        return
    yaml_path = plugin_dir / "plugin.yaml"
    if not yaml_path.exists():
        return

    try:
        import yaml
        with open(yaml_path) as f:
            meta = yaml.safe_load(f) or {}
    except Exception:
        return

    pip_deps = meta.get("pip_dependencies", [])
    if not pip_deps:
        return

    # pip name → import name mapping for packages where they differ
    _IMPORT_NAMES = {
        "honcho-ai": "honcho",
        "mem0ai": "mem0",
        "hindsight-client": "hindsight_client",
        "hindsight-all": "hindsight",
    }

    # Check which packages are missing
    missing = []
    for dep in pip_deps:
        import_name = _IMPORT_NAMES.get(dep, dep.replace("-", "_").split("[")[0])
        try:
            __import__(import_name)
        except ImportError:
            missing.append(dep)

    if not missing:
        return

    print(f"\n  正在安装依赖项: {', '.join(missing)}")

    import shutil
    uv_path = shutil.which("uv")
    if not uv_path:
        print(f"  ⚠ 未找到 uv — 无法安装依赖项")
        print(f"  安装 uv: curl -LsSf https://astral.sh/uv/install.sh | sh")
        print(f"  然后重新运行: qiqiclaw memory setup")
        return

    try:
        subprocess.run(
            [uv_path, "pip", "install", "--python", sys.executable, "--quiet"] + missing,
            check=True, timeout=120,
            capture_output=True,
        )
        print(f"  ✓ 已安装 {', '.join(missing)}")
    except subprocess.CalledProcessError as e:
        print(f"  ⚠ 安装失败 {', '.join(missing)}")
        stderr = (e.stderr or b"").decode()[:200]
        if stderr:
            print(f"    {stderr}")
        print(f"  手动运行: uv pip install --python {sys.executable} {' '.join(missing)}")
    except Exception as e:
        print(f"  ⚠ 安装失败: {e}")
        print(f"  手动运行: uv pip install --python {sys.executable} {' '.join(missing)}")

    # Also show external dependencies (non-pip) if any
    ext_deps = meta.get("external_dependencies", [])
    for dep in ext_deps:
        dep_name = dep.get("name", "")
        check_cmd = dep.get("check", "")
        install_cmd = dep.get("install", "")
        if check_cmd:
            try:
                subprocess.run(
                    check_cmd, shell=True, capture_output=True, timeout=5
                )
            except Exception:
                if install_cmd:
                    print(f"\n  ⚠ 未找到 '{dep_name}'。安装方式:")
                    print(f"    {install_cmd}")


def _get_available_providers() -> list:
    """Discover memory providers from plugins/memory/.

    Returns list of (name, description, provider_instance) tuples.
    """
    try:
        from plugins.memory import discover_memory_providers, load_memory_provider
        raw = discover_memory_providers()
    except Exception:
        raw = []

    results = []
    for name, desc, available in raw:
        try:
            provider = load_memory_provider(name)
            if not provider:
                continue
        except Exception:
            continue

        schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
        has_secrets = any(f.get("secret") for f in schema)
        has_non_secrets = any(not f.get("secret") for f in schema)
        if has_secrets and has_non_secrets:
            setup_hint = "API key / local"
        elif has_secrets:
            setup_hint = "requires API key"
        elif not schema:
            setup_hint = "no setup needed"
        else:
            setup_hint = "local"

        results.append((name, setup_hint, provider))
    return results


def _memory_provider_schema(name: str, setup_hint: str, provider) -> dict:
    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []
    fields = []
    for field in schema:
        fields.append({
            "key": field.get("key", ""),
            "description": field.get("description") or field.get("key", ""),
            "secret": bool(field.get("secret", False)),
            "env_var": field.get("env_var", ""),
            "url": field.get("url", ""),
            "choices": list(field.get("choices") or []),
            "default": field.get("default"),
            "when": field.get("when") or None,
        })
    try:
        available = bool(provider.is_available())
    except Exception:
        available = False
    return {
        "name": name,
        "setup_hint": setup_hint,
        "available": available,
        "fields": fields,
        "has_post_setup": hasattr(provider, "post_setup"),
    }


def memory_status_data() -> dict:
    """Return structured built-in and external memory provider status."""
    from qiqiclaw_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {})
    if not isinstance(mem_config, dict):
        mem_config = {}
    provider_name = str(mem_config.get("provider") or "")
    mem_dir = get_qiqiclaw_home() / "memories"

    providers = [
        _memory_provider_schema(name, setup_hint, provider)
        for name, setup_hint, provider in _get_available_providers()
    ]
    provider_names = {p["name"] for p in providers}

    return {
        "version": 1,
        "home": str(get_qiqiclaw_home()),
        "memory": {
            "memory_enabled": bool(mem_config.get("memory_enabled", True)),
            "user_profile_enabled": bool(mem_config.get("user_profile_enabled", True)),
            "memory_char_limit": int(mem_config.get("memory_char_limit", 2200) or 2200),
            "user_char_limit": int(mem_config.get("user_char_limit", 1375) or 1375),
        },
        "active_provider": provider_name,
        "active_provider_known": (not provider_name) or provider_name in provider_names,
        "providers": providers,
        "builtin_files": {
            "memory": (mem_dir / "MEMORY.md").stat().st_size
            if (mem_dir / "MEMORY.md").exists()
            else 0,
            "user": (mem_dir / "USER.md").stat().st_size
            if (mem_dir / "USER.md").exists()
            else 0,
        },
    }


def print_memory_status_json() -> None:
    print(json.dumps(memory_status_data(), indent=2, ensure_ascii=False))


def set_memory_provider(provider_name: str) -> dict:
    """Set the active external memory provider, validating known providers."""
    from qiqiclaw_cli.config import load_config, save_config

    provider = (provider_name or "").strip()
    if provider.lower() in {"built-in", "builtin", "none", "off"}:
        provider = ""
    if provider:
        valid = {name for name, _, _ in _get_available_providers()}
        if provider not in valid:
            raise ValueError(
                f"Unknown memory provider '{provider}'. Run `qiqiclaw memory setup` to configure a new one."
            )

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}
    config["memory"]["provider"] = provider
    save_config(config)
    return {"success": True, "active_provider": provider}


def print_set_memory_provider_json(provider_name: str) -> None:
    print(json.dumps(set_memory_provider(provider_name), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

def cmd_setup_provider(provider_name: str) -> None:
    """Run memory setup for a specific provider, skipping the picker."""
    from qiqiclaw_cli.config import load_config, save_config

    providers = _get_available_providers()
    match = None
    for name, desc, provider in providers:
        if name == provider_name:
            match = (name, desc, provider)
            break

    if not match:
        print(f"\n  未找到内存提供程序 '{provider_name}'。")
        print("  运行 'qiqiclaw memory setup' 查看可用的提供程序。\n")
        return

    name, _, provider = match

    _install_dependencies(name)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    if hasattr(provider, "post_setup"):
        qiqiclaw_home = str(get_qiqiclaw_home())
        provider.post_setup(qiqiclaw_home, config)
        return

    # Fallback: generic schema-based setup (same as cmd_setup)
    config["memory"]["provider"] = name
    save_config(config)
    print(f"\n  内存提供程序: {name}")
    print(f"  激活配置已保存到 config.yaml\n")


def cmd_setup(args) -> None:
    """Interactive memory provider setup wizard."""
    from qiqiclaw_cli.config import load_config, save_config

    providers = _get_available_providers()

    if not providers:
        print("\n  未检测到内存提供程序插件。")
        print("  请安装插件到 ~/.qiqiclaw/plugins/ 然后重试。\n")
        return

    # Build picker items
    items = []
    for name, desc, _ in providers:
        items.append((name, f"— {desc}"))
    items.append(("仅内置", "— MEMORY.md / USER.md (默认)"))

    builtin_idx = len(items) - 1
    selected = _curses_select("内存提供程序设置", items, default=builtin_idx)

    config = load_config()
    if not isinstance(config.get("memory"), dict):
        config["memory"] = {}

    # Built-in only
    if selected >= len(providers) or selected < 0:
        config["memory"]["provider"] = ""
        save_config(config)
        print("\n  ✓ 内存提供程序: 仅内置")
        print("  已保存到 config.yaml\n")
        return

    name, _, provider = providers[selected]

    # Install pip dependencies if declared in plugin.yaml
    _install_dependencies(name)

    # If the provider has a post_setup hook, delegate entirely to it.
    # The hook handles its own config, connection test, and activation.
    if hasattr(provider, "post_setup"):
        qiqiclaw_home = str(get_qiqiclaw_home())
        provider.post_setup(qiqiclaw_home, config)
        return

    schema = provider.get_config_schema() if hasattr(provider, "get_config_schema") else []

    provider_config = config["memory"].get(name, {})
    if not isinstance(provider_config, dict):
        provider_config = {}

    env_path = get_qiqiclaw_home() / ".env"
    env_writes = {}

    if schema:
        print(f"\n  正在配置 {name}:\n")

        for field in schema:
            key = field["key"]
            desc = field.get("description", key)
            default = field.get("default")
            # Dynamic default: look up default from another field's value
            default_from = field.get("default_from")
            if default_from and isinstance(default_from, dict):
                ref_field = default_from.get("field", "")
                ref_map = default_from.get("map", {})
                ref_value = provider_config.get(ref_field, "")
                if ref_value and ref_value in ref_map:
                    default = ref_map[ref_value]
            is_secret = field.get("secret", False)
            choices = field.get("choices")
            env_var = field.get("env_var")
            url = field.get("url")

            # Skip fields whose "when" condition doesn't match
            when = field.get("when")
            if when and isinstance(when, dict):
                if not all(provider_config.get(k) == v for k, v in when.items()):
                    continue

            if choices and not is_secret:
                # Use curses picker for choice fields
                choice_items = [(c, "") for c in choices]
                current = provider_config.get(key, default)
                current_idx = 0
                if current and current in choices:
                    current_idx = choices.index(current)
                sel = _curses_select(f"  {desc}", choice_items, default=current_idx)
                provider_config[key] = choices[sel]
            elif is_secret:
                # Prompt for secret
                existing = os.environ.get(env_var, "") if env_var else ""
                if existing:
                    masked = f"...{existing[-4:]}" if len(existing) > 4 else "已设置"
                    val = _prompt(f"{desc} (当前: {masked}, 留空保持不变)", secret=True)
                else:
                    hint = f"  在此获取: {url}" if url else ""
                    if hint:
                        print(hint)
                    val = _prompt(desc, secret=True)
                if val and env_var:
                    env_writes[env_var] = val
            else:
                # Regular text prompt
                current = provider_config.get(key)
                effective_default = current or default
                val = _prompt(desc, default=str(effective_default) if effective_default else None)
                if val:
                    provider_config[key] = val
                    # Also write to .env if this field has an env_var
                    if env_var and env_var not in env_writes:
                        env_writes[env_var] = val

    # Write activation key to config.yaml
    config["memory"]["provider"] = name
    save_config(config)

    # Write non-secret config to provider's native location
    qiqiclaw_home = str(get_qiqiclaw_home())
    if provider_config and hasattr(provider, "save_config"):
        try:
            provider.save_config(provider_config, qiqiclaw_home)
        except Exception as e:
            print(f"  写入提供程序配置失败: {e}")

    # Write secrets to .env
    if env_writes:
        _write_env_vars(env_path, env_writes)

    print(f"\n  内存提供程序: {name}")
    print(f"  激活配置已保存到 config.yaml")
    if provider_config:
        print(f"  提供程序配置已保存")
    if env_writes:
        print(f"  API 密钥已保存到 .env")
    print(f"\n  启动新会话以激活。\n")


def _write_env_vars(env_path: Path, env_writes: dict) -> None:
    """Append or update env vars in .env file."""
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing_lines = []
    if env_path.exists():
        existing_lines = env_path.read_text().splitlines()

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        key_match = line.split("=", 1)[0].strip() if "=" in line else ""
        if key_match in env_writes:
            new_lines.append(f"{key_match}={env_writes[key_match]}")
            updated_keys.add(key_match)
        else:
            new_lines.append(line)

    for key, val in env_writes.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def cmd_status(args) -> None:
    """Show current memory provider config."""
    from qiqiclaw_cli.config import load_config

    config = load_config()
    mem_config = config.get("memory", {})
    provider_name = mem_config.get("provider", "")

    print(f"\n内存状态\n" + "─" * 40)
    print(f"  内置:      始终激活")
    print(f"  提供程序:  {provider_name or '(无 — 仅内置)'}")

    if provider_name:
        provider_config = mem_config.get(provider_name, {})
        if provider_config:
            print(f"\n  {provider_name} 配置:")
            for key, val in provider_config.items():
                print(f"    {key}: {val}")

        providers = _get_available_providers()
        found = any(name == provider_name for name, _, _ in providers)
        if found:
            print(f"\n  插件:      已安装 ✓")
            for pname, _, p in providers:
                if pname == provider_name:
                    if p.is_available():
                        print(f"  状态:      可用 ✓")
                    else:
                        print(f"  状态:      不可用 ✗")
                        schema = p.get_config_schema() if hasattr(p, "get_config_schema") else []
                        # Check all fields that have env_var (both secret and non-secret)
                        required_fields = [f for f in schema if f.get("env_var")]
                        if required_fields:
                            print(f"  缺失:")
                            for f in required_fields:
                                env_var = f.get("env_var", "")
                                url = f.get("url", "")
                                is_set = bool(os.environ.get(env_var))
                                mark = "✓" if is_set else "✗"
                                line = f"    {mark} {env_var}"
                                if url and not is_set:
                                    line += f"  → {url}"
                                print(line)
                    break
        else:
            print(f"\n  插件:      未安装 ✗")
            print(f"  请安装 '{provider_name}' 内存插件到 ~/.qiqiclaw/plugins/")

    providers = _get_available_providers()
    if providers:
        print(f"\n  已安装的插件:")
        for pname, desc, _ in providers:
            active = " ← 激活" if pname == provider_name else ""
            print(f"    • {pname}  ({desc}){active}")

    print()


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def memory_command(args) -> None:
    """Route memory subcommands."""
    sub = getattr(args, "memory_command", None)
    if sub == "setup":
        provider = getattr(args, "provider", None)
        if provider:
            cmd_setup_provider(provider)
        else:
            cmd_setup(args)
    elif sub == "status":
        if getattr(args, "json", False):
            print_memory_status_json()
        else:
            cmd_status(args)
    elif sub == "provider":
        provider = getattr(args, "provider", "")
        if getattr(args, "json", False):
            print_set_memory_provider_json(provider)
        else:
            result = set_memory_provider(provider)
            print(f"\n  ✓ Memory provider: {result['active_provider'] or 'built-in only'}")
            print("  已保存到 config.yaml\n")
    else:
        cmd_status(args)
