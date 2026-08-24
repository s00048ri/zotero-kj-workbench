"""Failures the Zotero local API can produce, as distinct types.

The UI has to say something different for each of these, so they are not
collapsed into one exception class.
"""

from __future__ import annotations


class ZoteroError(RuntimeError):
    """Base class. Carries a message written for the researcher, not a log."""

    remedy: str | None = None


class ZoteroUnreachable(ZoteroError):
    """Nothing is listening on the local API port — Zotero is probably closed."""

    remedy = "Start Zotero and leave it running."


class ZoteroForbidden(ZoteroError):
    """Zotero is running but refuses to talk to other local applications."""

    remedy = (
        "In Zotero: Settings → Advanced → check "
        "“Allow other applications on this computer to communicate "
        "with Zotero”."
    )


class ZoteroWritesUnavailable(ZoteroError):
    """This Zotero predates local API write support (Zotero 10, 2026-08-17)."""

    remedy = "Upgrade Zotero to version 10 or newer. Everything else works without it."


class ZoteroHTTPError(ZoteroError):
    """Any other non-2xx response. Keeps the status and headers for the caller."""

    def __init__(self, status: int, headers: dict[str, str], message: str) -> None:
        super().__init__(message)
        self.status = status
        self.headers = headers


class ZoteroRateLimited(ZoteroHTTPError):
    """Zotero throttles authorization dialogs at five per minute."""

    @property
    def retry_after(self) -> int:
        try:
            return int(self.headers.get("Retry-After", "60"))
        except ValueError:
            return 60
