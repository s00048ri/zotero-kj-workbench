"""Machine summaries: what can be sent, what comes back, and what it may become.

The load-bearing test in this file is the one that says a summary never turns
into a card. Everything else is about failing one attachment without losing
the batch.
"""

from __future__ import annotations

import base64
import json

import pytest

from tests.conftest import FakeZotero
from zkj import llm, summarise
from zkj.importer import run_import
from zkj.materialize import revert
from zkj.store import connect
from zkj.writes import WriteSession
from zkj.zotero.notes import GENERATED_TAG, SUMMARY_TAG

PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def fake_answer(text: str = "It argues one thing.\n\nAnd rests it on another.") -> llm.LLMResult:
    return llm.LLMResult(
        text=text,
        model="claude-opus-5",
        stop_reason="end_turn",
        input_tokens=1000,
        output_tokens=400,
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A project imported from the fake library, with ATT1 a real file on disk."""

    def build(*, pdf: bytes = PDF_BYTES, answer=None, on_disk: bool = True, **kwargs):
        pdf_path = tmp_path / "smith2025.pdf"
        pdf_path.write_bytes(pdf)

        fake = FakeZotero(**kwargs)
        if on_disk:
            fake.data["files"] = {**fake.data.get("files", {}), "ATT1": pdf_path.as_uri()}

        conn = connect(tmp_path / f"s{id(kwargs)}{len(kwargs)}.sqlite3")
        client = fake.client()
        project_id, _ = run_import(conn, client, "p", "ROOT")
        project = dict(
            conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
        )
        session = WriteSession(client, conn, sleep=lambda _s: None)

        sent: list = []

        def send_content(content, **kw):
            sent.append((content, kw))
            return (answer or fake_answer())

        monkeypatch.setattr(llm, "send_content", send_content)
        return fake, conn, client, project, session, sent, pdf_path

    return build


def only(conn, project, key="ATT1"):
    return next(
        a for a in summarise.attachments(conn, project["id"]) if a["attachment_key"] == key
    )


# -- what can be sent at all ----------------------------------------------


def test_it_says_which_attachments_it_could_read(setup):
    _fake, conn, _client, project, _s, _sent, _p = setup()
    by_key = {a["attachment_key"]: a for a in summarise.attachments(conn, project["id"])}
    assert by_key["ATT1"]["sendable"] is True
    # an EPUB is a book this app can locate passages in, not one it can send
    assert by_key["ATT2"]["sendable"] is False
    # a linked URL has no file at all
    assert by_key["ATT3"]["sendable"] is False


def test_an_attachment_with_no_file_on_disk_fails_alone(setup):
    """Zotero knows about the attachment; the file was never downloaded."""
    _fake, conn, client, project, _s, _sent, _p = setup(on_disk=False)
    result = summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    assert result.written == 0
    assert "never downloaded" in result.attempts[0].reason
    assert conn.execute("SELECT count(*) FROM summary").fetchone()[0] == 0


def test_one_bad_attachment_does_not_abandon_the_good_ones(setup):
    _fake, conn, client, project, _s, _sent, _p = setup()
    ids = [only(conn, project, "ATT2")["id"], only(conn, project, "ATT1")["id"]]
    result = summarise.summarise(conn, client, project, ids)
    assert [a.ok for a in result.attempts] == [False, True]
    assert result.written == 1


def test_a_file_too_large_for_one_request_is_refused_with_its_size(setup):
    _fake, conn, client, project, _s, _sent, _p = setup(
        pdf=b"%PDF-1.4\n" + b"0" * (summarise.MAX_PDF_BYTES + 1)
    )
    result = summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    assert result.written == 0
    assert "MB" in result.attempts[0].reason


# -- what is sent ----------------------------------------------------------


def test_a_pdf_goes_as_a_document_block_not_as_extracted_text(setup):
    _fake, conn, client, project, _s, sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    content, _kw = sent[0]
    document, instruction = content
    assert document["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(document["source"]["data"]) == PDF_BYTES
    assert instruction["type"] == "text"
    # the instruction comes after the document, and forbids inventing evidence
    assert "not in the document" not in instruction["text"]
    assert "Describe only what is in the document" in instruction["text"]


# -- what is kept ----------------------------------------------------------


def test_the_summary_is_stored_with_what_it_cost(setup):
    _fake, conn, client, project, _s, _sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    row = conn.execute("SELECT * FROM summary").fetchone()
    assert row["text"].startswith("It argues one thing.")
    assert row["model"] == "claude-opus-5"
    assert (row["input_tokens"], row["output_tokens"]) == (1000, 400)
    assert row["zotero_note_key"] is None  # nothing written to Zotero yet


def test_it_will_not_overwrite_a_summary_without_being_asked(setup):
    _fake, conn, client, project, _s, sent, _p = setup()
    attachment_id = only(conn, project)["id"]
    summarise.summarise(conn, client, project, [attachment_id])
    again = summarise.summarise(conn, client, project, [attachment_id])
    assert again.written == 0
    assert "replace" in again.attempts[0].reason
    assert len(sent) == 1  # and it did not pay to find that out


def test_replacing_drops_the_link_to_the_note_that_described_the_old_one(setup):
    fake, conn, client, project, session, _sent, _p = setup()
    attachment_id = only(conn, project)["id"]
    summarise.summarise(conn, client, project, [attachment_id])
    summarise.write_notes(conn, client, session, project)
    assert conn.execute("SELECT zotero_note_key FROM summary").fetchone()[0] is not None

    summarise.summarise(conn, client, project, [attachment_id], replace=True)
    row = conn.execute("SELECT * FROM summary").fetchone()
    assert row["zotero_note_key"] is None and row["written_at"] is None
    assert conn.execute("SELECT count(*) FROM summary").fetchone()[0] == 1
    # the old note is still in Zotero — deleting it is the researcher's call
    assert len(fake.created_items) == 1


# -- what it becomes in Zotero --------------------------------------------


def test_the_note_hangs_off_the_source_and_belongs_to_no_collection(setup):
    fake, conn, client, project, session, _sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    result = summarise.write_notes(conn, client, session, project)

    assert result.created == 1
    note = next(iter(fake.created_items.values()))
    # a child note of the source item cannot be filed, which is the point:
    # it can never land in _KJ/Inbox, where the grouping happens
    assert note["parentItem"] == "SRC1"
    assert "collections" not in note
    tags = {t["tag"] for t in note["tags"]}
    assert SUMMARY_TAG in tags and GENERATED_TAG in tags
    assert "Machine summary" in note["note"]
    assert "It argues one thing." in note["note"]


def test_a_summary_note_never_becomes_a_card(setup):
    """The rule the whole design turns on. A re-import must not read a machine
    summary back as though the researcher had written it."""
    fake, conn, client, project, session, _sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    summarise.write_notes(conn, client, session, project)
    note_key = conn.execute("SELECT zotero_note_key FROM summary").fetchone()[0]

    before = conn.execute("SELECT count(*) FROM card").fetchone()[0]
    # the note is now a child of SRC1, exactly as Zotero would report it
    fake.data["children"]["SRC1"].append(
        {"data": {**fake.created_items[note_key], "itemType": "note"}}
    )
    _project_id, stats = run_import(conn, client, "p", "ROOT")

    assert conn.execute("SELECT count(*) FROM card").fetchone()[0] == before
    assert stats.generated_notes_seen == 1
    assert not conn.execute(
        "SELECT 1 FROM card WHERE origin_key = ?", (f"note:{note_key}",)
    ).fetchone()


def test_a_batch_of_summaries_can_be_taken_back_whole(setup):
    fake, conn, client, project, session, _sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    written = summarise.write_notes(conn, client, session, project)

    taken_back = revert(conn, client, session, written.batch_id)

    assert taken_back.deleted == 1 and taken_back.failures == []
    assert fake.deleted
    row = conn.execute("SELECT * FROM summary").fetchone()
    # unwritten again — and still summarised, so writing it costs nothing more
    assert row["zotero_note_key"] is None and row["written_at"] is None
    assert row["text"]
    batch = conn.execute(
        "SELECT * FROM write_batch WHERE id = ?", (written.batch_id,)
    ).fetchone()
    assert batch["kind"] == "summaries"
    assert json.loads(batch["card_ids_json"]) == []


def test_forgetting_a_summary_leaves_the_note_alone(setup):
    fake, conn, client, project, session, _sent, _p = setup()
    summarise.summarise(conn, client, project, [only(conn, project)["id"]])
    summarise.write_notes(conn, client, session, project)
    summary_id = conn.execute("SELECT id FROM summary").fetchone()[0]

    summarise.forget(conn, project["id"], summary_id)

    assert conn.execute("SELECT count(*) FROM summary").fetchone()[0] == 0
    assert len(fake.created_items) == 1 and not fake.deleted
