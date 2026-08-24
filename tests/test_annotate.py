"""Writing your own note on a passage, and keeping Zotero in step. §12 / M5."""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj.annotate import CommentConflict, write_my_note
from zkj.importer import run_import
from zkj.store import connect
from zkj.writes import WriteSession


@pytest.fixture
def project(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "n.sqlite3")
    client = fake.client()
    project_id, _ = run_import(conn, client, "p", "ROOT")
    session = WriteSession(client, conn, sleep=lambda _s: None)
    return fake, conn, client, project_id, session


def quote_without_a_note(conn):
    return dict(
        conn.execute(
            "SELECT * FROM card WHERE kind = 'quote' AND id NOT IN "
            "(SELECT parent_card_id FROM card WHERE parent_card_id IS NOT NULL) "
            "LIMIT 1"
        ).fetchone()
    )


def test_a_note_becomes_an_idea_card_linked_to_the_passage(project):
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    idea = write_my_note(
        conn, pid, quote["id"], "Oversight is organisational, not individual.",
        client=client, session=session,
    )
    assert idea["kind"] == "idea"
    assert idea["origin"] == "annotation_comment"
    assert idea["parent_card_id"] == quote["id"]
    assert idea["text"].startswith("Oversight is organisational")


def test_the_note_is_written_back_into_the_zotero_annotation(project):
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    idea = write_my_note(
        conn, pid, quote["id"], "My reading.", client=client, session=session
    )
    assert idea["pushed_to_zotero"] is True
    assert fake.updated == [
        {"key": "ANN6", "version": 7, "annotationComment": "My reading."}
    ]
    # and nothing else about the annotation was sent
    assert "annotationText" not in fake.updated[0]


def test_the_local_copy_of_the_annotation_agrees_afterwards(project):
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    write_my_note(conn, pid, quote["id"], "My reading.", client=client, session=session)
    assert conn.execute(
        "SELECT comment_raw FROM annotation WHERE zotero_annotation_key = 'ANN6'"
    ).fetchone()[0] == "My reading."


def test_rewriting_a_note_updates_the_same_card(project):
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    first = write_my_note(conn, pid, quote["id"], "First.", client=client, session=session)
    second = write_my_note(conn, pid, quote["id"], "Second.", client=client, session=session)
    assert first["id"] == second["id"]
    assert second["text"] == "Second."
    assert conn.execute(
        "SELECT COUNT(*) FROM card WHERE parent_card_id = ?", (quote["id"],)
    ).fetchone()[0] == 1


def test_an_existing_zotero_comment_is_never_replaced_silently(project):
    """The researcher wrote that comment in Zotero; it is not ours to discard."""
    fake, conn, client, pid, session = project
    quote = dict(
        conn.execute(
            "SELECT * FROM card WHERE origin_key = 'annotation:ANN1:quote'"
        ).fetchone()
    )
    with pytest.raises(CommentConflict) as excinfo:
        write_my_note(conn, pid, quote["id"], "Something else.",
                      client=client, session=session)
    assert "hinge of my argument" in excinfo.value.existing
    assert fake.updated == []


def test_replacing_it_is_possible_once_asked_for(project):
    fake, conn, client, pid, session = project
    quote = dict(
        conn.execute(
            "SELECT * FROM card WHERE origin_key = 'annotation:ANN1:quote'"
        ).fetchone()
    )
    idea = write_my_note(conn, pid, quote["id"], "Something else.",
                         client=client, session=session, overwrite=True)
    assert idea["text"] == "Something else."
    assert fake.updated[0]["annotationComment"] == "Something else."


def test_a_note_can_be_kept_here_only(project):
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    idea = write_my_note(conn, pid, quote["id"], "Private for now.",
                         client=client, session=session, push_to_zotero=False)
    assert idea["pushed_to_zotero"] is False
    assert fake.updated == []
    assert idea["text"] == "Private for now."


def test_a_card_with_no_annotation_still_takes_a_note(project):
    """A standalone note has no highlight behind it, so there is nothing to
    write back — but the researcher can still answer it."""
    fake, conn, client, pid, session = project
    card = dict(
        conn.execute("SELECT * FROM card WHERE origin = 'standalone_note'").fetchone()
    )
    idea = write_my_note(conn, pid, card["id"], "A reply to myself.",
                         client=client, session=session)
    assert idea["origin"] == "manual"
    assert idea["parent_card_id"] == card["id"]
    assert fake.updated == []


def test_an_empty_note_is_refused(project):
    _fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    with pytest.raises(ValueError):
        write_my_note(conn, pid, quote["id"], "   ", client=client, session=session)


def test_a_note_written_here_survives_the_next_import(project):
    """The origin key matches what an import derives, so the card is updated
    rather than duplicated."""
    fake, conn, client, pid, session = project
    quote = quote_without_a_note(conn)
    write_my_note(conn, pid, quote["id"], "My reading.", client=client, session=session)

    # Zotero now holds the comment, so the next import sees it
    for annotation in fake.data["annotations"]:
        if annotation["data"]["key"] == "ANN6":
            annotation["data"]["annotationComment"] = "My reading."
    run_import(conn, client, "p", "ROOT")

    assert conn.execute(
        "SELECT COUNT(*) FROM card WHERE origin_key = 'annotation:ANN6:idea'"
    ).fetchone()[0] == 1
    assert conn.execute(
        "SELECT parent_card_id FROM card WHERE origin_key = 'annotation:ANN6:idea'"
    ).fetchone()[0] == quote["id"]
