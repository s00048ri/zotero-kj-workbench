"""NotebookLM: prepare what goes in, hold the link, keep what comes back.

The workbench never talks to NotebookLM. Every endpoint here is either a
preparation the researcher pastes, a link they paste back, or a write into
Zotero they asked for.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from .. import notebooklm
from ..zotero import ZoteroClient, ZoteroError
from .deps import get_client, get_db
from .routes_writes import _project, _refuse_other_database, session_for

router = APIRouter(tags=["notebooklm"])


class NotebookIn(BaseModel):
    url: str
    title: str | None = None
    source_id: str | None = None


class LinkIn(BaseModel):
    notebook_ids: list[str] | None = None


class ReportIn(BaseModel):
    kind: str = "other"
    title: str | None = None
    text: str


class WriteReportsIn(BaseModel):
    report_ids: list[str] | None = None


@router.get("/api/projects/{project_id}/notebooks")
def list_notebooks(
    project_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    _project(conn, project_id)
    return {
        "notebooks": notebooklm.notebooks(conn, project_id),
        "reports": notebooklm.reports(conn, project_id),
        "report_kinds": list(notebooklm.REPORT_KINDS),
    }


@router.get("/api/projects/{project_id}/notebooks/bundle")
def bundle(
    project_id: str,
    source_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """What to paste into one notebook. Nothing is sent anywhere."""
    project = _project(conn, project_id)
    try:
        return notebooklm.bundle(conn, client, project, source_id=source_id).as_dict()
    except notebooklm.NotebookError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/api/projects/{project_id}/notebooks/stage")
def stage(
    project_id: str,
    source_id: str | None = None,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """Gather the files with no public URL into one folder to select all of."""
    project = _project(conn, project_id)
    try:
        return notebooklm.stage(conn, client, project, source_id=source_id)
    except notebooklm.NotebookError as e:
        raise HTTPException(404, str(e)) from e
    except OSError as e:
        raise HTTPException(500, f"Could not prepare the folder: {e}") from e


@router.post("/api/projects/{project_id}/notebooks")
def add_notebook(
    project_id: str, body: NotebookIn, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, Any]:
    """Remember a notebook the researcher made, by its address."""
    _project(conn, project_id)
    try:
        return notebooklm.register(
            conn, project_id, body.url, title=body.title, source_id=body.source_id
        )
    except notebooklm.NotebookError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/api/projects/{project_id}/notebooks/{notebook_id}")
def forget_notebook(
    project_id: str, notebook_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, str]:
    _project(conn, project_id)
    try:
        notebooklm.forget(conn, project_id, notebook_id)
    except notebooklm.NotebookError as e:
        raise HTTPException(404, str(e)) from e
    return {"forgotten": notebook_id}


@router.post("/api/projects/{project_id}/notebooks/links")
def write_links(
    project_id: str,
    body: LinkIn = Body(default=LinkIn()),
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """Write each notebook's link into Zotero, beside what it is about."""
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    try:
        result = notebooklm.link_into_zotero(
            conn, client, session, project, notebook_ids=body.notebook_ids
        )
    except ZoteroError as e:
        raise HTTPException(502, str(e)) from e
    return result.as_dict()


@router.post("/api/projects/{project_id}/notebooks/{notebook_id}/reports")
def add_report(
    project_id: str,
    notebook_id: str,
    body: ReportIn,
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Keep something NotebookLM made, pasted back."""
    _project(conn, project_id)
    try:
        return notebooklm.add_report(
            conn, project_id, notebook_id, kind=body.kind, text=body.text, title=body.title
        )
    except notebooklm.NotebookError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/api/projects/{project_id}/notebooks/reports/{report_id}")
def forget_report(
    project_id: str, report_id: str, conn: sqlite3.Connection = Depends(get_db)
) -> dict[str, str]:
    _project(conn, project_id)
    try:
        notebooklm.forget_report(conn, project_id, report_id)
    except notebooklm.NotebookError as e:
        raise HTTPException(404, str(e)) from e
    return {"forgotten": report_id}


@router.post("/api/projects/{project_id}/notebooks/reports/notes")
def write_report_notes(
    project_id: str,
    body: WriteReportsIn = Body(default=WriteReportsIn()),
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> dict[str, Any]:
    """File pasted-back reports in Zotero, marked as NotebookLM's work."""
    project = _project(conn, project_id)
    _refuse_other_database(project, client)
    session = session_for(conn, client)
    try:
        result = notebooklm.write_reports(
            conn, client, session, project, report_ids=body.report_ids
        )
    except ZoteroError as e:
        raise HTTPException(502, str(e)) from e
    return result.as_dict()
