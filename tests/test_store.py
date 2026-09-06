"""The schema's constraints do real work, so they are tested as behaviour."""

from __future__ import annotations

import sqlite3

import pytest

from zkj.store import connect, insert, migrate, now_iso, upsert


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "test.sqlite3")
    yield conn
    conn.close()


def project(db, name="p", root="ROOT") -> str:
    return insert(
        db,
        "project",
        {"name": name, "root_collection_key": root, "created_at": now_iso()},
    )


def card_values(project_id, origin_key, human_id, **kw):
    return {
        "project_id": project_id,
        "human_id": human_id,
        "origin_key": origin_key,
        "kind": "quote",
        "origin": "annotation_text",
        "text": "a passage",
        "content_hash": "h",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        **kw,
    }


def test_migrations_are_applied_once(tmp_path):
    path = tmp_path / "m.sqlite3"
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] >= 1
    assert migrate(conn) == 0  # re-running is a no-op
    conn.close()


def test_a_database_left_at_the_removed_migration_still_catches_up(tmp_path):
    """Migration 005 created a `summary` table and has since been deleted,
    leaving a gap in the numbering. A database that stopped at 005 must still
    get the notebook tables from 006 and lose the summary table to 007 — which
    is exactly why 006 was not renumbered down into the gap."""
    path = tmp_path / "old.sqlite3"
    conn = connect(path)
    # wind it back to the state a researcher who ran only 005 would be in
    conn.execute("DROP TABLE notebook_report")
    conn.execute("DROP TABLE notebook")
    conn.execute("CREATE TABLE summary (id TEXT PRIMARY KEY)")
    conn.execute("PRAGMA user_version = 5")

    assert migrate(conn) == 2

    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "summary" not in tables
    assert {"notebook", "notebook_report"} <= tables
    conn.close()


def test_a_database_that_never_saw_it_migrates_just_as_cleanly(tmp_path):
    """The drop tolerates the table's absence, so a fresh install is not a
    special case."""
    conn = connect(tmp_path / "fresh.sqlite3")
    tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "summary" not in tables
    assert {"notebook", "notebook_report"} <= tables
    conn.close()


def test_project_names_are_unique(db):
    project(db, "same")
    with pytest.raises(sqlite3.IntegrityError):
        project(db, "same")


def test_the_same_origin_cannot_produce_two_cards(db):
    p = project(db)
    insert(db, "card", card_values(p, "annotation:A:quote", "KJ-0001"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "card", card_values(p, "annotation:A:quote", "KJ-0002"))


def test_the_same_origin_in_two_projects_is_two_cards(db):
    """A source read for two papers must not collide."""
    a, b = project(db, "a"), project(db, "b")
    insert(db, "card", card_values(a, "annotation:A:quote", "KJ-0001"))
    insert(db, "card", card_values(b, "annotation:A:quote", "KJ-0001"))
    assert db.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 2


def test_human_ids_do_not_repeat_within_a_project(db):
    p = project(db)
    insert(db, "card", card_values(p, "annotation:A:quote", "KJ-0001"))
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "card", card_values(p, "annotation:B:quote", "KJ-0001"))


def test_an_unknown_card_kind_is_refused(db):
    p = project(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "card", card_values(p, "x", "KJ-0001", kind="scribble"))


def test_deleting_a_project_takes_its_cards(db):
    p = project(db)
    insert(db, "card", card_values(p, "annotation:A:quote", "KJ-0001"))
    db.execute("DELETE FROM project WHERE id = ?", (p,))
    assert db.execute("SELECT COUNT(*) FROM card").fetchone()[0] == 0


def test_upsert_updates_in_place(db):
    p = project(db)
    first = upsert(db, "collection", {"project_id": p, "zotero_collection_key": "C1"},
                   {"name": "Old", "path": "Old", "depth": 0})
    second = upsert(db, "collection", {"project_id": p, "zotero_collection_key": "C1"},
                    {"name": "New", "path": "New", "depth": 0})
    assert first == second
    assert db.execute("SELECT name FROM collection").fetchone()["name"] == "New"


def test_search_index_follows_the_card(db):
    """Trigram indexing needs three characters; shorter queries are handled
    with LIKE in the query layer, not here."""
    p = project(db)
    cid = insert(db, "card", card_values(p, "a", "KJ-0001", text="監督の問題"))
    found = db.execute(
        "SELECT c.id FROM card_fts f JOIN card c ON c.rowid = f.rowid "
        "WHERE card_fts MATCH ?", ("監督の",)).fetchall()
    assert [r["id"] for r in found] == [cid]

    db.execute("UPDATE card SET text = ? WHERE id = ?", ("capacity", cid))
    assert db.execute(
        "SELECT COUNT(*) FROM card_fts WHERE card_fts MATCH ?", ("監督の",)
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM card_fts WHERE card_fts MATCH ?", ("capacity",)
    ).fetchone()[0] == 1

    db.execute("DELETE FROM card WHERE id = ?", (cid,))
    assert db.execute(
        "SELECT COUNT(*) FROM card_fts WHERE card_fts MATCH ?", ("capacity",)
    ).fetchone()[0] == 0
