"""Installer coverage for desktop voice input dependencies."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
INSTALL_PS1 = REPO_ROOT / "scripts" / "install.ps1"


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
