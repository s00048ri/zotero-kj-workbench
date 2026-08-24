"""What a card looks like once it is a Zotero note.

A materialised card has to survive being dragged anywhere in Zotero and still
be recognisable on the next import, so it carries a marker block. It also has
to be readable *in Zotero*, beside the source it came from, because that is
where the researcher will next see it.

User text is escaped, never interpolated as markup: a quotation containing
``<script>`` is a quotation, not a script.
"""

from __future__ import annotations

from typing import Any

from ..text import escape_html

KJ_TAG = "kj-card"


def note_html(
    card: dict[str, Any],
    *,
    project_name: str,
    citation: str = "",
    parent_human_id: str | None = None,
) -> str:
    """One card as note HTML."""
    kind = card["kind"]
    is_label = card.get("origin") == "group_label"
    heading = f"{card['human_id']} · {'label' if is_label else kind}"
    parts = [f"<h2>{escape_html(heading)}</h2>"]

    if kind == "quote":
        parts.append(f"<blockquote>{escape_html(card['text'])}</blockquote>")
    elif is_label:
        head, _, rest = card["text"].partition("\n\n")
        parts.append(f"<p><strong>{escape_html(head)}</strong></p>")
        if rest:
            parts.append(f"<p>{escape_html(rest)}</p>")
        if card.get("kj_path"):
            parts.append(f"<p><em>Label for: {escape_html(card['kj_path'])}</em></p>")
    else:
        parts.append(f"<p>{escape_html(card['text'])}</p>")

    if card.get("human_label"):
        parts.append(f"<p><em>{escape_html(card['human_label'])}</em></p>")
    if citation:
        parts.append(f"<p><strong>Source:</strong> {escape_html(citation)}</p>")
    if card.get("source_title"):
        parts.append(f"<p><em>{escape_html(card['source_title'])}</em></p>")
    if card.get("locator_estimated"):
        parts.append("<p><em>Locator is estimated — verify before citing.</em></p>")
    if parent_human_id:
        parts.append(
            f"<p><strong>My reading of:</strong> {escape_html(parent_human_id)}</p>"
        )

    parts.append("<hr/>")
    parts.append(
        "<p>"
        f"kj:card={escape_html(card['human_id'])} "
        f"kj:kind={escape_html(kind)} "
        f"kj:project={escape_html(project_name)} "
        f"kj:origin={escape_html(card['origin_key'])}"
        "</p>"
    )
    return "".join(parts)


def note_payload(
    card: dict[str, Any],
    *,
    project_name: str,
    collection_key: str,
    citation: str = "",
    parent_human_id: str | None = None,
) -> dict[str, Any]:
    return {
        "itemType": "note",
        "note": note_html(
            card,
            project_name=project_name,
            citation=citation,
            parent_human_id=parent_human_id,
        ),
        "tags": [
            {"tag": KJ_TAG},
            {"tag": f"kj-kind:{card['kind']}"},
            {"tag": f"kj-project:{project_name}"},
        ],
        "collections": [collection_key],
    }


def target_collection(
    card: dict[str, Any], collection_keys: dict[str, str], inbox_key: str
) -> str:
    """A label belongs with the group it names; everything else starts in Inbox.

    Inbox is a holding pen the researcher empties by dragging, which is the
    grouping decision itself — so nothing else may pre-empt it.
    """
    if card.get("origin") == "group_label" and card.get("kj_path"):
        return collection_keys.get(card["kj_path"], inbox_key)
    return inbox_key
