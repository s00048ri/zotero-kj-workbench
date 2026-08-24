"""Reading cards back out: filters, search, and the counts worth showing.

The Cards screen is where the researcher spends real time, so the queries here
are written for reading rather than for listing. Two things they must get
right:

* search has to work on Japanese and French as well as English. The index is
  FTS5 with a trigram tokeniser, which is language-agnostic — but trigrams
  need three characters, so a one- or two-character query (common in Japanese)
  falls back to a LIKE scan rather than silently returning nothing;
* a quote and the researcher's own note on it belong together on screen, so a
  quote card carries its linked idea cards with it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .locators import Locator

CARD_SELECT = """
SELECT c.*, s.zotero_item_key AS source_key, s.title AS source_title,
       s.creators_short, s.year AS source_year, s.publication_title
FROM card c
LEFT JOIN source s ON s.id = c.source_id
"""

MIN_TRIGRAM = 3


@dataclass
class CardFilters:
    kind: str | None = None
    origin: str | None = None
    source_id: str | None = None
    year: str | None = None
    color: str | None = None
    locator_type: str | None = None
    group: str | None = None          # a path under _KJ the card was filed into
    prior_path: str | None = None     # the collection its source sits in
    has_comment: bool | None = None
    estimated_only: bool = False
    status: str | None = "active"
    search: str | None = None
    order: str = "reading"            # reading | newest | source
    limit: int = 100
    offset: int = 0

    def where(self) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        simple = {
            "c.kind": self.kind,
            "c.origin": self.origin,
            "c.source_id": self.source_id,
            "s.year": self.year,
            "c.color": self.color,
            "c.locator_type": self.locator_type,
            "c.kj_path": self.group,
            "c.prior_path": self.prior_path,
            "c.status": self.status,
        }
        for column, value in simple.items():
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        if self.estimated_only:
            clauses.append("c.locator_estimated = 1")
        if self.has_comment is not None:
            exists = (
                "EXISTS (SELECT 1 FROM card k WHERE k.parent_card_id = c.id "
                "AND k.origin = 'annotation_comment')"
            )
            clauses.append(exists if self.has_comment else f"NOT {exists}")
        return (" AND ".join(clauses), params)

    def order_by(self) -> str:
        if self.order == "newest":
            return "c.created_at DESC, c.human_id DESC"
        if self.order == "source":
            return "s.creators_short, s.year, c.sort_index, c.human_id"
        # reading order: the researcher's own outline, then position in the text
        return "c.prior_path, s.creators_short, s.year, c.sort_index, c.human_id"


def _search_clause(term: str) -> tuple[str, list[Any]]:
    term = term.strip()
    if len(term) >= MIN_TRIGRAM:
        quoted = '"' + term.replace('"', '""') + '"'
        return (
            "c.rowid IN (SELECT rowid FROM card_fts WHERE card_fts MATCH ?)",
            [quoted],
        )
    # Too short for trigrams. Rare, and cheap enough on one researcher's cards.
    like = f"%{term}%"
    return ("(c.text LIKE ? OR COALESCE(c.human_label, '') LIKE ?)", [like, like])


@dataclass
class CardPage:
    cards: list[dict[str, Any]]
    total: int
    counts: dict[str, int] = field(default_factory=dict)


def list_cards(
    conn: sqlite3.Connection, project_id: str, filters: CardFilters | None = None
) -> CardPage:
    filters = filters or CardFilters()
    where, params = filters.where()
    clauses = ["c.project_id = ?"]
    values: list[Any] = [project_id]
    if where:
        clauses.append(where)
        values += params
    if filters.search and filters.search.strip():
        clause, search_params = _search_clause(filters.search)
        clauses.append(clause)
        values += search_params

    condition = " AND ".join(clauses)
    total = conn.execute(
        f"SELECT COUNT(*) FROM card c LEFT JOIN source s ON s.id = c.source_id "
        f"WHERE {condition}",
        values,
    ).fetchone()[0]

    rows = conn.execute(
        f"{CARD_SELECT} WHERE {condition} ORDER BY {filters.order_by()} "
        f"LIMIT ? OFFSET ?",
        (*values, filters.limit, filters.offset),
    ).fetchall()

    cards = [dict(r) for r in rows]
    _attach_relations(conn, project_id, cards)
    return CardPage(cards=cards, total=total, counts=summary(conn, project_id))


def _attach_relations(
    conn: sqlite3.Connection, project_id: str, cards: list[dict[str, Any]]
) -> None:
    """Give every quote card its linked ideas, and every idea its quote."""
    if not cards:
        return
    ids = [c["id"] for c in cards]
    marks = ", ".join("?" for _ in ids)

    children: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
        f"SELECT id, human_id, kind, origin, text, parent_card_id FROM card "
        f"WHERE parent_card_id IN ({marks}) ORDER BY created_at",
        ids,
    ):
        children.setdefault(row["parent_card_id"], []).append(dict(row))

    parent_ids = [c["parent_card_id"] for c in cards if c["parent_card_id"]]
    parents: dict[str, dict[str, Any]] = {}
    if parent_ids:
        marks = ", ".join("?" for _ in parent_ids)
        for row in conn.execute(
            f"SELECT id, human_id, kind, text FROM card WHERE id IN ({marks})",
            parent_ids,
        ):
            parents[row["id"]] = dict(row)

    for card in cards:
        card["linked_ideas"] = children.get(card["id"], [])
        card["parent"] = parents.get(card["parent_card_id"]) if card["parent_card_id"] else None


def summary(conn: sqlite3.Connection, project_id: str) -> dict[str, int]:
    """The counter on the Cards screen: visible, and not a nag."""
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          SUM(kind = 'quote') AS quotes,
          SUM(kind = 'idea') AS ideas,
          SUM(kind = 'image') AS images,
          SUM(status = 'excluded') AS excluded,
          SUM(zotero_note_key IS NOT NULL) AS in_zotero,
          SUM(kj_path IS NOT NULL) AS grouped,
          SUM(locator_type = 'none') AS without_locator,
          SUM(locator_estimated = 1) AS estimated_locators
        FROM card WHERE project_id = ?
        """,
        (project_id,),
    ).fetchone()
    counts = {k: (row[k] or 0) for k in row.keys()}
    counts["quotes_with_my_note"] = conn.execute(
        "SELECT COUNT(DISTINCT c.id) FROM card c JOIN card k ON k.parent_card_id = c.id "
        "WHERE c.project_id = ? AND c.kind = 'quote' AND k.origin = 'annotation_comment'",
        (project_id,),
    ).fetchone()[0]
    return counts


def facets(conn: sqlite3.Connection, project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Only offer a filter that would actually narrow anything."""

    def group_by(sql: str, *params: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql, (project_id, *params))]

    return {
        "sources": group_by(
            "SELECT s.id AS value, "
            "COALESCE(s.creators_short, 'Anon.') || COALESCE(' ' || s.year, '') "
            "|| ' — ' || COALESCE(s.title, '(untitled)') AS label, COUNT(*) AS count "
            "FROM card c JOIN source s ON s.id = c.source_id "
            "WHERE c.project_id = ? GROUP BY s.id ORDER BY label"
        ),
        "years": group_by(
            "SELECT s.year AS value, s.year AS label, COUNT(*) AS count "
            "FROM card c JOIN source s ON s.id = c.source_id "
            "WHERE c.project_id = ? AND s.year IS NOT NULL "
            "GROUP BY s.year ORDER BY s.year DESC"
        ),
        "colors": group_by(
            "SELECT color AS value, color AS label, COUNT(*) AS count FROM card "
            "WHERE project_id = ? AND color IS NOT NULL GROUP BY color "
            "ORDER BY count DESC"
        ),
        "kinds": group_by(
            "SELECT kind AS value, kind AS label, COUNT(*) AS count FROM card "
            "WHERE project_id = ? GROUP BY kind ORDER BY count DESC"
        ),
        "origins": group_by(
            "SELECT origin AS value, origin AS label, COUNT(*) AS count FROM card "
            "WHERE project_id = ? GROUP BY origin ORDER BY count DESC"
        ),
        "locator_types": group_by(
            "SELECT locator_type AS value, locator_type AS label, COUNT(*) AS count "
            "FROM card WHERE project_id = ? GROUP BY locator_type ORDER BY count DESC"
        ),
        "prior_paths": group_by(
            "SELECT prior_path AS value, prior_path AS label, COUNT(*) AS count "
            "FROM card WHERE project_id = ? AND prior_path IS NOT NULL "
            "GROUP BY prior_path ORDER BY prior_path"
        ),
        "groups": group_by(
            "SELECT kj_path AS value, kj_path AS label, COUNT(*) AS count "
            "FROM card WHERE project_id = ? AND kj_path IS NOT NULL "
            "GROUP BY kj_path ORDER BY kj_path"
        ),
    }


def locator_of(card: dict[str, Any]) -> Locator:
    detail = card.get("locator_detail_json")
    return Locator(
        type=card.get("locator_type") or "none",
        value=card.get("locator_value") or "",
        source=card.get("locator_source") or "none",
        estimated=bool(card.get("locator_estimated")),
        detail=json.loads(detail) if detail else {},
    )


def citation_of(card: dict[str, Any]) -> str:
    """“Smith 2025, p. 132”. Author-only when the item has no date."""
    name = card.get("creators_short")
    if not name:
        return ""
    head = " ".join(filter(None, [name, card.get("source_year")]))
    locator = locator_of(card).render()
    return ", ".join(filter(None, [head, locator]))
