"""Permission, writing notes into Zotero, and taking a batch back."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from ..materialize import materialize, pending_cards, revert
from ..writes import WriteSession
from ..zotero import ZoteroClient, ZoteroError
from .deps import get_client, get_db

router = APIRouter(tags=["writes"])


class AuthorizeOut(BaseModel):
    available: bool
    remembered: bool
    message: str


class MaterializeIn(BaseModel):
    card_ids: list[str] | None = None
    kinds: list[str] = ["quote", "idea"]
    dry_run: bool = False


def session_for(conn: sqlite3.Connection, client: ZoteroClient) -> WriteSession:
    return WriteSession(client, conn)


def _project(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No project {project_id}.")
    project = dict(row)
    return project


def _refuse_other_database(project: dict[str, Any], client: ZoteroClient) -> None:
    server_id = client.server_info().server_id
    if project["zotero_server_id"] and server_id and project["zotero_server_id"] != server_id:
        raise HTTPException(
            409,
            "This project was imported from a different Zotero database. "
            "Writing into this one would attach its cards to the wrong library.",
        )


@router.get("/api/write-permission", response_model=AuthorizeOut)
def write_permission(
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> AuthorizeOut:
    session = session_for(conn, client)
    if not session.available:
        return AuthorizeOut(
            available=False,
            remembered=False,
            message="This Zotero cannot accept notes from other applications.",
        )
    if session.has_remembered_key:
        return AuthorizeOut(
            available=True,
            remembered=True,
            message="Zotero has given standing permission, so no further dialogs.",
        )
    return AuthorizeOut(
        available=True,
        remembered=False,
        message=(
            "Zotero will ask for permission. Choose “Always Allow” — with plain "
            "“Allow” the permission is used up by the first note written, and "
            "Zotero only shows five dialogs a minute."
        ),
    )


@router.post("/api/write-permission", response_model=AuthorizeOut)
def authorize(
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> AuthorizeOut:
    session = session_for(conn, client)
    session.acquire(force=True)
    return AuthorizeOut(
        available=True,
        remembered=session.remembered,
        message=(
            "Standing permission granted — nothing more to approve."
            if session.remembered
            else "Permission granted for one write. Zotero will ask again for "
            "each batch; choosing “Always Allow” avoids that."
        ),
    )


@router.delete("/api/write-permission", response_model=AuthorizeOut)
def forget_permission(
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> AuthorizeOut:
    session = session_for(conn, client)
    session.forget()
    return AuthorizeOut(
        available=session.available,
        remembered=False,
        message="Permission forgotten. Zotero will ask again next time.",
    )


@router.get("/api/projects/{project_id}/pending")
def pending(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    _project(conn, project_id)
    cards = pending_cards(conn, project_id)
    return {
        "count": len(cards),
        "by_kind": {
            kind: sum(1 for c in cards if c["kind"] == kind)
            for kind in sorted({c["kind"] for c in cards})
        },
    }


@router.post("/api/projects/{project_id}/notes")
def create_notes(
    project_id: str,
    body: MaterializeIn = Body(default=MaterializeIn()),
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    try:
        result = materialize(
            conn,
            client,
            session,
            project,
            kinds=tuple(body.kinds),
            card_ids=body.card_ids,
            dry_run=body.dry_run,
        )
    except ZoteroError as e:
        raise HTTPException(502, str(e)) from e
    return result.as_dict()


@router.get("/api/projects/{project_id}/batches")
def batches(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict[str, Any]]:
    _project(conn, project_id)
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "created_at": r["created_at"],
            "reverted_at": r["reverted_at"],
            "notes": len(json.loads(r["note_keys_json"])),
            "failures": len(json.loads(r["failures_json"] or "[]")),
        }
        for r in conn.execute(
            "SELECT * FROM write_batch WHERE project_id = ? ORDER BY created_at DESC "
            "LIMIT 20",
            (project_id,),
        )
    ]


@router.post("/api/projects/{project_id}/batches/{batch_id}/revert")
def revert_batch(
    project_id: str,
    batch_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    try:
        result = revert(conn, client, session, batch_id)
    except ZoteroError as e:
        raise HTTPException(409, str(e)) from e
    return {
        "batch_id": result.batch_id,
        "deleted": result.deleted,
        "already_gone": result.already_gone,
        "failures": result.failures,
    }
