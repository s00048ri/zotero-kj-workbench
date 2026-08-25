"""The HTTP surface for composing, exporting and pasting back."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect


@pytest.fixture
def api(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "api.sqlite3")
    app = create_app()
    app.dependency_overrides[deps.get_client] = fake.client
    app.dependency_overrides[deps.get_db] = lambda: conn
    client = TestClient(app)
    project = client.post(
        "/api/projects", json={"name": "p", "collection_key": "ROOT"}
    ).json()["project"]
    return conn, client, project["id"]


def a_quote(client, pid, needle="oversight becomes"):
    cards = client.get(f"/api/projects/{pid}/cards").json()["cards"]
    return next(c for c in cards if needle in c["text"])


def test_a_section_gathers_evidence_with_a_role_each(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections",
                          json={"title": "Institutional capacity"}).json()
    card = a_quote(client, pid)
    r = client.put(
        f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
        json={"citation_mode": "direct_quote", "argument_role": "evidence"},
    )
    assert r.status_code == 200
    evidence = client.get(f"/api/projects/{pid}/sections/{section['id']}/evidence").json()
    assert evidence[0]["citation_mode"] == "direct_quote"
    assert client.get(f"/api/projects/{pid}/sections").json()[0]["evidence_count"] == 1


def test_evidence_can_be_taken_out_again(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={})
    client.delete(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}")
    assert client.get(f"/api/projects/{pid}/sections/{section['id']}/evidence").json() == []


def test_prompt_availability_says_what_is_missing(api):
    conn, client, pid = api
    state = client.get(f"/api/projects/{pid}/prompts").json()
    assert state["section"]["ready"] is False
    assert state["section"]["blocked_by"] == "no section has evidence assigned"


def test_a_section_prompt_comes_back_with_its_size(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections",
                          json={"title": "S", "target_words": 1200}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={"citation_mode": "direct_quote"})
    body = client.post(f"/api/projects/{pid}/prompts",
                       json={"kind": "section", "section_id": section["id"]}).json()
    assert body["chars"] > 100
    assert body["tokens"] > 20
    assert body["id"]
    assert "Target length: 1200 words" in body["content"]
    assert client.get(f"/api/projects/{pid}/prompt-exports").json()[0]["id"] == body["id"]


def test_a_prompt_for_a_section_with_nothing_assigned_is_refused(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    r = client.post(f"/api/projects/{pid}/prompts",
                    json={"kind": "section", "section_id": section["id"]})
    assert r.status_code == 422
    assert "no evidence" in r.json()["detail"]


def test_pasting_a_draft_back_validates_and_versions_it(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={"citation_mode": "direct_quote"})

    draft = f'Smith writes that "{card["text"]}" [[CITE:{card["human_id"]}]].'
    body = client.post(f"/api/projects/{pid}/sections/{section['id']}/draft",
                       json={"content": draft}).json()
    assert body["validation"]["clean"] is True
    assert body["draft"]["version"] == 1
    assert "[@smith2025, p. 132]" in body["markdown"]

    again = client.post(f"/api/projects/{pid}/sections/{section['id']}/draft",
                        json={"content": draft}).json()
    assert again["draft"]["version"] == 2
    assert len(client.get(f"/api/projects/{pid}/sections/{section['id']}/drafts").json()) == 2


def test_a_bad_draft_comes_back_with_findings_and_can_be_checked_without_saving(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={"citation_mode": "direct_quote"})

    draft = 'It "says something else entirely here" [[CITE:KJ-9999]].'
    body = client.post(f"/api/projects/{pid}/sections/{section['id']}/draft",
                       json={"content": draft, "save": False}).json()
    assert body["draft"] is None
    assert body["validation"]["unknown"] == ["KJ-9999"]
    assert body["validation"]["clean"] is False
    assert client.get(f"/api/projects/{pid}/sections/{section['id']}/drafts").json() == []


def test_the_paper_export_is_plain_markdown(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "Capacity"}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={"citation_mode": "direct_quote"})
    client.post(f"/api/projects/{pid}/sections/{section['id']}/draft",
                json={"content": f'x "{card["text"]}" [[CITE:{card["human_id"]}]]'})
    r = client.get(f"/api/projects/{pid}/paper.md")
    assert r.headers["content-type"].startswith("text/plain")
    assert "## Capacity" in r.text
    assert "[@smith2025" in r.text


def test_choosing_a_question_unlocks_the_outline_prompt(api):
    conn, client, pid = api
    question = client.post(f"/api/projects/{pid}/questions",
                           json={"text": "Does capacity bind?"}).json()
    assert client.get(f"/api/projects/{pid}/prompts").json()["outline"]["ready"] is False
    client.post(f"/api/projects/{pid}/questions/{question['id']}/choose")
    assert client.get(f"/api/projects/{pid}/prompts").json()["outline"]["ready"] is True
    body = client.post(f"/api/projects/{pid}/prompts", json={"kind": "outline"}).json()
    assert "Does capacity bind?" in body["content"]


def test_claims_are_listed_under_the_question(api):
    conn, client, pid = api
    client.post(f"/api/projects/{pid}/claims",
                json={"text": "Capacity binds before law does.", "claim_type": "thesis"})
    claims = client.get(f"/api/projects/{pid}/claims").json()
    assert claims[0]["claim_type"] == "thesis"
    client.delete(f"/api/projects/{pid}/claims/{claims[0]['id']}")
    assert client.get(f"/api/projects/{pid}/claims").json() == []
