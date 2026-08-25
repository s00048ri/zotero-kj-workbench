"""Turning a Zotero collection into cards.

An import is re-runnable at any time and never destroys work. That property is
not implemented as a set of procedural checks — it falls out of two things:

* every card has a deterministic ``origin_key`` and the schema forbids two
  cards sharing one within a project;
* on a second sighting only *derived* columns are rewritten. Anything the
  researcher touched — a label they wrote, a card they excluded, the note this
  tool filed in Zotero and wherever they dragged it — is left alone.

What the researcher wrote is treated as first-class throughout. A highlight
carrying a comment produces **two** cards, linked: what the source said, and
what the researcher takes it to mean. The comment is never folded into the
quote as a subtitle.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from . import gbooks
from .continuations import is_continuation
from .locators import NO_LOCATOR, EpubIndex, Locator, resolve_locator
from .store import insert, new_id, now_iso, transaction, upsert
from .text import html_to_text, normalise_quote
from .zotero import ZoteroClient, ZoteroError
from .zotero.models import (
    NON_TEXTUAL_ANNOTATION_TYPES,
    Annotation,
    Attachment,
    Note,
    Source,
)
from .zotero.reader import SourceRecord, read_subtree
from .zotero.tree import CollectionNode, CollectionTree

KJ_TAG = "kj-card"
INBOX_NAME = "Inbox"

Progress = Callable[[str], None]

# Columns an import may overwrite. Everything else on a card belongs to the
# researcher and survives every re-import.
DERIVED_COLUMNS = (
    "kind",
    "origin",
    "text",
    "text_raw",
    "source_id",
    "annotation_id",
    "origin_note_key",
    "parent_card_id",
    "prior_collection_id",
    "prior_path",
    "prior_ambiguous",
    "locator_type",
    "locator_value",
    "locator_source",
    "locator_estimated",
    "locator_detail_json",
    "color",
    "sort_index",
    "content_hash",
)


def content_hash(*parts: Any) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(b"\x1f")
        if part is None:
            continue
        if not isinstance(part, str):
            part = json.dumps(part, sort_keys=True, ensure_ascii=False)
        h.update(part.encode("utf-8"))
    return h.hexdigest()


@dataclass
class ImportStats:
    sources: int = 0
    attachments: int = 0
    annotations: int = 0
    quote_cards: int = 0
    idea_cards: int = 0
    image_cards: int = 0
    updated: int = 0
    skipped_empty: int = 0
    epub_attachments: int = 0
    epub_unreadable: int = 0
    locator_none: int = 0
    locator_estimated: int = 0
    joined_highlights: int = 0
    own_notes_seen: int = 0
    placements_read: int = 0
    still_in_inbox: int = 0
    unknown_kj_notes: int = 0
    notes_gone: int = 0
    notes_outside: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class PriorStructure:
    """Where the researcher had already filed a source — their own outline."""

    collection_id: str | None = None
    path: str | None = None
    ambiguous: bool = False


class ProjectConflict(ZoteroError):
    """Refusing to mix two collections, or two Zotero databases, into one project."""


class Importer:
    def __init__(
        self,
        conn: sqlite3.Connection,
        client: ZoteroClient,
        *,
        use_google_books: bool = False,
        progress: Progress | None = None,
    ) -> None:
        self.conn = conn
        self.client = client
        self.use_google_books = use_google_books
        self.progress = progress
        self.stats = ImportStats()
        self._epubs: dict[str, EpubIndex | None] = {}
        self._next_number = 1
        self._card_ids: dict[str, str] = {}  # origin_key -> card id
        self._placed: set[str] = set()  # cards whose placement was read back

    # -- entry point -------------------------------------------------------

    def run(self, project_name: str, root_key: str) -> tuple[str, ImportStats]:
        info = self.client.server_info()
        tree = CollectionTree.from_payloads(self.client.collections())
        node = tree.get(root_key)

        project_id = self._open_project(project_name, root_key, info.server_id)
        self._say(f"Reading {node.path}")
        snapshot = read_subtree(self.client, tree, root_key, progress=self.progress)

        run_id = insert(
            self.conn,
            "import_run",
            {"project_id": project_id, "started_at": now_iso()},
        )
        with transaction(self.conn):
            collection_ids = self._save_collections(project_id, snapshot.collections)
            self._load_numbering(project_id)
            for record in snapshot.sources.values():
                self._absorb_source(project_id, record, tree, collection_ids)
            for note_record in snapshot.standalone_notes.values():
                self._absorb_standalone_note(
                    project_id, note_record.note, note_record.collection_keys,
                    tree, collection_ids,
                )
            self._link_comment_cards(project_id)
            self._reconcile_notes(project_id)
            self.conn.execute(
                "UPDATE project SET last_import_at = ? WHERE id = ?",
                (now_iso(), project_id),
            )
            self.conn.execute(
                "UPDATE import_run SET finished_at = ?, stats_json = ? WHERE id = ?",
                (now_iso(), json.dumps(self.stats.as_dict()), run_id),
            )
        return project_id, self.stats

    # -- project -----------------------------------------------------------

    def _open_project(self, name: str, root_key: str, server_id: str | None) -> str:
        row = self.conn.execute(
            "SELECT * FROM project WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return insert(
                self.conn,
                "project",
                {
                    "name": name,
                    "root_collection_key": root_key,
                    "zotero_server_id": server_id,
                    "created_at": now_iso(),
                },
            )
        if row["root_collection_key"] != root_key:
            raise ProjectConflict(
                f"“{name}” was built from a different Zotero collection. Mixing "
                f"two collections into one project would merge unrelated cards "
                f"and make the structure comparison meaningless. Make a new "
                f"project for this collection instead."
            )
        if row["zotero_server_id"] and server_id and row["zotero_server_id"] != server_id:
            raise ProjectConflict(
                f"“{name}” was imported from a different Zotero database. Item "
                f"keys are not comparable across databases, so this import "
                f"would corrupt the project."
            )
        if not row["zotero_server_id"] and server_id:
            self.conn.execute(
                "UPDATE project SET zotero_server_id = ? WHERE id = ?",
                (server_id, row["id"]),
            )
        return row["id"]

    def _save_collections(
        self, project_id: str, nodes: list[CollectionNode]
    ) -> dict[str, str]:
        ids: dict[str, str] = {}
        for node in nodes:
            ids[node.key] = upsert(
                self.conn,
                "collection",
                {"project_id": project_id, "zotero_collection_key": node.key},
                {
                    "parent_key": node.parent_key,
                    "name": node.name,
                    "path": node.path,
                    "depth": node.depth,
                },
            )
        return ids

    # -- numbering ---------------------------------------------------------

    def _load_numbering(self, project_id: str) -> None:
        row = self.conn.execute(
            "SELECT human_id FROM card WHERE project_id = ? ORDER BY human_id DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if row and row["human_id"].startswith("KJ-"):
            self._next_number = int(row["human_id"][3:]) + 1

    def _human_id(self) -> str:
        value = f"KJ-{self._next_number:04d}"
        self._next_number += 1
        return value

    # -- sources -----------------------------------------------------------

    def _absorb_source(
        self,
        project_id: str,
        record: SourceRecord,
        tree: CollectionTree,
        collection_ids: dict[str, str],
    ) -> None:
        source: Source = record.source
        source_id = upsert(
            self.conn,
            "source",
            {"project_id": project_id, "zotero_item_key": source.key},
            {
                "item_type": source.item_type,
                "title": source.title,
                "creators_json": json.dumps(source.creators, ensure_ascii=False),
                "creators_short": source.creators_short,
                "year": source.year,
                "publication_title": source.container_title,
                "doi": source.doi,
                "isbn": source.isbn,
                "url": source.url,
                "raw_json": json.dumps(source.raw, ensure_ascii=False),
            },
        )
        self.stats.sources += 1

        for key in record.collection_keys:
            if key in collection_ids:
                self.conn.execute(
                    "INSERT OR IGNORE INTO source_collection (source_id, collection_id) "
                    "VALUES (?, ?)",
                    (source_id, collection_ids[key]),
                )
        prior = self._prior_structure(record.collection_keys, tree, collection_ids)

        for note in record.child_notes:
            self._absorb_child_note(project_id, source_id, note, prior)

        for attachment_record in record.attachments:
            self._absorb_attachment(
                project_id, source_id, source, attachment_record, prior
            )

    @staticmethod
    def _prior_structure(
        collection_keys: set[str],
        tree: CollectionTree,
        collection_ids: dict[str, str],
    ) -> PriorStructure:
        """The deepest collection a source sits in is its slot in the outline."""
        nodes = [tree.get(k) for k in collection_keys if k in tree and k in collection_ids]
        if not nodes:
            return PriorStructure()
        nodes.sort(key=lambda n: (-n.depth, n.path))
        deepest = nodes[0]
        tied = sum(1 for n in nodes if n.depth == deepest.depth) > 1
        return PriorStructure(collection_ids[deepest.key], deepest.path, tied)

    def _absorb_attachment(
        self,
        project_id: str,
        source_id: str,
        source: Source,
        record: Any,
        prior: PriorStructure,
    ) -> None:
        attachment: Attachment = record.attachment
        attachment_id = upsert(
            self.conn,
            "attachment",
            {"source_id": source_id, "zotero_attachment_key": attachment.key},
            {
                "content_type": attachment.content_type,
                "title": attachment.title,
                "filename": attachment.filename,
                "link_mode": attachment.link_mode,
                "raw_json": json.dumps(attachment.raw, ensure_ascii=False),
            },
        )
        self.stats.attachments += 1

        epub = None
        page_count = None
        if attachment.is_epub:
            self.stats.epub_attachments += 1
            epub = self._epub_index(attachment.key)
            if epub is None:
                self.stats.epub_unreadable += 1
            elif self.use_google_books:
                page_count = gbooks.page_count(
                    self.conn,
                    isbn=source.isbn,
                    title=source.title,
                    author=source.creators_short,
                )

        for annotation in record.annotations:
            self._absorb_annotation(
                project_id, source_id, attachment_id, attachment,
                annotation, prior, epub, page_count,
            )
        self._link_continuations(record.annotations)

    def _link_continuations(self, annotations: list[Annotation]) -> None:
        """A highlight split by a page break is one quotation, not two.

        The cards are left alone — each keeps its own locator and its own note
        — and the link says how to read them together.
        """
        for first, second in zip(annotations, annotations[1:], strict=False):
            head = self._card_ids.get(f"annotation:{first.key}:quote")
            tail = self._card_ids.get(f"annotation:{second.key}:quote")
            if not head or not tail:
                continue
            joins = is_continuation(
                {"text": first.text, "comment": first.comment,
                 "sort_index": first.sort_index},
                {"text": second.text, "comment": second.comment,
                 "sort_index": second.sort_index},
            )
            current = self.conn.execute(
                "SELECT continues_card_id FROM card WHERE id = ?", (tail,)
            ).fetchone()["continues_card_id"]
            wanted = head if joins else None
            if current != wanted:
                self.conn.execute(
                    "UPDATE card SET continues_card_id = ? WHERE id = ?",
                    (wanted, tail),
                )
            if joins:
                self.stats.joined_highlights += 1

    def _epub_index(self, attachment_key: str) -> EpubIndex | None:
        if attachment_key in self._epubs:
            return self._epubs[attachment_key]
        index: EpubIndex | None = None
        url = self.client.file_url(attachment_key)
        if url and url.startswith("file://"):
            path = urllib.request.url2pathname(urllib.parse.urlparse(url).path)
            try:
                index = EpubIndex(path)
            except Exception as e:  # a damaged or missing file is not fatal
                self._say(f"  could not read EPUB {attachment_key}: {e}")
        self._epubs[attachment_key] = index
        return index

    # -- annotations -------------------------------------------------------

    def _absorb_annotation(
        self,
        project_id: str,
        source_id: str,
        attachment_id: str,
        attachment: Attachment,
        annotation: Annotation,
        prior: PriorStructure,
        epub: EpubIndex | None,
        page_count: int | None,
    ) -> None:
        self.stats.annotations += 1
        annotation_id = upsert(
            self.conn,
            "annotation",
            {"attachment_id": attachment_id, "zotero_annotation_key": annotation.key},
            {
                "annotation_type": annotation.annotation_type,
                "text_raw": annotation.text,
                "comment_raw": annotation.comment,
                "color": annotation.color,
                "page_label": annotation.page_label,
                "sort_index": annotation.sort_index,
                "position_json": json.dumps(annotation.position, ensure_ascii=False),
                "date_modified": annotation.date_modified,
                "raw_json": json.dumps(annotation.raw, ensure_ascii=False),
                "content_hash": content_hash(
                    annotation.text,
                    annotation.comment,
                    annotation.color,
                    annotation.page_label,
                    annotation.sort_index,
                    annotation.position,
                ),
            },
        )

        locator = resolve_locator(
            annotation, attachment, epub=epub, page_count=page_count
        )
        is_image = (annotation.annotation_type or "") in NON_TEXTUAL_ANNOTATION_TYPES

        if is_image:
            self._upsert_card(
                project_id,
                origin_key=f"annotation:{annotation.key}:image",
                kind="image",
                origin="annotation_text",
                text=f"[{annotation.annotation_type} annotation]",
                text_raw=None,
                source_id=source_id,
                annotation_id=annotation_id,
                prior=prior,
                locator=locator,
                color=annotation.color,
                sort_index=annotation.sort_index,
            )
        elif annotation.has_text:
            self._upsert_card(
                project_id,
                origin_key=f"annotation:{annotation.key}:quote",
                kind="quote",
                origin="annotation_text",
                text=normalise_quote(annotation.text),
                text_raw=annotation.text,
                source_id=source_id,
                annotation_id=annotation_id,
                prior=prior,
                locator=locator,
                color=annotation.color,
                sort_index=annotation.sort_index,
            )

        if annotation.has_comment:
            # The researcher's own reading, as its own card — linked to the
            # quote below, not buried inside it.
            self._upsert_card(
                project_id,
                origin_key=f"annotation:{annotation.key}:idea",
                kind="idea",
                origin="annotation_comment",
                text=annotation.comment.strip(),
                text_raw=annotation.comment,
                source_id=source_id,
                annotation_id=annotation_id,
                prior=prior,
                locator=locator,
                color=annotation.color,
                sort_index=annotation.sort_index,
            )

        if not is_image and not annotation.has_text and not annotation.has_comment:
            self.stats.skipped_empty += 1

    # -- notes -------------------------------------------------------------

    def _absorb_child_note(
        self, project_id: str, source_id: str, note: Note, prior: PriorStructure
    ) -> None:
        text = html_to_text(note.note)
        if not text:
            self.stats.skipped_empty += 1
            return
        self._upsert_card(
            project_id,
            origin_key=f"note:{note.key}",
            kind="idea",
            origin="child_note",
            text=text,
            text_raw=note.note,
            source_id=source_id,
            annotation_id=None,
            prior=prior,
            locator=NO_LOCATOR,
            # a child note hangs off its item and cannot be filed into a
            # collection, so it still needs a note of its own later
            origin_note_key=note.key,
        )

    def _absorb_standalone_note(
        self,
        project_id: str,
        note: Note,
        collection_keys: set[str],
        tree: CollectionTree,
        collection_ids: dict[str, str],
    ) -> None:
        if KJ_TAG in note.tag_names:
            # A note this tool created. Reading it back as a fresh idea would
            # duplicate the card it came from. Where the researcher has since
            # dragged it *is* the grouping decision, so that is what is read.
            self.stats.own_notes_seen += 1
            self._record_placement(project_id, note, collection_keys, tree)
            return
        text = html_to_text(note.note)
        if not text:
            self.stats.skipped_empty += 1
            return
        prior = self._prior_structure(collection_keys, tree, collection_ids)
        self._upsert_card(
            project_id,
            origin_key=f"note:{note.key}",
            kind="idea",
            origin="standalone_note",
            text=text,
            text_raw=note.note,
            source_id=None,
            annotation_id=None,
            prior=prior,
            locator=NO_LOCATOR,
            # already filable in Zotero, so it needs no second note
            origin_note_key=note.key,
            zotero_note_key=note.key,
        )

    def _record_placement(
        self,
        project_id: str,
        note: Note,
        collection_keys: set[str],
        tree: CollectionTree,
    ) -> None:
        """Where a card's note now sits is the researcher's grouping.

        A note living in both Inbox and a theme is encountered once per
        collection during the walk, so this counts cards, not sightings. Inbox
        is a holding pen rather than a group: a card only in Inbox is still
        unsorted, and says so.
        """
        row = self.conn.execute(
            "SELECT id FROM card WHERE project_id = ? AND zotero_note_key = ?",
            (project_id, note.key),
        ).fetchone()
        if row is None:
            # A note from another project, or from a database this one has
            # never seen. Not ours to interpret.
            self.stats.unknown_kj_notes += 1
            return

        # `note.collections` is the note's own list, which is complete;
        # collection_keys is only where the walk happened to meet it.
        keys = [k for k in (note.collections or collection_keys) if k in tree]
        meaningful = [k for k in keys if tree.get(k).name != INBOX_NAME]
        meaningful.sort(key=lambda k: (-tree.get(k).depth, tree.get(k).path))
        path = tree.get(meaningful[0]).path if meaningful else None

        self.conn.execute(
            "UPDATE card SET kj_collection_keys_json = ?, kj_path = ? WHERE id = ?",
            (json.dumps(keys), path, row["id"]),
        )
        if row["id"] not in self._placed:
            self._placed.add(row["id"])
            self.stats.placements_read += 1
            if not meaningful:
                self.stats.still_in_inbox += 1

    def _reconcile_notes(self, project_id: str) -> None:
        """Notes this tool made that the walk did not find.

        A card can claim to be a note in Zotero long after the note has been
        deleted there, and nothing said so: the Groups screen went on showing
        groups built out of notes that no longer existed. So each unseen note
        is asked about directly.

        Deleted in Zotero → the claim is dropped, because it is false. The card
        itself, its text, its label and its locator are untouched: what the
        researcher wrote is theirs, and only the tool's own bookkeeping is
        corrected. Moved out of the project instead → the note is still real,
        so its key is kept and only its place in this project is cleared.

        A note in Zotero's trash is gone, not moved. It is absent from every
        listing but still answers when asked for by key, so asking is not
        enough — the deleted flag has to be read, or a whole trashed batch
        reports itself as merely relocated.
        """
        rows = self.conn.execute(
            "SELECT id, zotero_note_key FROM card WHERE project_id = ? "
            "AND materialized_at IS NOT NULL AND zotero_note_key IS NOT NULL",
            (project_id,),
        ).fetchall()
        for row in rows:
            if row["id"] in self._placed:
                continue
            item = self.client.item(row["zotero_note_key"])
            trashed = bool((item or {}).get("data", item or {}).get("deleted"))
            if item is None or trashed:
                self.conn.execute(
                    "UPDATE card SET zotero_note_key = NULL, materialized_at = NULL, "
                    "kj_path = NULL, kj_collection_keys_json = NULL WHERE id = ?",
                    (row["id"],),
                )
                self.stats.notes_gone += 1
            else:
                self.conn.execute(
                    "UPDATE card SET kj_path = NULL, kj_collection_keys_json = NULL "
                    "WHERE id = ?",
                    (row["id"],),
                )
                self.stats.notes_outside += 1

    # -- cards -------------------------------------------------------------

    def _upsert_card(
        self,
        project_id: str,
        *,
        origin_key: str,
        kind: str,
        origin: str,
        text: str,
        text_raw: str | None,
        source_id: str | None,
        annotation_id: str | None,
        prior: PriorStructure,
        locator: Locator,
        color: str | None = None,
        sort_index: str | None = None,
        origin_note_key: str | None = None,
        zotero_note_key: str | None = None,
        parent_card_id: str | None = None,
        kj_path: str | None = None,
    ) -> str:
        digest = content_hash(kind, origin, text, locator.type, locator.value)
        derived = {
            "kind": kind,
            "origin": origin,
            "text": text,
            "text_raw": text_raw,
            "source_id": source_id,
            "annotation_id": annotation_id,
            "origin_note_key": origin_note_key,
            "parent_card_id": parent_card_id,
            "prior_collection_id": prior.collection_id,
            "prior_path": prior.path,
            "prior_ambiguous": int(prior.ambiguous),
            "locator_type": locator.type,
            "locator_value": locator.value,
            "locator_source": locator.source,
            "locator_estimated": int(locator.estimated),
            "locator_detail_json": json.dumps(locator.detail, ensure_ascii=False),
            "color": color,
            "sort_index": sort_index,
            "content_hash": digest,
        }

        row = self.conn.execute(
            "SELECT id, content_hash FROM card WHERE project_id = ? AND origin_key = ?",
            (project_id, origin_key),
        ).fetchone()

        if row is not None:
            self._card_ids[origin_key] = row["id"]
            if row["content_hash"] != digest:
                patch = {k: derived[k] for k in DERIVED_COLUMNS}
                sets = ", ".join(f"{k} = ?" for k in patch)
                self.conn.execute(
                    f"UPDATE card SET {sets}, updated_at = ? WHERE id = ?",
                    (*patch.values(), now_iso(), row["id"]),
                )
                self.stats.updated += 1
            return row["id"]

        card_id = new_id()
        self.conn.execute(
            "INSERT INTO card (id, project_id, human_id, origin_key, zotero_note_key, "
            "kj_path, created_at, updated_at, "
            + ", ".join(DERIVED_COLUMNS)
            + ") VALUES ("
            + ", ".join("?" for _ in range(8 + len(DERIVED_COLUMNS)))
            + ")",
            (
                card_id,
                project_id,
                self._human_id(),
                origin_key,
                zotero_note_key,
                kj_path,
                now_iso(),
                now_iso(),
                *[derived[k] for k in DERIVED_COLUMNS],
            ),
        )
        self._card_ids[origin_key] = card_id

        if kind == "quote":
            self.stats.quote_cards += 1
        elif kind == "idea":
            self.stats.idea_cards += 1
        else:
            self.stats.image_cards += 1
        if locator.type == "none":
            self.stats.locator_none += 1
        if locator.estimated:
            self.stats.locator_estimated += 1
        return card_id

    def _link_comment_cards(self, project_id: str) -> None:
        """Point each comment card at the quote it responds to.

        Done after the walk so the link survives whichever order the two cards
        were created in, including a re-import that only creates one of them.
        """
        rows = self.conn.execute(
            "SELECT id, origin_key, parent_card_id FROM card "
            "WHERE project_id = ? AND origin = 'annotation_comment'",
            (project_id,),
        ).fetchall()
        for row in rows:
            quote_key = row["origin_key"].rsplit(":", 1)[0] + ":quote"
            parent = self.conn.execute(
                "SELECT id FROM card WHERE project_id = ? AND origin_key = ?",
                (project_id, quote_key),
            ).fetchone()
            parent_id = parent["id"] if parent else None
            if parent_id != row["parent_card_id"]:
                self.conn.execute(
                    "UPDATE card SET parent_card_id = ? WHERE id = ?",
                    (parent_id, row["id"]),
                )

    # -- reporting ---------------------------------------------------------

    def _say(self, message: str) -> None:
        if self.progress:
            self.progress(message)


def run_import(
    conn: sqlite3.Connection,
    client: ZoteroClient,
    project_name: str,
    root_key: str,
    *,
    use_google_books: bool = False,
    progress: Progress | None = None,
) -> tuple[str, ImportStats]:
    return Importer(
        conn, client, use_google_books=use_google_books, progress=progress
    ).run(project_name, root_key)
