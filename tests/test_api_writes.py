"""The HTTP surface for writing, grouping and comparing."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect, now_iso


@pytest.fixture
def api(tmp_path):
    def build(**kwargs):
        fake = FakeZotero(**kwargs)
        conn = connect(tmp_path / "api.sqlite3")
        app = create_app()
        app.dependency_overrides[deps.get_client] = fake.client
        app.dependency_overrides[deps.get_db] = lambda: conn
        client = TestClient(app)
        project = client.post(
            "/api/projects", json={"name": "p", "collection_key": "ROOT"}
        ).json()["project"]
        return fake, conn, client, project["id"]

    return build


def test_permission_steers_towards_always_allow(api):
    _fake, _conn, client, _pid = api()
    body = client.get("/api/write-permission").json()
    assert body["available"] is True
    assert body["remembered"] is False
    assert "Always Allow" in body["message"]
    assert "five dialogs a minute" in body["message"]


def test_permission_is_remembered_once_granted(api):
    _fake, _conn, client, _pid = api(remember=True)
    assert client.post("/api/write-permission").json()["remembered"] is True
    assert client.get("/api/write-permission").json()["remembered"] is True
    assert client.delete("/api/write-permission").json()["remembered"] is False


def test_an_old_zotero_says_so_rather_than_failing(api):
    _fake, _conn, client, _pid = api(headers={"Zotero-API-Version": "3"})
    body = client.get("/api/write-permission").json()
    assert body["available"] is False
    assert "cannot accept notes" in body["message"]


def test_a_dry_run_reports_what_would_happen(api):
    fake, _conn, client, pid = api()
    body = client.post(f"/api/projects/{pid}/notes", json={"dry_run": True}).json()
    assert body["created"] == 0
    assert body["destinations"] == {"_KJ/Inbox": 5}
    assert len(body["preview"]) == 5
    assert fake.created_items == {}


def test_creating_notes_reports_where_they_went(api):
    fake, _conn, client, pid = api()
    assert client.get(f"/api/projects/{pid}/pending").json()["count"] == 5
    body = client.post(f"/api/projects/{pid}/notes", json={}).json()
    assert body["created"] == 5
    assert body["failures"] == []
    assert body["batch_id"]
    assert client.get(f"/api/projects/{pid}/pending").json()["count"] == 0


def test_a_batch_is_listed_and_can_be_taken_back(api):
    fake, _conn, client, pid = api()
    batch_id = client.post(f"/api/projects/{pid}/notes", json={}).json()["batch_id"]
    listed = client.get(f"/api/projects/{pid}/batches").json()
    assert listed[0]["id"] == batch_id
    assert listed[0]["notes"] == 5
    assert listed[0]["reverted_at"] is None

    reverted = client.post(f"/api/projects/{pid}/batches/{batch_id}/revert").json()
    assert reverted["deleted"] == 5
    assert client.get(f"/api/projects/{pid}/batches").json()[0]["reverted_at"]
    assert client.post(
        f"/api/projects/{pid}/batches/{batch_id}/revert"
    ).status_code == 409


def test_writing_into_another_zotero_database_is_refused(api, tmp_path):
    _fake, conn, _client, pid = api()
    other = FakeZotero(headers={"Zotero-API-Version": "3", "Zotero-Server-ID": "OTHER"})
    app = create_app()
    app.dependency_overrides[deps.get_client] = other.client
    app.dependency_overrides[deps.get_db] = lambda: conn
    with TestClient(app) as client:
        response = client.post(f"/api/projects/{pid}/notes", json={})
    assert response.status_code == 409
    assert "different Zotero database" in response.json()["detail"]


# -- groups ---------------------------------------------------------------


def file_two_cards(conn, pid, path="Agentic Governance/_KJ/Oversight"):
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM card WHERE project_id = ? AND kind = 'quote' LIMIT 2", (pid,))]
    for card_id in ids:
        conn.execute(
            "UPDATE card SET kj_path = ?, zotero_note_key = 'N', materialized_at = ? "
            "WHERE id = ?", (path, now_iso(), card_id))
    return path


def test_groups_report_their_members_and_what_is_still_unsorted(api):
    _fake, conn, client, pid = api()
    client.post(f"/api/projects/{pid}/notes", json={})
    path = file_two_cards(conn, pid)
    body = client.get(f"/api/projects/{pid}/groups").json()
    assert [g["path"] for g in body["groups"]] == [path]
    assert body["groups"][0]["size"] == 2
    assert body["summary"]["ungrouped"] == 3


def test_a_label_is_written_and_can_be_rewritten(api):
    _fake, conn, client, pid = api()
    path = file_two_cards(conn, pid)
    first = client.put(
        f"/api/projects/{pid}/groups/label",
        json={"path": path, "label": "Oversight is organisational."},
    ).json()
    second = client.put(
        f"/api/projects/{pid}/groups/label",
        json={"path": path, "label": "Better sentence."},
    ).json()
    assert first["id"] == second["id"]
    body = client.get(f"/api/projects/{pid}/groups").json()
    assert body["groups"][0]["label"]["text"] == "Better sentence."
    assert body["summary"]["labelled"] == 1


def test_an_empty_label_is_refused(api):
    _fake, conn, client, pid = api()
    path = file_two_cards(conn, pid)
    response = client.put(
        f"/api/projects/{pid}/groups/label", json={"path": path, "label": "  "}
    )
    assert response.status_code == 422


def test_labels_are_filed_in_zotero_beside_their_own_group(api):
    fake, conn, client, pid = api()
    path = file_two_cards(conn, pid)
    # the collection has to exist in the project for the label to land in it
    conn.execute(
        "INSERT INTO collection (id, project_id, zotero_collection_key, name, path, depth) "
        "VALUES ('c1', ?, 'THEME1', 'Oversight', ?, 2)", (pid, path))
    client.put(
        f"/api/projects/{pid}/groups/label",
        json={"path": path, "label": "Oversight is organisational."},
    )
    body = client.post(f"/api/projects/{pid}/groups/push").json()
    assert body["created"] == 1
    assert body["destinations"] == {path: 1}
    note = next(iter(fake.created_items.values()))
    assert note["collections"] == ["THEME1"]
    assert "Label for:" in note["note"]


# -- my note --------------------------------------------------------------


def test_writing_my_note_creates_a_linked_idea_card(api):
    fake, conn, client, pid = api()
    quote = next(
        c for c in client.get(f"/api/projects/{pid}/cards").json()["cards"]
        if c["kind"] == "quote" and not c["linked_ideas"]
    )
    body = client.put(
        f"/api/projects/{pid}/cards/{quote['id']}/my-note",
        json={"text": "Oversight is organisational."},
    ).json()
    assert body["kind"] == "idea"
    assert body["parent"]["id"] == quote["id"]
    assert fake.updated[0]["annotationComment"] == "Oversight is organisational."


def test_replacing_an_existing_zotero_comment_needs_a_second_answer(api):
    fake, conn, client, pid = api()
    quote = next(
        c for c in client.get(f"/api/projects/{pid}/cards").json()["cards"]
        if c["linked_ideas"]
    )
    refused = client.put(
        f"/api/projects/{pid}/cards/{quote['id']}/my-note",
        json={"text": "A different reading."},
    )
    assert refused.status_code == 409
    assert "hinge" in refused.json()["detail"]["existing"]
    assert fake.updated == []

    accepted = client.put(
        f"/api/projects/{pid}/cards/{quote['id']}/my-note",
        json={"text": "A different reading.", "overwrite": True},
    )
    assert accepted.status_code == 200


def test_a_note_can_stay_out_of_zotero(api):
    fake, conn, client, pid = api()
    quote = next(
        c for c in client.get(f"/api/projects/{pid}/cards").json()["cards"]
        if c["kind"] == "quote" and not c["linked_ideas"]
    )
    client.put(
        f"/api/projects/{pid}/cards/{quote['id']}/my-note",
        json={"text": "Kept here.", "push_to_zotero": False},
    )
    assert fake.updated == []


# -- structure ------------------------------------------------------------


def test_structure_says_plainly_when_there_is_too_little_to_compare(api):
    _fake, _conn, client, pid = api()
    response = client.get(f"/api/projects/{pid}/structure")
    assert response.status_code == 422
    assert "cards" in response.json()["detail"]


def test_progress_names_the_next_step(api):
    _fake, _conn, client, pid = api()
    body = client.get(f"/api/projects/{pid}/progress").json()
    assert body["current"] == "notes"
    assert [s["key"] for s in body["steps"]] == [
        "read", "notes", "sort", "label", "compare", "question", "write"
    ]
    assert body["counts"]["pending_notes"] == 5


def test_progress_moves_on_once_the_notes_exist(api):
    _fake, _conn, client, pid = api()
    client.post(f"/api/projects/{pid}/notes", json={})
    body = client.get(f"/api/projects/{pid}/progress").json()
    assert body["current"] == "sort"
    assert body["kj_inbox_key"]


def test_a_project_can_be_forgotten_without_touching_zotero(api):
    fake, _conn, client, pid = api()
    client.post(f"/api/projects/{pid}/notes", json={})
    body = client.delete(f"/api/projects/{pid}").json()
    assert body["deleted"] is True
    assert body["notes_left_in_zotero"] == 5
    assert len(fake.created_items) == 5  # still in Zotero, as promised
    assert client.get(f"/api/projects/{pid}").status_code == 404
