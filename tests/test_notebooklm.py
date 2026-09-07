"""NotebookLM: what goes in, the link that comes back, and what it becomes.

The workbench never talks to NotebookLM, so nothing here mocks a network. What
is worth pinning down is the URL guard — an address this tool writes into the
researcher's library as a clickable link — and the rule that nothing carried
across from NotebookLM can become a card.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import FakeZotero
from zkj import notebooklm
from zkj.importer import run_import
from zkj.materialize import materialize, revert
from zkj.store import connect
from zkj.writes import WriteSession
from zkj.zotero.notes import GENERATED_TAG, NOTEBOOK_TAG, REPORT_TAG

URL = "https://notebook.google.com/notebook/abc123def456"


@pytest.fixture
def setup(tmp_path):
    def build(**kwargs):
        fake = FakeZotero(**kwargs)
        conn = connect(tmp_path / f"n{id(kwargs)}{len(kwargs)}.sqlite3")
        client = fake.client()
        project_id, _ = run_import(conn, client, "p", "ROOT")
        project = dict(
            conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
        )
        session = WriteSession(client, conn, sleep=lambda _s: None)
        return fake, conn, client, project, session

    return build


# -- the URL guard ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://notebook.google.com/notebook/abc123def456",
        "https://notebooklm.google.com/notebook/abc123def456",  # the old host still works
        "https://notebook.google.com/notebook/abc123def456/",
        "https://notebook.google.com/notebook/abc123def456?pli=1",
    ],
)
def test_a_notebook_address_is_accepted_and_stripped_to_the_notebook(url):
    cleaned = notebooklm.clean_url(url)
    assert cleaned.endswith("/notebook/abc123def456")
    assert "?" not in cleaned


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "notebook.google.com/notebook/abc123def456",  # no scheme
        "http://notebook.google.com/notebook/abc123def456",  # not https
        "https://evil.example.com/notebook/abc123def456",
        "https://notebook.google.com/",  # the app, not a notebook
        "https://notebook.google.com/notebook/",
        "javascript:alert(1)",
    ],
)
def test_anything_that_is_not_a_notebook_is_refused(url):
    """This address is about to become a clickable link in the researcher's
    own library. A wrong one is worse than none."""
    with pytest.raises(notebooklm.NotebookError):
        notebooklm.clean_url(url)


# -- holding the link ------------------------------------------------------


def test_a_project_has_one_notebook_and_re_registering_repoints_it(setup):
    _fake, conn, _client, project, _s = setup()
    first = notebooklm.register(conn, project["id"], URL, title="Everything")
    again = notebooklm.register(
        conn, project["id"], "https://notebook.google.com/notebook/zzz99988877",
        title="Redone",
    )
    assert first["id"] == again["id"]
    assert again["url"].endswith("zzz99988877")
    assert conn.execute("SELECT count(*) FROM notebook").fetchone()[0] == 1


def test_a_source_can_have_its_own_notebook_beside_the_project_one(setup):
    _fake, conn, _client, project, _s = setup()
    source_id = conn.execute("SELECT id FROM source LIMIT 1").fetchone()[0]
    notebooklm.register(conn, project["id"], URL)
    notebooklm.register(
        conn, project["id"], "https://notebook.google.com/notebook/persource",
        source_id=source_id,
    )
    assert len(notebooklm.notebooks(conn, project["id"])) == 2
    # a source's own notebook wins over the project's
    assert notebooklm.for_source(conn, project["id"], source_id)["url"].endswith(
        "persource"
    )
    other = conn.execute(
        "SELECT id FROM source WHERE id != ?", (source_id,)
    ).fetchone()[0]
    assert notebooklm.for_source(conn, project["id"], other)["url"] == URL


def test_a_notebook_cannot_be_registered_for_a_source_in_another_project(setup):
    _fake, conn, _client, project, _s = setup()
    with pytest.raises(notebooklm.NotebookError):
        notebooklm.register(conn, project["id"], URL, source_id="not-a-source")


# -- what goes in ----------------------------------------------------------


def test_the_bundle_offers_urls_files_and_your_own_passages(setup):
    _fake, conn, client, project, _s = setup()
    prepared = notebooklm.bundle(conn, client, project)

    assert prepared.scope == "project"
    # bulk paste wants one per line
    assert "\n".join(u["url"] for u in prepared.urls) == prepared.urls_text
    assert all(u["url"].startswith("https://") for u in prepared.urls)
    # the passages are the point: one pasteable source, carrying citations
    assert prepared.card_count > 0
    assert "passages I selected" in prepared.cards_title
    assert "—" in prepared.cards_text


def test_a_doi_is_preferred_over_a_captured_publisher_url():
    assert notebooklm.public_url({"doi": "10.1234/abc", "url": "http://x.example"}) == (
        "https://doi.org/10.1234/abc"
    )
    # a DOI already written as a URL is not doubled up
    assert notebooklm.public_url({"doi": "https://doi.org/10.1234/abc"}) == (
        "https://doi.org/10.1234/abc"
    )
    assert notebooklm.public_url({"url": "https://x.example/p"}) == "https://x.example/p"
    assert notebooklm.public_url({"url": "file:///tmp/x.pdf"}) is None
    assert notebooklm.public_url({}) is None


def test_staging_puts_the_unreachable_files_in_one_folder(setup, tmp_path, monkeypatch):
    """Zotero hides an attachment under a hashed key directory. Twenty of them
    by hand is twenty trips through a file dialog; one folder is one."""
    from zkj import config

    pdf = tmp_path / "real.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    fake, conn, client, project, _s = setup()
    # SRC1 has a DOI and is reachable by link; SRC2 has neither, so its file
    # is the one that has to be carried across by hand.
    fake.data["files"] = {"ATT2": pdf.as_uri()}
    monkeypatch.setattr(
        config, "settings", config.Settings(db_path=str(tmp_path / "zkj.sqlite3"))
    )
    monkeypatch.setattr(notebooklm, "settings", config.settings)

    result = notebooklm.stage(conn, client, project)
    assert result["staged"] == 1 and result["failures"] == []
    from pathlib import Path

    staged = list(Path(result["folder"]).iterdir())
    assert len(staged) == 1
    assert staged[0].read_bytes() == b"%PDF-1.4\n%%EOF\n"


# -- the link, in Zotero ---------------------------------------------------


def test_a_project_notebook_becomes_a_standalone_note_in_kj_not_the_inbox(setup):
    fake, conn, client, project, session = setup()
    notebooklm.register(conn, project["id"], URL, title="Everything")
    result = notebooklm.link_into_zotero(conn, client, session, project)

    assert result.created == 1
    note = next(
        i for i in fake.created_items.values() if NOTEBOOK_TAG in
        {t["tag"] for t in i.get("tags", [])}
    )
    assert URL in note["note"] and "<a href=" in note["note"]
    # in _KJ, never in _KJ/Inbox — the Inbox is the researcher's sorting floor
    kj_key = conn.execute(
        "SELECT kj_root_key FROM project WHERE id = ?", (project["id"],)
    ).fetchone()[0]
    assert note["collections"] == [kj_key]
    assert "parentItem" not in note


def test_a_source_notebook_hangs_off_the_item_it_is_about(setup):
    fake, conn, client, project, session = setup()
    source = conn.execute("SELECT * FROM source LIMIT 1").fetchone()
    notebooklm.register(conn, project["id"], URL, source_id=source["id"])
    notebooklm.link_into_zotero(conn, client, session, project)

    note = next(iter(fake.created_items.values()))
    assert note["parentItem"] == source["zotero_item_key"]
    assert "collections" not in note
    assert source["title"] in note["note"]


def test_the_link_note_says_what_was_put_in(setup):
    fake, conn, client, project, session = setup()
    notebooklm.register(conn, project["id"], URL)
    notebooklm.link_into_zotero(conn, client, session, project)
    note = next(iter(fake.created_items.values()))
    assert "your own selected passages" in note["note"]
    assert "briefing" in note["note"]  # what the notebook can be asked for


def test_a_card_note_written_afterwards_links_to_the_notebook(setup):
    """The ask: the notes this tool creates carry the link, so the notebook is
    reachable from wherever the researcher happens to be."""
    fake, conn, client, project, session = setup()
    notebooklm.register(conn, project["id"], URL)
    materialize(conn, client, session, project)

    card_notes = [
        i for i in fake.created_items.values()
        if "kj-card" in {t["tag"] for t in i.get("tags", [])}
    ]
    assert card_notes
    assert all(URL in n["note"] for n in card_notes)
    assert all("Ask NotebookLM" in n["note"] for n in card_notes)


def test_links_can_be_taken_back_like_any_other_batch(setup):
    fake, conn, client, project, session = setup()
    notebooklm.register(conn, project["id"], URL)
    written = notebooklm.link_into_zotero(conn, client, session, project)

    taken_back = revert(conn, client, session, written.batch_id)

    assert taken_back.deleted == 1 and taken_back.failures == []
    assert fake.deleted
    batch = conn.execute(
        "SELECT * FROM write_batch WHERE id = ?", (written.batch_id,)
    ).fetchone()
    assert batch["kind"] == "notebook-links"
    assert json.loads(batch["card_ids_json"]) == []


# -- what comes back -------------------------------------------------------


def test_a_pasted_report_is_kept_and_can_be_filed(setup):
    fake, conn, client, project, session = setup()
    notebook = notebooklm.register(conn, project["id"], URL)
    notebooklm.add_report(
        conn,
        project["id"],
        notebook["id"],
        kind="briefing",
        title="What these papers agree on",
        text="They agree on one thing.\n\nThey disagree on another.",
    )
    written = notebooklm.write_reports(conn, client, session, project)

    assert written.created == 1
    note = next(
        i for i in fake.created_items.values()
        if REPORT_TAG in {t["tag"] for t in i.get("tags", [])}
    )
    assert "What these papers agree on" in note["note"]
    assert "not your reading" in note["note"]
    assert URL in note["note"]  # traceable back to the sources it read


def test_an_empty_paste_is_refused(setup):
    _fake, conn, _client, project, _s = setup()
    notebook = notebooklm.register(conn, project["id"], URL)
    with pytest.raises(notebooklm.NotebookError):
        notebooklm.add_report(
            conn, project["id"], notebook["id"], kind="briefing", text="   "
        )


def test_nothing_carried_back_from_notebooklm_becomes_a_card(setup):
    """One tag covers every generated kind, so a kind added later is refused
    by the importer without anyone remembering to add a case."""
    fake, conn, client, project, session = setup()
    notebook = notebooklm.register(conn, project["id"], URL)
    notebooklm.add_report(
        conn, project["id"], notebook["id"], kind="briefing", text="A briefing."
    )
    notebooklm.link_into_zotero(conn, client, session, project)
    notebooklm.write_reports(conn, client, session, project)

    before = conn.execute("SELECT count(*) FROM card").fetchone()[0]
    kj_key = conn.execute(
        "SELECT kj_root_key FROM project WHERE id = ?", (project["id"],)
    ).fetchone()[0]
    for note in fake.created_items.values():
        assert GENERATED_TAG in {t["tag"] for t in note["tags"]}
        # Zotero would hand these back where this tool filed them
        payload = {"data": {**note, "itemType": "note"}}
        if note.get("parentItem"):
            fake.data["children"].setdefault(note["parentItem"], []).append(payload)
        else:
            fake.data["top"].setdefault(kj_key, []).append(payload)
    _pid, stats = run_import(conn, client, "p", "ROOT")

    assert conn.execute("SELECT count(*) FROM card").fetchone()[0] == before
    assert stats.generated_notes_seen == 2


def test_forgetting_a_notebook_leaves_zotero_and_google_alone(setup):
    fake, conn, client, project, session = setup()
    notebook = notebooklm.register(conn, project["id"], URL)
    notebooklm.link_into_zotero(conn, client, session, project)

    notebooklm.forget(conn, project["id"], notebook["id"])

    assert notebooklm.notebooks(conn, project["id"]) == []
    assert len(fake.created_items) == 1 and not fake.deleted
    # the report went with it
    assert conn.execute("SELECT count(*) FROM notebook_report").fetchone()[0] == 0


# -- two machines, one synced library -------------------------------------


def test_a_link_note_synced_from_another_machine_is_adopted_not_duplicated(setup):
    """Two machines share one Zotero library but each keep their own workbench
    database. The second one has no record of a notebook the first registered
    — and Zotero has already synced its note. Writing a second one saying the
    same thing is the wart this closes."""
    fake, conn, client, project, session = setup()
    source = conn.execute("SELECT * FROM source LIMIT 1").fetchone()
    notebooklm.register(conn, project["id"], URL, source_id=source["id"])
    first = notebooklm.link_into_zotero(conn, client, session, project)
    note_key = conn.execute("SELECT zotero_note_key FROM notebook").fetchone()[0]
    # Zotero now reports it as a child of the item, as a sync would
    fake.data["children"].setdefault(source["zotero_item_key"], []).append(
        {"data": {**fake.created_items[note_key], "itemType": "note"}}
    )

    # the other machine: same library, same notebook, empty database
    other = connect(":memory:")
    run_import(other, client, "p", "ROOT")
    other_project = dict(
        other.execute("SELECT * FROM project").fetchone()
    )
    other_source = other.execute(
        "SELECT id FROM source WHERE zotero_item_key = ?", (source["zotero_item_key"],)
    ).fetchone()[0]
    notebooklm.register(other, other_project["id"], URL, source_id=other_source)

    second = notebooklm.link_into_zotero(other, client, session, other_project)

    assert first.created == 1 and first.adopted == 0
    assert second.created == 0 and second.adopted == 1
    assert second.batch_id is None  # nothing was written, so nothing to take back
    assert len(fake.created_items) == 1
    # and the second machine now points at the note that already exists
    assert other.execute("SELECT zotero_note_key FROM notebook").fetchone()[0] == note_key
    other.close()


def test_a_different_notebook_on_the_same_item_still_gets_its_own_note(setup):
    """Adoption matches on the URL, so a second notebook about the same source
    is a second notebook, not a duplicate."""
    fake, conn, client, project, session = setup()
    source = conn.execute("SELECT * FROM source LIMIT 1").fetchone()
    notebooklm.register(conn, project["id"], URL, source_id=source["id"])
    notebooklm.link_into_zotero(conn, client, session, project)
    note_key = conn.execute("SELECT zotero_note_key FROM notebook").fetchone()[0]
    fake.data["children"].setdefault(source["zotero_item_key"], []).append(
        {"data": {**fake.created_items[note_key], "itemType": "note"}}
    )

    notebooklm.register(
        conn,
        project["id"],
        "https://notebook.google.com/notebook/adifferentone",
        source_id=source["id"],
    )
    again = notebooklm.link_into_zotero(conn, client, session, project)

    assert again.created == 1 and again.adopted == 0
    assert len(fake.created_items) == 2


def test_a_project_notebook_is_adopted_from_the_kj_collection(setup):
    fake, conn, client, project, session = setup()
    notebooklm.register(conn, project["id"], URL)
    notebooklm.link_into_zotero(conn, client, session, project)
    note_key = conn.execute("SELECT zotero_note_key FROM notebook").fetchone()[0]
    kj_key = conn.execute(
        "SELECT kj_root_key FROM project WHERE id = ?", (project["id"],)
    ).fetchone()[0]
    fake.data["top"].setdefault(kj_key, []).append(
        {"data": {**fake.created_items[note_key], "itemType": "note"}}
    )

    conn.execute("UPDATE notebook SET zotero_note_key = NULL, linked_at = NULL")
    again = notebooklm.link_into_zotero(conn, client, session, project)

    assert again.created == 0 and again.adopted == 1
    assert len(fake.created_items) == 1


def test_the_passages_block_is_measured_and_never_trimmed(setup):
    """NotebookLM refuses a source over 500,000 words. Half a researcher's
    selections pasted without saying so is worse than being told to split."""
    _fake, conn, client, project, _s = setup()
    prepared = notebooklm.bundle(conn, client, project)

    assert prepared.cards_words == len(prepared.cards_text.split())
    assert prepared.cards_over_limit is False

    body = prepared.as_dict()
    assert body["source_word_limit"] == notebooklm.SOURCE_WORD_LIMIT
    assert body["cards_words"] > 0


def test_an_oversized_passages_block_says_so_rather_than_being_cut(setup, monkeypatch):
    _fake, conn, client, project, _s = setup()
    monkeypatch.setattr(notebooklm, "SOURCE_WORD_LIMIT", 5)
    prepared = notebooklm.bundle(conn, client, project)

    assert prepared.cards_over_limit is True
    # every passage is still there — the researcher decides how to split
    assert prepared.card_count > 0
    assert prepared.cards_text.count("##") >= prepared.card_count
