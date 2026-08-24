"""Groups and their labels, and the outline-versus-evidence comparison."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..groups import as_dict, group_summary, list_groups, save_label
from ..materialize import materialize
from ..structure import NotEnoughToCompare, compare
from ..zotero import ZoteroClient, ZoteroError
from .deps import get_client, get_db
from .routes_writes import _project, _refuse_other_database, session_for

router = APIRouter(prefix="/api/projects/{project_id}", tags=["groups"])


class LabelIn(BaseModel):
    path: str
    label: str
    note: str = ""


@router.get("/groups")
def get_groups(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    _project(conn, project_id)
    return {
        "groups": [as_dict(g) for g in list_groups(conn, project_id)],
        "summary": group_summary(conn, project_id),
    }


@router.put("/groups/label")
def put_label(
    project_id: str,
    body: LabelIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    _project(conn, project_id)
    try:
        card = save_label(conn, project_id, body.path, body.label, body.note)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"id": card["id"], "human_id": card["human_id"], "text": card["text"]}


@router.post("/groups/push")
def push_labels(
    project_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """File each label in Zotero, inside the collection it names."""
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    label_ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM card WHERE project_id = ? AND origin = 'group_label' "
            "AND zotero_note_key IS NULL",
            (project_id,),
        )
    ]
    if not label_ids:
        return {"created": 0, "destinations": {}, "failures": []}
    try:
        result = materialize(
            conn, client, session, project, kinds=("idea",), card_ids=label_ids
        )
    except ZoteroError as e:
        raise HTTPException(502, str(e)) from e
    return result.as_dict()


@router.get("/structure")
def get_structure(
    project_id: str,
    basis: str | None = None,
    k: int | None = None,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    _project(conn, project_id)
    try:
        return compare(conn, project_id, basis=basis, k=k).as_dict()
    except NotEnoughToCompare as e:
        raise HTTPException(422, str(e)) from e
