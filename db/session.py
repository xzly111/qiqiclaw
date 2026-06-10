import os
from pathlib import Path

from tortoise import Tortoise

from domain.adapters import runtime_registry


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
    await Tortoise.init(config=TORTOISE_ORM, _enable_global_fallback=True)
    await Tortoise.generate_schemas()
    await runtime_registry.refresh()


async def close_db():
    await Tortoise.close_connections()
