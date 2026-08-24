"""FastAPI application. One process, one port, no CORS.

At this milestone the app has no real interface: it exposes the Zotero adapter
and a status page that answers "can this machine do the thing at all?".
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse

from ..config import settings
from ..zotero import ZoteroClient, ZoteroError, ZoteroForbidden
from ..zotero.reader import read_subtree
from ..zotero.tree import CollectionTree
from .deps import get_client, zotero_error_handler
from .schemas import CollectionOut, CollectionPreview, ConnectionStatus

STATIC_DIR = Path(__file__).parent / "web"


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="0.1.0")
    app.add_exception_handler(ZoteroError, zotero_error_handler)

    @app.get("/api/status", response_model=ConnectionStatus)
    def status(client: ZoteroClient = Depends(get_client)) -> ConnectionStatus:
        """Never raises: this endpoint's whole job is to report bad news."""
        try:
            info = client.server_info(refresh=True)
        except ZoteroForbidden as e:
            return ConnectionStatus.from_info(
                client.server_info(),
                permitted=False,
                collection_count=None,
                message=str(e),
                remedy=e.remedy,
            )
        if not info.reachable:
            return ConnectionStatus.from_info(
                info,
                permitted=False,
                collection_count=None,
                message=f"No answer from Zotero at {client.base}.",
                remedy="Start Zotero and leave it running.",
            )
        try:
            count = len(client.collections())
        except ZoteroError as e:
            return ConnectionStatus.from_info(
                info,
                permitted=False,
                collection_count=None,
                message=str(e),
                remedy=e.remedy,
            )
        if info.writes_available:
            message = (
                f"Connected to Zotero {info.zotero_version or ''}".strip()
                + ". Reading and writing notes are both available."
            )
            remedy = None
        else:
            message = (
                "Connected. This Zotero cannot accept notes from other "
                "applications, so cards can be read and analysed but not "
                "written back."
            )
            remedy = "Upgrade to Zotero 10 or newer to create notes from cards."
        return ConnectionStatus.from_info(
            info,
            permitted=True,
            collection_count=count,
            message=message,
            remedy=remedy,
        )

    @app.get("/api/collections", response_model=list[CollectionOut])
    def collections(client: ZoteroClient = Depends(get_client)) -> list[CollectionOut]:
        tree = CollectionTree.from_payloads(client.collections())
        return [CollectionOut.from_node(n) for n in tree.roots]

    @app.get("/api/collections/{key}/preview", response_model=CollectionPreview)
    def preview(key: str, client: ZoteroClient = Depends(get_client)) -> CollectionPreview:
        tree = CollectionTree.from_payloads(client.collections())
        node = tree.get(key)
        snapshot = read_subtree(client, tree, key)
        return CollectionPreview.from_snapshot(node, snapshot)

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "status.html")

    return app


app = create_app()
