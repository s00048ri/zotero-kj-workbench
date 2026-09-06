"""Machine summaries of attachments: generate, keep, write into Zotero.

Two steps on purpose. Generating costs money and touches nothing; writing
touches Zotero and is what the researcher approves. Neither one implies the
other, and a summary can be read here and never written at all.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from .. import llm, summarise
from ..zotero import ZoteroClient, ZoteroError
from .deps import get_client, get_db
from .routes_writes import _project, _refuse_other_database, session_for

router = APIRouter(tags=["summaries"])


class SummariseIn(BaseModel):
    attachment_ids: list[str]
    effort: str = "medium"
    replace: bool = False


class WriteNotesIn(BaseModel):
    summary_ids: list[str] | None = None


@router.get("/api/projects/{project_id}/attachments")
def list_attachments(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    _project(conn, project_id)
    rows = summarise.attachments(conn, project_id)
    return {
        "attachments": rows,
        "sendable": sum(1 for r in rows if r["sendable"]),
        "summarised": sum(1 for r in rows if r["summary_id"]),
        "llm": llm.availability().as_dict(),
    }


@router.get("/api/projects/{project_id}/summaries")
def list_summaries(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> list[dict[str, Any]]:
    _project(conn, project_id)
    return summarise.summaries(conn, project_id)


@router.post("/api/projects/{project_id}/summaries")
def create_summaries(
    project_id: str,
    body: SummariseIn,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """Read the files and keep what comes back. Zotero is not written to."""
    project = _project(conn, project_id)
    if not body.attachment_ids:
        raise HTTPException(422, "Nothing to summarise.")
    state = llm.availability()
    if not state.ready:
        raise HTTPException(409, state.as_dict())
    result = summarise.summarise(
        conn,
        client,
        project,
        body.attachment_ids,
        effort=body.effort,
        replace=body.replace,
    )
    return result.as_dict()


@router.post("/api/projects/{project_id}/summaries/notes")
def write_summary_notes(
    project_id: str,
    body: WriteNotesIn = Body(default=WriteNotesIn()),
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """File the summaries in Zotero, beside the sources they are about."""
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    try:
        result = summarise.write_notes(
            conn, client, session, project, summary_ids=body.summary_ids
        )
    except ZoteroError as e:
        raise HTTPException(502, str(e)) from e
    return result.as_dict()


@router.delete("/api/projects/{project_id}/summaries/{summary_id}")
def forget_summary(
    project_id: str,
    summary_id: str,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, str]:
    _project(conn, project_id)
    try:
        summarise.forget(conn, project_id, summary_id)
    except summarise.Unsummarisable as e:
        raise HTTPException(404, str(e)) from e
    return {"forgotten": summary_id}
