"""The status surface must tell the truth in every failure mode."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app


def app_with(fake: FakeZotero) -> TestClient:
    app = create_app()
    app.dependency_overrides[deps.get_client] = fake.client
    return TestClient(app)


def test_status_reports_a_working_zotero(fake_zotero):
    with app_with(fake_zotero) as c:
        s = c.get("/api/status").json()
    assert s["reachable"] and s["permitted"]
    assert s["writes_available"] is True
    assert s["collection_count"] == 6
    assert s["server_id"] == "TESTSERVER01"


def test_status_explains_a_closed_zotero():
    with app_with(FakeZotero(unreachable=True)) as c:
        r = c.get("/api/status")
    s = r.json()
    assert r.status_code == 200  # the status endpoint reports, it does not fail
    assert s["reachable"] is False
    assert "Start Zotero" in s["remedy"]


def test_status_explains_the_disabled_preference():
    with app_with(FakeZotero(forbidden=True)) as c:
        s = c.get("/api/status").json()
    assert s["permitted"] is False
    assert "Allow other applications" in s["remedy"]


def test_status_says_plainly_that_an_old_zotero_cannot_take_notes():
    fake = FakeZotero(headers={"Zotero-API-Version": "3"})
    with app_with(fake) as c:
        s = c.get("/api/status").json()
    assert s["writes_available"] is False
    assert "not written back" in s["message"]
    assert "Zotero 10" in s["remedy"]


def test_collection_tree_is_nested_with_paths(fake_zotero):
    with app_with(fake_zotero) as c:
        roots = c.get("/api/collections").json()
    root = next(r for r in roots if r["key"] == "ROOT")
    assert [ch["name"] for ch in root["children"]] == ["02 Oversight", "03 Capacity"]
    assert root["children"][1]["children"][0]["path"].endswith("03 Capacity/03a Fiscal")


def test_preview_counts_what_an_import_would_find(fake_zotero):
    with app_with(fake_zotero) as c:
        p = c.get("/api/collections/ROOT/preview").json()
    assert p["counts"]["sources"] == 3
    assert p["counts"]["highlights"] == 3
    assert p["counts"]["comments"] == 1
    assert p["sources_without_annotations"] == 1
    assert len(p["sample_highlights"]) == 3


def test_preview_of_an_unknown_collection_is_a_readable_error(fake_zotero):
    with app_with(fake_zotero) as c:
        r = c.get("/api/collections/NOPE/preview")
    assert r.status_code == 502
    assert "NOPE" in r.json()["message"]


def test_index_page_is_served(fake_zotero):
    with app_with(fake_zotero) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert "Zotero KJ Workbench" in r.text
