"""The one place this program talks to Zotero.

Everything the rest of the app knows about Zotero comes through here, so that
a Zotero plugin front-end stays possible later and so that the local API's
quirks are recorded in exactly one file.

Local API behaviour that shapes this client, all of it observed rather than
assumed:

* reads need no authentication and no key, but Zotero must be configured to
  talk to other local applications, otherwise every read is a 403;
* reads are **not paginated** by default — a whole collection arrives in one
  response, so ``limit=0`` and no cursor logic;
* ``/items/<attachmentKey>/children`` returns child *notes* but **no
  annotations**. Annotations are only reachable through a library-wide
  ``itemType=annotation`` query, which is also one request instead of N;
* every response carries ``Zotero-Server-ID`` on Zotero 10 and newer. Its
  absence is how this app detects that writes are unavailable, and its value
  is how stored state is partitioned: object versions are local to one Zotero
  database and mean nothing against another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from ..config import settings
from .errors import (
    ZoteroForbidden,
    ZoteroHTTPError,
    ZoteroRateLimited,
    ZoteroUnreachable,
)
from .models import Annotation, parse_item

READ_TIMEOUT = 60.0


@dataclass(frozen=True)
class ServerInfo:
    """What the running Zotero is, and what it will let this app do."""

    reachable: bool
    api_version: str | None = None
    server_id: str | None = None
    schema_version: str | None = None
    zotero_version: str | None = None

    @property
    def writes_available(self) -> bool:
        """Writes need Zotero 10+, which is exactly what the server ID marks."""
        return bool(self.server_id)


class ZoteroClient:
    """Read-only client for the Zotero local API.

    Writes are deliberately absent at this milestone: they carry their own
    authorization lifecycle and land with the code that needs them.
    """

    def __init__(
        self,
        base: str | None = None,
        user: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base = (base or settings.zotero_base).rstrip("/")
        self.user = user or settings.zotero_user
        self.prefix = f"{self.base}/users/{self.user}"
        self._client = httpx.Client(
            timeout=READ_TIMEOUT,
            transport=transport,
            headers={
                "Zotero-API-Version": "3",
                "User-Agent": settings.user_agent,
                "Accept": "application/json",
            },
        )
        self._server: ServerInfo | None = None

    # -- plumbing ----------------------------------------------------------

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        try:
            resp = self._client.request(
                method, url, params=params, json=json_body, headers=headers
            )
        except httpx.ConnectError as e:
            raise ZoteroUnreachable(
                f"Nothing is answering at {self.base}. Is Zotero running?"
            ) from e
        except httpx.TransportError as e:
            raise ZoteroUnreachable(f"Cannot reach Zotero at {self.base}: {e}") from e

        if resp.is_success:
            return resp

        resp_headers = dict(resp.headers)
        if resp.status_code == 403 and method == "GET":
            raise ZoteroForbidden(
                "Zotero is running but refuses local API requests (403)."
            )
        if resp.status_code == 429:
            raise ZoteroRateLimited(
                429, resp_headers, "Zotero is rate-limiting requests."
            )
        raise ZoteroHTTPError(
            resp.status_code,
            resp_headers,
            f"HTTP {resp.status_code} for {method} {url}: {resp.text[:300]}",
        )

    def _get_json(self, path: str, **params: Any) -> Any:
        # limit=0 means "no limit": local API reads are unpaginated.
        params.setdefault("limit", 0)
        params = {k: v for k, v in params.items() if v is not None}
        resp = self._request("GET", f"{self.prefix}{path}", params=params)
        if not resp.content:
            return []
        return resp.json()

    # -- capability --------------------------------------------------------

    def server_info(self, refresh: bool = False) -> ServerInfo:
        """Probe the API root. Never raises for an unreachable Zotero."""
        if self._server is not None and not refresh:
            return self._server
        try:
            resp = self._request("GET", f"{self.base}/")
        except ZoteroUnreachable:
            self._server = ServerInfo(reachable=False)
            return self._server
        except ZoteroForbidden:
            # Reachable, but the preference is off. Report it as reachable so
            # the UI can show the remedy rather than "is Zotero running?".
            self._server = ServerInfo(reachable=True)
            raise
        h = resp.headers
        self._server = ServerInfo(
            reachable=True,
            api_version=h.get("Zotero-API-Version"),
            server_id=h.get("Zotero-Server-ID"),
            schema_version=h.get("Zotero-Schema-Version"),
            zotero_version=h.get("X-Zotero-Version"),
        )
        return self._server

    # -- reads -------------------------------------------------------------

    def collections(self) -> list[dict[str, Any]]:
        return self._get_json("/collections") or []

    def collection_items_top(self, collection_key: str) -> list[dict[str, Any]]:
        """Top-level items of one collection. Child items are not included."""
        return self._get_json(f"/collections/{collection_key}/items/top") or []

    def children(self, item_key: str) -> list[dict[str, Any]]:
        """Child items of an item.

        Returns notes and attachments. It does **not** return annotations —
        see the module docstring; use :meth:`annotation_index` for those.
        """
        return self._get_json(f"/items/{item_key}/children") or []

    def items(self, **params: Any) -> list[dict[str, Any]]:
        """Library-wide item query."""
        return self._get_json("/items", **params) or []

    def annotations(self) -> list[dict[str, Any]]:
        return self.items(itemType="annotation")

    def annotation_index(self) -> dict[str, list[Annotation]]:
        """Every annotation in the library, keyed by the attachment it hangs on.

        One request for the whole library, built once per import. Per-attachment
        lookups do not work at all (they return nothing), and would be N
        requests even if they did.
        """
        index: dict[str, list[Annotation]] = {}
        for payload in self.annotations():
            ann = Annotation.from_payload(payload)
            if ann.parent_item:
                index.setdefault(ann.parent_item, []).append(ann)
        for anns in index.values():
            anns.sort(key=lambda a: (a.sort_index or "", a.key))
        return index

    def file_url(self, attachment_key: str) -> str | None:
        """Local-API-only: the ``file://`` URL of an attachment, as plain text.

        Returns None when the attachment has no file on disk (a linked URL, or
        a file that was never downloaded), which is not an error.
        """
        try:
            resp = self._request(
                "GET", f"{self.prefix}/items/{attachment_key}/file/view/url"
            )
        except ZoteroHTTPError:
            return None
        text = resp.text.strip()
        return text or None

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ZoteroClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def parse_items(payloads: Iterable[dict[str, Any]]) -> list[Any]:
    return [parse_item(p) for p in payloads]
