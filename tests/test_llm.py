"""Sending a prompt to Claude — the part that is opt-in, and the part that isn't.

The rule these tests protect: turning the API on changes who does the pasting
and nothing else. The same prompt goes out and the same validator checks what
comes back.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import FakeZotero
from zkj import llm
from zkj.api import deps
from zkj.api.app import create_app
from zkj.store import connect


@pytest.fixture(autouse=True)
def no_ambient_key(monkeypatch):
    """These tests must not depend on the machine's own credentials."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    llm.set_session_key(None)
    yield
    llm.set_session_key(None)


@pytest.fixture
def api(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "llm.sqlite3")
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


# -- availability ---------------------------------------------------------


def test_with_no_credentials_it_says_what_would_make_one(api, monkeypatch):
    monkeypatch.setattr(llm, "_session_key", None)
    _conn, client, _pid = api
    body = client.get("/api/llm").json()
    if body["ready"]:
        pytest.skip("this machine has Anthropic credentials of its own")
    assert "No Anthropic credentials" in body["reason"]
    assert "ant auth login" in body["remedy"]
    assert "never written to disk" in body["remedy"]


def test_a_key_given_to_the_run_is_held_in_memory_only(api, tmp_path):
    conn, client, _pid = api
    body = client.put("/api/llm/key", json={"key": "sk-ant-not-a-real-key"}).json()
    assert body["ready"] is True
    assert body["source"] == "a key held in memory for this run only"

    # not in the database, under any column
    dumped = "".join(
        str(row) for row in conn.execute("SELECT * FROM sqlite_master").fetchall()
    )
    assert "sk-ant-not-a-real-key" not in dumped
    for table in ("project", "prompt_export", "draft", "write_auth"):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        assert "sk-ant-not-a-real-key" not in "".join(str(r) for r in rows)

    assert client.delete("/api/llm/key").json()["ready"] in (True, False)


def test_an_empty_key_is_refused_rather_than_stored(api):
    _conn, client, _pid = api
    response = client.put("/api/llm/key", json={"key": "   "})
    if response.status_code == 200:
        pytest.skip("this machine has Anthropic credentials of its own")
    assert response.status_code == 422
    assert llm.has_session_key() is False


# -- sending --------------------------------------------------------------


class FakeResult(llm.LLMResult):
    pass


def test_sending_runs_the_same_prompt_and_the_same_checks(api, monkeypatch):
    conn, client, pid = api
    card = a_quote(client, pid)
    sent: dict[str, str] = {}

    def fake_send(prompt: str, *, effort: str = "high") -> llm.LLMResult:
        sent["prompt"] = prompt
        sent["effort"] = effort
        return llm.LLMResult(
            text=f'They write "{card["text"]}" [[CITE:{card["human_id"]}]].',
            model="claude-opus-5",
            stop_reason="end_turn",
            input_tokens=2000,
            output_tokens=400,
        )

    monkeypatch.setattr(llm, "send", fake_send)
    body = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"}).json()

    # the prompt posted is the prompt the Copy button would have given
    built = client.post(
        f"/api/projects/{pid}/prompts", json={"kind": "paper", "store": False}
    ).json()
    assert sent["prompt"] == built["content"]

    assert body["validation"]["clean"] is True
    assert body["draft"]["version"] == 1
    assert "[@smith2025, p. 132]" in body["markdown"]
    assert body["llm"]["cost_usd"] == round((2000 * 5 + 400 * 25) / 1_000_000, 4)
    assert body["prompt"]["id"]


def test_what_comes_back_is_checked_exactly_as_a_pasted_draft_is(api, monkeypatch):
    conn, client, pid = api
    card = a_quote(client, pid)

    monkeypatch.setattr(
        llm,
        "send",
        lambda prompt, effort="high": llm.LLMResult(
            text=(
                f'They write "{card["text"].replace("increasingly", "impossibly")}" '
                f'[[CITE:{card["human_id"]}]]. And [[CITE:KJ-9999]].'
            ),
            model="claude-opus-5",
        ),
    )
    body = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"}).json()
    kinds = {f["kind"] for f in body["validation"]["findings"]}
    assert "quotation_altered" in kinds
    assert body["validation"]["unknown"] == ["KJ-9999"]
    assert body["validation"]["clean"] is False


def test_the_prompt_is_stored_even_when_the_send_fails(api, monkeypatch):
    """The researcher can still copy what would have gone out."""
    conn, client, pid = api

    def boom(prompt: str, *, effort: str = "high"):
        raise RuntimeError("Could not reach Anthropic.")

    monkeypatch.setattr(llm, "send", boom)
    response = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"})
    assert response.status_code == 502
    assert "Could not reach Anthropic" in response.json()["detail"]
    assert client.get(f"/api/projects/{pid}/prompt-exports").json()


def test_no_credentials_is_a_409_carrying_the_remedy(api, monkeypatch):
    conn, client, pid = api

    def unavailable(prompt: str, *, effort: str = "high"):
        raise llm.LLMUnavailable(
            llm.Availability(ready=False, reason="No credentials.", remedy="Do X.")
        )

    monkeypatch.setattr(llm, "send", unavailable)
    response = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"})
    assert response.status_code == 409
    assert response.json()["detail"]["remedy"] == "Do X."


def test_a_refusal_is_reported_rather_than_saved_as_a_draft(api, monkeypatch):
    conn, client, pid = api
    monkeypatch.setattr(
        llm,
        "send",
        lambda prompt, effort="high": llm.LLMResult(
            text="", model="claude-opus-5", stop_reason="refusal",
            refusal="Declined.",
        ),
    )
    body = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"}).json()
    assert body["llm"]["refusal"] == "Declined."
    assert body["draft"] is None
    assert client.get(f"/api/projects/{pid}/drafts").json() == []


def test_a_truncated_answer_says_so(api, monkeypatch):
    conn, client, pid = api
    monkeypatch.setattr(
        llm,
        "send",
        lambda prompt, effort="high": llm.LLMResult(
            text="A paper that stops mid-",
            model="claude-opus-5",
            stop_reason="max_tokens",
            warnings=["The answer hit the length limit and stops mid-thought."],
        ),
    )
    body = client.post(f"/api/projects/{pid}/send", json={"kind": "paper"}).json()
    assert "hit the length limit" in body["llm"]["warnings"][0]


def test_a_section_send_is_scoped_to_that_section(api, monkeypatch):
    conn, client, pid = api
    section = client.post(f"/api/projects/{pid}/sections", json={"title": "S"}).json()
    card = a_quote(client, pid)
    client.put(f"/api/projects/{pid}/sections/{section['id']}/evidence/{card['id']}",
               json={"citation_mode": "direct_quote"})
    monkeypatch.setattr(
        llm,
        "send",
        lambda prompt, effort="high": llm.LLMResult(
            text=f"A claim [[CITE:{card['human_id']}]].", model="claude-opus-5"
        ),
    )
    body = client.post(
        f"/api/projects/{pid}/send",
        json={"kind": "section", "section_id": section["id"]},
    ).json()
    assert body["validation"]["stats"]["scope"] == "section"
    assert len(
        client.get(f"/api/projects/{pid}/sections/{section['id']}/drafts").json()
    ) == 1
