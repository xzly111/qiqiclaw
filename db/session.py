import os
import inspect
from pathlib import Path

from tortoise import Tortoise

from domain.adapters import runtime_registry


def _patch_aiosqlite_connection_start() -> None:
    """Keep Tortoise 0.25.x compatible with aiosqlite 0.22.x.

    Tortoise's sqlite backend still calls ``Connection.start()`` before
    ``_connect()``. aiosqlite 0.22 moved thread start into ``__await__`` and
    dropped that public method, but Tortoise does not await the connection
    object on this path. Restore the tiny method Tortoise expects.
    """
    try:
        import aiosqlite
    except Exception:
        return

    connection_cls = getattr(aiosqlite, "Connection", None)
    if connection_cls is None or hasattr(connection_cls, "start"):
        return

    def start(self):
        thread = getattr(self, "_thread", None)
        if thread is not None and not thread.is_alive():
            thread.start()

    connection_cls.start = start


def _qiqiclaw_file_module_data_dir() -> Path:
    base = os.environ.get("QIQICLAW_FILE_MODULE_DATA_DIR")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".qiqiclaw" / "file-modules"


_DATA_DIR = _qiqiclaw_file_module_data_dir()
_DB_DIR = _DATA_DIR / "db"
_DB_DIR.mkdir(parents=True, exist_ok=True)
_DB_FILE = _DB_DIR / "db.sqlite3"

TORTOISE_ORM = {
    "connections": {"default": f"sqlite://{_DB_FILE}"},
    "apps": {
        "models": {
            "models": ["models.database"],
            "default_connection": "default",
        }
    },
}


async def init_db():
    _patch_aiosqlite_connection_start()
    init_kwargs = {"config": TORTOISE_ORM}
    if "_enable_global_fallback" in inspect.signature(Tortoise.init).parameters:
        init_kwargs["_enable_global_fallback"] = True
    await Tortoise.init(**init_kwargs)
    await Tortoise.generate_schemas()
    await runtime_registry.refresh()


async def close_db():
    await Tortoise.close_connections()
