"""Writing cards into Zotero, and taking a batch back."""

from __future__ import annotations

import json

import pytest

from tests.conftest import FakeZotero
from zkj.importer import run_import
from zkj.materialize import materialize, pending_cards, revert
from zkj.store import connect
from zkj.writes import WriteSession, parse_write_result
from zkj.zotero.errors import ZoteroError
from zkj.zotero.notes import KJ_TAG, note_html


@pytest.fixture
def setup(tmp_path):
    """A project imported from a fake library, ready to materialise."""

    def build(**kwargs):
        fake = FakeZotero(**kwargs)
        conn = connect(tmp_path / f"m{len(kwargs)}{id(kwargs)}.sqlite3")
        client = fake.client()
        project_id, _ = run_import(conn, client, "p", "ROOT")
        project = dict(
            conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
        )
        session = WriteSession(client, conn, sleep=lambda _s: None)
        return fake, conn, client, project, session

    return build


# -- what gets written ----------------------------------------------------


def test_every_card_without_a_note_gets_one(setup):
    fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project)
    # 3 quotes + 1 comment + 1 child note; the standalone note is already
    # filable in Zotero and needs no second note, and images are not written
    assert result.created == 5
    assert result.failures == []
    assert len(fake.created_items) == 5


def test_a_card_derived_from_a_note_is_not_mistaken_for_a_filed_card(setup):
    """origin_note_key and zotero_note_key are different columns for a reason:
    conflating them silently excludes every note-derived card."""
    _fake, conn, _client, project, _session = setup()
    pending = {c["origin"] for c in pending_cards(conn, project["id"])}
    assert "child_note" in pending
    assert "standalone_note" not in pending


def test_notes_land_in_the_inbox_and_the_collections_are_created(setup):
    fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project)
    assert result.destinations == {"_KJ/Inbox": 5}
    names = [c["name"] for c in fake.created_collections.values()]
    assert names == ["_KJ", "Inbox"]
    row = conn.execute(
        "SELECT kj_root_key, kj_inbox_key FROM project WHERE id = ?", (project["id"],)
    ).fetchone()
    assert row["kj_root_key"] and row["kj_inbox_key"]


def test_existing_kj_collections_are_reused(setup):
    fake, conn, client, project, session = setup()
    materialize(conn, client, session, project)
    before = len(fake.created_collections)
    conn.execute("UPDATE card SET zotero_note_key = NULL")
    materialize(conn, client, session, project)
    assert len(fake.created_collections) == before  # not created a second time


def test_a_note_carries_its_citation_and_a_marker_that_survives_dragging(setup):
    fake, conn, client, project, session = setup()
    materialize(conn, client, session, project)
    note = next(
        item for item in fake.created_items.values() if "Smith 2025, p. 132" in item["note"]
    )
    assert KJ_TAG in [t["tag"] for t in note["tags"]]
    assert "kj:card=" in note["note"]
    assert "kj:origin=annotation:ANN1:quote" in note["note"]


def test_a_comment_note_names_the_quote_it_answers(setup):
    fake, conn, client, project, session = setup()
    materialize(conn, client, session, project)
    idea = next(
        item for item in fake.created_items.values() if "My reading of" in item["note"]
    )
    assert "kj:kind=idea" in idea["note"]


def test_note_rendering_escapes_user_text():
    card = {
        "human_id": "KJ-0001",
        "kind": "quote",
        "origin": "annotation_text",
        "origin_key": "annotation:A:quote",
        "text": "<script>alert(1)</script>",
    }
    html = note_html(card, project_name="p")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_materialising_twice_writes_nothing_the_second_time(setup):
    fake, conn, client, project, session = setup()
    materialize(conn, client, session, project)
    second = materialize(conn, client, session, project)
    assert second.created == 0
    assert len(fake.created_items) == 5


def test_a_dry_run_writes_nothing_at_all(setup):
    fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project, dry_run=True)
    assert result.created == 0
    assert len(result.preview) == 5
    assert fake.created_items == {}
    assert fake.created_collections == {}
    assert fake.dialogs == 0  # not even a permission dialog


def test_cards_can_be_chosen_one_by_one(setup):
    fake, conn, client, project, session = setup()
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM card WHERE kind = 'quote' LIMIT 2")]
    result = materialize(conn, client, session, project, card_ids=ids)
    assert result.created == 2
    assert len(fake.created_items) == 2


def test_a_card_set_aside_is_not_written(setup):
    fake, conn, client, project, session = setup()
    conn.execute("UPDATE card SET status = 'excluded' WHERE kind = 'quote'")
    result = materialize(conn, client, session, project)
    assert result.created == 2  # the comment and the child note only


# -- authorization semantics ---------------------------------------------


def test_always_allow_shows_one_dialog_for_the_whole_run(setup):
    fake, conn, client, project, session = setup(remember=True)
    materialize(conn, client, session, project)
    assert fake.dialogs == 1
    assert session.has_remembered_key


def test_a_remembered_key_survives_into_a_later_session(setup):
    fake, conn, client, project, session = setup(remember=True)
    materialize(conn, client, session, project)
    later = WriteSession(client, conn, sleep=lambda _s: None)
    assert later.has_remembered_key
    conn.execute("UPDATE card SET zotero_note_key = NULL")
    materialize(conn, client, later, project)
    assert fake.dialogs == 1  # still only the one


def test_a_single_use_key_is_re_requested_without_losing_cards(setup):
    """Pressing "Allow" rather than "Always Allow" must still complete."""
    fake, conn, client, project, session = setup(remember=False, dialog_limit=50)
    result = materialize(conn, client, session, project)
    assert result.created == 5
    assert result.failures == []
    assert fake.dialogs > 1  # one per write, as Zotero requires


def test_a_single_use_run_paces_itself_under_the_dialog_limit(setup):
    """Zotero shows at most five dialogs a minute; the session waits rather
    than letting the sixth request fail."""
    slept: list[float] = []
    fake = FakeZotero(remember=False)
    conn = connect(":memory:")
    client = fake.client()
    project_id, _ = run_import(conn, client, "p", "ROOT")
    project = dict(conn.execute("SELECT * FROM project").fetchone())
    session = WriteSession(client, conn, sleep=slept.append)
    materialize(conn, client, session, project)
    assert slept, "a single-use run must pace its permission dialogs"
    assert all(s <= 13.0 for s in slept)


def test_writes_are_refused_when_the_key_is_denied(setup):
    _fake, conn, client, project, session = setup(deny_writes=True)
    with pytest.raises(ZoteroError):
        materialize(conn, client, session, project)


def test_a_partial_failure_reports_which_cards_and_saves_the_rest(setup):
    fake, conn, client, project, session = setup(fail_indexes={1})
    result = materialize(conn, client, session, project)
    assert result.created == 4
    assert len(result.failures) == 1
    assert "refused by fixture" in result.failures[0]["error"]
    failed_id = result.failures[0]["human_id"]
    still_pending = [c["human_id"] for c in pending_cards(conn, project["id"])]
    assert still_pending == [failed_id]


def test_the_multi_object_response_shapes_are_both_understood():
    ok, errors = parse_write_result({"success": {"0": "AAA"}}, 1)
    assert ok == {0: "AAA"} and errors == {}
    ok, errors = parse_write_result(
        {"successful": {"0": {"key": "BBB", "data": {"key": "BBB"}}}}, 1
    )
    assert ok == {0: "BBB"}
    # a card Zotero says nothing about is a failure, not a success
    ok, errors = parse_write_result({"success": {}}, 2)
    assert ok == {} and set(errors) == {0, 1}


# -- taking it back -------------------------------------------------------


def test_a_batch_can_be_taken_back_whole(setup):
    fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project)
    assert result.batch_id

    reverted = revert(conn, client, session, result.batch_id)
    assert reverted.deleted == 5
    assert len(fake.deleted) == 5
    assert fake.created_items == {}
    # materialized_at is the marker for "this tool filed it". The standalone
    # note card keeps its key: that note is the researcher's own and predates
    # anything written here.
    assert conn.execute(
        "SELECT COUNT(*) FROM card WHERE materialized_at IS NOT NULL"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT zotero_note_key FROM card WHERE origin = 'standalone_note'"
    ).fetchone()[0] == "NOTE9"
    # and the cards are available to write again
    assert len(pending_cards(conn, project["id"])) == 5


def test_reverting_twice_is_refused(setup):
    _fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project)
    revert(conn, client, session, result.batch_id)
    with pytest.raises(ZoteroError, match="already been taken back"):
        revert(conn, client, session, result.batch_id)


def test_reverting_tolerates_a_note_the_researcher_already_deleted(setup):
    fake, conn, client, project, session = setup()
    result = materialize(conn, client, session, project)
    gone = json.loads(
        conn.execute("SELECT note_keys_json FROM write_batch").fetchone()[0]
    )[0]
    fake.created_items.pop(gone)

    reverted = revert(conn, client, session, result.batch_id)
    assert reverted.already_gone == 1
    assert reverted.deleted == 4
    assert reverted.failures == []


def test_a_revert_only_touches_its_own_batch(setup):
    fake, conn, client, project, session = setup()
    first = materialize(conn, client, session, project, card_ids=[
        r["id"] for r in conn.execute("SELECT id FROM card WHERE kind='quote' LIMIT 1")
    ])
    second = materialize(conn, client, session, project)
    assert second.created == 4

    revert(conn, client, session, first.batch_id)
    assert len(fake.created_items) == 4
    assert conn.execute(
        "SELECT COUNT(*) FROM card WHERE materialized_at IS NOT NULL"
    ).fetchone()[0] == 4
