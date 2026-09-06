"""The HTTP surface for NotebookLM: prepare, register, link, paste back."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect

URL = "https://notebook.google.com/notebook/abc123def456"


@pytest.fixture
def api(tmp_path):
    def build(**kwargs):
        fake = FakeZotero(**kwargs)
        conn = connect(tmp_path / "apinb.sqlite3")
        app = create_app()
        app.dependency_overrides[deps.get_client] = fake.client
        app.dependency_overrides[deps.get_db] = lambda: conn
        client = TestClient(app)
        project = client.post(
            "/api/projects", json={"name": "p", "collection_key": "ROOT"}
        ).json()["project"]
        return fake, conn, client, project["id"]

    return build


def test_the_bundle_is_ready_to_paste(api):
    _fake, _conn, client, pid = api()
    body = client.get(f"/api/projects/{pid}/notebooks/bundle").json()
    assert body["scope"] == "project"
    assert body["urls_text"].splitlines() == [u["url"] for u in body["urls"]]
    assert body["card_count"] > 0
    assert "50 sources" in body["source_limit_note"]


def test_a_bad_address_is_refused_with_what_to_do(api):
    _fake, _conn, client, pid = api()
    response = client.post(
        f"/api/projects/{pid}/notebooks", json={"url": "https://example.com/notebook/x"}
    )
    assert response.status_code == 422
    assert "notebook.google.com" in response.json()["detail"]


def test_registering_then_linking_writes_one_note_and_reverts(api):
    fake, _conn, client, pid = api()
    notebook = client.post(
        f"/api/projects/{pid}/notebooks", json={"url": URL, "title": "Everything"}
    ).json()
    assert notebook["url"] == URL

    written = client.post(f"/api/projects/{pid}/notebooks/links", json={}).json()
    assert written["created"] == 1
    note = next(iter(fake.created_items.values()))
    assert URL in note["note"]

    listed = client.get(f"/api/projects/{pid}/notebooks").json()
    assert listed["notebooks"][0]["zotero_note_key"]
    assert "briefing" in listed["report_kinds"]

    taken_back = client.post(
        f"/api/projects/{pid}/batches/{written['batch_id']}/revert"
    ).json()
    assert taken_back["deleted"] == 1


def test_a_report_is_pasted_back_kept_and_filed(api):
    fake, _conn, client, pid = api()
    notebook = client.post(f"/api/projects/{pid}/notebooks", json={"url": URL}).json()
    report = client.post(
        f"/api/projects/{pid}/notebooks/{notebook['id']}/reports",
        json={"kind": "study_guide", "title": "How to read these", "text": "Start here."},
    ).json()
    assert report["kind"] == "study_guide"

    written = client.post(f"/api/projects/{pid}/notebooks/reports/notes", json={}).json()
    assert written["created"] == 1
    assert any("How to read these" in i["note"] for i in fake.created_items.values())

    assert client.get(f"/api/projects/{pid}/notebooks").json()["reports"][0][
        "zotero_note_key"
    ]
    assert (
        client.delete(
            f"/api/projects/{pid}/notebooks/reports/{report['id']}"
        ).status_code
        == 200
    )


def test_staging_reports_a_folder_even_when_there_is_nothing_to_stage(
    api, tmp_path, monkeypatch
):
    from zkj import config, notebooklm

    monkeypatch.setattr(
        notebooklm, "settings", config.Settings(db_path=str(tmp_path / "zkj.sqlite3"))
    )
    _fake, _conn, client, pid = api()
    body = client.post(f"/api/projects/{pid}/notebooks/stage").json()
    assert body["folder"].startswith(str(tmp_path))
    assert body["staged"] == 0 and body["failures"] == []


def test_a_notebook_for_an_unknown_source_is_refused(api):
    _fake, _conn, client, pid = api()
    response = client.post(
        f"/api/projects/{pid}/notebooks", json={"url": URL, "source_id": "nope"}
    )
    assert response.status_code == 422


def test_the_sources_list_names_them_the_way_the_researcher_would(api):
    """The notebook scope picker reads this, and nothing else needs to."""
    _fake, _conn, client, pid = api()
    sources = client.get(f"/api/projects/{pid}/sources").json()
    assert sources
    assert all(s["id"] and s["zotero_item_key"] for s in sources)
    assert any(s["citation"] for s in sources)


def test_a_notebook_can_be_scoped_to_a_source_from_that_list(api):
    fake, _conn, client, pid = api()
    source = client.get(f"/api/projects/{pid}/sources").json()[0]
    client.post(
        f"/api/projects/{pid}/notebooks", json={"url": URL, "source_id": source["id"]}
    )
    client.post(f"/api/projects/{pid}/notebooks/links", json={})
    note = next(iter(fake.created_items.values()))
    assert note["parentItem"] == source["zotero_item_key"]
