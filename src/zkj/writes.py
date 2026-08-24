"""Holding a Zotero write key, and staying inside Zotero's rules for one.

Zotero grants a key through a dialog the researcher answers. What they press
changes everything about how a batch must be paced:

* **Always Allow** — the key is remembered, one dialog covers the whole run;
* **Allow** — the key is consumed by the first successful write, so the next
  write needs a fresh dialog, and Zotero accepts at most five dialog-showing
  requests per minute. A naive implementation that authorized per item would
  deadlock on the sixth card.

So: the interface steers hard toward "Always Allow", a remembered key is
stored per Zotero database, a 401 means the key was consumed rather than that
something is broken, and when the key is single-use the session paces itself.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar

from .config import settings
from .store import now_iso
from .zotero import ZoteroClient, ZoteroError, ZoteroHTTPError
from .zotero.errors import ZoteroRateLimited, ZoteroWritesUnavailable

T = TypeVar("T")

# Five dialogs a minute is the ceiling; 13 seconds apart stays under it with
# room for the round trip.
DIALOG_INTERVAL = 13.0


class WriteSession:
    def __init__(
        self,
        client: ZoteroClient,
        conn: sqlite3.Connection,
        *,
        app_name: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.conn = conn
        self.app_name = app_name or settings.app_name
        self._sleep = sleep
        self._clock = clock
        self.server_id = client.server_info().server_id or ""
        self.key: str | None = None
        self.remembered = False
        self._last_dialog: float | None = None
        self.dialogs_shown = 0
        self._load()

    # -- stored keys -------------------------------------------------------

    def _load(self) -> None:
        if not self.server_id:
            return
        row = self.conn.execute(
            "SELECT * FROM write_auth WHERE server_id = ?", (self.server_id,)
        ).fetchone()
        if row and row["remember"]:
            self.key = row["api_key"]
            self.remembered = True

    def _store(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO write_auth "
            "(server_id, api_key, remember, granted_at) VALUES (?, ?, ?, ?)",
            (self.server_id, self.key, int(self.remembered), now_iso()),
        )

    def forget(self) -> None:
        self.conn.execute(
            "DELETE FROM write_auth WHERE server_id = ?", (self.server_id,)
        )
        self.key = None
        self.remembered = False

    # -- the key -----------------------------------------------------------

    @property
    def available(self) -> bool:
        return bool(self.server_id)

    @property
    def has_remembered_key(self) -> bool:
        return self.remembered and bool(self.key)

    def acquire(self, force: bool = False) -> str:
        if self.key and not force:
            return self.key
        if not self.available:
            raise ZoteroWritesUnavailable(
                "This Zotero cannot accept notes from other applications."
            )
        self._pace()
        try:
            data = self.client.authorize(self.app_name)
        except ZoteroRateLimited as e:
            raise ZoteroError(
                f"Zotero is limiting permission dialogs. Wait {e.retry_after} "
                f"seconds and try again — and choose “Always Allow” this time, "
                f"so one dialog covers the whole run."
            ) from e
        self._last_dialog = self._clock()
        self.dialogs_shown += 1
        self.key = data.get("key")
        self.remembered = bool(data.get("remember"))
        if not self.key:
            raise ZoteroError("Zotero granted no write key.")
        if self.remembered:
            self._store()
        return self.key

    def _pace(self) -> None:
        if self._last_dialog is None:
            return
        gap = self._clock() - self._last_dialog
        if gap < DIALOG_INTERVAL:
            self._sleep(DIALOG_INTERVAL - gap)

    def run(self, call: Callable[[str], T]) -> T:
        """Perform one write, re-authorizing once if the key was consumed."""
        key = self.acquire()
        try:
            return call(key)
        except ZoteroHTTPError as e:
            if e.status != 401:
                raise
            # 401 here means the key was single-use and has now been spent.
            if self.remembered:
                self.forget()
            self.key = None
            return call(self.acquire(force=True))

    def spend(self) -> None:
        """Call after a successful write: a single-use key is now gone."""
        if not self.remembered:
            self.key = None


def parse_write_result(
    result: dict[str, Any], sent: int
) -> tuple[dict[int, str], dict[int, str]]:
    """Split a multi-object response into {index: key} and {index: error}.

    A partial failure is normal and must be reported per object, not as a
    blanket error — otherwise one bad card loses the other forty-nine.
    """
    ok: dict[int, str] = {}
    for index, key in (result.get("success") or {}).items():
        ok[int(index)] = key
    for index, obj in (result.get("successful") or {}).items():
        data = obj.get("data", obj)
        if data.get("key"):
            ok[int(index)] = data["key"]
    errors: dict[int, str] = {}
    for index, err in (result.get("failed") or {}).items():
        errors[int(index)] = f"{err.get('code')}: {err.get('message')}"
    for i in range(sent):
        if i not in ok and i not in errors:
            errors[i] = "Zotero reported no result for this one."
    return ok, errors
