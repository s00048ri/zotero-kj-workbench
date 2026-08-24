"""Process-wide settings. One user, one machine, no config file yet."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def default_db_path() -> str:
    """Keep the database out of whatever directory the app was started from."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "zkj"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home())) / "zkj"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "zkj"
    return str(base / "zkj.sqlite3")


@dataclass(frozen=True)
class Settings:
    zotero_base: str = os.environ.get("ZOTERO_LOCAL_API", "http://localhost:23119/api")
    zotero_user: str = os.environ.get("ZOTERO_USER_ID", "0")
    db_path: str = os.environ.get("ZKJ_DB") or default_db_path()
    host: str = os.environ.get("ZKJ_HOST", "127.0.0.1")
    port: int = int(os.environ.get("ZKJ_PORT", "8420"))
    app_name: str = "Zotero KJ Workbench"
    user_agent: str = "zkj/0.1 (local research tool)"


settings = Settings()
