"""The HTTP surface the Cards screen talks to."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect


@pytest.fixture
def api(tmp_path, fake_zotero):
    conn = connect(tmp_path / "api.sqlite3")
    app = create_app()
    app.dependency_overrides[deps.get_client] = fake_zotero.client
    app.dependency_overrides[deps.get_db] = lambda: conn
    with TestClient(app) as c:
        yield c
    conn.close()


@pytest.fixture
def project(api):
    r = api.post("/api/projects", json={"name": "p", "collection_key": "ROOT"})
    assert r.status_code == 201, r.text
    return r.json()


def test_creating_a_project_imports_it(project):
    assert project["stats"]["quote_cards"] == 3
    assert project["project"]["counts"]["quotes"] == 3
    assert project["project"]["root_path"] == "Agentic Governance"


def test_a_duplicate_project_name_is_refused(api, project):
    r = api.post("/api/projects", json={"name": "p", "collection_key": "CH02"})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


def test_pointing_a_project_at_another_collection_is_refused(api, project):
    """Two collections merged into one project would corrupt the comparison."""
    conn_id = project["project"]["id"]
    r = api.post("/api/projects", json={"name": "p2", "collection_key": "CH02"})
    assert r.status_code == 201
    r = api.post(f"/api/projects/{conn_id}/import")
    assert r.status_code == 200  # same collection, so re-import is fine


def test_reimport_is_idempotent(api, project):
    pid = project["project"]["id"]
    before = api.get(f"/api/projects/{pid}/cards").json()["total"]
    r = api.post(f"/api/projects/{pid}/import")
    assert r.json()["stats"]["quote_cards"] == 0
    assert api.get(f"/api/projects/{pid}/cards").json()["total"] == before


def test_cards_carry_citation_locator_and_linked_ideas(api, project):
    pid = project["project"]["id"]
    body = api.get(f"/api/projects/{pid}/cards", params={"search": "oversight becomes"}).json()
    card = body["cards"][0]
    assert card["citation"] == "Smith 2025, p. 132"
    assert card["locator"] == {
        "type": "page", "value": "132", "source": "page_label",
        "estimated": False, "rendered": "p. 132", "estimated_page": None, "detail": {},
    }
    assert card["source"]["title"].startswith("Human oversight")
    assert card["linked_ideas"][0]["kind"] == "idea"


def test_filters_and_counts_come_back_together(api, project):
    pid = project["project"]["id"]
    body = api.get(f"/api/projects/{pid}/cards", params={"kind": "quote"}).json()
    assert body["total"] == 3
    assert body["counts"]["quotes_with_my_note"] == 1


def test_facets_are_offered_for_the_filter_bar(api, project):
    pid = project["project"]["id"]
    f = api.get(f"/api/projects/{pid}/facets").json()
    assert {v["value"] for v in f["kinds"]} == {"quote", "idea", "image"}
    assert f["sources"][0]["count"] >= 1


def test_a_card_can_be_labelled_and_excluded_but_never_rewritten(api, project):
    pid = project["project"]["id"]
    card = api.get(f"/api/projects/{pid}/cards").json()["cards"][0]
    r = api.patch(
        f"/api/projects/{pid}/cards/{card['id']}",
        json={"human_label": "the hinge", "status": "excluded", "text": "tampered"},
    )
    assert r.status_code == 200
    assert r.json()["human_label"] == "the hinge"
    assert r.json()["status"] == "excluded"
    assert r.json()["text"] == card["text"]  # the quotation is evidence


def test_patching_only_a_quotation_is_refused(api, project):
    pid = project["project"]["id"]
    card = api.get(f"/api/projects/{pid}/cards").json()["cards"][0]
    r = api.patch(f"/api/projects/{pid}/cards/{card['id']}", json={"text": "tampered"})
    assert r.status_code == 422


def test_unknown_project_and_card_are_404(api, project):
    pid = project["project"]["id"]
    assert api.get("/api/projects/nope/cards").status_code == 404
    assert api.get(f"/api/projects/{pid}/cards/nope").status_code == 404


def test_a_project_from_another_zotero_database_is_marked_unwritable(api, project, tmp_path):
    """Object versions are local to one database, so this must be visible."""
    pid = project["project"]["id"]
    app = create_app()
    other = FakeZotero(headers={"Zotero-API-Version": "3", "Zotero-Server-ID": "OTHER"})
    conn = connect(tmp_path / "api.sqlite3")
    app.dependency_overrides[deps.get_client] = other.client
    app.dependency_overrides[deps.get_db] = lambda: conn
    with TestClient(app) as c:
        body = c.get(f"/api/projects/{pid}").json()
    conn.close()
    assert body["writable_here"] is False
