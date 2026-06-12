"""Regression tests for qiqiclaw_cli.pty_bridge platform backends."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path


def _load_pty_bridge_as_win32(tmp_path: Path):
    (tmp_path / "winpty.py").write_text(
        textwrap.dedent(
            """
            class FakeProc:
                def __init__(self):
                    self.pid = 4242
                    self.calls = []
                    self._alive = True

                def isalive(self):
                    return self._alive

                def read(self, size):
                    self.calls.append(("read", size))
                    return "win-ok"

                def write(self, data):
                    self.calls.append(("write", data))

                def setwinsize(self, rows, cols):
                    self.calls.append(("setwinsize", rows, cols))

                def terminate(self):
                    self.calls.append(("terminate",))
                    self._alive = False

                def close(self, *args, **kwargs):
                    self.calls.append(("close", args, kwargs))

            class PtyProcess:
                last = None

                @classmethod
                def spawn(cls, argv, cwd=None, env=None, dimensions=None):
                    proc = FakeProc()
                    proc.calls.append(("spawn", argv, cwd, env, dimensions))
                    cls.last = proc
                    return proc
            """
        )
    )

    sys.path.insert(0, str(tmp_path))
    source = Path(__file__).resolve().parents[2] / "qiqiclaw_cli" / "pty_bridge.py"
    spec = importlib.util.spec_from_file_location("pty_bridge_win32_test", source)
    module = importlib.util.module_from_spec(spec)
    old_platform = sys.platform
    old_winpty = sys.modules.pop("winpty", None)
    sys.platform = "win32"
    try:
        assert spec.loader is not None
        spec.loader.exec_module(module)
    finally:
        sys.platform = old_platform
        if old_winpty is not None:
            sys.modules["winpty"] = old_winpty
        sys.path.remove(str(tmp_path))
    return module


def test_windows_backend_uses_winpty_lifecycle(tmp_path: Path):
    module = _load_pty_bridge_as_win32(tmp_path)

    assert module.PtyBridge.is_available() is True
    bridge = module.PtyBridge.spawn(
        ["cmd.exe"],
        cwd="C:/work",
        env={"A": "B"},
        cols=100,
        rows=30,
    )

    assert bridge.pid == 4242
    assert bridge.read() == b"win-ok"
    bridge.write(b"abc")
    bridge.resize(cols=120, rows=40)
    bridge.close()

    assert module.WinPtyProcess.last.calls == [
        ("spawn", ["cmd.exe"], "C:/work", {"A": "B"}, (30, 100)),
        ("read", 65536),
        ("write", b"abc"),
        ("setwinsize", 40, 120),
        ("terminate",),
        ("close", (), {"force": True}),
    ]


def test_posix_backend_still_spawns_shell():
    if sys.platform.startswith("win"):
        return

    from qiqiclaw_cli.pty_bridge import PtyBridge

    with PtyBridge.spawn(["sh", "-lc", "printf posix-ok"]) as bridge:
        output = bytearray()
        for _ in range(20):
            chunk = bridge.read(0.2)
            if chunk is None:
                break
            output += chunk
            if b"posix-ok" in output:
                break

    assert b"posix-ok" in output
