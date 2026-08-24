"""Groups: the collections the researcher dragged cards into, and what they
claim.

The grouping itself happens in Zotero — this app does not rebuild a card board,
because Zotero already has one that the researcher knows, with search, tags,
colours and undo. What Zotero has no place for is the *proposition*: the one
sentence saying what a group of passages actually claims. That is what this
module is for, and writing one is where the thinking happens.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .cards import CARD_SELECT, citation_of
from .similarity import least_alike
from .store import new_id, now_iso

LABEL_ORIGIN = "group_label"


@dataclass
class Group:
    path: str
    name: str
    collection_key: str | None
    cards: list[dict[str, Any]] = field(default_factory=list)
    label: dict[str, Any] | None = None
    least_alike: tuple[str, str] | None = None

    @property
    def size(self) -> int:
        return len(self.cards)


def _member_cards(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            CARD_SELECT
            + " WHERE c.project_id = ? AND c.kj_path IS NOT NULL "
            "AND c.origin != ? AND c.status = 'active' "
            "ORDER BY c.kj_path, c.human_id",
            (project_id, LABEL_ORIGIN),
        )
    ]


def list_groups(conn: sqlite3.Connection, project_id: str) -> list[Group]:
    """Every collection holding cards, with its members and its label."""
    by_path: dict[str, list[dict[str, Any]]] = {}
    for card in _member_cards(conn, project_id):
        by_path.setdefault(card["kj_path"], []).append(card)

    labels = {
        r["kj_path"]: dict(r)
        for r in conn.execute(
            CARD_SELECT + " WHERE c.project_id = ? AND c.origin = ?",
            (project_id, LABEL_ORIGIN),
        )
    }
    collection_keys = {
        r["path"]: r["zotero_collection_key"]
        for r in conn.execute(
            "SELECT path, zotero_collection_key FROM collection WHERE project_id = ?",
            (project_id,),
        )
    }

    groups: list[Group] = []
    for path, cards in sorted(by_path.items()):
        # A group worth labelling holds evidence, or holds more than one card.
        # A single note filed somewhere is already its own statement.
        if len(cards) < 2 and not any(c["kind"] in ("quote", "image") for c in cards):
            continue
        pair = least_alike([c["text"] for c in cards])
        groups.append(
            Group(
                path=path,
                name=path.rsplit("/", 1)[-1],
                collection_key=collection_keys.get(path),
                cards=cards,
                label=labels.get(path),
                least_alike=(
                    (cards[pair.first]["human_id"], cards[pair.second]["human_id"])
                    if pair
                    else None
                ),
            )
        )
    return groups


def ungrouped_count(conn: sqlite3.Connection, project_id: str) -> int:
    """Cards whose note is in Zotero but still sitting in Inbox."""
    return conn.execute(
        "SELECT COUNT(*) FROM card WHERE project_id = ? AND materialized_at IS NOT NULL "
        "AND kj_path IS NULL AND status = 'active'",
        (project_id,),
    ).fetchone()[0]


def save_label(
    conn: sqlite3.Connection,
    project_id: str,
    path: str,
    label: str,
    note: str = "",
) -> dict[str, Any]:
    """Write, or rewrite, the proposition for one group.

    A label is an idea card like any other — the researcher's own words — so
    it can be cited, exported and pushed into Zotero the same way. Re-saving
    updates the same card rather than making a second one, which falls out of
    the origin key being derived from the group's path.
    """
    text = f"{label.strip()}\n\n{note.strip()}".strip() if note.strip() else label.strip()
    if not text:
        raise ValueError("A label needs a sentence.")

    origin_key = f"group:{path}"
    collection = conn.execute(
        "SELECT id FROM collection WHERE project_id = ? AND path = ?",
        (project_id, path),
    ).fetchone()

    existing = conn.execute(
        "SELECT id FROM card WHERE project_id = ? AND origin_key = ?",
        (project_id, origin_key),
    ).fetchone()

    if existing:
        conn.execute(
            "UPDATE card SET text = ?, updated_at = ? WHERE id = ?",
            (text, now_iso(), existing["id"]),
        )
        card_id = existing["id"]
    else:
        card_id = new_id()
        row = conn.execute(
            "SELECT human_id FROM card WHERE project_id = ? ORDER BY human_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        number = int(row["human_id"][3:]) + 1 if row else 1
        conn.execute(
            "INSERT INTO card (id, project_id, human_id, origin_key, kind, origin, "
            "text, prior_collection_id, prior_path, kj_path, content_hash, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card_id,
                project_id,
                f"KJ-{number:04d}",
                origin_key,
                LABEL_ORIGIN,
                text,
                collection["id"] if collection else None,
                path,
                path,
                new_id(),
                now_iso(),
                now_iso(),
            ),
        )

    return dict(
        conn.execute(CARD_SELECT + " WHERE c.id = ?", (card_id,)).fetchone()
    )


def group_summary(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    groups = list_groups(conn, project_id)
    return {
        "groups": len(groups),
        "labelled": sum(1 for g in groups if g.label),
        "cards_grouped": sum(g.size for g in groups),
        "ungrouped": ungrouped_count(conn, project_id),
    }


def as_dict(group: Group) -> dict[str, Any]:
    return {
        "path": group.path,
        "name": group.name,
        "collection_key": group.collection_key,
        "size": group.size,
        "least_alike": list(group.least_alike) if group.least_alike else None,
        "label": (
            {
                "id": group.label["id"],
                "human_id": group.label["human_id"],
                "text": group.label["text"],
                "in_zotero": bool(group.label["zotero_note_key"]),
            }
            if group.label
            else None
        ),
        "cards": [
            {
                "id": c["id"],
                "human_id": c["human_id"],
                "kind": c["kind"],
                "origin": c["origin"],
                "text": c["text"],
                "citation": citation_of(c),
                "locator_estimated": bool(c["locator_estimated"]),
                "color": c["color"],
            }
            for c in group.cards
        ],
    }
