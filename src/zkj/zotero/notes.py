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
# Anything this tool generated or carried across from elsewhere. The importer
# refuses every note carrying it, so a kind added later is covered without
# anyone remembering to add a case.
GENERATED_TAG = "kj-generated"
# A link to a NotebookLM notebook, and what that notebook produced.
NOTEBOOK_TAG = "kj-notebook"
REPORT_TAG = "kj-notebook-report"


def _notebook_link(url: str, label: str) -> str:
    """A link Zotero will render as a link, with nothing interpolated raw."""
    return f'<a href="{escape_html(url)}">{escape_html(label)}</a>'


def note_html(
    card: dict[str, Any],
    *,
    project_name: str,
    citation: str = "",
    parent_human_id: str | None = None,
    notebook_url: str | None = None,
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
    if notebook_url:
        # The notebook covering this card's source, reachable from the card
        # itself — which is where the researcher is when the question occurs
        # to them.
        parts.append(
            "<p>"
            + _notebook_link(notebook_url, "Ask NotebookLM about this source")
            + "</p>"
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
    notebook_url: str | None = None,
) -> dict[str, Any]:
    return {
        "itemType": "note",
        "note": note_html(
            card,
            project_name=project_name,
            citation=citation,
            parent_human_id=parent_human_id,
            notebook_url=notebook_url,
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


def notebook_note_html(
    notebook: dict[str, Any],
    *,
    project_name: str,
    contents: dict[str, int] | None = None,
) -> str:
    """The note that carries a notebook's link into Zotero.

    Its job is to be found later. The researcher will meet it in the item pane
    months from now, so it says what the notebook is *of* and what was put in
    it, not just that a notebook exists.
    """
    scope = notebook.get("source_title") or project_name
    title = notebook.get("title") or f"NotebookLM — {scope}"
    parts = [
        f"<h2>{escape_html(title)}</h2>",
        f"<p>{_notebook_link(notebook['url'], 'Open this notebook in NotebookLM')}</p>",
    ]

    counts = contents or {}
    described = []
    if counts.get("urls"):
        described.append(
            f"{counts['urls']} source" + ("s" if counts["urls"] != 1 else "") + " by link"
        )
    if counts.get("files"):
        described.append(f"{counts['files']} file" + ("s" if counts["files"] != 1 else ""))
    if counts.get("passages"):
        described.append(f"{counts['passages']} of your own selected passages")
    described = [d for d in described if d]
    if described:
        parts.append(
            "<p><strong>Put in when this link was written:</strong> "
            + escape_html(", ".join(described))
            + ". What is in it now is whatever you have added since.</p>"
        )

    parts.append(
        "<p><em>Ask the notebook for a briefing, a study guide, a timeline, a "
        "mind map, an audio overview — as many as you like. Anything you want "
        "kept can be pasted back into the workbench, which files it beside the "
        "evidence it came from.</em></p>"
    )
    parts.append("<hr/>")
    parts.append(
        "<p>"
        f"kj:notebook={escape_html(notebook['id'])} "
        f"kj:project={escape_html(project_name)}"
        "</p>"
    )
    return "".join(parts)


def notebook_note_payload(
    notebook: dict[str, Any],
    *,
    project_name: str,
    parent_item_key: str | None = None,
    collection_key: str | None = None,
    contents: dict[str, int] | None = None,
) -> dict[str, Any]:
    """A notebook's link as a note.

    A notebook about one source hangs off that item, where the researcher will
    be reading. A notebook about the project is standalone and filed in `_KJ`
    — never in `_KJ/Inbox`, which is the sorting floor and takes nothing this
    tool decided to put there.
    """
    payload: dict[str, Any] = {
        "itemType": "note",
        "note": notebook_note_html(
            notebook, project_name=project_name, contents=contents
        ),
        "tags": [
            {"tag": NOTEBOOK_TAG},
            {"tag": GENERATED_TAG},
            {"tag": f"kj-project:{project_name}"},
        ],
    }
    if parent_item_key:
        payload["parentItem"] = parent_item_key
    elif collection_key:
        payload["collections"] = [collection_key]
    return payload


def report_note_html(
    report: dict[str, Any],
    *,
    project_name: str,
    notebook: dict[str, Any],
) -> str:
    """Something NotebookLM produced, kept beside the evidence it came from."""
    kind = (report.get("kind") or "other").replace("_", " ")
    title = report.get("title") or f"NotebookLM {kind}"
    parts = [
        f"<h2>{escape_html(title)}</h2>",
        "<p><em>Written by NotebookLM from the sources in "
        f"{_notebook_link(notebook['url'], 'this notebook')}"
        ", and pasted back unchanged. It is not your reading, it has not been "
        "checked against the sources, and it is not evidence.</em></p>",
    ]
    for block in (report.get("text") or "").split("\n\n"):
        block = block.strip()
        if block:
            parts.append(f"<p>{escape_html(block)}</p>")

    parts.append("<hr/>")
    parts.append(
        "<p>"
        f"kj:report={escape_html(report['id'])} "
        f"kj:kind={escape_html(report.get('kind') or 'other')} "
        f"kj:notebook={escape_html(notebook['id'])} "
        f"kj:project={escape_html(project_name)}"
        "</p>"
    )
    return "".join(parts)


def report_note_payload(
    report: dict[str, Any],
    *,
    project_name: str,
    notebook: dict[str, Any],
    parent_item_key: str | None = None,
    collection_key: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "itemType": "note",
        "note": report_note_html(report, project_name=project_name, notebook=notebook),
        "tags": [
            {"tag": REPORT_TAG},
            {"tag": GENERATED_TAG},
            {"tag": f"kj-project:{project_name}"},
        ],
    }
    if parent_item_key:
        payload["parentItem"] = parent_item_key
    elif collection_key:
        payload["collections"] = [collection_key]
    return payload
