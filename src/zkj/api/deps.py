"""One Zotero client per process, and one place to turn its failures into HTTP."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from functools import lru_cache

from fastapi import Request
from fastapi.responses import JSONResponse

from ..config import settings
from ..store import connect
from ..zotero import (
    ZoteroClient,
    ZoteroError,
    ZoteroForbidden,
    ZoteroHTTPError,
    ZoteroUnreachable,
)


@lru_cache(maxsize=1)
def get_client() -> ZoteroClient:
    return ZoteroClient()


def reset_client() -> None:
    get_client.cache_clear()


def get_db() -> Iterator[sqlite3.Connection]:
    """One connection per request. SQLite connections do not cross threads,
    and FastAPI runs sync endpoints in a pool."""
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


_STATUS = {
    ZoteroUnreachable: 503,
    ZoteroForbidden: 403,
}


async def zotero_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Report what went wrong and what the researcher can do about it.

    A Zotero failure is almost always a settings or lifecycle problem on this
    machine, not a bug, so the remedy travels with the error rather than being
    written again in the UI.
    """
    assert isinstance(exc, ZoteroError)
    status = _STATUS.get(type(exc), 502)
    if isinstance(exc, ZoteroHTTPError):
        status = 502
    return JSONResponse(
        status_code=status,
        content={
            "error": type(exc).__name__,
            "message": str(exc),
            "remedy": exc.remedy,
        },
    )
