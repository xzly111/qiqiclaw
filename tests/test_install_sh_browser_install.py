"""Regression tests for install.sh browser setup.

Browser automation is optional. The installer should not leave QiQiClaw
half-installed just because Playwright's managed Chromium download hangs on an
unsupported distribution.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


def test_install_script_skips_playwright_download_when_system_browser_exists() -> None:
    text = INSTALL_SH.read_text()

    assert "find_system_browser()" in text
    assert "google-chrome google-chrome-stable chromium chromium-browser chrome" in text
    assert "Skipping Playwright browser download; QiQiClaw will use the system browser." in text


def test_install_script_persists_system_browser_for_agent_browser() -> None:
    text = INSTALL_SH.read_text()

    assert "configure_browser_env_from_system_browser()" in text
    assert "AGENT_BROWSER_EXECUTABLE_PATH=$browser_path" in text


def test_playwright_installs_are_timeout_guarded() -> None:
    text = INSTALL_SH.read_text()

    assert "run_browser_install_with_timeout()" in text
    assert "install_playwright_system_deps()" in text
    assert "playwright install-deps chromium" in text
    assert "run_browser_install_with_timeout 600 npx playwright install chromium" in text
    assert "install_system_chromium_fallback" in text


def test_install_script_supports_skip_browser_flag() -> None:
    """--skip-browser (and --no-playwright alias) skips the Playwright install."""
    text = INSTALL_SH.read_text()

    assert "--skip-browser|--no-playwright)" in text
    assert "SKIP_BROWSER=true" in text
    assert 'if [ "$SKIP_BROWSER" = true ]; then' in text
    assert "--skip-browser Skip Playwright/Chromium install" in text


def test_install_script_uses_privilege_helper_for_browser_deps() -> None:
    """Browser dependency installs should use sudo/pkexec/osascript handoff."""
    text = INSTALL_SH.read_text()

    assert "run_privileged()" in text
    assert "pkexec" in text
    assert "with administrator privileges" in text
    assert "run_privileged \"Installing Playwright Chromium system dependencies\"" in text
    assert "sudo npx playwright install-deps chromium" in text


def test_system_chromium_fallback_tries_apt_candidates_individually() -> None:
    text = INSTALL_SH.read_text()

    assert "for candidate in chromium-browser chromium; do" in text
    assert 'apt install -y "$candidate"' in text
    assert "google-chrome-stable" not in text.split("install_system_chromium_fallback()", 1)[1].split("configure_browser_env_from_system_browser()", 1)[0]
