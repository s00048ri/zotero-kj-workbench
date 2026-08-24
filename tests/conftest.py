"""Fixture-backed stand-in for a running Zotero.

The point of testing against a transport rather than a fake client class is
that the client's own URL building, parameter handling, header reading and
error mapping are exercised. A fake ZoteroClient subclass would assert that
the test's own code works.

The fake mirrors two real behaviours that matter more than any other:

* ``/items/<key>/children`` returns notes and attachments but **never**
  annotations, so any code that relies on it silently finds nothing;
* ``/items?itemType=annotation`` returns the whole library's annotations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str = "library.json") -> dict[str, Any]:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


class FakeZotero:
    """An httpx transport that answers like the Zotero local API."""

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        forbidden: bool = False,
        unreachable: bool = False,
    ) -> None:
        self.data = data if data is not None else load_fixture()
        self.headers = self.data.get("headers", {}) if headers is None else headers
        self.forbidden = forbidden
        self.unreachable = unreachable
        self.requests: list[httpx.Request] = []

    # -- inspection helpers used by tests ---------------------------------

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]

    def count(self, needle: str) -> int:
        return sum(1 for r in self.requests if needle in str(r.url))

    # -- transport ---------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def client(self):
        from zkj.zotero.client import ZoteroClient

        return ZoteroClient(transport=self.transport())

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.unreachable:
            raise httpx.ConnectError("connection refused", request=request)
        if self.forbidden:
            return httpx.Response(403, text="Forbidden", request=request)

        path = request.url.path
        params = dict(request.url.params)

        if path in ("/api/", "/api"):
            return httpx.Response(
                200, text="Zotero local API", headers=self.headers, request=request
            )

        if path == "/api/users/0/collections":
            return self._json(self.data["collections"], request)

        if path.startswith("/api/users/0/collections/") and path.endswith("/items/top"):
            key = path.split("/")[-3]
            return self._json(self.data["top"].get(key, []), request)

        if path.startswith("/api/users/0/items/") and path.endswith("/children"):
            key = path.split("/")[-2]
            children = self.data["children"].get(key, [])
            # the real API never hands back annotations here
            children = [
                c
                for c in children
                if c.get("data", c).get("itemType") != "annotation"
            ]
            return self._json(children, request)

        if path.startswith("/api/users/0/items/") and path.endswith("/file/view/url"):
            key = path.split("/")[-4]
            url = self.data.get("files", {}).get(key)
            if not url:
                return httpx.Response(404, text="Not found", request=request)
            return httpx.Response(200, text=url, request=request)

        if path == "/api/users/0/items":
            if params.get("itemType") == "annotation":
                return self._json(self.data["annotations"], request)
            return self._json([], request)

        return httpx.Response(404, text=f"no fixture for {path}", request=request)

    def _json(self, payload: Any, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers=self.headers, request=request)


@pytest.fixture
def fake_zotero() -> FakeZotero:
    return FakeZotero()


@pytest.fixture
def client(fake_zotero: FakeZotero):
    with fake_zotero.client() as c:
        yield c
