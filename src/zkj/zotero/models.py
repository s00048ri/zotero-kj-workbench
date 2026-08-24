"""Typed views over Zotero local API objects.

Two rules shape every model here:

* the full API envelope is kept in ``raw``, so a field this version does not
  know about is never lost between an import and a later release;
* nothing is coerced away. ``annotationPosition`` arrives as an object from
  some Zotero builds and as a JSON string from others, and both are preserved.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Annotation types Zotero can produce. `image` and `ink` carry no text.
TEXTUAL_ANNOTATION_TYPES = frozenset({"highlight", "underline", "text", "note"})
NON_TEXTUAL_ANNOTATION_TYPES = frozenset({"image", "ink"})


class ZoteroObject(BaseModel):
    """Common shape: an envelope with a ``data`` payload."""

    model_config = ConfigDict(extra="ignore")

    key: str
    version: int = 0
    raw: dict[str, Any] = Field(default_factory=dict, repr=False)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ZoteroObject":
        data = payload.get("data", payload)
        return cls(**data, raw=payload)


class Collection(ZoteroObject):
    name: str = "(untitled)"
    parent_key: str | None = Field(default=None, alias="parentCollection")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("parent_key", mode="before")
    @classmethod
    def _false_is_none(cls, v: Any) -> Any:
        # Zotero sends `false`, not null, for a top-level collection.
        return None if v is False or v == "" else v


class Source(ZoteroObject):
    """A bibliographic item: the thing a citation points at."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    item_type: str = Field(default="document", alias="itemType")
    title: str | None = None
    creators: list[dict[str, Any]] = Field(default_factory=list)
    date: str | None = None
    publication_title: str | None = Field(default=None, alias="publicationTitle")
    book_title: str | None = Field(default=None, alias="bookTitle")
    doi: str | None = Field(default=None, alias="DOI")
    isbn: str | None = Field(default=None, alias="ISBN")
    url: str | None = None
    collections: list[str] = Field(default_factory=list)

    @property
    def year(self) -> str | None:
        """First plausible four-digit year in the date field, if any.

        Some items have no date at all, in which case a citation renders
        author-only rather than inventing one.
        """
        m = re.search(r"(1[5-9]\d{2}|20\d{2}|21\d{2})", self.date or "")
        return m.group(1) if m else None

    @property
    def creators_short(self) -> str:
        names = [
            (c.get("lastName") or c.get("name") or "").strip()
            for c in self.creators
            if c.get("creatorType") in (None, "author", "editor")
        ]
        names = [n for n in names if n]
        if not names:
            return "Anon."
        if len(names) == 1:
            return names[0]
        if len(names) == 2:
            return f"{names[0]} & {names[1]}"
        return f"{names[0]} et al."

    @property
    def container_title(self) -> str | None:
        return self.publication_title or self.book_title


class Attachment(ZoteroObject):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    item_type: Literal["attachment"] = Field(default="attachment", alias="itemType")
    parent_item: str | None = Field(default=None, alias="parentItem")
    content_type: str | None = Field(default=None, alias="contentType")
    title: str | None = None
    filename: str | None = None
    link_mode: str | None = Field(default=None, alias="linkMode")

    @property
    def is_epub(self) -> bool:
        return "epub" in (self.content_type or "").lower()

    @property
    def is_pdf(self) -> bool:
        return "pdf" in (self.content_type or "").lower()

    @property
    def can_hold_annotations(self) -> bool:
        """A bookmark has no file, so it can never carry a highlight."""
        return self.link_mode != "linked_url" and bool(self.content_type)


class Annotation(ZoteroObject):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    item_type: Literal["annotation"] = Field(default="annotation", alias="itemType")
    parent_item: str | None = Field(default=None, alias="parentItem")
    annotation_type: str | None = Field(default=None, alias="annotationType")
    text: str = Field(default="", alias="annotationText")
    comment: str = Field(default="", alias="annotationComment")
    color: str | None = Field(default=None, alias="annotationColor")
    page_label: str | None = Field(default=None, alias="annotationPageLabel")
    sort_index: str | None = Field(default=None, alias="annotationSortIndex")
    position: dict[str, Any] = Field(default_factory=dict, alias="annotationPosition")
    position_raw: str | None = None
    date_modified: str | None = Field(default=None, alias="dateModified")

    @field_validator("text", "comment", mode="before")
    @classmethod
    def _null_is_empty(cls, v: Any) -> Any:
        return "" if v is None else v

    @field_validator("position", mode="before")
    @classmethod
    def _position_may_be_a_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return {"value": v}
        return v or {}

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    @property
    def has_comment(self) -> bool:
        return bool(self.comment.strip())

    @property
    def is_textual(self) -> bool:
        return (self.annotation_type or "") in TEXTUAL_ANNOTATION_TYPES


class Note(ZoteroObject):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    item_type: Literal["note"] = Field(default="note", alias="itemType")
    parent_item: str | None = Field(default=None, alias="parentItem")
    note: str = ""
    collections: list[str] = Field(default_factory=list)
    tags: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def tag_names(self) -> set[str]:
        return {t.get("tag", "") for t in self.tags}


def item_type_of(payload: dict[str, Any]) -> str:
    return (payload.get("data", payload) or {}).get("itemType", "")


def parse_item(payload: dict[str, Any]) -> Source | Attachment | Annotation | Note:
    """Dispatch one API object onto the right model."""
    itype = item_type_of(payload)
    if itype == "attachment":
        return Attachment.from_payload(payload)  # type: ignore[return-value]
    if itype == "annotation":
        return Annotation.from_payload(payload)  # type: ignore[return-value]
    if itype == "note":
        return Note.from_payload(payload)  # type: ignore[return-value]
    return Source.from_payload(payload)  # type: ignore[return-value]
