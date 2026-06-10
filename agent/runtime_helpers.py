"""Runtime helper utilities extracted from ``run_agent.py``."""

from __future__ import annotations

import logging
import os
import sys
import urllib.request
from typing import Optional

from utils import base_url_hostname, normalize_proxy_url

logger = logging.getLogger(__name__)


class _SafeWriter:
    """Transparent stdio wrapper that swallows broken-pipe style errors."""

    __slots__ = ("_inner",)

    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)

    def write(self, data):
        try:
            return self._inner.write(data)
        except (OSError, ValueError):
            return len(data) if isinstance(data, str) else 0

    def flush(self):
        try:
            self._inner.flush()
        except (OSError, ValueError):
            pass

    def fileno(self):
        return self._inner.fileno()

    def isatty(self):
        try:
            return self._inner.isatty()
        except (OSError, ValueError):
            return False

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _get_proxy_from_env() -> Optional[str]:
    """Read proxy URL from environment variables."""
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return normalize_proxy_url(value)
    return None


def _get_proxy_for_base_url(base_url: Optional[str]) -> Optional[str]:
    """Return an env-configured proxy unless NO_PROXY excludes this base URL."""
    proxy = _get_proxy_from_env()
    if not proxy or not base_url:
        return proxy

    host = base_url_hostname(base_url)
    if not host:
        return proxy

    try:
        if urllib.request.proxy_bypass_environment(host):
            return None
    except Exception:
        pass

    return proxy


def _install_safe_stdio() -> None:
    """Wrap stdout/stderr so best-effort console output cannot crash the agent."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and not isinstance(stream, _SafeWriter):
            setattr(sys, stream_name, _SafeWriter(stream))


def _routermint_headers() -> dict:
    """Return the User-Agent RouterMint needs to avoid Cloudflare 1010 blocks."""
    from qiqiclaw_cli import __version__ as _HERMES_VERSION

    return {
        "User-Agent": f"HermesAgent/{_HERMES_VERSION}",
    }


def _pool_may_recover_from_rate_limit(pool) -> bool:
    """Return True only when a credential pool has somewhere to rotate."""
    if pool is None:
        return False
    if not pool.has_available():
        return False
    return len(pool.entries()) > 1


def _qwen_portal_headers() -> dict:
    """Return default HTTP headers required by Qwen Portal API."""
    import platform as _plat

    qwen_code_version = "0.14.1"
    ua = f"QwenCode/{qwen_code_version} ({_plat.system().lower()}; {_plat.machine()})"
    return {
        "User-Agent": ua,
        "X-DashScope-CacheControl": "enable",
        "X-DashScope-UserAgent": ua,
        "X-DashScope-AuthType": "qwen-oauth",
    }


__all__ = [
    "_SafeWriter",
    "_get_proxy_for_base_url",
    "_get_proxy_from_env",
    "_install_safe_stdio",
    "_pool_may_recover_from_rate_limit",
    "_qwen_portal_headers",
    "_routermint_headers",
]
