"""The HTTP surface for machine summaries: generate, list, file, forget."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj import llm
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect

PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


@pytest.fixture
def api(tmp_path, monkeypatch):
    def build(*, ready: bool = True, **kwargs):
        pdf = tmp_path / "smith2025.pdf"
        pdf.write_bytes(PDF_BYTES)

        fake = FakeZotero(**kwargs)
        fake.data["files"] = {**fake.data.get("files", {}), "ATT1": pdf.as_uri()}
        conn = connect(tmp_path / "apisum.sqlite3")
        app = create_app()
        app.dependency_overrides[deps.get_client] = fake.client
        app.dependency_overrides[deps.get_db] = lambda: conn
        client = TestClient(app)
        project = client.post(
            "/api/projects", json={"name": "p", "collection_key": "ROOT"}
        ).json()["project"]

        monkeypatch.setattr(
            llm,
            "availability",
            lambda: llm.Availability(
                ready=ready,
                reason="Ready." if ready else "No Anthropic credentials.",
                remedy=None if ready else "Run `ant auth login`.",
            ),
        )
        monkeypatch.setattr(
            llm,
            "send_content",
            lambda content, **kw: llm.LLMResult(
                text="It argues one thing.",
                model="claude-opus-5",
                stop_reason="end_turn",
                input_tokens=10,
                output_tokens=5,
            ),
        )
        return fake, conn, client, project["id"]

    return build


def sendable_id(client, pid):
    body = client.get(f"/api/projects/{pid}/attachments").json()
    return next(a["id"] for a in body["attachments"] if a["sendable"])


def test_it_lists_attachments_and_says_which_it_could_read(api):
    _fake, _conn, client, pid = api()
    body = client.get(f"/api/projects/{pid}/attachments").json()
    assert body["sendable"] >= 1
    assert body["summarised"] == 0
    assert body["llm"]["ready"] is True
    assert any(a["sendable"] is False for a in body["attachments"])


def test_without_credentials_it_says_so_rather_than_failing_per_file(api):
    _fake, _conn, client, pid = api(ready=False)
    body = client.get(f"/api/projects/{pid}/attachments").json()
    attachment_id = body["attachments"][0]["id"]
    response = client.post(
        f"/api/projects/{pid}/summaries", json={"attachment_ids": [attachment_id]}
    )
    assert response.status_code == 409
    assert "credentials" in response.json()["detail"]["reason"]


def test_generating_does_not_touch_zotero(api):
    fake, _conn, client, pid = api()
    body = client.post(
        f"/api/projects/{pid}/summaries", json={"attachment_ids": [sendable_id(client, pid)]}
    ).json()
    assert body["summarised"] == 1 and body["failed"] == 0
    assert fake.created_items == {}

    summaries = client.get(f"/api/projects/{pid}/summaries").json()
    assert summaries[0]["text"] == "It argues one thing."
    assert summaries[0]["zotero_note_key"] is None


def test_filing_is_a_separate_ask_and_shows_up_as_a_batch(api):
    fake, _conn, client, pid = api()
    client.post(
        f"/api/projects/{pid}/summaries", json={"attachment_ids": [sendable_id(client, pid)]}
    )
    written = client.post(f"/api/projects/{pid}/summaries/notes", json={}).json()
    assert written["created"] == 1
    assert len(fake.created_items) == 1

    batches = client.get(f"/api/projects/{pid}/batches").json()
    assert batches[0]["kind"] == "summaries" and batches[0]["notes"] == 1

    taken_back = client.post(
        f"/api/projects/{pid}/batches/{written['batch_id']}/revert"
    ).json()
    assert taken_back["deleted"] == 1
    assert client.get(f"/api/projects/{pid}/summaries").json()[0]["zotero_note_key"] is None


def test_forgetting_a_summary(api):
    _fake, _conn, client, pid = api()
    client.post(
        f"/api/projects/{pid}/summaries", json={"attachment_ids": [sendable_id(client, pid)]}
    )
    summary_id = client.get(f"/api/projects/{pid}/summaries").json()[0]["id"]
    assert client.delete(f"/api/projects/{pid}/summaries/{summary_id}").status_code == 200
    assert client.get(f"/api/projects/{pid}/summaries").json() == []
    assert client.delete(f"/api/projects/{pid}/summaries/{summary_id}").status_code == 404


def test_nothing_to_summarise_is_a_bad_request_not_an_empty_run(api):
    _fake, _conn, client, pid = api()
    response = client.post(f"/api/projects/{pid}/summaries", json={"attachment_ids": []})
    assert response.status_code == 422
