"""The argument layer: a question, its claims, the sections, and the evidence.

Nothing here drags. A card's section is a select and so are its citation mode
and its role, because the thing being decided is what a passage *does* in an
argument, and that is a choice from a short list rather than a position on a
board.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .cards import citation_of
from .continuations import attach
from .store import insert, now_iso

CITATION_MODES = ("direct_quote", "paraphrase", "reference_only")
ARGUMENT_ROLES = (
    "evidence",
    "counterevidence",
    "background",
    "definition",
    "method",
    "example",
)


# -- research questions ---------------------------------------------------


def list_questions(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM research_question WHERE project_id = ? "
            "ORDER BY status = 'chosen' DESC, sort_order, created_at",
            (project_id,),
        )
    ]


def add_question(
    conn: sqlite3.Connection,
    project_id: str,
    text: str,
    *,
    rationale: str = "",
    origin: str = "mine",
) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("A question needs to be asked.")
    question_id = insert(
        conn,
        "research_question",
        {
            "project_id": project_id,
            "text": text,
            "rationale": rationale.strip() or None,
            "origin": origin,
            "created_at": now_iso(),
        },
    )
    return _one(conn, "research_question", question_id)


def choose_question(
    conn: sqlite3.Connection, project_id: str, question_id: str
) -> dict[str, Any]:
    """One question at a time is the paper's. The rest stay as candidates."""
    conn.execute(
        "UPDATE research_question SET status = 'candidate' "
        "WHERE project_id = ? AND status = 'chosen'",
        (project_id,),
    )
    conn.execute(
        "UPDATE research_question SET status = 'chosen' WHERE id = ? AND project_id = ?",
        (question_id, project_id),
    )
    row = _one(conn, "research_question", question_id)
    conn.execute(
        "UPDATE project SET research_question = ? WHERE id = ?", (row["text"], project_id)
    )
    return row


def chosen_question(conn: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM research_question WHERE project_id = ? AND status = 'chosen'",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


# -- claims ---------------------------------------------------------------


def list_claims(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM claim WHERE project_id = ? ORDER BY sort_order, created_at",
            (project_id,),
        )
    ]


def add_claim(
    conn: sqlite3.Connection,
    project_id: str,
    text: str,
    *,
    claim_type: str = "supporting",
    research_question_id: str | None = None,
) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("A claim needs to say something.")
    claim_id = insert(
        conn,
        "claim",
        {
            "project_id": project_id,
            "text": text,
            "claim_type": claim_type,
            "research_question_id": research_question_id,
            "sort_order": _next_order(conn, "claim", "project_id", project_id),
            "created_at": now_iso(),
        },
    )
    return _one(conn, "claim", claim_id)


# -- sections -------------------------------------------------------------


def list_sections(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    sections = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM outline_section WHERE project_id = ? "
            "ORDER BY sort_order, created_at",
            (project_id,),
        )
    ]
    counts = {
        r["section_id"]: r["n"]
        for r in conn.execute(
            "SELECT section_id, COUNT(*) AS n FROM section_card_usage "
            "WHERE include = 1 GROUP BY section_id"
        )
    }
    for section in sections:
        section["evidence_count"] = counts.get(section["id"], 0)
    return sections


def add_section(
    conn: sqlite3.Connection,
    project_id: str,
    title: str,
    *,
    purpose: str = "",
    thesis: str = "",
    target_words: int | None = None,
    parent_section_id: str | None = None,
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise ValueError("A section needs a title.")
    section_id = insert(
        conn,
        "outline_section",
        {
            "project_id": project_id,
            "parent_section_id": parent_section_id,
            "title": title,
            "purpose": purpose.strip() or None,
            "thesis": thesis.strip() or None,
            "target_words": target_words,
            "sort_order": _next_order(conn, "outline_section", "project_id", project_id),
            "created_at": now_iso(),
        },
    )
    return _one(conn, "outline_section", section_id)


def update_section(
    conn: sqlite3.Connection, section_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    allowed = {"title", "purpose", "thesis", "target_words", "sort_order"}
    patch = {k: v for k, v in changes.items() if k in allowed}
    if patch:
        sets = ", ".join(f"{k} = ?" for k in patch)
        conn.execute(
            f"UPDATE outline_section SET {sets} WHERE id = ?",
            (*patch.values(), section_id),
        )
    return _one(conn, "outline_section", section_id)


def delete_section(conn: sqlite3.Connection, section_id: str) -> None:
    conn.execute("DELETE FROM outline_section WHERE id = ?", (section_id,))


# -- evidence -------------------------------------------------------------


def assign_card(
    conn: sqlite3.Connection,
    section_id: str,
    card_id: str,
    *,
    citation_mode: str = "paraphrase",
    argument_role: str = "evidence",
    user_instruction: str = "",
    include: bool = True,
) -> dict[str, Any]:
    if citation_mode not in CITATION_MODES:
        raise ValueError(f"citation_mode must be one of {CITATION_MODES}")
    if argument_role not in ARGUMENT_ROLES:
        raise ValueError(f"argument_role must be one of {ARGUMENT_ROLES}")
    row = conn.execute(
        "SELECT id FROM section_card_usage WHERE section_id = ? AND card_id = ?",
        (section_id, card_id),
    ).fetchone()
    values = {
        "citation_mode": citation_mode,
        "argument_role": argument_role,
        "user_instruction": user_instruction.strip() or None,
        "include": int(include),
    }
    if row:
        sets = ", ".join(f"{k} = ?" for k in values)
        conn.execute(
            f"UPDATE section_card_usage SET {sets} WHERE id = ?",
            (*values.values(), row["id"]),
        )
        usage_id = row["id"]
    else:
        usage_id = insert(
            conn,
            "section_card_usage",
            {
                "section_id": section_id,
                "card_id": card_id,
                "sort_order": _next_order(
                    conn, "section_card_usage", "section_id", section_id
                ),
                **values,
            },
        )
    return _one(conn, "section_card_usage", usage_id)


def unassign_card(conn: sqlite3.Connection, section_id: str, card_id: str) -> None:
    conn.execute(
        "DELETE FROM section_card_usage WHERE section_id = ? AND card_id = ?",
        (section_id, card_id),
    )


def section_evidence(
    conn: sqlite3.Connection, section_id: str, *, included_only: bool = True
) -> list[dict[str, Any]]:
    """Every card assigned to a section, with what it is doing there."""
    # Not CARD_SELECT: this query is driven by the usage row, so the join runs
    # the other way round.
    sql = (
        "SELECT c.*, s.zotero_item_key AS source_key, s.title AS source_title, "
        "s.creators_short, s.year AS source_year, s.publication_title, "
        "u.citation_mode, u.argument_role, u.user_instruction, u.include, "
        "u.sort_order AS usage_order "
        "FROM section_card_usage u "
        "JOIN card c ON c.id = u.card_id "
        "LEFT JOIN source s ON s.id = c.source_id "
        "WHERE u.section_id = ?"
    )
    if included_only:
        sql += " AND u.include = 1"
    sql += " ORDER BY u.sort_order, c.human_id"
    rows = [dict(r) for r in conn.execute(sql, (section_id,))]
    for row in rows:
        row["citation"] = citation_of(row)
    attach(conn, rows)
    return rows


# -- groups as sections ---------------------------------------------------


def adopt_groups_as_sections(
    conn: sqlite3.Connection, project_id: str
) -> list[dict[str, Any]]:
    """Turn each group into a section, carrying its cards in as evidence.

    The passages a researcher put together are already their claim about what
    belongs with what — that is the whole point of having sorted them. This
    makes that grouping into an outline they can rename, reorder and prune,
    without asking them to build one from nothing.

    A section that already carries the same name is the same section — a
    researcher who wrote "AI is politics" as a group's label and then made a
    section called "AI is politics" meant one thing, and the tool has no
    business treating them as two. An empty one of those is filled from the
    group; one that already holds evidence is left exactly as it is.
    """
    from .groups import list_groups  # circular at module level

    existing = {s["title"]: s for s in list_sections(conn, project_id)}
    made: list[dict[str, Any]] = []
    for group in list_groups(conn, project_id):
        title = group.label["text"].split("\n\n")[0] if group.label else group.name
        title = title.rstrip(".")
        already = existing.get(title)
        if already is not None:
            if already["evidence_count"]:
                continue
            _fill_section(conn, already["id"], group.cards)
            made.append(_one(conn, "outline_section", already["id"]))
            continue
        section = add_section(
            conn,
            project_id,
            title,
            purpose=""
            if group.label
            else f"What the passages under “{group.name}” add up to.",
        )
        _fill_section(conn, section["id"], group.cards)
        existing[title] = {**section, "evidence_count": len(group.cards)}
        made.append(section)
    return made


def _fill_section(
    conn: sqlite3.Connection, section_id: str, cards: list[dict[str, Any]]
) -> None:
    for card in cards:
        assign_card(
            conn,
            section_id,
            card["id"],
            citation_mode="paraphrase" if card["kind"] == "quote" else "reference_only",
        )


def move_section(conn: sqlite3.Connection, section_id: str, delta: int) -> list[dict[str, Any]]:
    """Move one section up or down. Order is the researcher's to set."""
    row = conn.execute(
        "SELECT project_id FROM outline_section WHERE id = ?", (section_id,)
    ).fetchone()
    if row is None:
        raise ValueError("No such section.")
    sections = list_sections(conn, row["project_id"])
    index = next((i for i, s in enumerate(sections) if s["id"] == section_id), None)
    if index is None:
        raise ValueError("No such section.")
    target = index + delta
    if not 0 <= target < len(sections):
        return sections
    sections[index], sections[target] = sections[target], sections[index]
    for position, section in enumerate(sections):
        conn.execute(
            "UPDATE outline_section SET sort_order = ? WHERE id = ?",
            (position, section["id"]),
        )
    return list_sections(conn, row["project_id"])


def unassigned_cards(
    conn: sqlite3.Connection, project_id: str
) -> list[dict[str, Any]]:
    """Cards no section is using — the material still waiting to be placed."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT c.*, s.zotero_item_key AS source_key, s.title AS source_title, "
            "s.creators_short, s.year AS source_year, s.publication_title "
            "FROM card c LEFT JOIN source s ON s.id = c.source_id "
            "WHERE c.project_id = ? AND c.status = 'active' AND c.kind != 'image' "
            "AND c.origin != 'group_label' AND c.id NOT IN "
            "(SELECT card_id FROM section_card_usage WHERE include = 1) "
            "ORDER BY COALESCE(c.kj_path, c.prior_path), c.human_id",
            (project_id,),
        )
    ]
    for row in rows:
        row["citation"] = citation_of(row)
    attach(conn, rows)
    # A half-sentence written out on its own would be quoted on its own; the
    # whole passage travels under the card it starts on.
    return [r for r in rows if not r["is_continuation"]]


# -- helpers --------------------------------------------------------------


def _one(conn: sqlite3.Connection, table: str, row_id: str) -> dict[str, Any]:
    return dict(conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone())


def _next_order(
    conn: sqlite3.Connection, table: str, column: str, value: str
) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM {table} WHERE {column} = ?",
        (value,),
    ).fetchone()
    return row["n"]
