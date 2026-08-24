"""Reading back where the researcher dragged each card. §12.9."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

import pytest

from tests.conftest import FakeZotero, load_fixture
from zkj.importer import run_import
from zkj.materialize import materialize
from zkj.store import connect
from zkj.writes import WriteSession


@dataclass
class Library:
    """An imported, materialised project, plus a way to move its notes."""

    fake: FakeZotero
    conn: Any
    client: Any
    project: dict
    inbox_key: str
    kj_key: str

    def note_key(self, origin_key: str) -> str:
        return self.conn.execute(
            "SELECT zotero_note_key FROM card WHERE origin_key = ?", (origin_key,)
        ).fetchone()[0]

    def entry(self, note_key: str) -> dict:
        return next(
            e
            for e in self.fake.data["top"][self.inbox_key]
            if e["data"]["key"] == note_key
        )

    def drag(self, origin_key: str, *targets: str) -> None:
        """Move a card's note into these collections, as Zotero would."""
        note_key = self.note_key(origin_key)
        entry = self.entry(note_key)
        entry["data"]["collections"] = list(targets)
        for target in targets:
            self.fake.data["top"].setdefault(target, []).append(entry)
        if self.inbox_key not in targets:
            self.fake.data["top"][self.inbox_key].remove(entry)

    def reimport(self):
        return run_import(self.conn, self.client, "p", "ROOT")

    def kj_path(self, origin_key: str) -> str | None:
        return self.conn.execute(
            "SELECT kj_path FROM card WHERE origin_key = ?", (origin_key,)
        ).fetchone()[0]


@pytest.fixture
def library(tmp_path) -> Library:
    fake = FakeZotero(copy.deepcopy(load_fixture()))
    conn = connect(tmp_path / "p.sqlite3")
    client = fake.client()
    project_id, _ = run_import(conn, client, "p", "ROOT")
    project = dict(conn.execute("SELECT * FROM project").fetchone())
    session = WriteSession(client, conn, sleep=lambda _s: None)
    materialize(conn, client, session, project)

    row = conn.execute(
        "SELECT kj_root_key, kj_inbox_key FROM project WHERE id = ?", (project_id,)
    ).fetchone()
    kj_key, inbox_key = row["kj_root_key"], row["kj_inbox_key"]

    # a theme collection the researcher makes in Zotero
    fake.data["collections"].append(
        {"data": {"key": "THEME1", "name": "Oversight is organisational",
                  "parentCollection": kj_key}}
    )
    fake.data["top"]["THEME1"] = []

    # every note Zotero now holds, sitting in Inbox where it was written
    fake.data["top"].setdefault(inbox_key, [])
    for key, item in fake.created_items.items():
        fake.data["top"][inbox_key].append(
            {"data": {"key": key, "itemType": "note", "note": item["note"],
                      "tags": item["tags"], "collections": [inbox_key]}}
        )
    return Library(fake, conn, client, project, inbox_key, kj_key)


def test_dragging_a_note_into_a_theme_is_read_back_as_the_grouping(library):
    library.drag("annotation:ANN1:quote", "THEME1")
    _, stats = library.reimport()
    assert library.kj_path("annotation:ANN1:quote") == (
        "Agentic Governance/_KJ/Oversight is organisational"
    )
    assert stats.placements_read == 5


def test_a_note_in_two_collections_counts_once_and_reports_the_theme(library):
    """Inbox is a holding pen; a card in both is filed, not unsorted."""
    library.drag("annotation:ANN1:quote", "THEME1", library.inbox_key)
    _, stats = library.reimport()
    assert stats.placements_read == 5  # one card, two sightings
    assert library.kj_path("annotation:ANN1:quote").endswith("Oversight is organisational")
    assert stats.still_in_inbox == 4


def test_cards_still_in_the_inbox_are_reported_as_unsorted(library):
    library.drag("annotation:ANN1:quote", "THEME1")
    _, stats = library.reimport()
    assert stats.still_in_inbox == 4
    assert library.conn.execute(
        "SELECT COUNT(*) FROM card WHERE materialized_at IS NOT NULL AND kj_path IS NULL"
    ).fetchone()[0] == 4


def test_a_materialised_note_is_never_re_imported_as_a_new_idea(library):
    before = library.conn.execute("SELECT COUNT(*) FROM card").fetchone()[0]
    _, stats = library.reimport()
    assert library.conn.execute("SELECT COUNT(*) FROM card").fetchone()[0] == before
    assert stats.own_notes_seen == 5


def test_a_kj_note_from_another_project_is_left_alone(library):
    """Someone else's card, or one from a database this project never saw."""
    library.fake.data["top"][library.inbox_key].append(
        {"data": {"key": "STRANGER", "itemType": "note",
                  "note": "<p>kj:card=KJ-9999</p>",
                  "tags": [{"tag": "kj-card"}], "collections": [library.inbox_key]}}
    )
    _, stats = library.reimport()
    assert stats.unknown_kj_notes == 1
    assert library.conn.execute(
        "SELECT COUNT(*) FROM card WHERE origin_key = 'note:STRANGER'"
    ).fetchone()[0] == 0


def test_moving_a_card_to_another_theme_updates_the_grouping(library):
    library.fake.data["collections"].append(
        {"data": {"key": "THEME2", "name": "Capacity is fiscal",
                  "parentCollection": library.kj_key}}
    )
    library.fake.data["top"]["THEME2"] = []
    library.drag("annotation:ANN1:quote", "THEME1")
    library.reimport()
    entry = library.entry_by_key = None  # the note has left Inbox

    note_key = library.note_key("annotation:ANN1:quote")
    entry = next(e for e in library.fake.data["top"]["THEME1"]
                 if e["data"]["key"] == note_key)
    entry["data"]["collections"] = ["THEME2"]
    library.fake.data["top"]["THEME1"].remove(entry)
    library.fake.data["top"]["THEME2"].append(entry)

    library.reimport()
    assert library.kj_path("annotation:ANN1:quote").endswith("Capacity is fiscal")
