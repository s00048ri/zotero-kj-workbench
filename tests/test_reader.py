"""Reading a subtree: count items, not sightings."""

from __future__ import annotations

import pytest

from zkj.zotero import CollectionTree
from zkj.zotero.reader import read_subtree
from tests.conftest import load_fixture


@pytest.fixture
def snapshot(client):
    tree = CollectionTree.from_payloads(load_fixture()["collections"])
    return read_subtree(client, tree, "ROOT")


def test_the_whole_chain_is_resolvable(snapshot):
    src = snapshot.sources["SRC1"]
    att = src.attachments[0]
    ann = next(a for a in att.annotations if a.key == "ANN1")
    assert src.source.title.startswith("Human oversight")
    assert att.attachment.is_pdf
    assert ann.parent_item == att.attachment.key
    assert ann.page_label == "132"


def test_a_source_in_two_collections_is_one_source(snapshot, fake_zotero):
    src = snapshot.sources["SRC1"]
    assert src.collection_keys == {"CH02", "CH03"}
    assert snapshot.counts()["sources"] == 3
    # and its children are read once, not once per sighting
    assert fake_zotero.count("/items/SRC1/children") == 1


def test_a_standalone_note_in_two_collections_is_one_note(snapshot):
    note = snapshot.standalone_notes["NOTE9"]
    assert note.collection_keys == {"CH02", "CH03"}
    assert snapshot.counts()["standalone_notes"] == 1


def test_annotations_outside_the_subtree_are_not_counted(snapshot):
    keys = {a.key for a in snapshot.annotations}
    assert "ANN5" not in keys  # lives on a source in an unrelated collection
    assert keys == {"ANN1", "ANN2", "ANN3", "ANN4", "ANN6"}


def test_counts_separate_highlights_from_comments(snapshot):
    counts = snapshot.counts()
    assert counts["annotations"] == 5
    # ANN2 is an image and ANN3 is empty, so neither is a usable highlight
    assert counts["highlights"] == 3
    assert counts["comments"] == 1
    assert counts["child_notes"] == 1
    assert counts["collections"] == 4


def test_a_bookmark_cannot_hold_highlights(snapshot):
    att = snapshot.sources["SRC3"].attachments[0]
    assert att.attachment.can_hold_annotations is False
    assert snapshot.unreadable_attachments == 1


def test_annotation_index_is_built_once_for_the_whole_walk(client, fake_zotero):
    tree = CollectionTree.from_payloads(load_fixture()["collections"])
    read_subtree(client, tree, "ROOT")
    assert fake_zotero.count("itemType=annotation") == 1


def test_japanese_highlight_arrives_unaltered(snapshot):
    ann = next(a for a in snapshot.annotations if a.key == "ANN6")
    assert ann.text == "ＡＩガバナンスは「監督」を個人の能力として扱ってきた。"
