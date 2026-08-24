"""Row → API shape. One place, so the UI never parses a JSON column."""

from __future__ import annotations

import json
from typing import Any

from ..cards import citation_of, locator_of
from .schemas import CardOut, LinkedCardOut, LocatorOut, SourceOut


def card_out(row: dict[str, Any]) -> CardOut:
    locator = locator_of(row)
    source = None
    if row.get("source_id"):
        source = SourceOut(
            id=row["source_id"],
            key=row.get("source_key"),
            title=row.get("source_title"),
            creators_short=row.get("creators_short"),
            year=row.get("source_year"),
            publication_title=row.get("publication_title"),
        )
    return CardOut(
        id=row["id"],
        human_id=row["human_id"],
        kind=row["kind"],
        origin=row["origin"],
        text=row["text"],
        text_raw=row.get("text_raw"),
        human_label=row.get("human_label"),
        color=row.get("color"),
        status=row["status"],
        prior_path=row.get("prior_path"),
        prior_ambiguous=bool(row.get("prior_ambiguous")),
        kj_path=row.get("kj_path"),
        zotero_note_key=row.get("zotero_note_key"),
        origin_note_key=row.get("origin_note_key"),
        materialized_at=row.get("materialized_at"),
        citation=citation_of(row),
        source=source,
        locator=LocatorOut(
            type=locator.type,
            value=locator.value,
            source=locator.source,
            estimated=locator.estimated,
            rendered=locator.render(),
            estimated_page=locator.estimated_page,
            detail=locator.detail,
        ),
        linked_ideas=[LinkedCardOut(**_linked(c)) for c in row.get("linked_ideas", [])],
        parent=LinkedCardOut(**_linked(row["parent"])) if row.get("parent") else None,
    )


def _linked(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "human_id": row["human_id"],
        "kind": row["kind"],
        "origin": row.get("origin"),
        "text": row["text"],
    }


def json_or_none(value: str | None) -> Any:
    return json.loads(value) if value else None
