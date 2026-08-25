"""Import: the acceptance tests of §12.1–6, plus the edits an import must not eat."""

from __future__ import annotations

import copy

import pytest

from tests.conftest import FakeZotero, load_fixture
from tests.test_locators import make_epub
from zkj.importer import ProjectConflict, run_import
from zkj.store import connect


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "cards.sqlite3")
    yield conn
    conn.close()


@pytest.fixture
def imported(db, client):
    project_id, stats = run_import(db, client, "agentic-governance", "ROOT")
    return project_id, stats


def cards(db, project_id, **where):
    sql = "SELECT * FROM card WHERE project_id = ?"
    params = [project_id]
    for key, value in where.items():
        sql += f" AND {key} = ?"
        params.append(value)
    return [dict(r) for r in db.execute(sql + " ORDER BY human_id", params)]


def card_by_origin(db, project_id, origin_key):
    return dict(
        db.execute(
            "SELECT * FROM card WHERE project_id = ? AND origin_key = ?",
            (project_id, origin_key),
        ).fetchone()
    )


# -- §12.1 the whole chain resolves ---------------------------------------


def test_one_card_per_text_annotation_with_the_chain_intact(db, imported):
    project_id, stats = imported
    quotes = cards(db, project_id, kind="quote")
    assert len(quotes) == 3  # ANN1, ANN4, ANN6; ANN2 is an image, ANN3 is empty
    assert stats.skipped_empty == 1

    card = card_by_origin(db, project_id, "annotation:ANN1:quote")
    chain = db.execute(
        "SELECT s.title, s.creators_short, a.zotero_annotation_key, at.content_type "
        "FROM card c "
        "JOIN annotation a ON a.id = c.annotation_id "
        "JOIN attachment at ON at.id = a.attachment_id "
        "JOIN source s ON s.id = c.source_id WHERE c.id = ?",
        (card["id"],),
    ).fetchone()
    assert chain["creators_short"] == "Smith"
    assert chain["zotero_annotation_key"] == "ANN1"
    assert chain["content_type"] == "application/pdf"


def test_quote_text_is_cleaned_and_the_original_kept(db, imported):
    project_id, _ = imported
    card = card_by_origin(db, project_id, "annotation:ANN1:quote")
    assert "difficult as autonomous" in card["text"]
    assert "dif-\n" in card["text_raw"]


def test_image_annotations_become_placeholder_cards(db, imported):
    project_id, _ = imported
    images = cards(db, project_id, kind="image")
    assert [c["origin_key"] for c in images] == ["annotation:ANN2:image"]
    assert images[0]["text"] == "[image annotation]"


def test_cards_carry_the_locator_they_earned(db, imported):
    project_id, _ = imported
    pdf = card_by_origin(db, project_id, "annotation:ANN1:quote")
    assert (pdf["locator_type"], pdf["locator_value"], pdf["locator_source"]) == (
        "page", "132", "page_label",
    )
    roman = card_by_origin(db, project_id, "annotation:ANN6:quote")
    assert roman["locator_value"] == "iv"  # a displayed label, not an index


def test_prior_structure_is_the_deepest_collection(db, imported):
    project_id, _ = imported
    fiscal = db.execute(
        "SELECT prior_path FROM source s JOIN card c ON c.source_id = s.id "
        "WHERE s.zotero_item_key = 'SRC2'"
    ).fetchall()
    assert all(r["prior_path"] == "Agentic Governance/03 Capacity" for r in fiscal)

    # SRC1 sits in two collections at the same depth: neither is "the" chapter
    ambiguous = card_by_origin(db, project_id, "annotation:ANN1:quote")
    assert ambiguous["prior_ambiguous"] == 1


# -- §12.3 a comment is its own card --------------------------------------


def test_a_comment_produces_a_second_card_linked_to_the_quote(db, imported):
    project_id, _ = imported
    quote = card_by_origin(db, project_id, "annotation:ANN1:quote")
    idea = card_by_origin(db, project_id, "annotation:ANN1:idea")
    assert idea["kind"] == "idea"
    assert idea["origin"] == "annotation_comment"
    assert idea["parent_card_id"] == quote["id"]
    assert idea["text"] == "This is the hinge of my argument in ch.2."
    # the comment is never folded into the quote
    assert idea["text"] not in quote["text"]


def test_the_idea_card_keeps_the_locator_of_its_quote(db, imported):
    project_id, _ = imported
    idea = card_by_origin(db, project_id, "annotation:ANN1:idea")
    assert idea["locator_value"] == "132"


# -- notes ----------------------------------------------------------------


def test_notes_of_both_kinds_become_idea_cards(db, imported):
    project_id, _ = imported
    child = card_by_origin(db, project_id, "note:NOTE1")
    standalone = card_by_origin(db, project_id, "note:NOTE9")
    assert child["origin"] == "child_note"
    assert child["origin_note_key"] == "NOTE1"
    # a child note cannot be filed in a collection, so it still needs a note
    assert child["zotero_note_key"] is None
    assert standalone["origin"] == "standalone_note"
    # a standalone note is already filable and needs no second note
    assert standalone["zotero_note_key"] == "NOTE9"
    assert standalone["source_id"] is None


def test_a_note_this_tool_wrote_is_not_re_imported_as_an_idea(db, client, fake_zotero):
    """Otherwise every card materialised into Zotero comes back as a duplicate."""
    data = copy.deepcopy(load_fixture())
    data["top"]["CH02"].append(
        {"data": {"key": "KJNOTE1", "itemType": "note",
                  "note": "<h2>KJ-0001 quote</h2><p>kj:card=KJ-0001</p>",
                  "tags": [{"tag": "kj-card"}], "collections": ["CH02"]}}
    )
    fake = FakeZotero(data)
    with fake.client() as c:
        project_id, stats = run_import(db, c, "p", "ROOT")
    assert stats.own_notes_seen == 1
    assert db.execute(
        "SELECT COUNT(*) FROM card WHERE origin_key = 'note:KJNOTE1'"
    ).fetchone()[0] == 0


# -- §12.6 Japanese ------------------------------------------------------


def test_a_japanese_quotation_survives_with_its_characters_intact(db, imported):
    project_id, _ = imported
    card = card_by_origin(db, project_id, "annotation:ANN6:quote")
    assert card["text"] == "ＡＩガバナンスは「監督」を個人の能力として扱ってきた。"


# -- §12.2 re-import ------------------------------------------------------


def test_re_import_creates_no_duplicates(db, client, imported):
    project_id, _ = imported
    before = cards(db, project_id)
    run_import(db, client, "agentic-governance", "ROOT")
    after = cards(db, project_id)
    assert len(after) == len(before)
    assert [c["human_id"] for c in after] == [c["human_id"] for c in before]


def test_re_import_preserves_every_edit(db, client, imported):
    """The highlight itself changes, so the card really is rewritten — and the
    researcher's own columns still have to come through untouched. Without the
    change the card would be skipped entirely and this would prove nothing."""
    project_id, _ = imported
    card = card_by_origin(db, project_id, "annotation:ANN1:quote")
    db.execute(
        "UPDATE card SET human_label = ?, status = 'excluded', "
        "zotero_note_key = 'KJNOTE7', kj_path = 'Agentic Governance/_KJ/Theme', "
        "materialized_at = '2026-08-24T00:00:00+00:00' WHERE id = ?",
        ("my own heading", card["id"]),
    )

    data = copy.deepcopy(load_fixture())
    for a in data["annotations"]:
        if a["data"]["key"] == "ANN1":
            a["data"]["annotationText"] = "The passage, re-highlighted."
    fake = FakeZotero(data)
    # The note this card was filed as has to exist in Zotero, or the import is
    # right to stop claiming it does — see test_placement.py.
    fake.created_items["KJNOTE7"] = {
        "key": "KJNOTE7", "version": 1, "itemType": "note", "note": "<p>x</p>",
        "tags": [{"tag": "kj-card"}], "collections": [],
    }
    with fake.client() as c:
        _, stats = run_import(db, c, "agentic-governance", "ROOT")

    after = card_by_origin(db, project_id, "annotation:ANN1:quote")
    assert after["text"] == "The passage, re-highlighted."
    assert stats.updated >= 1
    assert after["human_label"] == "my own heading"
    assert after["status"] == "excluded"
    assert after["zotero_note_key"] == "KJNOTE7"
    # kj_path is cleared: the note was not found anywhere under this project,
    # so where it sits here is no longer known. What the researcher wrote is
    # untouched.
    assert after["materialized_at"] == "2026-08-24T00:00:00+00:00"


def test_an_edited_highlight_updates_its_card(db, client, imported):
    project_id, _ = imported
    data = copy.deepcopy(load_fixture())
    for a in data["annotations"]:
        if a["data"]["key"] == "ANN1":
            a["data"]["annotationText"] = "A shorter passage."
            a["data"]["annotationPageLabel"] = "133"
    fake = FakeZotero(data)
    with fake.client() as c:
        _, stats = run_import(db, c, "agentic-governance", "ROOT")
    after = card_by_origin(db, project_id, "annotation:ANN1:quote")
    assert after["text"] == "A shorter passage."
    assert after["locator_value"] == "133"
    assert stats.updated >= 1
    assert stats.quote_cards == 0  # nothing new was created


# -- §12.4 two projects ---------------------------------------------------


def test_the_same_source_imports_into_two_projects(db, client, imported):
    """Reading one book for two papers must not raise a constraint error."""
    project_id, _ = imported
    second, stats = run_import(db, client, "second-paper", "ROOT")
    assert second != project_id
    assert stats.quote_cards == 3
    assert db.execute("SELECT COUNT(*) FROM card").fetchone()[0] == len(
        cards(db, project_id)
    ) * 2


def test_a_project_refuses_a_different_root_collection(db, client, imported):
    with pytest.raises(ProjectConflict, match="different Zotero collection"):
        run_import(db, client, "agentic-governance", "CH03")


def test_a_project_refuses_a_different_zotero_database(db, client, imported):
    project_id, _ = imported
    other = FakeZotero(
        headers={"Zotero-API-Version": "3", "Zotero-Server-ID": "OTHERDB"}
    )
    with other.client() as c:
        with pytest.raises(ProjectConflict, match="different Zotero database"):
            run_import(db, c, "agentic-governance", "ROOT")


# -- §12.5 EPUB -----------------------------------------------------------


def test_an_epub_annotation_gets_a_chapter_not_a_page(db, tmp_path):
    data = copy.deepcopy(load_fixture())
    epub_path = make_epub(tmp_path / "book.epub")
    data["files"]["ATT2"] = epub_path.as_uri()
    for a in data["annotations"]:
        if a["data"]["key"] == "ANN4":
            a["data"]["annotationPosition"] = (
                '{"type": "FragmentSelector", "value": "epubcfi(/6/6[c3]!/4/2/1:0)"}'
            )
    fake = FakeZotero(data)
    with fake.client() as c:
        project_id, stats = run_import(db, c, "p", "ROOT")

    card = card_by_origin(db, project_id, "annotation:ANN4:quote")
    assert card["locator_type"] == "chapter"
    assert card["locator_value"] == "Fiscal capacity"
    assert card["locator_estimated"] == 0
    assert stats.epub_attachments == 1
    assert stats.epub_unreadable == 0


def test_an_unreadable_epub_degrades_to_an_unknown_location(db, client):
    """The fixture's EPUB URL points nowhere, which must not fail the import."""
    project_id, stats = run_import(db, client, "p", "ROOT")
    card = card_by_origin(db, project_id, "annotation:ANN4:quote")
    assert card["locator_type"] == "cfi"
    assert stats.epub_unreadable == 1
