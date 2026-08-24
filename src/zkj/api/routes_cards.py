"""Cards: the reading surface's data."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from ..cards import CardFilters, facets, list_cards
from ..store import now_iso
from .deps import get_db
from .schemas import CardOut, CardPageOut, FacetsOut
from .serialise import card_out

router = APIRouter(prefix="/api/projects/{project_id}", tags=["cards"])


def _exists(conn: sqlite3.Connection, project_id: str) -> None:
    if conn.execute("SELECT 1 FROM project WHERE id = ?", (project_id,)).fetchone() is None:
        raise HTTPException(404, f"No project {project_id}.")


@router.get("/cards", response_model=CardPageOut)
def get_cards(
    project_id: str,
    kind: str | None = None,
    origin: str | None = None,
    source_id: str | None = None,
    year: str | None = None,
    color: str | None = None,
    locator_type: str | None = None,
    group: str | None = None,
    prior_path: str | None = None,
    has_comment: bool | None = None,
    estimated_only: bool = False,
    status: str | None = "active",
    search: str | None = None,
    order: str = "reading",
    limit: int = Query(100, le=500),
    offset: int = 0,
    conn: sqlite3.Connection = Depends(get_db),
) -> CardPageOut:
    _exists(conn, project_id)
    page = list_cards(
        conn,
        project_id,
        CardFilters(
            kind=kind,
            origin=origin,
            source_id=source_id,
            year=year,
            color=color,
            locator_type=locator_type,
            group=group,
            prior_path=prior_path,
            has_comment=has_comment,
            estimated_only=estimated_only,
            status=status if status != "any" else None,
            search=search,
            order=order,
            limit=limit,
            offset=offset,
        ),
    )
    return CardPageOut(
        cards=[card_out(c) for c in page.cards],
        total=page.total,
        counts=page.counts,
    )


@router.get("/facets", response_model=FacetsOut)
def get_facets(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> FacetsOut:
    _exists(conn, project_id)
    return FacetsOut(**facets(conn, project_id))


@router.get("/cards/{card_id}", response_model=CardOut)
def get_card(
    project_id: str, card_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> CardOut:
    row = conn.execute(
        "SELECT c.*, s.zotero_item_key AS source_key, s.title AS source_title, "
        "s.creators_short, s.year AS source_year, s.publication_title "
        "FROM card c LEFT JOIN source s ON s.id = c.source_id "
        "WHERE c.project_id = ? AND c.id = ?",
        (project_id, card_id),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "No such card.")
    card = dict(row)
    card["linked_ideas"] = [
        dict(r)
        for r in conn.execute(
            "SELECT id, human_id, kind, origin, text FROM card WHERE parent_card_id = ?",
            (card_id,),
        )
    ]
    card["parent"] = None
    if card["parent_card_id"]:
        parent = conn.execute(
            "SELECT id, human_id, kind, origin, text FROM card WHERE id = ?",
            (card["parent_card_id"],),
        ).fetchone()
        card["parent"] = dict(parent) if parent else None
    return card_out(card)


@router.patch("/cards/{card_id}", response_model=CardOut)
def update_card(
    project_id: str,
    card_id: str,
    payload: dict,
    conn: sqlite3.Connection = Depends(get_db),
) -> CardOut:
    """The researcher's own columns. Never the quotation itself.

    ``text`` and ``text_raw`` are deliberately absent: a quotation is evidence
    and this app does not offer to edit it.
    """
    editable = {"human_label", "status"}
    changes = {k: v for k, v in payload.items() if k in editable}
    if not changes:
        raise HTTPException(422, f"Nothing editable here. Allowed: {sorted(editable)}.")
    if "status" in changes and changes["status"] not in ("active", "excluded"):
        raise HTTPException(422, "status must be 'active' or 'excluded'.")
    sets = ", ".join(f"{k} = ?" for k in changes)
    updated = conn.execute(
        f"UPDATE card SET {sets}, updated_at = ? WHERE project_id = ? AND id = ?",
        (*changes.values(), now_iso(), project_id, card_id),
    ).rowcount
    if not updated:
        raise HTTPException(404, "No such card.")
    return get_card(project_id, card_id, conn)
