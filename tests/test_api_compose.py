"""The HTTP surface for composing, exporting and pasting back."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect, now_iso


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


def test_prompt_availability_names_what_will_be_inferred(api):
    conn, client, pid = api
    state = client.get(f"/api/projects/{pid}/prompts").json()
    assert state["paper"]["ready"] is True
    assert state["paper"]["blocked_by"] is None
    assert "argument" in state["paper"]["infers"]
    # a section prompt still needs a section to be about
    assert state["section"]["ready"] is False
    assert state["section"]["blocked_by"] == "add a section first"


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


def test_a_section_with_nothing_assigned_still_exports(api):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    body = client.post(f"/api/projects/{pid}/prompts",
                       json={"kind": "section", "section_id": section["id"]}).json()
    assert body["chars"] > 100
    assert "No evidence is assigned" in body["note"]


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


def test_the_outline_exports_with_or_without_a_question(api):
    conn, client, pid = api
    without = client.post(f"/api/projects/{pid}/prompts", json={"kind": "outline"}).json()
    assert "No research question has been chosen" in without["content"]

    question = client.post(f"/api/projects/{pid}/questions",
                           json={"text": "Does capacity bind?"}).json()
    client.post(f"/api/projects/{pid}/questions/{question['id']}/choose")
    with_one = client.post(f"/api/projects/{pid}/prompts", json={"kind": "outline"}).json()
    assert "Research question (fixed): Does capacity bind?" in with_one["content"]


def test_the_whole_paper_can_be_built_drafted_and_checked(api):
    conn, client, pid = api
    prompt = client.post(f"/api/projects/{pid}/prompts", json={"kind": "paper"}).json()
    assert prompt["kind"] == "paper"
    assert prompt["id"]

    card = a_quote(client, pid)
    draft = (
        f'Oversight fails at the boundary. "{card["text"]}" '
        f'[[CITE:{card["human_id"]}]]. [EVIDENCE NEEDED: a case after 2020]'
    )
    body = client.post(f"/api/projects/{pid}/draft",
                       json={"content": draft, "prompt_export_id": prompt["id"]}).json()
    assert body["draft"]["version"] == 1
    assert body["validation"]["clean"] is True
    assert body["validation"]["evidence_needed"] == ["a case after 2020"]
    assert "[@smith2025, p. 132]" in body["markdown"]
    assert len(client.get(f"/api/projects/{pid}/drafts").json()) == 1

    paper = client.get(f"/api/projects/{pid}/paper.md").text
    assert "[@smith2025, p. 132]" in paper
    assert "the whole paper (draft v1)" in paper
    assert "a case after 2020" in paper


def test_claims_are_listed_under_the_question(api):
    conn, client, pid = api
    client.post(f"/api/projects/{pid}/claims",
                json={"text": "Capacity binds before law does.", "claim_type": "thesis"})
    claims = client.get(f"/api/projects/{pid}/claims").json()
    assert claims[0]["claim_type"] == "thesis"
    client.delete(f"/api/projects/{pid}/claims/{claims[0]['id']}")
    assert client.get(f"/api/projects/{pid}/claims").json() == []


def test_groups_can_be_adopted_and_reordered_over_http(api):
    conn, client, pid = api
    conn.execute(
        "UPDATE card SET kj_path = 'P/_KJ/Oversight', zotero_note_key = 'N', "
        "materialized_at = ? WHERE kind = 'quote'", (now_iso(),))
    body = client.post(f"/api/projects/{pid}/sections/adopt-groups").json()
    assert body["created"] == 1
    section = client.get(f"/api/projects/{pid}/sections").json()[0]
    assert section["evidence_count"] == 3

    other = client.post(f"/api/projects/{pid}/sections", json={"title": "Later"}).json()
    moved = client.post(f"/api/projects/{pid}/sections/{other['id']}/move?delta=-1").json()
    assert [s["title"] for s in moved][0] == "Later"


def test_the_paper_export_carries_the_evidence_when_nothing_is_drafted(api):
    conn, client, pid = api
    card = a_quote(client, pid)
    paper = client.get(f"/api/projects/{pid}/paper.md").text
    assert card["text"] in paper
    assert "not drafted yet" not in paper


def test_the_paper_prompt_takes_a_mode_and_a_quoting_choice(api):
    conn, client, pid = api
    draft = client.post(f"/api/projects/{pid}/prompts",
                        json={"kind": "paper"}).json()
    assert "prose in sections" in draft["content"]

    assess = client.post(f"/api/projects/{pid}/prompts",
                         json={"kind": "paper", "mode": "assess"}).json()
    assert "Do not draft anything" in assess["content"]

    ideas = client.post(f"/api/projects/{pid}/prompts",
                        json={"kind": "paper", "quoting": "ideas"}).json()
    assert "Do not quote." in ideas["content"]

    bad = client.post(f"/api/projects/{pid}/prompts",
                      json={"kind": "paper", "mode": "wing it"})
    assert bad.status_code == 422


def test_the_file_carries_the_task_unless_asked_not_to(api):
    conn, client, pid = api
    with_task = client.get(f"/api/projects/{pid}/paper.md").text
    assert "## What to do with this file" in with_task
    assert "prose in sections" in with_task

    without = client.get(f"/api/projects/{pid}/paper.md?instructions=false").text
    assert "## What to do with this file" not in without


def test_a_draft_that_wrote_through_a_gap_reports_where(api):
    conn, client, pid = api
    card = a_quote(client, pid)
    draft = (
        f'Oversight fails [[CITE:{card["human_id"]}]]. '
        f'[UNSUPPORTED: that this generalises to procurement]'
    )
    body = client.post(f"/api/projects/{pid}/draft", json={"content": draft}).json()
    assert body["validation"]["unsupported"] == [
        "that this generalises to procurement"
    ]
