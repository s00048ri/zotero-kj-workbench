"""SQLite connection, migrations, and the small helpers every query needs.

Schema changes are numbered files in ``migrations/`` applied in order and
tracked in ``PRAGMA user_version``. Nothing edits an existing migration once
it has shipped: a later milestone adds a new file.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

MIGRATION_DIR = Path(__file__).parent / "migrations"
_NUMBERED = re.compile(r"^(\d+)_")


def new_id() -> str:
    return uuid.uuid4().hex


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def migrations() -> list[tuple[int, Path]]:
    found: list[tuple[int, Path]] = []
    for path in sorted(MIGRATION_DIR.glob("*.sql")):
        m = _NUMBERED.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return sorted(found)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, creating and migrating it if necessary."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    applied = 0
    for number, path in migrations():
        if number <= version:
            continue
        # executescript performs no transaction control of its own, so the
        # script carries it: a migration either lands whole or not at all.
        script = "\n".join(
            [
                "BEGIN;",
                path.read_text(encoding="utf-8"),
                f"PRAGMA user_version = {number};",
                "COMMIT;",
            ]
        )
        try:
            conn.executescript(script)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied += 1
    return applied


def transaction(conn: sqlite3.Connection):
    """``with transaction(conn):`` — commit on success, roll back on error."""

    class _Tx:
        def __enter__(self) -> sqlite3.Connection:
            conn.execute("BEGIN")
            return conn

        def __exit__(self, exc_type, *_: object) -> bool:
            conn.execute("ROLLBACK" if exc_type else "COMMIT")
            return False

    return _Tx()


def insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> str:
    payload = {"id": values.get("id") or new_id(), **values}
    columns = ", ".join(payload)
    marks = ", ".join("?" for _ in payload)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(payload.values()))
    return payload["id"]


def upsert(
    conn: sqlite3.Connection,
    table: str,
    keys: dict[str, Any],
    values: dict[str, Any],
) -> str:
    """Insert or update the row identified by ``keys``, returning its id."""
    where = " AND ".join(f"{k} = ?" for k in keys)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {where}", tuple(keys.values())
    ).fetchone()
    if row is None:
        return insert(conn, table, {**keys, **values})
    if values:
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(
            f"UPDATE {table} SET {sets} WHERE id = ?",
            (*values.values(), row["id"]),
        )
    return row["id"]


def rows(cursor: Iterable[sqlite3.Row]) -> Iterator[dict[str, Any]]:
    for row in cursor:
        yield dict(row)


def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
    row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None
