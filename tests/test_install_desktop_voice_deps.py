"""Installer coverage for desktop voice input dependencies."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"
DESKTOP_MAIN = REPO_ROOT / "apps" / "desktop" / "electron" / "main.cjs"
DESKTOP_CONTROLLER = REPO_ROOT / "apps" / "desktop" / "src" / "app" / "desktop-controller.tsx"
DIAGNOSTICS_DIALOG = REPO_ROOT / "apps" / "desktop" / "src" / "components" / "full-feature-diagnostics-dialog.tsx"


def test_posix_installer_installs_desktop_voice_stt_dependencies() -> None:
    text = INSTALL_SH.read_text()

    assert "Desktop voice transcription:faster_whisper:faster-whisper==1.2.1" in text
    assert "Desktop voice audio IO:sounddevice:sounddevice==0.5.5" in text
    assert "Desktop voice numeric runtime:numpy:numpy==2.4.3" in text


def test_windows_installer_installs_desktop_voice_stt_dependencies() -> None:
    text = INSTALL_PS1.read_text()

    assert 'Label = "Desktop voice transcription"; Import = "faster_whisper"; Spec = "faster-whisper==1.2.1"' in text
    assert 'Label = "Desktop voice audio IO"; Import = "sounddevice"; Spec = "sounddevice==0.5.5"' in text
    assert 'Label = "Desktop voice numeric runtime"; Import = "numpy"; Spec = "numpy==2.4.3"' in text


def test_platform_sdk_install_stage_has_soft_timeout() -> None:
    posix = INSTALL_SH.read_text()
    windows = INSTALL_PS1.read_text()

    assert 'PLATFORM_SDKS_TIMEOUT_SECONDS="${QIQICLAW_PLATFORM_SDKS_TIMEOUT_SECONDS:-600}"' in posix
    assert "run_command_with_timeout" in posix
    assert "continuing to the desktop" in posix
    assert "return 0" in posix

    assert "$PlatformSdksTimeoutSeconds = 600" in windows
    assert "$env:QIQICLAW_PLATFORM_SDKS_TIMEOUT_SECONDS" in windows
    assert "WaitForExit([Math]::Max(1, $remainingSeconds) * 1000)" in windows
    assert "continuing to the desktop" in windows


def test_gitee_full_feature_diagnostics_waits_for_api_setup_and_prompts_user() -> None:
    main = DESKTOP_MAIN.read_text()
    controller = DESKTOP_CONTROLLER.read_text()
    dialog = DIAGNOSTICS_DIALOG.read_text()

    assert "/api/status" in main
    assert "/api/model/info" in main
    assert "no configured model provider is ready yet" in main
    assert "apiReachable: false" in main
    assert "isGiteeInstallBuild" in main
    assert "platform-sdks" in main

    assert "FullFeatureDiagnosticsDialog" in controller
    assert "全功能依赖完整" in dialog
    assert "发现未安装的全功能依赖" in dialog
    assert "配置缺失依赖" in dialog
    assert "result.apiReachable === false" in dialog
