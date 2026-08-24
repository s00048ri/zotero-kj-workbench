"""Read one collection subtree into memory, once, without double counting.

The import at M2 and the collection preview on the Project screen both need
the same thing: every source under a collection, its attachments, and the
annotations on those attachments, with the researcher's own folder structure
recorded alongside.

Two counting rules, both of them corrections of real mistakes:

* an item that sits in two collections is encountered once per collection.
  Count items, not sightings, and union the collections it was seen in;
* annotations never arrive through ``children``. They come from the
  library-wide annotation index, built in one request before the walk starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .client import ZoteroClient
from .models import Annotation, Attachment, Note, Source, item_type_of, parse_item
from .tree import CollectionNode, CollectionTree

Progress = Callable[[str], None]


@dataclass
class AttachmentRecord:
    attachment: Attachment
    annotations: list[Annotation] = field(default_factory=list)

    @property
    def textual_annotations(self) -> list[Annotation]:
        return [a for a in self.annotations if a.is_textual and a.has_text]

    @property
    def comments(self) -> list[Annotation]:
        return [a for a in self.annotations if a.has_comment]


@dataclass
class SourceRecord:
    source: Source
    collection_keys: set[str] = field(default_factory=set)
    attachments: list[AttachmentRecord] = field(default_factory=list)
    child_notes: list[Note] = field(default_factory=list)

    @property
    def annotations(self) -> list[Annotation]:
        return [a for rec in self.attachments for a in rec.annotations]


@dataclass
class NoteRecord:
    """A note filed straight into a collection — already the researcher's own."""

    note: Note
    collection_keys: set[str] = field(default_factory=set)


@dataclass
class SubtreeSnapshot:
    root_key: str
    collections: list[CollectionNode]
    sources: dict[str, SourceRecord] = field(default_factory=dict)
    standalone_notes: dict[str, NoteRecord] = field(default_factory=dict)
    unreadable_attachments: int = 0

    @property
    def attachment_records(self) -> list[AttachmentRecord]:
        return [rec for s in self.sources.values() for rec in s.attachments]

    @property
    def annotations(self) -> list[Annotation]:
        return [a for rec in self.attachment_records for a in rec.annotations]

    def counts(self) -> dict[str, int]:
        anns = self.annotations
        return {
            "collections": len(self.collections),
            "sources": len(self.sources),
            "attachments": len(self.attachment_records),
            "annotations": len(anns),
            "highlights": sum(1 for a in anns if a.is_textual and a.has_text),
            "comments": sum(1 for a in anns if a.has_comment),
            "child_notes": sum(len(s.child_notes) for s in self.sources.values()),
            "standalone_notes": len(self.standalone_notes),
        }


def read_subtree(
    client: ZoteroClient,
    tree: CollectionTree,
    root_key: str,
    *,
    annotation_index: dict[str, list[Annotation]] | None = None,
    progress: Progress | None = None,
) -> SubtreeSnapshot:
    """Walk one collection subtree and collect everything under it."""
    nodes = tree.subtree(root_key)
    snapshot = SubtreeSnapshot(root_key=root_key, collections=nodes)
    index = annotation_index if annotation_index is not None else client.annotation_index()

    for node in nodes:
        if progress:
            progress(node.path)
        for payload in client.collection_items_top(node.key):
            _absorb(client, snapshot, node, payload, index)
    return snapshot


def _absorb(
    client: ZoteroClient,
    snapshot: SubtreeSnapshot,
    node: CollectionNode,
    payload: dict[str, Any],
    index: dict[str, list[Annotation]],
) -> None:
    itype = item_type_of(payload)

    if itype == "note":
        note = parse_item(payload)
        assert isinstance(note, Note)
        record = snapshot.standalone_notes.get(note.key)
        if record is None:
            record = NoteRecord(note=note)
            snapshot.standalone_notes[note.key] = record
        record.collection_keys.add(node.key)
        return

    # A loose attachment or annotation at collection top level has no
    # bibliographic identity, so it cannot become a citable card.
    if itype in ("attachment", "annotation"):
        return

    source = parse_item(payload)
    assert isinstance(source, Source)
    record = snapshot.sources.get(source.key)
    if record is not None:
        # Second sighting of the same source in another collection: record the
        # membership, but do not re-read its children.
        record.collection_keys.add(node.key)
        return

    record = SourceRecord(source=source, collection_keys={node.key})
    snapshot.sources[source.key] = record

    for child_payload in client.children(source.key):
        child = parse_item(child_payload)
        if isinstance(child, Note):
            record.child_notes.append(child)
        elif isinstance(child, Attachment):
            if not child.can_hold_annotations:
                snapshot.unreadable_attachments += 1
            record.attachments.append(
                AttachmentRecord(
                    attachment=child, annotations=list(index.get(child.key, []))
                )
            )
