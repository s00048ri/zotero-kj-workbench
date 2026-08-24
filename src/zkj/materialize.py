"""Cards into Zotero, as standalone notes the researcher can move.

A Zotero annotation is a child of an attachment and cannot belong to a
collection. That is the whole reason this step exists: turning a highlight
into a standalone note is what makes it something the researcher can drag into
a subcollection — and dragging it there *is* the grouping.

So the destination matters. Everything lands in ``_KJ/Inbox``, which the
researcher empties by sorting; a group label is the one exception, because it
belongs in the collection it names, beside the evidence it is about.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .cards import CARD_SELECT, citation_of
from .store import insert, now_iso
from .writes import WriteSession, parse_write_result
from .zotero import ZoteroClient, ZoteroError
from .zotero.client import WRITE_BATCH
from .zotero.notes import note_payload, target_collection
from .zotero.tree import CollectionTree

KJ_ROOT_NAME = "_KJ"
INBOX_NAME = "Inbox"


@dataclass
class Placement:
    card: dict[str, Any]
    collection_key: str
    destination: str


@dataclass
class MaterializeResult:
    batch_id: str | None = None
    created: int = 0
    destinations: dict[str, int] = field(default_factory=dict)
    failures: list[dict[str, str]] = field(default_factory=list)
    dialogs_shown: int = 0
    dry_run: bool = False
    preview: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "created": self.created,
            "destinations": self.destinations,
            "failures": self.failures,
            "dialogs_shown": self.dialogs_shown,
            "dry_run": self.dry_run,
            "preview": self.preview,
        }


@dataclass
class RevertResult:
    batch_id: str
    deleted: int = 0
    already_gone: int = 0
    failures: list[str] = field(default_factory=list)


def pending_cards(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    kinds: tuple[str, ...] = ("quote", "idea"),
    card_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Cards with no note of their own yet.

    ``zotero_note_key`` is the note this tool created. It is deliberately not
    the same column as ``origin_note_key`` — a card whose text came *from* a
    Zotero note is not a card that has already been filed, and conflating the
    two silently excludes every note-derived card from ever being materialised.
    """
    sql = CARD_SELECT + (
        " WHERE c.project_id = ? AND c.zotero_note_key IS NULL "
        "AND c.status = 'active'"
    )
    params: list[Any] = [project_id]
    if kinds:
        sql += f" AND c.kind IN ({', '.join('?' for _ in kinds)})"
        params += list(kinds)
    if card_ids is not None:
        if not card_ids:
            return []
        sql += f" AND c.id IN ({', '.join('?' for _ in card_ids)})"
        params += card_ids
    sql += " ORDER BY c.human_id"
    return [dict(r) for r in conn.execute(sql, params)]


def ensure_kj_collections(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    session: WriteSession,
    project: dict[str, Any],
) -> tuple[str, str]:
    """Find or create ``_KJ`` and ``_KJ/Inbox`` under the project's root."""
    tree = CollectionTree.from_payloads(client.collections())
    root_key = project["root_collection_key"]

    kj = tree.child_named(root_key, KJ_ROOT_NAME)
    kj_key = kj.key if kj else None
    if kj_key is None:
        kj_key = _create_collection(client, session, KJ_ROOT_NAME, root_key)
        tree = CollectionTree.from_payloads(client.collections())

    inbox = tree.child_named(kj_key, INBOX_NAME)
    inbox_key = inbox.key if inbox else None
    if inbox_key is None:
        inbox_key = _create_collection(client, session, INBOX_NAME, kj_key)

    conn.execute(
        "UPDATE project SET kj_root_key = ?, kj_inbox_key = ? WHERE id = ?",
        (kj_key, inbox_key, project["id"]),
    )
    return kj_key, inbox_key


def _create_collection(
    client: ZoteroClient, session: WriteSession, name: str, parent_key: str
) -> str:
    result = session.run(
        lambda key: client.create_collections(
            [{"name": name, "parentCollection": parent_key}], key
        )
    )
    session.spend()
    ok, errors = parse_write_result(result, 1)
    if not ok:
        raise ZoteroError(f"Zotero refused to create “{name}”: {errors.get(0)}")
    return ok[0]


def materialize(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    session: WriteSession,
    project: dict[str, Any],
    *,
    kinds: tuple[str, ...] = ("quote", "idea"),
    card_ids: list[str] | None = None,
    dry_run: bool = False,
) -> MaterializeResult:
    cards = pending_cards(conn, project["id"], kinds=kinds, card_ids=card_ids)
    result = MaterializeResult(dry_run=dry_run)
    if not cards:
        return result

    if dry_run:
        # No dialog, no collection created, nothing written.
        result.preview = [
            {
                "human_id": c["human_id"],
                "kind": c["kind"],
                "destination": (
                    c["kj_path"]
                    if c.get("origin") == "group_label" and c.get("kj_path")
                    else f"{KJ_ROOT_NAME}/{INBOX_NAME}"
                ),
                "citation": citation_of(c),
                "text": c["text"],
            }
            for c in cards
        ]
        result.created = 0
        for item in result.preview:
            result.destinations[item["destination"]] = (
                result.destinations.get(item["destination"], 0) + 1
            )
        return result

    _kj_key, inbox_key = ensure_kj_collections(conn, client, session, project)
    collection_keys = {
        r["path"]: r["zotero_collection_key"]
        for r in conn.execute(
            "SELECT path, zotero_collection_key FROM collection WHERE project_id = ?",
            (project["id"],),
        )
    }
    parents = {
        r["id"]: r["human_id"]
        for r in conn.execute(
            "SELECT id, human_id FROM card WHERE project_id = ?", (project["id"],)
        )
    }

    written_keys: list[str] = []
    written_cards: list[str] = []

    for start in range(0, len(cards), WRITE_BATCH):
        batch = cards[start : start + WRITE_BATCH]
        payload = []
        for card in batch:
            collection_key = target_collection(card, collection_keys, inbox_key)
            payload.append(
                note_payload(
                    card,
                    project_name=project["name"],
                    collection_key=collection_key,
                    citation=citation_of(card),
                    parent_human_id=parents.get(card["parent_card_id"]),
                )
            )
        response = session.run(lambda key, p=payload: client.create_items(p, key))
        session.spend()
        ok, errors = parse_write_result(response, len(batch))

        for index, note_key in ok.items():
            card = batch[index]
            destination = (
                card["kj_path"]
                if card.get("origin") == "group_label" and card.get("kj_path")
                else f"{KJ_ROOT_NAME}/{INBOX_NAME}"
            )
            conn.execute(
                "UPDATE card SET zotero_note_key = ?, materialized_at = ? WHERE id = ?",
                (note_key, now_iso(), card["id"]),
            )
            result.created += 1
            result.destinations[destination] = result.destinations.get(destination, 0) + 1
            written_keys.append(note_key)
            written_cards.append(card["id"])

        for index, message in errors.items():
            result.failures.append(
                {"human_id": batch[index]["human_id"], "error": message}
            )

    if written_keys:
        result.batch_id = insert(
            conn,
            "write_batch",
            {
                "project_id": project["id"],
                "kind": "labels" if kinds == ("group_label",) else "notes",
                "created_at": now_iso(),
                "note_keys_json": json.dumps(written_keys),
                "card_ids_json": json.dumps(written_cards),
                "failures_json": json.dumps(result.failures, ensure_ascii=False),
            },
        )
    result.dialogs_shown = session.dialogs_shown
    return result


def revert(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    session: WriteSession,
    batch_id: str,
) -> RevertResult:
    """Take back one batch: delete the notes it made, forget they existed.

    Only notes from this batch are touched. A note the researcher has since
    edited is still deleted — it was this tool that put it there — but nothing
    outside the batch is ever in range.
    """
    row = conn.execute("SELECT * FROM write_batch WHERE id = ?", (batch_id,)).fetchone()
    if row is None:
        raise ZoteroError("No such batch.")
    if row["reverted_at"]:
        raise ZoteroError("That batch has already been taken back.")

    keys: list[str] = json.loads(row["note_keys_json"])
    card_ids: list[str] = json.loads(row["card_ids_json"])
    result = RevertResult(batch_id=batch_id)

    alive = []
    for key in keys:
        if client.item(key) is None:
            result.already_gone += 1
        else:
            alive.append(key)

    for start in range(0, len(alive), WRITE_BATCH):
        chunk = alive[start : start + WRITE_BATCH]
        version = client.library_version()
        try:
            session.run(lambda key, c=chunk, v=version: client.delete_items(c, key, v))
            session.spend()
            result.deleted += len(chunk)
        except ZoteroError as e:
            result.failures.append(str(e))

    if not result.failures:
        marks = ", ".join("?" for _ in card_ids)
        conn.execute(
            f"UPDATE card SET zotero_note_key = NULL, materialized_at = NULL, "
            f"kj_collection_keys_json = NULL, kj_path = NULL WHERE id IN ({marks})",
            card_ids,
        )
        conn.execute(
            "UPDATE write_batch SET reverted_at = ? WHERE id = ?",
            (now_iso(), batch_id),
        )
    return result
