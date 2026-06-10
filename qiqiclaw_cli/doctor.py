"""
Doctor command for QiQiClaw CLI.

Diagnoses issues with QiQiClaw setup.
"""

import os
import sys
import subprocess
import shutil
import importlib.util
from pathlib import Path

from qiqiclaw_cli.config import get_project_root, get_qiqiclaw_home, get_env_path
from qiqiclaw_constants import display_qiqiclaw_home, ensure_project_root_on_syspath

PROJECT_ROOT = get_project_root()
QIQICLAW_HOME = get_qiqiclaw_home()
_DHH = display_qiqiclaw_home()  # user-facing display path (e.g. ~/.qiqiclaw or ~/.qiqiclaw/profiles/coder)

# Load environment variables from ~/.qiqiclaw/.env so API key checks work
from dotenv import load_dotenv
_env_path = get_env_path()
if _env_path.exists():
    try:
        load_dotenv(_env_path, encoding="utf-8")
    except UnicodeDecodeError:
        load_dotenv(_env_path, encoding="latin-1")
# Also try project .env as dev fallback
load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8")

from qiqiclaw_cli.colors import Colors, color
from qiqiclaw_cli.models import _HERMES_USER_AGENT
from qiqiclaw_cli.vercel_auth import describe_vercel_auth
from qiqiclaw_constants import OPENROUTER_MODELS_URL
from utils import base_url_host_matches


_PROVIDER_ENV_HINTS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_TOKEN",
    "OPENAI_BASE_URL",
    "NOUS_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "Z_AI_API_KEY",
    "KIMI_API_KEY",
    "KIMI_CN_API_KEY",
    "GMI_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "KILOCODE_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "HF_TOKEN",
    "AI_GATEWAY_API_KEY",
    "OPENCODE_ZEN_API_KEY",
    "OPENCODE_GO_API_KEY",
    "XIAOMI_API_KEY",
    "TOKENHUB_API_KEY",
)


from qiqiclaw_constants import is_termux as _is_termux


def _python_install_cmd() -> str:
    return "python -m pip install" if _is_termux() else "uv pip install"


def _system_package_install_cmd(pkg: str) -> str:
    if _is_termux():
        return f"pkg install {pkg}"
    if sys.platform == "darwin":
        return f"brew install {pkg}"
    return f"sudo apt install {pkg}"


def _safe_which(cmd: str) -> str | None:
    """shutil.which wrapper resilient to platform monkeypatching in tests."""
    try:
        return shutil.which(cmd)
    except Exception:
        return None


def _termux_browser_setup_steps(node_installed: bool) -> list[str]:
    steps: list[str] = []
    step = 1
    if not node_installed:
        steps.append(f"{step}) pkg install nodejs")
        step += 1
    steps.append(f"{step}) npm install -g agent-browser")
    steps.append(f"{step + 1}) agent-browser install")
    return steps


def _has_provider_env_config(content: str) -> bool:
    """Return True when ~/.qiqiclaw/.env contains provider auth/base URL settings."""
    return any(key in content for key in _PROVIDER_ENV_HINTS)


def _honcho_is_configured_for_doctor() -> bool:
    """Return True when Honcho is configured, even if this process has no active session."""
    try:
        from plugins.memory.honcho.client import HonchoClientConfig

        cfg = HonchoClientConfig.from_global_config()
        return bool(cfg.enabled and (cfg.api_key or cfg.base_url))
    except Exception:
        return False


def _apply_doctor_tool_availability_overrides(available: list[str], unavailable: list[dict]) -> tuple[list[str], list[dict]]:
    """Adjust runtime-gated tool availability for doctor diagnostics."""
    if not _honcho_is_configured_for_doctor():
        return available, unavailable

    updated_available = list(available)
    updated_unavailable = []
    for item in unavailable:
        if item.get("name") == "honcho":
            if "honcho" not in updated_available:
                updated_available.append("honcho")
            continue
        updated_unavailable.append(item)
    return updated_available, updated_unavailable


def check_ok(text: str, detail: str = ""):
    print(f"  {color('✓', Colors.GREEN)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_warn(text: str, detail: str = ""):
    print(f"  {color('⚠', Colors.YELLOW)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_fail(text: str, detail: str = ""):
    print(f"  {color('✗', Colors.RED)} {text}" + (f" {color(detail, Colors.DIM)}" if detail else ""))

def check_info(text: str):
    print(f"    {color('→', Colors.CYAN)} {text}")


def _check_gateway_service_linger(issues: list[str]) -> None:
    """Warn when a systemd user gateway service will stop after logout."""
    try:
        from qiqiclaw_cli.gateway import (
            get_systemd_linger_status,
            get_systemd_unit_path,
            is_linux,
        )
    except Exception as e:
        check_warn("Gateway service linger", f"(could not import gateway helpers: {e})")
        return

    if not is_linux():
        return

    unit_path = get_systemd_unit_path()
    if not unit_path.exists():
        return

    print()
    print(color("◆ Gateway Service", Colors.CYAN, Colors.BOLD))

    linger_enabled, linger_detail = get_systemd_linger_status()
    if linger_enabled is True:
        check_ok("Systemd linger enabled", "(gateway service survives logout)")
    elif linger_enabled is False:
        check_warn("Systemd linger disabled", "(gateway may stop after logout)")
        check_info("Run: sudo loginctl enable-linger $USER")
        issues.append("Enable linger for the gateway user service: sudo loginctl enable-linger $USER")
    else:
        check_warn("Could not verify systemd linger", f"({linger_detail})")


def run_doctor(args):
    """Run diagnostic checks."""
    should_fix = getattr(args, 'fix', False)

    # Doctor runs from the interactive CLI, so CLI-gated tool availability
    # checks (like cronjob management) should see the same context as `qiqiclaw`.
    os.environ.setdefault("QIQICLAW_INTERACTIVE", "1")
    
    issues = []
    manual_issues = []  # issues that can't be auto-fixed
    fixed_count = 0
    
    print()
    print(color("┌─────────────────────────────────────────────────────────┐", Colors.CYAN))
    print(color("│              🩺 QiQiClaw 诊断工具                         │", Colors.CYAN))
    print(color("└─────────────────────────────────────────────────────────┘", Colors.CYAN))
    
    # =========================================================================
    # Check: Python version
    # =========================================================================
    print()
    print(color("◆ Python 环境", Colors.CYAN, Colors.BOLD))
    
    py_version = sys.version_info
    if py_version >= (3, 11):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
    elif py_version >= (3, 10):
        check_ok(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
        check_warn("建议使用 Python 3.11+ 以支持 RL 训练工具（tinker 需要 >= 3.11）")
    elif py_version >= (3, 8):
        check_warn(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "（建议使用 3.10+）")
    else:
        check_fail(f"Python {py_version.major}.{py_version.minor}.{py_version.micro}", "（需要 3.10+）")
        issues.append("将 Python 升级到 3.10+")

    # Check if in virtual environment
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        check_ok("虚拟环境已激活")
    else:
        check_warn("未在虚拟环境中", "（建议使用）")
    
    # =========================================================================
    # Check: Required packages
    # =========================================================================
    print()
    print(color("◆ 必需的软件包", Colors.CYAN, Colors.BOLD))
    
    required_packages = [
        ("openai", "OpenAI SDK"),
        ("rich", "Rich (terminal UI)"),
        ("dotenv", "python-dotenv"),
        ("yaml", "PyYAML"),
        ("httpx", "HTTPX"),
    ]
    
    optional_packages = [
        ("croniter", "Croniter (cron expressions)"),
        ("telegram", "python-telegram-bot"),
        ("discord", "discord.py"),
    ]
    
    for module, name in required_packages:
        try:
            __import__(module)
            check_ok(name)
        except ImportError:
            check_fail(name, "（缺失）")
            issues.append(f"安装 {name}：{_python_install_cmd()} {module}")

    for module, name in optional_packages:
        try:
            __import__(module)
            check_ok(name, "（可选）")
        except ImportError:
            check_warn(name, "（可选，未安装）")
    
    # =========================================================================
    # Check: Configuration files
    # =========================================================================
    print()
    print(color("◆ 配置文件", Colors.CYAN, Colors.BOLD))
    
    # Check ~/.qiqiclaw/.env (primary location for user config)
    env_path = QIQICLAW_HOME / '.env'
    if env_path.exists():
        check_ok(f"{_DHH}/.env 文件存在")

        # Check for common issues
        content = env_path.read_text()
        if _has_provider_env_config(content):
            check_ok("API key or custom endpoint configured")
        else:
            check_warn(f"在 {_DHH}/.env 中未找到 API 密钥")
            issues.append("运行 'qiqiclaw setup' 来配置 API 密钥")
    else:
        # Also check project root as fallback
        fallback_env = PROJECT_ROOT / '.env'
        if fallback_env.exists():
            check_ok(".env 文件存在（在项目目录中）")
        else:
            check_fail(f"{_DHH}/.env 文件缺失")
            if should_fix:
                env_path.parent.mkdir(parents=True, exist_ok=True)
                env_path.touch()
                check_ok(f"已创建空的 {_DHH}/.env")
                check_info("运行 'qiqiclaw setup' 来配置 API 密钥")
                fixed_count += 1
            else:
                check_info("运行 'qiqiclaw setup' 来创建")
                issues.append("运行 'qiqiclaw setup' 来创建 .env")
    
    # Check ~/.qiqiclaw/config.yaml (primary) or project cli-config.yaml (fallback)
    config_path = QIQICLAW_HOME / 'config.yaml'
    if config_path.exists():
        check_ok(f"{_DHH}/config.yaml 存在")

        # Validate model.provider and model.default values
        try:
            import yaml as _yaml
            cfg = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            model_section = cfg.get("model") or {}
            provider_raw = (model_section.get("provider") or "").strip()
            provider = provider_raw.lower()
            default_model = (model_section.get("default") or model_section.get("model") or "").strip()

            known_providers: set = set()
            try:
                from qiqiclaw_cli.auth import (
                    PROVIDER_REGISTRY,
                    resolve_provider as _resolve_auth_provider,
                )
                known_providers = set(PROVIDER_REGISTRY.keys()) | {"openrouter", "custom", "auto"}
            except Exception:
                _resolve_auth_provider = None
                pass
            try:
                from qiqiclaw_cli.config import get_compatible_custom_providers as _compatible_custom_providers
                from qiqiclaw_cli.providers import (
                    normalize_provider as _normalize_catalog_provider,
                    resolve_provider_full as _resolve_provider_full,
                )
            except Exception:
                _compatible_custom_providers = None
                _normalize_catalog_provider = None
                _resolve_provider_full = None

            custom_providers = []
            if _compatible_custom_providers is not None:
                try:
                    custom_providers = _compatible_custom_providers(cfg)
                except Exception:
                    custom_providers = []

            user_providers = cfg.get("providers")
            if isinstance(user_providers, dict):
                known_providers.update(str(name).strip().lower() for name in user_providers if str(name).strip())
            for entry in custom_providers:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                if name:
                    known_providers.add("custom:" + name.lower().replace(" ", "-"))

            valid_provider_ids = set(known_providers)
            provider_ids_to_accept = {provider} if provider else set()
            if _normalize_catalog_provider is not None:
                for known_provider in known_providers:
                    try:
                        valid_provider_ids.add(_normalize_catalog_provider(known_provider))
                    except Exception:
                        continue

            runtime_provider = provider
            if (
                provider
                and _resolve_auth_provider is not None
                and provider not in ("auto", "custom")
            ):
                try:
                    runtime_provider = _resolve_auth_provider(provider)
                    provider_ids_to_accept.add(runtime_provider)
                except Exception:
                    runtime_provider = provider

            catalog_provider = provider
            if (
                provider
                and _resolve_provider_full is not None
                and provider not in ("auto", "custom")
            ):
                provider_def = _resolve_provider_full(provider, user_providers, custom_providers)
                catalog_provider = provider_def.id if provider_def is not None else None
                if catalog_provider is not None:
                    provider_ids_to_accept.add(catalog_provider)

            if provider and provider != "auto":
                if catalog_provider is None or (
                    known_providers
                    and not (provider_ids_to_accept & valid_provider_ids)
                ):
                    known_list = ", ".join(sorted(known_providers)) if known_providers else "（不可用）"
                    check_fail(
                        f"model.provider '{provider_raw}' 不是已识别的提供商",
                        f"（已知：{known_list}）",
                    )
                    issues.append(
                        f"model.provider '{provider_raw}' 未知。"
                        f"有效的提供商：{known_list}。"
                        f"修复：运行 'qiqiclaw config set model.provider <有效提供商>'"
                    )

            # Warn if model is set to a provider-prefixed name on a provider that doesn't use them
            provider_for_policy = runtime_provider or catalog_provider
            providers_accepting_vendor_slugs = {
                "openrouter",
                "custom",
                "auto",
                "ai-gateway",
                "kilocode",
                "opencode-zen",
                "huggingface",
                "lmstudio",
                "nous",
            }
            if (
                default_model
                and "/" in default_model
                and provider_for_policy
                and provider_for_policy not in providers_accepting_vendor_slugs
            ):
                check_warn(
                    f"model.default '{default_model}' 使用供应商/模型格式，但提供商是 '{provider_raw}'",
                    "（供应商前缀格式属于聚合器，如 openrouter）",
                )
                issues.append(
                    f"model.default '{default_model}' 使用供应商前缀，但 model.provider 是 '{provider_raw}'。"
                    "请将 model.provider 设置为 'openrouter'，或删除供应商前缀。"
                )

            # Check credentials for the configured provider.
            # Limit to API-key providers in PROVIDER_REGISTRY — other provider
            # types (OAuth, SDK, openrouter/anthropic/custom/auto) have their
            # own env-var checks elsewhere in doctor, and get_auth_status()
            # returns a bare {logged_in: False} for anything it doesn't
            # explicitly dispatch, which would produce false positives.
            if runtime_provider and runtime_provider not in ("auto", "custom", "openrouter"):
                try:
                    from qiqiclaw_cli.auth import PROVIDER_REGISTRY, get_auth_status
                    pconfig = PROVIDER_REGISTRY.get(runtime_provider)
                    if pconfig and getattr(pconfig, "auth_type", "") == "api_key":
                        status = get_auth_status(runtime_provider) or {}
                        configured = bool(
                            status.get("configured")
                            or status.get("logged_in")
                            or status.get("api_key")
                        )
                        if not configured:
                            check_fail(
                                f"model.provider '{runtime_provider}' 已设置但未配置 API 密钥",
                                "（检查 ~/.qiqiclaw/.env 或运行 'qiqiclaw setup'）",
                            )
                            issues.append(
                                f"未找到提供商 '{runtime_provider}' 的凭据。"
                                f"运行 'qiqiclaw setup' 或在 {_DHH}/.env 中设置提供商的 API 密钥，"
                                f"或使用 'qiqiclaw config set model.provider <名称>' 切换提供商"
                            )
                except Exception:
                    pass

        except Exception as e:
            check_warn("无法验证模型/提供商配置", f"（{e}）")
    else:
        fallback_config = PROJECT_ROOT / 'cli-config.yaml'
        if fallback_config.exists():
            check_ok("cli-config.yaml 存在（在项目目录中）")
        else:
            example_config = PROJECT_ROOT / 'cli-config.yaml.example'
            if should_fix and example_config.exists():
                config_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(example_config), str(config_path))
                check_ok(f"从 cli-config.yaml.example 创建了 {_DHH}/config.yaml")
                fixed_count += 1
            elif should_fix:
                check_warn("未找到 config.yaml 且没有示例可复制")
                manual_issues.append(f"手动创建 {_DHH}/config.yaml")
            else:
                check_warn("未找到 config.yaml", "（使用默认值）")

    # Check config version and stale keys
    config_path = QIQICLAW_HOME / 'config.yaml'
    if config_path.exists():
        try:
            from qiqiclaw_cli.config import check_config_version, migrate_config
            current_ver, latest_ver = check_config_version()
            if current_ver < latest_ver:
                check_warn(
                    f"配置版本已过时（v{current_ver} → v{latest_ver}）",
                    "（有新设置可用）"
                )
                if should_fix:
                    try:
                        migrate_config(interactive=False, quiet=False)
                        check_ok("配置已迁移到最新版本")
                        fixed_count += 1
                    except Exception as mig_err:
                        check_warn(f"自动迁移失败：{mig_err}")
                        issues.append("运行 'qiqiclaw setup' 来迁移配置")
                else:
                    issues.append("运行 'qiqiclaw doctor --fix' 或 'qiqiclaw setup' 来迁移配置")
            else:
                check_ok(f"配置版本是最新的（v{current_ver}）")
        except Exception:
            pass

        # Detect stale root-level model keys (known bug source — PR #4329)
        try:
            import yaml
            with open(config_path) as f:
                raw_config = yaml.safe_load(f) or {}
            stale_root_keys = [k for k in ("provider", "base_url") if k in raw_config and isinstance(raw_config[k], str)]
            if stale_root_keys:
                check_warn(
                    f"过时的根级配置键：{', '.join(stale_root_keys)}",
                    "（应该在 'model:' 部分下）"
                )
                if should_fix:
                    model_section = raw_config.setdefault("model", {})
                    for k in stale_root_keys:
                        if not model_section.get(k):
                            model_section[k] = raw_config.pop(k)
                        else:
                            raw_config.pop(k)
                    from utils import atomic_yaml_write
                    atomic_yaml_write(config_path, raw_config)
                    check_ok("已将过时的根级键迁移到 model 部分")
                    fixed_count += 1
                else:
                    issues.append("config.yaml 中存在过时的根级 provider/base_url — 运行 'qiqiclaw doctor --fix'")
        except Exception:
            pass

        # Validate config structure (catches malformed custom_providers, etc.)
        try:
            from qiqiclaw_cli.config import validate_config_structure
            config_issues = validate_config_structure()
            if config_issues:
                print()
                print(color("◆ 配置结构", Colors.CYAN, Colors.BOLD))
                for ci in config_issues:
                    if ci.severity == "error":
                        check_fail(ci.message)
                    else:
                        check_warn(ci.message)
                    # Show the hint indented
                    for hint_line in ci.hint.splitlines():
                        check_info(hint_line)
                    issues.append(ci.message)
        except Exception:
            pass

    # =========================================================================
    # Check: Auth providers
    # =========================================================================
    print()
    print(color("◆ 认证提供商", Colors.CYAN, Colors.BOLD))

    try:
        from qiqiclaw_cli.auth import (
            get_nous_auth_status,
            get_codex_auth_status,
            get_gemini_oauth_auth_status,
            get_minimax_oauth_auth_status,
        )

        nous_status = get_nous_auth_status()
        if nous_status.get("logged_in"):
            check_ok("Nous Portal 认证", "（已登录）")
        else:
            check_warn("Nous Portal 认证", "（未登录）")

        codex_status = get_codex_auth_status()
        if codex_status.get("logged_in"):
            check_ok("OpenAI Codex 认证", "（已登录）")
        else:
            check_warn("OpenAI Codex 认证", "（未登录）")
            if codex_status.get("error"):
                check_info(codex_status["error"])

        gemini_status = get_gemini_oauth_auth_status()
        if gemini_status.get("logged_in"):
            email = gemini_status.get("email") or ""
            project = gemini_status.get("project_id") or ""
            pieces = []
            if email:
                pieces.append(email)
            if project:
                pieces.append(f"project={project}")
            suffix = f" ({', '.join(pieces)})" if pieces else ""
            check_ok("Google Gemini OAuth", f"（已登录{suffix}）")
        else:
            check_warn("Google Gemini OAuth", "（未登录）")

        minimax_status = get_minimax_oauth_auth_status()
        if minimax_status.get("logged_in"):
            region = minimax_status.get("region", "global")
            check_ok("MiniMax OAuth", f"（已登录，区域={region}）")
        else:
            check_warn("MiniMax OAuth", "（未登录）")
    except Exception as e:
        check_warn("认证提供商状态", f"（无法检查：{e}）")

    if _safe_which("codex"):
        check_ok("codex CLI")
    else:
        # Native OAuth uses QiQiClaw' own device-code flow — the Codex CLI is
        # only needed if you want to import existing tokens from
        # ~/.codex/auth.json.  Downgrade to info so users running
        # `qiqiclaw auth openai-codex` aren't told they're missing something.
        check_info(
            "未安装 codex CLI "
            "（可选 — 仅在从现有 Codex CLI 登录导入令牌时需要）"
        )

    # =========================================================================
    # Check: Directory structure
    # =========================================================================
    print()
    print(color("◆ 目录结构", Colors.CYAN, Colors.BOLD))
    
    qiqiclaw_home = QIQICLAW_HOME
    if qiqiclaw_home.exists():
        check_ok(f"{_DHH} 目录存在")
    else:
        if should_fix:
            qiqiclaw_home.mkdir(parents=True, exist_ok=True)
            check_ok(f"已创建 {_DHH} 目录")
            fixed_count += 1
        else:
            check_warn(f"{_DHH} 未找到", "（将在首次使用时创建）")
    
    # Check expected subdirectories
    expected_subdirs = ["cron", "sessions", "logs", "skills", "memories"]
    for subdir_name in expected_subdirs:
        subdir_path = qiqiclaw_home / subdir_name
        if subdir_path.exists():
            check_ok(f"{_DHH}/{subdir_name}/ 存在")
        else:
            if should_fix:
                subdir_path.mkdir(parents=True, exist_ok=True)
                check_ok(f"已创建 {_DHH}/{subdir_name}/")
                fixed_count += 1
            else:
                check_warn(f"{_DHH}/{subdir_name}/ 未找到", "（将在首次使用时创建）")
    
    # Check for SOUL.md persona file
    soul_path = qiqiclaw_home / "SOUL.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8").strip()
        # Check if it's just the template comments (no real content)
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith(("<!--", "-->", "#"))]
        if lines:
            check_ok(f"{_DHH}/SOUL.md 存在（已配置个性）")
        else:
            check_info(f"{_DHH}/SOUL.md 存在但为空 — 编辑它以自定义个性")
    else:
        check_warn(f"{_DHH}/SOUL.md 未找到", "（创建它以赋予 QiQiClaw 自定义个性）")
        if should_fix:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
            soul_path.write_text(
                "# QiQiClaw Persona\n\n"
                "<!-- Edit this file to customize how QiQiClaw communicates. -->\n\n"
                "You are QiQiClaw, a helpful AI assistant.\n",
                encoding="utf-8",
            )
            check_ok(f"已创建 {_DHH}/SOUL.md 基础模板")
            fixed_count += 1
    
    # Check memory directory
    memories_dir = qiqiclaw_home / "memories"
    if memories_dir.exists():
        check_ok(f"{_DHH}/memories/ 目录存在")
        memory_file = memories_dir / "MEMORY.md"
        user_file = memories_dir / "USER.md"
        if memory_file.exists():
            size = len(memory_file.read_text(encoding="utf-8").strip())
            check_ok(f"MEMORY.md 存在（{size} 字符）")
        else:
            check_info("MEMORY.md 尚未创建（将在代理首次写入记忆时创建）")
        if user_file.exists():
            size = len(user_file.read_text(encoding="utf-8").strip())
            check_ok(f"USER.md 存在（{size} 字符）")
        else:
            check_info("USER.md 尚未创建（将在代理首次写入记忆时创建）")
    else:
        check_warn(f"{_DHH}/memories/ 未找到", "（将在首次使用时创建）")
        if should_fix:
            memories_dir.mkdir(parents=True, exist_ok=True)
            check_ok(f"已创建 {_DHH}/memories/")
            fixed_count += 1
    
    # Check SQLite session store
    state_db_path = qiqiclaw_home / "state.db"
    if state_db_path.exists():
        try:
            import sqlite3
            conn = sqlite3.connect(str(state_db_path))
            cursor = conn.execute("SELECT COUNT(*) FROM sessions")
            count = cursor.fetchone()[0]
            conn.close()
            check_ok(f"{_DHH}/state.db 存在（{count} 个会话）")
        except Exception as e:
            check_warn(f"{_DHH}/state.db 存在但有问题：{e}")
    else:
        check_info(f"{_DHH}/state.db 尚未创建（将在首次会话时创建）")

    # Check WAL file size (unbounded growth indicates missed checkpoints)
    wal_path = qiqiclaw_home / "state.db-wal"
    if wal_path.exists():
        try:
            wal_size = wal_path.stat().st_size
            if wal_size > 50 * 1024 * 1024:  # 50 MB
                check_warn(
                    f"WAL 文件较大（{wal_size // (1024*1024)} MB）",
                    "（可能表示错过了检查点）"
                )
                if should_fix:
                    import sqlite3
                    conn = sqlite3.connect(str(state_db_path))
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    conn.close()
                    new_size = wal_path.stat().st_size if wal_path.exists() else 0
                    check_ok(f"已执行 WAL 检查点（{wal_size // 1024}K → {new_size // 1024}K）")
                    fixed_count += 1
                else:
                    issues.append("WAL 文件较大 — 运行 'qiqiclaw doctor --fix' 来执行检查点")
            elif wal_size > 10 * 1024 * 1024:  # 10 MB
                check_info(f"WAL 文件为 {wal_size // (1024*1024)} MB（活动会话正常）")
        except Exception:
            pass

    _check_gateway_service_linger(issues)

    # =========================================================================
    # Check: Command installation (qiqiclaw bin symlink)
    # =========================================================================
    if sys.platform != "win32":
        print()
        print(color("◆ Command Installation", Colors.CYAN, Colors.BOLD))

        # Determine the venv entry point location
        _venv_bin = None
        for _venv_name in ("venv", ".venv"):
            _candidate = PROJECT_ROOT / _venv_name / "bin" / "qiqiclaw"
            if _candidate.exists():
                _venv_bin = _candidate
                break

        # Determine the expected command link directory (mirrors install.sh logic)
        _prefix = os.environ.get("PREFIX", "")
        _is_termux_env = bool(os.environ.get("TERMUX_VERSION")) or "com.termux/files/usr" in _prefix
        if _is_termux_env and _prefix:
            _cmd_link_dir = Path(_prefix) / "bin"
            _cmd_link_display = "$PREFIX/bin"
        else:
            _cmd_link_dir = Path.home() / ".local" / "bin"
            _cmd_link_display = "~/.local/bin"
        _cmd_link = _cmd_link_dir / "qiqiclaw"

        if _venv_bin is None:
            check_warn(
                "Venv entry point not found",
                "（qiqiclaw 不在 venv/bin/ 或 .venv/bin/ 中 — 使用 pip install -e '.[all]' 重新安装）"
            )
            manual_issues.append(
                f"重新安装入口点：cd {PROJECT_ROOT} && source venv/bin/activate && pip install -e '.[all]'"
            )
        else:
            check_ok(f"Venv entry point exists ({_venv_bin.relative_to(PROJECT_ROOT)})")

            # Check the symlink at the command link location
            if _cmd_link.is_symlink():
                _target = _cmd_link.resolve()
                _expected = _venv_bin.resolve()
                if _target == _expected:
                    check_ok(f"{_cmd_link_display}/qiqiclaw → correct target")
                else:
                    check_warn(
                        f"{_cmd_link_display}/qiqiclaw wrong target",
                        f"(→ {_target}，期望 → {_expected})"
                    )
                    if should_fix:
                        _cmd_link.unlink()
                        _cmd_link.symlink_to(_venv_bin)
                        check_ok(f"Fixed symlink: {_cmd_link_display}/qiqiclaw → {_venv_bin}")
                        fixed_count += 1
                    else:
                        issues.append(f"{_cmd_link_display}/qiqiclaw 符号链接损坏 — 运行 'qiqiclaw doctor --fix'")
            elif _cmd_link.exists():
                # It's a regular file, not a symlink — possibly a wrapper script
                check_ok(f"{_cmd_link_display}/qiqiclaw exists (non-symlink)")
            else:
                check_fail(
                    f"{_cmd_link_display}/qiqiclaw not found",
                    "（qiqiclaw 命令可能在 venv 外无法工作）"
                )
                if should_fix:
                    _cmd_link_dir.mkdir(parents=True, exist_ok=True)
                    _cmd_link.symlink_to(_venv_bin)
                    check_ok(f"Created symlink: {_cmd_link_display}/qiqiclaw → {_venv_bin}")
                    fixed_count += 1

                    # Check if the link dir is on PATH
                    _path_dirs = os.environ.get("PATH", "").split(os.pathsep)
                    if str(_cmd_link_dir) not in _path_dirs:
                        check_warn(
                            f"{_cmd_link_display} 不在您的 PATH 中",
                            "（将其添加到您的 shell 配置：export PATH=\"$HOME/.local/bin:$PATH\"）"
                        )
                        manual_issues.append(f"将 {_cmd_link_display} 添加到您的 PATH")
                else:
                    issues.append(f"缺少 {_cmd_link_display}/qiqiclaw 符号链接 — 运行 'qiqiclaw doctor --fix'")

    # =========================================================================
    # Check: External tools
    # =========================================================================
    print()
    print(color("◆ 外部工具", Colors.CYAN, Colors.BOLD))

    # Git
    if _safe_which("git"):
        check_ok("git")
    else:
        check_warn("未找到 git", "（可选）")

    # ripgrep (optional, for faster file search)
    if _safe_which("rg"):
        check_ok("ripgrep (rg)", "（更快的文件搜索）")
    else:
        check_warn("未找到 ripgrep (rg)", "（文件搜索使用 grep 后备）")
        check_info(f"安装以获得更快的搜索：{_system_package_install_cmd('ripgrep')}")

    # Docker (optional)
    terminal_env = os.getenv("TERMINAL_ENV", "local")
    if terminal_env == "docker":
        if _safe_which("docker"):
            # Check if docker daemon is running
            try:
                result = subprocess.run(["docker", "info"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok("docker", "（守护进程运行中）")
            else:
                check_fail("docker 守护进程未运行")
                issues.append("启动 Docker 守护进程")
        else:
            check_fail("未找到 docker", "（TERMINAL_ENV=docker 需要）")
            issues.append("安装 Docker 或更改 TERMINAL_ENV")
    else:
        if _safe_which("docker"):
            check_ok("docker", "（可选）")
        else:
            if _is_termux():
                check_info("Docker backend is not available inside Termux (expected on Android)")
            else:
                check_warn("未找到 docker", "（可选）")

    # SSH (if using ssh backend)
    if terminal_env == "ssh":
        ssh_host = os.getenv("TERMINAL_SSH_HOST")
        if ssh_host:
            # Try to connect
            try:
                result = subprocess.run(
                    ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", ssh_host, "echo ok"],
                    capture_output=True,
                    text=True,
                    timeout=15
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0:
                check_ok(f"SSH 连接到 {ssh_host}")
            else:
                check_fail(f"SSH 连接到 {ssh_host}")
                issues.append(f"检查 {ssh_host} 的 SSH 配置")
        else:
            check_fail("未设置 TERMINAL_SSH_HOST", "（TERMINAL_ENV=ssh 需要）")
            issues.append("在 .env 中设置 TERMINAL_SSH_HOST")

    # Daytona (if using daytona backend)
    if terminal_env == "daytona":
        daytona_key = os.getenv("DAYTONA_API_KEY")
        if daytona_key:
            check_ok("Daytona API 密钥", "（已配置）")
        else:
            check_fail("未设置 DAYTONA_API_KEY", "（TERMINAL_ENV=daytona 需要）")
            issues.append("设置 DAYTONA_API_KEY 环境变量")
        try:
            from daytona import Daytona  # noqa: F401 — SDK presence check
            check_ok("daytona SDK", "（已安装）")
        except ImportError:
            check_fail("未安装 daytona SDK", "（pip install daytona）")
            issues.append("安装 daytona SDK：pip install daytona")

    # Vercel Sandbox (if using vercel_sandbox backend)
    if terminal_env == "vercel_sandbox":
        runtime = os.getenv("TERMINAL_VERCEL_RUNTIME", "node24").strip() or "node24"
        from tools.terminal_tool import _SUPPORTED_VERCEL_RUNTIMES
        if runtime in _SUPPORTED_VERCEL_RUNTIMES:
            check_ok("Vercel runtime", f"({runtime})")
        else:
            supported = ", ".join(_SUPPORTED_VERCEL_RUNTIMES)
            check_fail("Unsupported Vercel runtime", f"({runtime}; use {supported})")
            issues.append(f"将 TERMINAL_VERCEL_RUNTIME 设置为以下之一：{supported}")

        disk = os.getenv("TERMINAL_CONTAINER_DISK", "51200").strip()
        if disk in ("", "0", "51200"):
            check_ok("Vercel disk setting", "(using platform default)")
        else:
            check_fail("Vercel custom disk unsupported", "(reset terminal.container_disk to 51200)")
            issues.append("Vercel Sandbox 不支持自定义 container_disk；使用共享默认值 51200")

        if importlib.util.find_spec("vercel") is not None:
            check_ok("vercel SDK", "（已安装）")
        else:
            check_fail("未安装 vercel SDK", "（pip install 'qiqiclaw[vercel]'）")
            issues.append("安装 Vercel 可选依赖：pip install 'qiqiclaw[vercel]'")

        auth_status = describe_vercel_auth()
        if auth_status.ok:
            check_ok("Vercel auth", f"({auth_status.label})")
        elif auth_status.label.startswith("partial"):
            check_fail("Vercel auth incomplete", f"({auth_status.label})")
            issues.append("同时设置 VERCEL_TOKEN、VERCEL_PROJECT_ID 和 VERCEL_TEAM_ID")
        else:
            check_fail("Vercel auth not configured", f"({auth_status.label})")
            issues.append(
                "使用 VERCEL_TOKEN、VERCEL_PROJECT_ID 和 VERCEL_TEAM_ID 配置 Vercel Sandbox 认证"
            )
        for line in auth_status.detail_lines:
            check_info(f"Vercel auth {line}")

        persistent = os.getenv("TERMINAL_CONTAINER_PERSISTENT", "true").lower() in ("1", "true", "yes", "on")
        if persistent:
            check_info("Vercel persistence: snapshot filesystem only; active processes do not survive sandbox rebuild")
        else:
            check_info("Vercel persistence: ephemeral filesystem")

    # Node.js + agent-browser (for browser automation tools)
    if _safe_which("node"):
        check_ok("Node.js")
        # Check if agent-browser is installed
        agent_browser_path = PROJECT_ROOT / "node_modules" / "agent-browser"
        if agent_browser_path.exists():
            check_ok("agent-browser (Node.js)", "（浏览器自动化）")
        else:
            if _is_termux():
                check_info("agent-browser is not installed (expected in the tested Termux path)")
                check_info("Install later: npm install -g agent-browser && agent-browser install")
                check_info("Termux browser setup:")
                for step in _termux_browser_setup_steps(node_installed=True):
                    check_info(step)
            else:
                check_warn("未安装 agent-browser", "（运行：npm install）")
    else:
        if _is_termux():
            check_info("Node.js not found (browser tools are optional in the tested Termux path)")
            check_info("Install Node.js on Termux with: pkg install nodejs")
            check_info("Termux browser setup:")
            for step in _termux_browser_setup_steps(node_installed=False):
                check_info(step)
        else:
            check_warn("未找到 Node.js", "（可选，浏览器工具需要）")
    
    # npm audit for all Node.js packages
    if _safe_which("npm"):
        npm_dirs = [
            (PROJECT_ROOT, "浏览器工具 (agent-browser)"),
            (PROJECT_ROOT / "scripts" / "whatsapp-bridge", "WhatsApp 桥接"),
        ]
        for npm_dir, label in npm_dirs:
            if not (npm_dir / "node_modules").exists():
                continue
            try:
                audit_result = subprocess.run(
                    ["npm", "audit", "--json"],
                    cwd=str(npm_dir),
                    capture_output=True, text=True, timeout=30,
                )
                import json as _json
                audit_data = _json.loads(audit_result.stdout) if audit_result.stdout.strip() else {}
                vuln_count = audit_data.get("metadata", {}).get("vulnerabilities", {})
                critical = vuln_count.get("critical", 0)
                high = vuln_count.get("high", 0)
                moderate = vuln_count.get("moderate", 0)
                total = critical + high + moderate
                if total == 0:
                    check_ok(f"{label} 依赖", "（无已知漏洞）")
                elif critical > 0 or high > 0:
                    check_warn(
                        f"{label} 依赖",
                        f"（{critical} 个严重，{high} 个高危，{moderate} 个中危 — 运行：cd {npm_dir} && npm audit fix）"
                    )
                    issues.append(f"{label} 有 {total} 个 npm 漏洞")
                else:
                    check_ok(f"{label} 依赖", f"（{moderate} 个中危漏洞）")
            except Exception:
                pass

    # =========================================================================
    # Check: API connectivity
    # =========================================================================
    print()
    print(color("◆ API 连接性", Colors.CYAN, Colors.BOLD))

    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if openrouter_key:
        print("  检查 OpenRouter API...", end="", flush=True)
        try:
            import httpx
            response = httpx.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {openrouter_key}"},
                timeout=10
            )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} OpenRouter API                          ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('（无效的 API 密钥）', Colors.DIM)}                ")
                issues.append("检查 .env 中的 OPENROUTER_API_KEY")
            elif response.status_code == 402:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('（余额不足 — 需要付款）', Colors.DIM)}")
                issues.append(
                    "OpenRouter 账户余额不足。"
                    "修复：运行 'qiqiclaw config set model.provider <provider>' 切换提供商，"
                    "或在 https://openrouter.ai/settings/credits 为您的 OpenRouter 账户充值"
                )
            elif response.status_code == 429:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color('（速率限制）', Colors.DIM)}                ")
                issues.append("OpenRouter 速率限制 — 考虑切换到其他提供商或等待")
            else:
                print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'（HTTP {response.status_code}）', Colors.DIM)}                ")
        except Exception as e:
            print(f"\r  {color('✗', Colors.RED)} OpenRouter API {color(f'（{e}）', Colors.DIM)}                ")
            issues.append("检查网络连接")
    else:
        check_warn("OpenRouter API", "（未配置）")

    from qiqiclaw_cli.auth import get_anthropic_key
    anthropic_key = get_anthropic_key()
    if anthropic_key:
        print("  检查 Anthropic API...", end="", flush=True)
        try:
            import httpx
            from agent.anthropic_adapter import (
                _is_oauth_token,
                _COMMON_BETAS,
                _OAUTH_ONLY_BETAS,
                _CONTEXT_1M_BETA,
            )

            headers = {"anthropic-version": "2023-06-01"}
            is_oauth = _is_oauth_token(anthropic_key)
            if is_oauth:
                headers["Authorization"] = f"Bearer {anthropic_key}"
                headers["anthropic-beta"] = ",".join(_COMMON_BETAS + _OAUTH_ONLY_BETAS)
            else:
                headers["x-api-key"] = anthropic_key
            response = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers=headers,
                timeout=10
            )
            # Reactive recovery: OAuth subscriptions that don't include 1M
            # context reject the request with 400 "long context beta is not
            # yet available for this subscription". Retry once with that
            # beta stripped so the doctor check doesn't falsely report the
            # Anthropic API as unreachable for those users.
            if (
                is_oauth
                and response.status_code == 400
                and "long context beta" in response.text.lower()
                and "not yet available" in response.text.lower()
            ):
                headers["anthropic-beta"] = ",".join(
                    [b for b in _COMMON_BETAS if b != _CONTEXT_1M_BETA] + list(_OAUTH_ONLY_BETAS)
                )
                response = httpx.get(
                    "https://api.anthropic.com/v1/models",
                    headers=headers,
                    timeout=10,
                )
            if response.status_code == 200:
                print(f"\r  {color('✓', Colors.GREEN)} Anthropic API                           ")
            elif response.status_code == 401:
                print(f"\r  {color('✗', Colors.RED)} Anthropic API {color('（无效的 API 密钥）', Colors.DIM)}                 ")
            else:
                msg = "（无法验证）"
                print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(msg, Colors.DIM)}                 ")
        except Exception as e:
            print(f"\r  {color('⚠', Colors.YELLOW)} Anthropic API {color(f'（{e}）', Colors.DIM)}                 ")

    # -- API-key providers --
    # Tuple: (name, env_vars, default_url, base_env, supports_models_endpoint)
    # If supports_models_endpoint is False, we skip the health check and just show "configured"
    _apikey_providers = [
        ("Z.AI / GLM",      ("GLM_API_KEY", "ZAI_API_KEY", "Z_AI_API_KEY"), "https://api.z.ai/api/paas/v4/models", "GLM_BASE_URL", True),
        ("Kimi / Moonshot",  ("KIMI_API_KEY",),                              "https://api.moonshot.ai/v1/models",   "KIMI_BASE_URL", True),
        ("StepFun Step Plan",   ("STEPFUN_API_KEY",),                           "https://api.stepfun.ai/step_plan/v1/models", "STEPFUN_BASE_URL", True),
        ("Kimi / Moonshot (China)", ("KIMI_CN_API_KEY",),                    "https://api.moonshot.cn/v1/models",   None, True),
        ("Arcee AI",         ("ARCEEAI_API_KEY",),                            "https://api.arcee.ai/api/v1/models",  "ARCEE_BASE_URL", True),
        ("GMI Cloud",        ("GMI_API_KEY",),                                "https://api.gmi-serving.com/v1/models", "GMI_BASE_URL", True),
        ("DeepSeek",         ("DEEPSEEK_API_KEY",),                           "https://api.deepseek.com/v1/models",  "DEEPSEEK_BASE_URL", True),
        ("Hugging Face",     ("HF_TOKEN",),                                   "https://router.huggingface.co/v1/models", "HF_BASE_URL", True),
        ("NVIDIA NIM",       ("NVIDIA_API_KEY",),                             "https://integrate.api.nvidia.com/v1/models", "NVIDIA_BASE_URL", True),
        ("Alibaba/DashScope", ("DASHSCOPE_API_KEY",),                         "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models", "DASHSCOPE_BASE_URL", True),
        # MiniMax: the /anthropic endpoint doesn't support /models, but the /v1 endpoint does.
        ("MiniMax",          ("MINIMAX_API_KEY",),                            "https://api.minimax.io/v1/models",    "MINIMAX_BASE_URL", True),
        ("MiniMax (China)",  ("MINIMAX_CN_API_KEY",),                         "https://api.minimaxi.com/v1/models",  "MINIMAX_CN_BASE_URL", True),
        ("Vercel AI Gateway",       ("AI_GATEWAY_API_KEY",),                          "https://ai-gateway.vercel.sh/v1/models", "AI_GATEWAY_BASE_URL", True),
        ("Kilo Code",        ("KILOCODE_API_KEY",),                            "https://api.kilo.ai/api/gateway/models",  "KILOCODE_BASE_URL", True),
        ("OpenCode Zen",     ("OPENCODE_ZEN_API_KEY",),                        "https://opencode.ai/zen/v1/models",  "OPENCODE_ZEN_BASE_URL", True),
        # OpenCode Go has no shared /models endpoint; skip the health check.
        ("OpenCode Go",      ("OPENCODE_GO_API_KEY",),                         None,                                  "OPENCODE_GO_BASE_URL", False),
    ]
    for _pname, _env_vars, _default_url, _base_env, _supports_health_check in _apikey_providers:
        _key = ""
        for _ev in _env_vars:
            _key = os.getenv(_ev, "")
            if _key:
                break
        if _key:
            _label = _pname.ljust(20)
            # Some providers (like MiniMax) don't support /models endpoint
            if not _supports_health_check:
                print(f"  {color('✓', Colors.GREEN)} {_label} {color('(key configured)', Colors.DIM)}")
                continue
            print(f"  检查 {_pname} API...", end="", flush=True)
            try:
                import httpx
                _base = os.getenv(_base_env, "") if _base_env else ""
                # Auto-detect Kimi Code keys (sk-kimi-) → api.kimi.com/coding/v1
                # (OpenAI-compat surface, which exposes /models for health check).
                if not _base and _key.startswith("sk-kimi-"):
                    _base = "https://api.kimi.com/coding/v1"
                # Anthropic-compat endpoints (/anthropic, api.kimi.com/coding
                # with no /v1) don't support /models.  Rewrite to the OpenAI-compat
                # /v1 surface for health checks.
                if _base and _base.rstrip("/").endswith("/anthropic"):
                    from agent.auxiliary_client import _to_openai_base_url
                    _base = _to_openai_base_url(_base)
                if base_url_host_matches(_base, "api.kimi.com") and _base.rstrip("/").endswith("/coding"):
                    _base = _base.rstrip("/") + "/v1"
                _url = (_base.rstrip("/") + "/models") if _base else _default_url
                _headers = {
                    "Authorization": f"Bearer {_key}",
                    "User-Agent": _HERMES_USER_AGENT,
                }
                if base_url_host_matches(_base, "api.kimi.com"):
                    _headers["User-Agent"] = "claude-code/0.1.0"
                _resp = httpx.get(
                    _url,
                    headers=_headers,
                    timeout=10,
                )
                if _resp.status_code == 200:
                    print(f"\r  {color('✓', Colors.GREEN)} {_label}                          ")
                elif _resp.status_code == 401:
                    print(f"\r  {color('✗', Colors.RED)} {_label} {color('（无效的 API 密钥）', Colors.DIM)}           ")
                    issues.append(f"检查 .env 中的 {_env_vars[0]}")
                else:
                    print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'（HTTP {_resp.status_code}）', Colors.DIM)}           ")
            except Exception as _e:
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'（{_e}）', Colors.DIM)}           ")

    # -- AWS Bedrock --
    # Bedrock uses the AWS SDK credential chain, not API keys.
    try:
        from agent.bedrock_adapter import has_aws_credentials, resolve_aws_auth_env_var, resolve_bedrock_region
        if has_aws_credentials():
            _auth_var = resolve_aws_auth_env_var()
            _region = resolve_bedrock_region()
            _label = "AWS Bedrock".ljust(20)
            print(f"  检查 AWS Bedrock...", end="", flush=True)
            try:
                import boto3
                _br_client = boto3.client("bedrock", region_name=_region)
                _br_resp = _br_client.list_foundation_models()
                _model_count = len(_br_resp.get("modelSummaries", []))
                print(f"\r  {color('✓', Colors.GREEN)} {_label} {color(f'（{_auth_var}，{_region}，{_model_count} 个模型）', Colors.DIM)}           ")
            except ImportError:
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'（未安装 boto3 — {sys.executable} -m pip install boto3）', Colors.DIM)}           ")
                issues.append(f"为 Bedrock 安装 boto3：{sys.executable} -m pip install boto3")
            except Exception as _e:
                _err_name = type(_e).__name__
                print(f"\r  {color('⚠', Colors.YELLOW)} {_label} {color(f'（{_err_name}：{_e}）', Colors.DIM)}           ")
                issues.append(f"AWS Bedrock：{_err_name} — 检查 bedrock:ListFoundationModels 的 IAM 权限")
    except ImportError:
        pass  # bedrock_adapter not available — skip silently

    # =========================================================================
    # Check: Submodules
    # =========================================================================
    print()
    print(color("◆ 子模块", Colors.CYAN, Colors.BOLD))

    # tinker-atropos (RL training backend)
    tinker_dir = PROJECT_ROOT / "tinker-atropos"
    if tinker_dir.exists() and (tinker_dir / "pyproject.toml").exists():
        if py_version >= (3, 11):
            try:
                __import__("tinker_atropos")
                check_ok("tinker-atropos", "（RL 训练后端）")
            except ImportError:
                install_cmd = f"{_python_install_cmd()} -e ./tinker-atropos"
                check_warn("找到 tinker-atropos 但未安装", f"（运行：{install_cmd}）")
                issues.append(f"安装 tinker-atropos：{install_cmd}")
        else:
            check_warn("tinker-atropos 需要 Python 3.11+", f"（当前：{py_version.major}.{py_version.minor}）")
    else:
        check_warn("未找到 tinker-atropos", "（运行：git submodule update --init --recursive）")

    # =========================================================================
    # Check: Tool Availability
    # =========================================================================
    print()
    print(color("◆ 工具可用性", Colors.CYAN, Colors.BOLD))

    try:
        # Add project root to path for imports
        ensure_project_root_on_syspath()
        from model_tools import check_tool_availability, TOOLSET_REQUIREMENTS

        available, unavailable = check_tool_availability()
        available, unavailable = _apply_doctor_tool_availability_overrides(available, unavailable)

        for tid in available:
            info = TOOLSET_REQUIREMENTS.get(tid, {})
            check_ok(info.get("name", tid))

        for item in unavailable:
            env_vars = item.get("missing_vars") or item.get("env_vars") or []
            if env_vars:
                vars_str = ", ".join(env_vars)
                check_warn(item["name"], f"（缺少 {vars_str}）")
            else:
                check_warn(item["name"], "(system dependency not met)")

        # Count disabled tools with API key requirements
        api_disabled = [u for u in unavailable if (u.get("missing_vars") or u.get("env_vars"))]
        if api_disabled:
            issues.append("运行 'qiqiclaw setup' 配置缺少的 API 密钥以获得完整工具访问权限")
    except Exception as e:
        check_warn("无法检查工具可用性", f"（{e}）")

    # =========================================================================
    # Check: Skills Hub
    # =========================================================================
    print()
    print(color("◆ 技能中心", Colors.CYAN, Colors.BOLD))

    hub_dir = QIQICLAW_HOME / "skills" / ".hub"
    if hub_dir.exists():
        check_ok("技能中心目录存在")
        lock_file = hub_dir / "lock.json"
        if lock_file.exists():
            try:
                import json
                lock_data = json.loads(lock_file.read_text())
                count = len(lock_data.get("installed", {}))
                check_ok(f"锁定文件正常（{count} 个从中心安装的技能）")
            except Exception:
                check_warn("锁定文件", "（损坏或不可读）")
        quarantine = hub_dir / "quarantine"
        q_count = sum(1 for d in quarantine.iterdir() if d.is_dir()) if quarantine.exists() else 0
        if q_count > 0:
            check_warn(f"{q_count} 个技能在隔离区", "（待审查）")
    else:
        check_warn("技能中心目录未初始化", "（运行：qiqiclaw skills list）")

    from qiqiclaw_cli.config import get_env_value
    github_token = get_env_value("GITHUB_TOKEN") or get_env_value("GH_TOKEN")
    if github_token:
        check_ok("GitHub 令牌已配置（已认证的 API 访问）")
    else:
        check_warn("无 GITHUB_TOKEN", f"（60 请求/小时速率限制 — 在 {_DHH}/.env 中设置以获得更好的速率）")

    # =========================================================================
    # Memory Provider (only check the active provider, if any)
    # =========================================================================
    print()
    print(color("◆ Memory Provider", Colors.CYAN, Colors.BOLD))

    _active_memory_provider = ""
    try:
        import yaml as _yaml
        _mem_cfg_path = QIQICLAW_HOME / "config.yaml"
        if _mem_cfg_path.exists():
            with open(_mem_cfg_path) as _f:
                _raw_cfg = _yaml.safe_load(_f) or {}
            _active_memory_provider = (_raw_cfg.get("memory") or {}).get("provider", "")
    except Exception:
        pass

    if not _active_memory_provider:
        check_ok("Built-in memory active", "(no external provider configured — this is OK)")
    elif _active_memory_provider == "honcho":
        try:
            from plugins.memory.honcho.client import HonchoClientConfig, resolve_config_path
            hcfg = HonchoClientConfig.from_global_config()
            _honcho_cfg_path = resolve_config_path()

            if not _honcho_cfg_path.exists():
                check_warn("未找到 Honcho 配置", "运行：qiqiclaw memory setup")
            elif not hcfg.enabled:
                check_info(f"Honcho 已禁用（在 {_honcho_cfg_path} 中设置 enabled: true 以激活）")
            elif not (hcfg.api_key or hcfg.base_url):
                check_fail("未设置 Honcho API 密钥或基础 URL", "运行：qiqiclaw memory setup")
                issues.append("无 Honcho API 密钥 — 运行 'qiqiclaw memory setup'")
            else:
                from plugins.memory.honcho.client import get_honcho_client, reset_honcho_client
                reset_honcho_client()
                try:
                    get_honcho_client(hcfg)
                    check_ok(
                        "Honcho 已连接",
                        f"workspace={hcfg.workspace_id} mode={hcfg.recall_mode} freq={hcfg.write_frequency}",
                    )
                except Exception as _e:
                    check_fail("Honcho 连接失败", str(_e))
                    issues.append(f"Honcho 无法访问：{_e}")
        except ImportError:
            check_fail("未安装 honcho-ai", "pip install honcho-ai")
            issues.append("Honcho 被设置为内存提供程序但未安装 honcho-ai")
        except Exception as _e:
            check_warn("Honcho 检查失败", str(_e))
    elif _active_memory_provider == "mem0":
        try:
            from plugins.memory.mem0 import _load_config as _load_mem0_config
            mem0_cfg = _load_mem0_config()
            mem0_key = mem0_cfg.get("api_key", "")
            if mem0_key:
                check_ok("Mem0 API 密钥已配置")
                check_info(f"user_id={mem0_cfg.get('user_id', '?')}  agent_id={mem0_cfg.get('agent_id', '?')}")
            else:
                check_fail("未设置 Mem0 API 密钥", "（在 .env 中设置 MEM0_API_KEY 或运行 qiqiclaw memory setup）")
                issues.append("Mem0 被设置为内存提供程序但缺少 API 密钥")
        except ImportError:
            check_fail("无法加载 Mem0 插件", "pip install mem0ai")
            issues.append("Mem0 被设置为内存提供程序但未安装 mem0ai")
        except Exception as _e:
            check_warn("Mem0 检查失败", str(_e))
    else:
        # Generic check for other memory providers (openviking, hindsight, etc.)
        try:
            from plugins.memory import load_memory_provider
            _provider = load_memory_provider(_active_memory_provider)
            if _provider and _provider.is_available():
                check_ok(f"{_active_memory_provider} 提供程序激活")
            elif _provider:
                check_warn(f"{_active_memory_provider} 已配置但不可用", "运行：qiqiclaw memory status")
            else:
                check_warn(f"{_active_memory_provider} 插件未找到", "运行：qiqiclaw memory setup")
        except Exception as _e:
            check_warn(f"{_active_memory_provider} 检查失败", str(_e))

    # =========================================================================
    # Profiles
    # =========================================================================
    try:
        from qiqiclaw_cli.profiles import list_profiles, _get_wrapper_dir, profile_exists
        import re as _re

        named_profiles = [p for p in list_profiles() if not p.is_default]
        if named_profiles:
            print()
            print(color("◆ 配置文件", Colors.CYAN, Colors.BOLD))
            check_ok(f"找到 {len(named_profiles)} 个配置文件")
            wrapper_dir = _get_wrapper_dir()
            for p in named_profiles:
                parts = []
                if p.gateway_running:
                    parts.append("网关运行中")
                if p.model:
                    parts.append(p.model[:30])
                if not (p.path / "config.yaml").exists():
                    parts.append("⚠ 缺少配置")
                if not (p.path / ".env").exists():
                    parts.append("无 .env")
                wrapper = wrapper_dir / p.name
                if not wrapper.exists():
                    parts.append("无别名")
                status = ", ".join(parts) if parts else "已配置"
                check_ok(f"  {p.name}: {status}")

            # Check for orphan wrappers
            if wrapper_dir.is_dir():
                for wrapper in wrapper_dir.iterdir():
                    if not wrapper.is_file():
                        continue
                    try:
                        content = wrapper.read_text()
                        if "qiqiclaw -p" in content:
                            _m = _re.search(r"qiqiclaw -p (\S+)", content)
                            if _m and not profile_exists(_m.group(1)):
                                check_warn(f"孤立别名：{wrapper.name} → 配置文件 '{_m.group(1)}' 不再存在")
                    except Exception:
                        pass
    except ImportError:
        pass
    except Exception:
        pass

    # =========================================================================
    # Summary
    # =========================================================================
    print()
    remaining_issues = issues + manual_issues
    if should_fix and fixed_count > 0:
        print(color("─" * 60, Colors.GREEN))
        print(color(f"  已修复 {fixed_count} 个问题。", Colors.GREEN, Colors.BOLD), end="")
        if remaining_issues:
            print(color(f" {len(remaining_issues)} 个问题需要手动干预。", Colors.YELLOW, Colors.BOLD))
        else:
            print()
        print()
        if remaining_issues:
            for i, issue in enumerate(remaining_issues, 1):
                print(f"  {i}. {issue}")
            print()
    elif remaining_issues:
        print(color("─" * 60, Colors.YELLOW))
        print(color(f"  发现 {len(remaining_issues)} 个需要解决的问题：", Colors.YELLOW, Colors.BOLD))
        print()
        for i, issue in enumerate(remaining_issues, 1):
            print(f"  {i}. {issue}")
        print()
        if not should_fix:
            print(color("  提示：运行 'qiqiclaw doctor --fix' 自动修复可能的问题。", Colors.DIM))
    else:
        print(color("─" * 60, Colors.GREEN))
        print(color("  所有检查通过！", Colors.GREEN, Colors.BOLD))

    print()
