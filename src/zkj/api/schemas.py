"""What the API hands to the browser."""

from __future__ import annotations

from pydantic import BaseModel

from ..zotero.client import ServerInfo
from ..zotero.reader import SubtreeSnapshot
from ..zotero.tree import CollectionNode


class ConnectionStatus(BaseModel):
    reachable: bool
    permitted: bool
    api_version: str | None = None
    zotero_version: str | None = None
    server_id: str | None = None
    schema_version: str | None = None
    writes_available: bool = False
    collection_count: int | None = None
    message: str
    remedy: str | None = None

    @classmethod
    def from_info(
        cls,
        info: ServerInfo,
        *,
        permitted: bool,
        collection_count: int | None,
        message: str,
        remedy: str | None = None,
    ) -> ConnectionStatus:
        return cls(
            reachable=info.reachable,
            permitted=permitted,
            api_version=info.api_version,
            zotero_version=info.zotero_version,
            server_id=info.server_id,
            schema_version=info.schema_version,
            writes_available=info.writes_available,
            collection_count=collection_count,
            message=message,
            remedy=remedy,
        )


class CollectionOut(BaseModel):
    key: str
    name: str
    path: str
    depth: int
    parent_key: str | None = None
    children: list[CollectionOut] = []

    @classmethod
    def from_node(cls, node: CollectionNode) -> CollectionOut:
        return cls(
            key=node.key,
            name=node.name,
            path=node.path,
            depth=node.depth,
            parent_key=node.parent_key,
            children=[cls.from_node(c) for c in node.children],
        )


class CollectionPreview(BaseModel):
    """What an import of this collection would find, before committing to it."""

    key: str
    name: str
    path: str
    counts: dict[str, int]
    unreadable_attachments: int
    sources_without_annotations: int
    sample_highlights: list[str] = []

    @classmethod
    def from_snapshot(
        cls, node: CollectionNode, snapshot: SubtreeSnapshot
    ) -> CollectionPreview:
        without = sum(1 for s in snapshot.sources.values() if not s.annotations)
        samples = [
            a.text.strip()[:240]
            for a in snapshot.annotations
            if a.is_textual and a.has_text
        ][:3]
        return cls(
            key=node.key,
            name=node.name,
            path=node.path,
            counts=snapshot.counts(),
            unreadable_attachments=snapshot.unreadable_attachments,
            sources_without_annotations=without,
            sample_highlights=samples,
        )


# -- projects and cards ---------------------------------------------------


class ProjectIn(BaseModel):
    name: str
    collection_key: str
    use_google_books: bool = False


class ProjectOut(BaseModel):
    id: str
    name: str
    root_collection_key: str
    root_path: str | None = None
    zotero_server_id: str | None = None
    research_question: str | None = None
    created_at: str
    last_import_at: str | None = None
    counts: dict[str, int] = {}
    writable_here: bool = True


class ImportResult(BaseModel):
    project: ProjectOut
    stats: dict[str, int]


class SourceOut(BaseModel):
    id: str | None = None
    key: str | None = None
    title: str | None = None
    creators_short: str | None = None
    year: str | None = None
    publication_title: str | None = None


class LocatorOut(BaseModel):
    type: str
    value: str | None = None
    source: str
    estimated: bool
    rendered: str
    estimated_page: int | None = None
    detail: dict = {}


class LinkedCardOut(BaseModel):
    id: str
    human_id: str
    kind: str
    origin: str | None = None
    text: str


class CardOut(BaseModel):
    id: str
    human_id: str
    kind: str
    origin: str
    text: str
    text_raw: str | None = None
    human_label: str | None = None
    color: str | None = None
    status: str
    prior_path: str | None = None
    prior_ambiguous: bool = False
    kj_path: str | None = None
    zotero_note_key: str | None = None
    origin_note_key: str | None = None
    materialized_at: str | None = None
    citation: str
    source: SourceOut | None = None
    locator: LocatorOut
    linked_ideas: list[LinkedCardOut] = []
    parent: LinkedCardOut | None = None


class CardPageOut(BaseModel):
    cards: list[CardOut]
    total: int
    counts: dict[str, int]


class FacetValue(BaseModel):
    value: str | None = None
    label: str | None = None
    count: int


class FacetsOut(BaseModel):
    sources: list[FacetValue] = []
    years: list[FacetValue] = []
    colors: list[FacetValue] = []
    kinds: list[FacetValue] = []
    origins: list[FacetValue] = []
    locator_types: list[FacetValue] = []
    prior_paths: list[FacetValue] = []
    groups: list[FacetValue] = []
