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
    """An httpx transport that answers like the Zotero local API.

    The write side models the semantics that break naive batch code, because
    those are exactly what the tests exist to pin down: a key from plain
    "Allow" is consumed by the first successful write and every later use of
    it is a 401; a request without the server ID header is a 428; and Zotero
    stops showing dialogs after five in a minute.
    """

    def __init__(
        self,
        data: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        forbidden: bool = False,
        unreachable: bool = False,
        remember: bool = True,
        deny_writes: bool = False,
        dialog_limit: int = 5,
        fail_indexes: set[int] | None = None,
    ) -> None:
        self.data = data if data is not None else load_fixture()
        self.headers = self.data.get("headers", {}) if headers is None else headers
        self.forbidden = forbidden
        self.unreachable = unreachable
        self.requests: list[httpx.Request] = []

        # write state
        self.remember = remember
        self.deny_writes = deny_writes
        self.dialog_limit = dialog_limit
        self.dialogs = 0
        self.fail_indexes = fail_indexes or set()
        self.valid_keys: set[str] = set()
        self.spent_keys: set[str] = set()
        self.created_items: dict[str, dict[str, Any]] = {}
        self.created_collections: dict[str, dict[str, Any]] = {}
        self.deleted: list[str] = []
        self.updated: list[dict[str, Any]] = []
        self._counter = 0

    # -- write helpers -----------------------------------------------------

    def _next_key(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}{self._counter:04d}"

    def _check_write(self, request: httpx.Request) -> httpx.Response | None:
        if not request.headers.get("Zotero-Server-ID"):
            return httpx.Response(428, text="Zotero-Server-ID required", request=request)
        key = request.headers.get("Zotero-API-Key", "")
        if key in self.spent_keys:
            return httpx.Response(401, text="key already used", request=request)
        if key not in self.valid_keys:
            return httpx.Response(403, text="unknown key", request=request)
        return None

    def _consume(self, key: str) -> None:
        if not self.remember:
            self.valid_keys.discard(key)
            self.spent_keys.add(key)

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
        method = request.method

        if path == "/api/local/authorize":
            if self.deny_writes:
                return httpx.Response(403, text="denied", request=request)
            self.dialogs += 1
            if self.dialogs > self.dialog_limit:
                return httpx.Response(
                    429, text="too many prompts",
                    headers={"Retry-After": "60"}, request=request,
                )
            key = self._next_key("APIKEY")
            self.valid_keys.add(key)
            return httpx.Response(
                200, json={"key": key, "remember": self.remember}, request=request
            )

        if path == "/api/users/0/items" and method == "POST":
            refused = self._check_write(request)
            if refused:
                return refused
            self._consume(request.headers["Zotero-API-Key"])
            sent = json.loads(request.content)
            success, failed = {}, {}
            for index, item in enumerate(sent):
                if index in self.fail_indexes:
                    failed[str(index)] = {"code": 400, "message": "refused by fixture"}
                    continue
                key = self._next_key("KJNOTE")
                self.created_items[key] = {"key": key, "version": 1, **item}
                success[str(index)] = key
            return httpx.Response(
                200, json={"success": success, "failed": failed, "unchanged": {}},
                request=request,
            )

        if path == "/api/users/0/items" and method == "PATCH":
            refused = self._check_write(request)
            if refused:
                return refused
            self._consume(request.headers["Zotero-API-Key"])
            sent = json.loads(request.content)
            self.updated += sent
            return httpx.Response(
                200,
                json={"success": {str(i): o.get("key") for i, o in enumerate(sent)},
                      "failed": {}},
                request=request,
            )

        if path == "/api/users/0/items" and method == "DELETE":
            refused = self._check_write(request)
            if refused:
                return refused
            self._consume(request.headers["Zotero-API-Key"])
            for key in (params.get("itemKey") or "").split(","):
                if key:
                    self.deleted.append(key)
                    self.created_items.pop(key, None)
            return httpx.Response(204, request=request)

        if path == "/api/users/0/collections" and method == "POST":
            refused = self._check_write(request)
            if refused:
                return refused
            self._consume(request.headers["Zotero-API-Key"])
            sent = json.loads(request.content)
            success = {}
            for index, collection in enumerate(sent):
                key = self._next_key("KJCOLL")
                self.created_collections[key] = collection
                self.data["collections"].append(
                    {"data": {"key": key, "name": collection["name"],
                              "parentCollection": collection.get("parentCollection", False)}}
                )
                self.data["top"].setdefault(key, [])
                success[str(index)] = key
            return httpx.Response(
                200, json={"success": success, "failed": {}}, request=request
            )

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

        if path.startswith("/api/users/0/items/") and path.count("/") == 5:
            key = path.split("/")[-1]
            if key in self.created_items:
                item = self.created_items[key]
                return self._json({"key": key, "version": item.get("version", 1),
                                   "data": item}, request)
            found = self._find(key)
            if found is not None:
                return self._json(found, request)
            return httpx.Response(404, text="not found", request=request)

        if path == "/api/users/0/items":
            if params.get("itemType") == "annotation":
                return self._json(self.data["annotations"], request)
            return httpx.Response(
                200, json=[],
                headers={**self.headers, "Last-Modified-Version": "42"},
                request=request,
            )

        return httpx.Response(404, text=f"no fixture for {path}", request=request)

    def _find(self, key: str) -> dict[str, Any] | None:
        """Any object in the fixture library, by key."""
        pools: list[Any] = [self.data["annotations"], self.data["collections"]]
        pools += list(self.data["top"].values())
        pools += list(self.data["children"].values())
        for pool in pools:
            for payload in pool:
                if payload.get("data", payload).get("key") == key:
                    data = payload.get("data", payload)
                    return {"key": key, "version": data.get("version", 1), "data": data}
        return None

    def _json(self, payload: Any, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers=self.headers, request=request)


@pytest.fixture
def fake_zotero() -> FakeZotero:
    return FakeZotero()


@pytest.fixture
def client(fake_zotero: FakeZotero):
    with fake_zotero.client() as c:
        yield c
