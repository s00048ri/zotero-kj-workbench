"""The paper, as Markdown a typesetter can use.

Citations leave as citekeys, never as pre-rendered author-year strings, so the
file can go straight into pandoc or be re-linked in Zotero. And the appendix
says which sections were drafted with a model's help, from the prompts this
app actually sent — a record the researcher keeps for themselves, and can hand
over if they are ever asked.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .citekeys import citekeys
from .compose import chosen_question, list_claims, list_sections
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


def paper_markdown(conn: sqlite3.Connection, project_id: str) -> str:
    project = dict(
        conn.execute("SELECT * FROM project WHERE id = ?", (project_id,)).fetchone()
    )
    question = chosen_question(conn, project_id)
    sections = list_sections(conn, project_id)
    drafts = latest_drafts(conn, project_id)

    lines = [f"# {project['name']}", ""]
    if question:
        lines += [f"**Research question.** {question['text']}", ""]
    claims = list_claims(conn, project_id)
    if claims:
        lines += ["**Claims.**", ""]
        lines += [f"- {c['text']}" for c in claims]
        lines.append("")

    open_work: list[str] = []
    assisted: list[str] = []

    # A draft of the whole paper is the paper. Section drafts, if any, follow
    # it as the parts that were written separately.
    whole = latest_whole_paper(conn, project_id)
    if whole:
        lines += [
            to_markdown(conn, project_id, None, whole["content"]).strip(),
            "",
        ]
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
        draft = drafts.get(section["id"])
        if not draft:
            lines += [
                f"<!-- not drafted yet — {section['evidence_count']} cards assigned -->",
                "",
            ]
            continue
        body = to_markdown(conn, project_id, section["id"], draft["content"])
        lines += [body.strip(), ""]
        open_work += [
            f"{section['title']}: {m.group(1).strip() or '(unspecified)'}"
            for m in EVIDENCE_NEEDED_RE.finditer(draft["content"])
        ]
        if draft["prompt_export_id"]:
            assisted.append(f"{section['title']} (draft v{draft['version']})")

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
            lines.append(
                f"- `@{key}` — {name} {row['year'] or 'n.d.'}, *{row['title'] or '—'}* "
                f"(Zotero {row['zotero_item_key']})"
            )
        lines.append("")

    return "\n".join(lines)
