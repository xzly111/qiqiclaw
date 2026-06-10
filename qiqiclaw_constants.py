"""Shared constants for QIQI claw.

Import-safe module with no dependencies — can be imported from anywhere
without risk of circular imports.
"""

import os
import sys
from pathlib import Path





def _legacy_env(name: str, default: str | None = None) -> str | None:
    """Resolve a QiQiClaw-prefixed environment variable.

    Checks ``QIQICLAW_{name}``, then legacy ``HERMES_{name}``, then *default*.

    Example::

        timeout = _legacy_env("API_TIMEOUT", "1800")
        # Tries: QIQICLAW_API_TIMEOUT -> "1800"
    """
    qiqiclaw_value = os.getenv(f"QIQICLAW_{name}")
    if qiqiclaw_value is not None:
        return qiqiclaw_value
    return os.getenv(f"HERMES_{name}", default)


def _set_legacy_env(name: str, value: str) -> None:
    """Set a QiQiClaw-prefixed env var.

    Example::

        _set_legacy_env("QUIET", "1")
        # Sets QIQICLAW_QUIET=1
    """
    os.environ[f"QIQICLAW_{name}"] = value


def get_qiqiclaw_home() -> Path:
    """Return the QiQi Claw home directory (default: ~/.qiqiclaw).

    Reads QIQICLAW_HOME and falls back to ~/.qiqiclaw.
    This is the single source of truth — all other copies should import this.
    """
    val = os.environ.get("QIQICLAW_HOME", "").strip()
    if val:
        return Path(val).expanduser()

    return Path.home() / ".qiqiclaw"


def get_default_qiqiclaw_root() -> Path:
    """Return the root QiQi Claw directory for profile-level operations.

    In standard deployments this is ``~/.qiqiclaw``.

    In Docker or custom deployments where ``QIQICLAW_HOME`` points outside
    ``~/.qiqiclaw`` (e.g. ``/opt/data``), returns ``QIQICLAW_HOME`` directly
    — that IS the root.

    In profile mode where ``QIQICLAW_HOME`` is ``<root>/profiles/<name>``,
    returns ``<root>`` so that ``profile list`` can see all profiles.
    Works both for standard (``~/.qiqiclaw/profiles/coder``) and Docker
    (``/opt/data/profiles/coder``) layouts.

    Import-safe — no dependencies beyond stdlib.
    """
    native_home = Path.home() / ".qiqiclaw"

    env_home = os.environ.get("QIQICLAW_HOME", "")
    if not env_home:
        return native_home
    env_path = Path(env_home).expanduser()
    try:
        env_path.resolve().relative_to(native_home.resolve())
        # QIQICLAW_HOME is under ~/.qiqiclaw (normal or profile mode)
        return native_home
    except ValueError:
        pass

    # Docker / custom deployment.
    # Check if this is a profile path: <root>/profiles/<name>
    # If the immediate parent dir is named "profiles", the root is
    # the grandparent — this covers Docker profiles correctly.
    if env_path.parent.name == "profiles":
        return env_path.parent.parent

    # Not a profile path — QIQICLAW_HOME itself is the root
    return env_path


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """Return the optional-skills directory, honoring package-manager wrappers.

    Packaged installs may ship ``optional-skills`` outside the Python package
    tree and expose it via ``QIQICLAW_OPTIONAL_SKILLS``.
    """
    override = os.getenv("QIQICLAW_OPTIONAL_SKILLS", "")
    override = override.strip()
    if override:
        return Path(override)
    if default is not None:
        return default
    return get_qiqiclaw_home() / "optional-skills"


def get_project_root() -> Path:
    """Return the repository / installation root for QiQi claw."""
    return Path(__file__).resolve().parent


def ensure_project_root_on_syspath() -> Path:
    """Ensure the project root is importable and return it.

    Centralizes the repeated ``sys.path.insert(0, <repo-root>)`` pattern so
    entrypoints and auxiliary tools resolve imports consistently.
    """
    project_root = get_project_root()
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)
    return project_root


def get_qiqiclaw_dir(new_subpath: str, old_name: str) -> Path:
    """Resolve a QiQi Claw subdirectory with backward compatibility.

    New installs get the consolidated layout (e.g. ``cache/images``).
    Existing installs that already have the old path (e.g. ``image_cache``)
    keep using it — no migration required.

    Args:
        new_subpath: Preferred path relative to QIQICLAW_HOME (e.g. ``"cache/images"``).
        old_name: Legacy path relative to QIQICLAW_HOME (e.g. ``"image_cache"``).

    Returns:
        Absolute ``Path`` — old location if it exists on disk, otherwise the new one.
    """
    home = get_qiqiclaw_home()
    old_path = home / old_name
    if old_path.exists():
        return old_path
    return home / new_subpath


def display_qiqiclaw_home() -> str:
    """Return a user-friendly display string for the current QiQi Claw home.

    Uses ``~/`` shorthand for readability::

        default:  ``~/.qiqiclaw``
        profile:  ``~/.qiqiclaw/profiles/coder``
        custom:   ``/opt/qiqiclaw-custom``

    Use this in **user-facing** print/log messages instead of hardcoding
    ``~/.qiqiclaw``.  For code that needs a real ``Path``, use
    :func:`get_qiqiclaw_home` instead.
    """
    home = get_qiqiclaw_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def get_subprocess_home() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None.

    When ``{QIQICLAW_HOME}/home/`` exists on disk, subprocesses should use it
    as ``HOME`` so system tools (git, ssh, gh, npm …) write their configs
    inside the QiQi Claw data directory instead of the OS-level ``/root`` or
    ``~/``.  This provides:

    * **Docker persistence** — tool configs land inside the persistent volume.
    * **Profile isolation** — each profile gets its own git identity, SSH
      keys, gh tokens, etc.

    The Python process's own ``os.environ["HOME"]`` and ``Path.home()`` are
    **never** modified — only subprocess environments should inject this value.
    Activation is directory-based: if the ``home/`` subdirectory doesn't
    exist, returns ``None`` and behavior is unchanged.
    """
    qiqiclaw_home = os.getenv("QIQICLAW_HOME")
    if not qiqiclaw_home:
        return None
    profile_home = os.path.join(qiqiclaw_home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort: str) -> dict | None:
    """Parse a reasoning effort level into a config dict.

    Valid levels: "none", "minimal", "low", "medium", "high", "xhigh".
    Returns None when the input is empty or unrecognized (caller uses default).
    Returns {"enabled": False} for "none".
    Returns {"enabled": True, "effort": <level>} for valid effort levels.
    """
    if not effort or not effort.strip():
        return None
    effort = effort.strip().lower()
    if effort == "none":
        return {"enabled": False}
    if effort in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": effort}
    return None


def is_termux() -> bool:
    """Return True when running inside a Termux (Android) environment.

    Checks ``TERMUX_VERSION`` (set by Termux) or the Termux-specific
    ``PREFIX`` path.  Import-safe — no heavy deps.
    """
    prefix = os.getenv("PREFIX", "")
    return bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)


_wsl_detected: bool | None = None


def is_wsl() -> bool:
    """Return True when running inside WSL (Windows Subsystem for Linux).

    Checks ``/proc/version`` for the ``microsoft`` marker that both WSL1
    and WSL2 inject.  Result is cached for the process lifetime.
    Import-safe — no heavy deps.
    """
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r") as f:
            _wsl_detected = "microsoft" in f.read().lower()
    except Exception:
        _wsl_detected = False
    return _wsl_detected


_container_detected: bool | None = None


def is_container() -> bool:
    """Return True when running inside a Docker/Podman container.

    Checks ``/.dockerenv`` (Docker), ``/run/.containerenv`` (Podman),
    and ``/proc/1/cgroup`` for container runtime markers.  Result is
    cached for the process lifetime.  Import-safe — no heavy deps.
    """
    global _container_detected
    if _container_detected is not None:
        return _container_detected
    if os.path.exists("/.dockerenv"):
        _container_detected = True
        return True
    if os.path.exists("/run/.containerenv"):
        _container_detected = True
        return True
    try:
        with open("/proc/1/cgroup", "r") as f:
            cgroup = f.read()
            if "docker" in cgroup or "podman" in cgroup or "/lxc/" in cgroup:
                _container_detected = True
                return True
    except OSError:
        pass
    _container_detected = False
    return False


# ─── Well-Known Paths ─────────────────────────────────────────────────────────


def get_config_path() -> Path:
    """Return the path to ``config.yaml`` under QIQICLAW_HOME.

    Replaces the ``get_qiqiclaw_home() / "config.yaml"`` pattern repeated
    in 7+ files (skill_utils.py, hermes_logging.py, hermes_time.py, etc.).
    """
    return get_qiqiclaw_home() / "config.yaml"


def get_skills_dir() -> Path:
    """Return the path to the skills directory under QIQICLAW_HOME."""
    return get_qiqiclaw_home() / "skills"



def get_env_path() -> Path:
    """Return the path to the ``.env`` file under QIQICLAW_HOME."""
    return get_qiqiclaw_home() / ".env"


# ─── Network Preferences ─────────────────────────────────────────────────────


def apply_ipv4_preference(force: bool = False) -> None:
    """Monkey-patch ``socket.getaddrinfo`` to prefer IPv4 connections.

    On servers with broken or unreachable IPv6, Python tries AAAA records
    first and hangs for the full TCP timeout before falling back to IPv4.
    This affects httpx, requests, urllib, the OpenAI SDK — everything that
    uses ``socket.getaddrinfo``.

    When *force* is True, patches ``getaddrinfo`` so that calls with
    ``family=AF_UNSPEC`` (the default) resolve as ``AF_INET`` instead,
    skipping IPv6 entirely.  If no A record exists, falls back to the
    original unfiltered resolution so pure-IPv6 hosts still work.

    Safe to call multiple times — only patches once.
    Set ``network.force_ipv4: true`` in ``config.yaml`` to enable.
    """
    if not force:
        return

    import socket

    # Guard against double-patching
    if getattr(socket.getaddrinfo, "_hermes_ipv4_patched", False):
        return

    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:  # AF_UNSPEC — caller didn't request a specific family
            try:
                return _original_getaddrinfo(
                    host, port, socket.AF_INET, type, proto, flags
                )
            except socket.gaierror:
                # No A record — fall back to full resolution (pure-IPv6 hosts)
                return _original_getaddrinfo(host, port, family, type, proto, flags)
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    _ipv4_getaddrinfo._hermes_ipv4_patched = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"

AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
