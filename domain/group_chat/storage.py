from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any


def _storage_path() -> Path:
    base = (
        os.getenv("QIQICLAW_HOME")
        or os.getenv("HERMES_HOME")
        or os.getenv("XDG_STATE_HOME")
        or str(Path.home() / ".qiqiclaw")
    )
    return Path(base).expanduser() / "group-chat.json"


class GroupChatStorage:
    def __init__(self, path: Path | None = None):
        self.path = path or _storage_path()
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self._empty()

            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return self._empty()

            if not isinstance(data, dict):
                return self._empty()

            for key in ("rooms", "agents", "messages", "runs", "decisions"):
                if not isinstance(data.get(key), dict):
                    data[key] = {}

            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)

            fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                    tmp.write(payload)
                    tmp.write("\n")
                os.replace(tmp_name, self.path)
            finally:
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except OSError:
                    pass

    def mutate(self, fn):
        with self._lock:
            data = self.load()
            result = fn(data)
            data["updated_at"] = time.time()
            self.save(data)
            return deepcopy(result)

    @staticmethod
    def _empty() -> dict[str, Any]:
        now = time.time()
        return {
            "version": 1,
            "created_at": now,
            "updated_at": now,
            "rooms": {},
            "agents": {},
            "messages": {},
            "runs": {},
            "decisions": {},
        }

