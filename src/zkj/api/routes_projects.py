"""Projects: creating one, re-importing it, and reporting what is in it."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..cards import summary
from ..importer import ProjectConflict, run_import
from ..zotero import ZoteroClient
from ..zotero.tree import CollectionTree
from .deps import get_client, get_db
from .schemas import ImportResult, ProjectIn, ProjectOut

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _project_out(
    conn: sqlite3.Connection, row: dict, *, path: str | None = None,
    server_id: str | None = None,
) -> ProjectOut:
    return ProjectOut(
        id=row["id"],
        name=row["name"],
        root_collection_key=row["root_collection_key"],
        root_path=path,
        zotero_server_id=row["zotero_server_id"],
        research_question=row["research_question"],
        created_at=row["created_at"],
        last_import_at=row["last_import_at"],
        counts=summary(conn, row["id"]),
        # object versions are local to one Zotero database, so a project
        # imported elsewhere must not be written from here
        writable_here=not (
            row["zotero_server_id"] and server_id and row["zotero_server_id"] != server_id
        ),
    )


def _fetch(conn: sqlite3.Connection, project_id: str) -> dict:
    row = conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"No project {project_id}.")
    return dict(row)


def _paths(client: ZoteroClient) -> dict[str, str]:
    try:
        tree = CollectionTree.from_payloads(client.collections())
    except Exception:
        return {}
    return {n.key: n.path for n in tree.nodes.values()}


@router.get("", response_model=list[ProjectOut])
def list_projects(
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> list[ProjectOut]:
    paths = _paths(client)
    server_id = client.server_info().server_id
    return [
        _project_out(conn, dict(r), path=paths.get(r["root_collection_key"]),
                     server_id=server_id)
        for r in conn.execute("SELECT * FROM project ORDER BY created_at DESC")
    ]


@router.post("", response_model=ImportResult, status_code=201)
def create_project(
    body: ProjectIn,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> ImportResult:
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "A project needs a name.")
    existing = conn.execute("SELECT id FROM project WHERE name = ?", (name,)).fetchone()
    if existing:
        raise HTTPException(409, f"A project called “{name}” already exists.")
    return _import(conn, client, name, body.collection_key, body.use_google_books)


@router.post("/{project_id}/import", response_model=ImportResult)
def reimport(
    project_id: str,
    use_google_books: bool = False,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> ImportResult:
    row = _fetch(conn, project_id)
    return _import(
        conn, client, row["name"], row["root_collection_key"], use_google_books
    )


def _import(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    name: str,
    collection_key: str,
    use_google_books: bool,
) -> ImportResult:
    try:
        project_id, stats = run_import(
            conn, client, name, collection_key, use_google_books=use_google_books
        )
    except ProjectConflict as e:
        raise HTTPException(409, str(e)) from e
    row = _fetch(conn, project_id)
    paths = _paths(client)
    return ImportResult(
        project=_project_out(
            conn, row, path=paths.get(collection_key),
            server_id=client.server_info().server_id,
        ),
        stats=stats.as_dict(),
    )


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    conn: sqlite3.Connection = Depends(get_db),
    client: ZoteroClient = Depends(get_client),
) -> ProjectOut:
    row = _fetch(conn, project_id)
    return _project_out(
        conn, row, path=_paths(client).get(row["root_collection_key"]),
        server_id=client.server_info().server_id,
    )
