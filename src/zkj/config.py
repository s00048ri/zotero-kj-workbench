"""Process-wide settings. One user, one machine, no config file yet."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    zotero_base: str = os.environ.get("ZOTERO_LOCAL_API", "http://localhost:23119/api")
    zotero_user: str = os.environ.get("ZOTERO_USER_ID", "0")
    db_path: str = os.environ.get("ZKJ_DB", "zkj.sqlite3")
    host: str = os.environ.get("ZKJ_HOST", "127.0.0.1")
    port: int = int(os.environ.get("ZKJ_PORT", "8420"))
    app_name: str = "Zotero KJ Workbench"
    user_agent: str = "zkj/0.1 (local research tool)"


settings = Settings()
