"""The paper, as Markdown a typesetter can use — and as a file that stands alone.

Citations leave as citekeys, never as pre-rendered author-year strings, so the
file can go straight into pandoc or be re-linked in Zotero. The appendix says
which sections were drafted with a model's help, from the prompts this app
actually sent — a record the researcher keeps for themselves, and can hand over
if they are ever asked.

Where a section has not been drafted, its **evidence is written out in full**
rather than left as a stub. A file that says "not drafted yet — 0 cards
assigned" is no use to anybody: not to the researcher reading it, and not to a
model being handed it. Everything the passages say travels with them.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .citekeys import citekeys
from .compose import (
    chosen_question,
    list_claims,
    list_sections,
    section_evidence,
    unassigned_cards,
)
from .continuations import label_of, quotation_of
from .prompts import DRAFT_TASK, PAPER_RULES, QUOTING
from .validate import EVIDENCE_NEEDED_RE, to_markdown


def latest_drafts(conn: sqlite3.Connection, project_id: str) -> dict[str, dict[str, Any]]:
    drafts: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT * FROM draft WHERE project_id = ? ORDER BY section_id, version",
        (project_id,),
    ):
        drafts[row["section_id"]] = dict(row)
    return drafts


def latest_whole_paper(conn: sqlite3.Connection, project_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM draft WHERE project_id = ? AND section_id IS NULL "
        "ORDER BY version DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    return dict(row) if row else None


def card_markdown(card: dict[str, Any]) -> list[str]:
    """One card, whole. The quotation is the thing; nothing is abbreviated."""
    lines: list[str] = []
    if card["kind"] == "quote":
        citation = card.get("citation") or ""
        estimated = " *(locator estimated — verify)*" if card.get("locator_estimated") else ""
        split = (
            " *(one passage, split across a page break)*"
            if len(card.get("joined_ids") or []) > 1
            else ""
        )
        lines.append(f"**[{label_of(card)}]** {citation}{estimated}{split}")
        lines.append("")
        lines.append(f"> {quotation_of(card)}")
    else:
        citation = card.get("citation") or ""
        lines.append(
            f"**[{label_of(card)}]** the researcher's own words, written while "
            f"reading {citation}"
            if citation
            else f"**[{label_of(card)}]** the researcher's own words"
        )
        lines.append("")
        lines.append(card["text"])
    if card.get("citation_mode") and card.get("argument_role"):
        lines.append("")
        lines.append(f"*{card['citation_mode']} · {card['argument_role']}*")
    lines.append("")
    return lines


def instructions_block() -> list[str]:
    """What to do with this file, written into the file.

    A researcher will hand this to a model, because it is the file that holds
    everything. Handed material and no task, a model reports on the material —
    which is a reasonable thing to do and not what was wanted. So the task
    travels with it.
    """
    return [
        "## What to do with this file",
        "",
        "<!-- Delete this section if you are reading rather than drafting. -->",
        "",
        "If you are a language model being handed this file, the task is:",
        "",
        "```",
        DRAFT_TASK.format(quoting=QUOTING["model"]),
        "",
        PAPER_RULES,
        "```",
        "",
        "The passages below are the only sources that exist. Their markers are",
        "the `[KJ-0000]` numbers; cite them as `[[CITE:KJ-0000]]`.",
        "",
    ]


def paper_markdown(
    conn: sqlite3.Connection, project_id: str, *, instructions: bool = True
) -> str:
    project = dict(
        conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    )
    question = chosen_question(conn, project_id)
    sections = list_sections(conn, project_id)
    drafts = latest_drafts(conn, project_id)
    whole = latest_whole_paper(conn, project_id)
    claims = list_claims(conn, project_id)

    lines = [f"# {project['name']}", ""]
    lines += [
        "<!-- Everything this project holds: what has been drafted, and the",
        "     evidence for what has not. Quotations are reproduced in full. -->",
        "",
    ]
    if instructions and not whole:
        lines += instructions_block()

    if question:
        lines += [f"**Research question.** {question['text']}", ""]
    else:
        lines += [
            "**Research question.** Not chosen — a reader of this file should",
            "work out what these passages can answer.",
            "",
        ]
    if claims:
        lines += ["**Claims.**", ""]
        lines += [f"- {c['text']}" for c in claims]
        lines.append("")

    open_work: list[str] = []
    assisted: list[str] = []

    if whole:
        lines += [to_markdown(conn, project_id, None, whole["content"]).strip(), ""]
        open_work += [
            f"the paper: {m.group(1).strip() or '(unspecified)'}"
            for m in EVIDENCE_NEEDED_RE.finditer(whole["content"])
        ]
        if whole["prompt_export_id"]:
            assisted.append(f"the whole paper (draft v{whole['version']})")
        if sections:
            lines += ["## Sections drafted separately", ""]

    for section in sections:
        lines += [f"## {section['title']}", ""]
        if section["purpose"]:
            lines += [f"*{section['purpose']}*", ""]
        draft = drafts.get(section["id"])
        if draft:
            body = to_markdown(conn, project_id, section["id"], draft["content"])
            lines += [body.strip(), ""]
            open_work += [
                f"{section['title']}: {m.group(1).strip() or '(unspecified)'}"
                for m in EVIDENCE_NEEDED_RE.finditer(draft["content"])
            ]
            if draft["prompt_export_id"]:
                assisted.append(f"{section['title']} (draft v{draft['version']})")
            continue

        evidence = section_evidence(conn, section["id"])
        if evidence:
            lines += ["*Not drafted. Its evidence, in full:*", ""]
            for card in evidence:
                lines += card_markdown(card)
        else:
            lines += [
                "*Not drafted, and no evidence assigned to it. The material "
                "below is what there is.*",
                "",
            ]

    loose = unassigned_cards(conn, project_id)
    if loose:
        heading = (
            "## Material not placed in a section"
            if sections
            else "## The material, as the researcher grouped it"
        )
        lines += [heading, ""]
        lines += [
            "<!-- These are the researcher's own groupings. The order is not an",
            "     argument: it is the order the folders happened to be in. -->",
            "",
        ]
        by_group: dict[str, list[dict[str, Any]]] = {}
        # Straight from the label cards: list_groups() also computes a
        # similarity pair per group, which an export has no use for.
        labels = {
            r["kj_path"]: r["text"]
            for r in conn.execute(
                "SELECT kj_path, text FROM card WHERE project_id = ? "
                "AND origin = 'group_label'",
                (project_id,),
            )
        }
        for card in loose:
            key = card["kj_path"] or card["prior_path"] or "not in any group"
            by_group.setdefault(key, []).append(card)
        for path, cards in by_group.items():
            lines += [f"### {path.rsplit('/', 1)[-1]}", ""]
            if labels.get(path):
                lines += [f"*The researcher's label: {labels[path]}*", ""]
            else:
                # Silence here reads as "no label was possible". Say which it is,
                # so nothing downstream takes a folder name for a claim.
                lines += [
                    "*No label written — the folder name is not a claim about "
                    "these passages.*",
                    "",
                ]
            for card in cards:
                lines += card_markdown(card)

    if open_work:
        lines += ["## Open work", ""]
        lines += [f"- {item}" for item in open_work]
        lines.append("")

    lines += ["## Appendix: how this draft was made", ""]
    if assisted:
        lines += [
            "These sections were drafted by a language model from a prompt built",
            "by Zotero KJ Workbench, containing only the evidence listed for that",
            "section, and were then checked against their sources:",
            "",
        ]
        lines += [f"- {item}" for item in assisted]
    else:
        lines.append("No section here was drafted by a model.")
    lines += [
        "",
        "Every quotation was compared against the highlighted text it came from,",
        "and every paraphrase against the original's wording.",
        "",
    ]

    keys = citekeys(conn, project_id)
    if keys:
        lines += ["## Sources cited by key", ""]
        for row in conn.execute(
            "SELECT id, creators_short, year, title, zotero_item_key FROM source "
            "WHERE project_id = ? ORDER BY creators_short, year",
            (project_id,),
        ):
            key = keys.get(row["id"])
            name = row["creators_short"] or "—"
            thin = (
                "  ← **this Zotero record has "
                + " and ".join(
                    filter(
                        None,
                        [
                            "no author" if not row["creators_short"] else None,
                            "no date" if not row["year"] else None,
                        ],
                    )
                )
                + "**; refer to it by title until it is fixed"
                if not row["creators_short"] or not row["year"]
                else ""
            )
            lines.append(
                f"- `@{key}` — {name} {row['year'] or 'n.d.'}, *{row['title'] or '—'}* "
                f"(Zotero {row['zotero_item_key']}){thin}"
            )
        lines.append("")

    return "\n".join(lines)
