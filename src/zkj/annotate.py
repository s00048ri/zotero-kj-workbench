"""Writing your own note on a passage — the scarce input.

Measured on a real library: seventeen highlights, zero comments. A general
model can summarise a literature it has read; it cannot tell you which twelve
passages you found arresting or why you put three of them together. So the
researcher's own writing is not an optional field that happens to be empty —
it is the input this whole tool exists to capture, and it gets a first-class
path of its own.

A note written here becomes an idea card linked to the quote, and — when the
card came from an annotation — is also written back into that annotation's
comment, so Zotero and this app do not drift apart. Nothing else about the
annotation is touched: the highlighted text is evidence and is never written.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .cards import CARD_SELECT
from .store import new_id, now_iso
from .writes import WriteSession, parse_write_result
from .zotero import ZoteroClient, ZoteroError


class CommentConflict(ZoteroError):
    """Zotero already holds a different comment on this annotation."""

    def __init__(self, existing: str) -> None:
        super().__init__(
            "This highlight already carries a different comment in Zotero. "
            "Saving would replace what is there."
        )
        self.existing = existing


def _card(conn: sqlite3.Connection, project_id: str, card_id: str) -> dict[str, Any]:
    row = conn.execute(
        CARD_SELECT + " WHERE c.project_id = ? AND c.id = ?", (project_id, card_id)
    ).fetchone()
    if row is None:
        raise ZoteroError("No such card.")
    return dict(row)


def write_my_note(
    conn: sqlite3.Connection,
    project_id: str,
    card_id: str,
    text: str,
    *,
    client: ZoteroClient | None = None,
    session: WriteSession | None = None,
    push_to_zotero: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save the researcher's note on a card, and offer it back to Zotero."""
    text = text.strip()
    if not text:
        raise ValueError("A note needs something in it.")

    card = _card(conn, project_id, card_id)
    annotation = None
    if card["annotation_id"]:
        annotation = conn.execute(
            "SELECT * FROM annotation WHERE id = ?", (card["annotation_id"],)
        ).fetchone()

    # A conflict raises before anything is saved, so the researcher decides
    # whether to replace what Zotero already holds.
    pushed = False
    if annotation is not None and push_to_zotero and client and session:
        pushed = _push_comment(
            client, session, annotation["zotero_annotation_key"], text,
            overwrite=overwrite,
        )
        if pushed:
            conn.execute(
                "UPDATE annotation SET comment_raw = ? WHERE id = ?",
                (text, annotation["id"]),
            )

    idea = _save_idea_card(conn, project_id, card, annotation, text)
    idea["pushed_to_zotero"] = pushed
    return idea


def _push_comment(
    client: ZoteroClient,
    session: WriteSession,
    annotation_key: str,
    text: str,
    *,
    overwrite: bool,
) -> bool:
    """Set the annotation's comment. The highlighted text is never touched."""
    item = client.item(annotation_key)
    if item is None:
        raise ZoteroError(
            "That highlight is no longer in Zotero, so the note was kept here only."
        )
    data = item.get("data", item)
    existing = (data.get("annotationComment") or "").strip()
    if existing and existing != text and not overwrite:
        raise CommentConflict(existing)

    version = item.get("version") or data.get("version") or 0
    response = session.run(
        lambda key: client.update_items(
            [
                {
                    "key": annotation_key,
                    "version": version,
                    "annotationComment": text,
                }
            ],
            key,
        )
    )
    session.spend()
    ok, errors = parse_write_result(response, 1)
    if errors:
        raise ZoteroError(f"Zotero refused the comment: {errors[0]}")
    return bool(ok)


def _save_idea_card(
    conn: sqlite3.Connection,
    project_id: str,
    card: dict[str, Any],
    annotation: sqlite3.Row | None,
    text: str,
) -> dict[str, Any]:
    """One idea card per passage, created or rewritten in place.

    The origin key matches what an import would derive from the same
    annotation, so the next import updates this card instead of making a
    second one beside it.
    """
    if annotation is not None:
        origin_key = f"annotation:{annotation['zotero_annotation_key']}:idea"
        origin = "annotation_comment"
    else:
        origin_key = f"note-on:{card['id']}"
        origin = "manual"

    existing = conn.execute(
        "SELECT id FROM card WHERE project_id = ? AND origin_key = ?",
        (project_id, origin_key),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE card SET text = ?, text_raw = ?, parent_card_id = ?, "
            "updated_at = ? WHERE id = ?",
            (text, text, card["id"], now_iso(), existing["id"]),
        )
        idea_id = existing["id"]
    else:
        row = conn.execute(
            "SELECT human_id FROM card WHERE project_id = ? ORDER BY human_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        number = int(row["human_id"][3:]) + 1 if row else 1
        idea_id = new_id()
        conn.execute(
            "INSERT INTO card (id, project_id, human_id, origin_key, kind, origin, "
            "text, text_raw, source_id, annotation_id, parent_card_id, "
            "prior_collection_id, prior_path, prior_ambiguous, locator_type, "
            "locator_value, locator_source, locator_estimated, locator_detail_json, "
            "color, sort_index, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                idea_id,
                project_id,
                f"KJ-{number:04d}",
                origin_key,
                origin,
                text,
                text,
                card["source_id"],
                card["annotation_id"],
                card["id"],
                card["prior_collection_id"],
                card["prior_path"],
                card["prior_ambiguous"],
                card["locator_type"],
                card["locator_value"],
                card["locator_source"],
                card["locator_estimated"],
                card["locator_detail_json"] or json.dumps({}),
                card["color"],
                card["sort_index"],
                new_id(),
                now_iso(),
                now_iso(),
            ),
        )

    return dict(conn.execute(CARD_SELECT + " WHERE c.id = ?", (idea_id,)).fetchone())
