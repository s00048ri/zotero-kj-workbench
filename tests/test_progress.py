"""Which step of the loop the project is on.

These exist because a researcher granted write permission and then went
straight to re-reading, with nothing written to read back. The app knew every
one of those facts and showed none of them.
"""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj.importer import run_import
from zkj.materialize import materialize
from zkj.progress import progress
from zkj.store import connect, now_iso
from zkj.writes import WriteSession


@pytest.fixture
def setup(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "p.sqlite3")
    client = fake.client()
    project_id, _ = run_import(conn, client, "p", "ROOT")
    project = dict(conn.execute("SELECT * FROM project").fetchone())
    session = WriteSession(client, conn, sleep=lambda _s: None)
    return fake, conn, client, project, session


def step(result, key):
    return next(s for s in result.steps if s.key == key)


def test_a_fresh_import_asks_for_notes_next(setup):
    _fake, conn, _client, project, _session = setup
    result = progress(conn, project)
    assert result.current == "notes"
    assert step(result, "read").done is True
    assert step(result, "notes").done is False
    assert step(result, "notes").count == 5


def test_the_notes_step_says_why_it_exists(setup):
    """A highlight belongs to a PDF; only a note can go in a collection."""
    _fake, conn, _client, project, _session = setup
    assert "collection" in step(progress(conn, project), "notes").detail


def test_after_writing_notes_the_next_step_is_sorting_in_zotero(setup):
    _fake, conn, client, project, session = setup
    materialize(conn, client, session, project)
    result = progress(conn, project)
    assert result.current == "sort"
    assert step(result, "notes").done is True
    assert "Sort them in Zotero" in step(result, "sort").detail


def test_sorting_is_not_reported_as_done_until_a_card_is_in_a_group(setup):
    _fake, conn, client, project, session = setup
    materialize(conn, client, session, project)
    # a re-import has seen the notes, but they are all still in Inbox
    conn.execute(
        "UPDATE card SET kj_collection_keys_json = '[\"INBOX\"]' "
        "WHERE materialized_at IS NOT NULL"
    )
    result = progress(conn, project)
    assert result.current == "sort"
    assert step(result, "sort").done is False
    assert "waiting in _KJ/Inbox" in step(result, "sort").detail


def test_once_cards_are_grouped_the_next_step_is_writing_labels(setup):
    _fake, conn, client, project, session = setup
    materialize(conn, client, session, project)
    conn.execute(
        "UPDATE card SET kj_path = 'P/_KJ/Theme', kj_collection_keys_json = '[\"T\"]' "
        "WHERE kind = 'quote' AND materialized_at IS NOT NULL"
    )
    result = progress(conn, project)
    assert result.current == "label"
    assert step(result, "sort").done is True
    assert step(result, "label").count == 1


def test_a_project_with_no_cards_asks_to_be_read(tmp_path):
    conn = connect(tmp_path / "empty.sqlite3")
    from zkj.store import insert

    project_id = insert(
        conn, "project",
        {"name": "empty", "root_collection_key": "ROOT", "created_at": now_iso()},
    )
    project = dict(
        conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    )
    result = progress(conn, project)
    assert result.current == "read"
    assert step(result, "read").done is False


def test_progress_carries_the_keys_needed_to_open_zotero(setup):
    _fake, conn, client, project, session = setup
    materialize(conn, client, session, project)
    project = dict(
        conn.execute("SELECT * FROM project WHERE id = ?", (project["id"],)).fetchone()
    )
    result = progress(conn, project)
    assert result.kj_inbox_key
    assert result.kj_root_key
