"""The block of text the researcher pastes into a chat.

No API key, no calls, no cost. What leaves this app is a complete,
self-contained prompt, and the exact text is stored so the researcher can see
later which bundle produced which draft.

Two things every export has to carry, because the pasted text is the only
place the model will ever see them:

* **whose words each card is.** A quote is a source's; an idea card is the
  researcher's own and is never citable as a source. That distinction is this
  product's whole thesis and it has to survive into plain text;
* **what is not allowed.** A model asked to write about evidence will fill a
  gap rather than leave one, so the instruction to write
  ``[EVIDENCE NEEDED: …]`` instead is stated first and stated plainly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .cards import citation_of
from .compose import chosen_question, list_claims, list_sections, section_evidence
from .groups import list_groups
from .store import insert, now_iso

SOFT_LIMIT_CHARS = 150_000

KINDS = ("themes", "questions", "outline", "section")


def estimate_tokens(text: str) -> int:
    """A rough count, and honest about being rough.

    Latin script runs about four characters to a token; Japanese runs closer
    to one. Counting both separately is far better than dividing by four.
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    other = len(text) - ascii_chars
    return round(ascii_chars / 4 + other)


@dataclass
class Prompt:
    kind: str
    content: str
    section_id: str | None = None
    title: str = ""
    warning: str | None = None

    @property
    def chars(self) -> int:
        return len(self.content)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.content)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "title": self.title,
            "section_id": self.section_id,
            "content": self.content,
            "chars": self.chars,
            "tokens": self.tokens,
            "warning": self.warning or self._length_warning(),
        }

    def _length_warning(self) -> str | None:
        if self.chars <= SOFT_LIMIT_CHARS:
            return None
        return (
            f"This is {self.chars:,} characters. Long prompts get skimmed. "
            f"Export one section at a time instead."
        )


# -- shared pieces --------------------------------------------------------

WHOSE_WORDS = (
    "Two kinds of card appear below.\n"
    "  quote — the source's own words. Citable.\n"
    "  idea  — the researcher's own words, written while reading. NOT a\n"
    "          source, never cited as one, and never attributed to anybody\n"
    "          but the researcher.\n"
)


def _card_block(card: dict[str, Any], *, index: str = "") -> str:
    kind = "quote" if card["kind"] == "quote" else "idea"
    head = f"[{card['human_id']}] {kind}"
    if index:
        head += f" | {index}"
    lines = [head]
    if kind == "quote":
        citation = card.get("citation") or citation_of(card)
        if citation:
            estimated = " (locator estimated — verify)" if card.get("locator_estimated") else ""
            lines.append(f"  {citation}{estimated}")
        lines.append(f"  \"{card['text']}\"")
    else:
        lines.append("  (the researcher's own words)")
        lines.append(f"  {card['text']}")
    if card.get("user_instruction"):
        lines.append(f"  instruction: {card['user_instruction']}")
    return "\n".join(lines)


def _wrap(title: str, body: str) -> str:
    return f"=== {title} ===\n{body.rstrip()}\n"


# -- 1. groups → themes and tensions --------------------------------------


def themes_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    groups = list_groups(conn, project["id"])
    if not groups:
        raise ValueError(
            "There are no groups yet. Sort your cards in Zotero first — the "
            "groups are what this prompt is about."
        )

    parts = [
        _wrap(
            "TASK",
            "Below are groups of passages a researcher put together while\n"
            "reading, and the proposition they wrote about each group.\n\n"
            "Tell them:\n"
            "  1. what each group is really claiming — in one sentence, and say\n"
            "     so plainly where their own label does not match its members;\n"
            "  2. which groups are in tension with each other, and where the\n"
            "     tension actually lies;\n"
            "  3. what is conspicuously absent, given what is here.\n\n"
            "Do not summarise the passages back. Do not introduce sources,\n"
            "authors or facts that are not below. A gap in this collection is\n"
            "not a gap in the literature — say which you think it is.",
        ),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
    ]

    for group in groups:
        body = [f"GROUP: {group.name}"]
        if group.label:
            body.append(f"the researcher's label: {group.label['text']}")
        else:
            body.append("the researcher has not labelled this group yet")
        body.append("")
        body += [_card_block(card) for card in group.cards]
        parts.append(_wrap(f"GROUP — {group.name}", "\n".join(body)))

    return Prompt(kind="themes", content="\n".join(parts), title="Groups → themes and tensions")


# -- 2. themes → research questions ---------------------------------------


def questions_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    groups = [g for g in list_groups(conn, project["id"]) if g.label]
    if not groups:
        raise ValueError(
            "No group has a label yet. The labels are the material this prompt "
            "works from — write one sentence per group first."
        )

    labels = "\n".join(
        f"- {g.name}: {g.label['text']}  ({g.size} cards)" for g in groups
    )
    parts = [
        _wrap(
            "TASK",
            "Below are the propositions a researcher wrote about groups of\n"
            "passages they collected.\n\n"
            "Propose 3–5 research questions this material could actually\n"
            "answer. For each one:\n"
            "  - name the groups that support it;\n"
            "  - say what would count as an answer;\n"
            "  - say what is missing before it could be answered.\n\n"
            "A question this collection cannot answer is not a good question\n"
            "here, however interesting. And a gap in this collection is not a\n"
            "gap in the literature: the researcher has read what they have\n"
            "read, and nothing below tells you what exists elsewhere.",
        ),
        _wrap("THE RESEARCHER'S PROPOSITIONS", labels),
    ]
    return Prompt(kind="questions", content="\n".join(parts), title="Themes → research questions")


# -- 3. outline ------------------------------------------------------------


def outline_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    question = chosen_question(conn, project["id"])
    groups = [g for g in list_groups(conn, project["id"]) if g.label]
    claims = list_claims(conn, project["id"])
    if not question:
        raise ValueError("Choose a research question first — the outline is for it.")

    body = [f"Research question: {question['text']}"]
    if question.get("rationale"):
        body.append(f"Why it matters: {question['rationale']}")
    if claims:
        body.append("")
        body.append("Claims the researcher wants to make:")
        body += [f"- [{c['claim_type']}] {c['text']}" for c in claims]
    if groups:
        body.append("")
        body.append("Evidence available, as the researcher grouped it:")
        body += [f"- {g.name}: {g.label['text']}  ({g.size} cards)" for g in groups]

    parts = [
        _wrap(
            "TASK",
            "Propose a section structure for a paper answering the question\n"
            "below, using only the evidence described.\n\n"
            "For each section give: a title, what it has to establish, which\n"
            "groups supply its evidence, and a rough length. Say which claims\n"
            "have no evidence behind them yet, rather than inventing a section\n"
            "that would need some.",
        ),
        _wrap("THE ARGUMENT SO FAR", "\n".join(body)),
    ]
    return Prompt(kind="outline", content="\n".join(parts), title="Outline")


# -- 4. section draft ------------------------------------------------------

SECTION_RULES = """Draft the section named below using ONLY the evidence listed in
ALLOWED EVIDENCE.

Rules:
- Every source-dependent claim carries a marker: [[CITE:KJ-0042]]
- Never invent a source, author, date, page number, or quotation.
- Where citation_mode is direct_quote, reproduce the quotation exactly as
  given, in quotation marks.
- Where citation_mode is paraphrase, restate it in your own words — do not
  track the original's wording or sentence shape.
- Where citation_mode is reference_only, refer to the source's position
  without quoting or closely paraphrasing it.
- If the evidence does not support something the section needs, write
  [EVIDENCE NEEDED: what is missing] rather than filling the gap.
- Distinguish what a source states, what the researcher takes it to mean,
  and what this paper argues.
- Do not open with a summary of what the section will do, and do not close
  with a summary of what it did."""


def section_prompt(
    conn: sqlite3.Connection, project: dict[str, Any], section_id: str
) -> Prompt:
    section = conn.execute(
        "SELECT * FROM outline_section WHERE id = ? AND project_id = ?",
        (section_id, project["id"]),
    ).fetchone()
    if section is None:
        raise ValueError("No such section.")
    section = dict(section)

    evidence = section_evidence(conn, section_id)
    if not evidence:
        raise ValueError(
            f"“{section['title']}” has no evidence assigned yet. A section "
            f"prompt with no cards would be an invitation to invent some."
        )

    question = chosen_question(conn, project["id"])
    head = [f"Title:   {section['title']}"]
    if section.get("purpose"):
        head.append(f"Purpose: {section['purpose']}")
    if question:
        head.append(f"Research question: {question['text']}")
    if section.get("thesis"):
        head.append(f"Thesis: {section['thesis']}")
    if section.get("target_words"):
        head.append(f"Target length: {section['target_words']} words")

    blocks = []
    for card in evidence:
        index = f"{card['citation_mode']} | {card['argument_role']}"
        blocks.append(_card_block(card, index=index))

    estimated = [c["human_id"] for c in evidence if c["locator_estimated"]]
    parts = [
        _wrap("TASK", SECTION_RULES),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
        _wrap("SECTION", "\n".join(head)),
        _wrap("ALLOWED EVIDENCE", "\n\n".join(blocks)),
    ]
    if estimated:
        parts.append(
            _wrap(
                "LOCATORS TO VERIFY",
                "These cards carry an estimated locator, not a page the\n"
                "researcher has checked. Cite them without the page, or leave\n"
                "the page for them to fill in: " + ", ".join(estimated),
            )
        )

    return Prompt(
        kind="section",
        content="\n".join(parts),
        section_id=section_id,
        title=f"Draft: {section['title']}",
    )


# -- building and storing --------------------------------------------------


def build(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    kind: str,
    *,
    section_id: str | None = None,
) -> Prompt:
    if kind == "themes":
        return themes_prompt(conn, project)
    if kind == "questions":
        return questions_prompt(conn, project)
    if kind == "outline":
        return outline_prompt(conn, project)
    if kind == "section":
        if not section_id:
            raise ValueError("Which section?")
        return section_prompt(conn, project, section_id)
    raise ValueError(f"kind must be one of {KINDS}")


def store(conn: sqlite3.Connection, project_id: str, prompt: Prompt) -> str:
    return insert(
        conn,
        "prompt_export",
        {
            "project_id": project_id,
            "kind": prompt.kind,
            "section_id": prompt.section_id,
            "content": prompt.content,
            "chars": prompt.chars,
            "created_at": now_iso(),
        },
    )


def available(conn: sqlite3.Connection, project_id: str) -> dict[str, Any]:
    """Which exports would work right now, and what is missing for the rest."""
    groups = list_groups(conn, project_id)
    labelled = [g for g in groups if g.label]
    question = chosen_question(conn, project_id)
    sections = list_sections(conn, project_id)
    return {
        "themes": {
            "ready": bool(groups),
            "blocked_by": None if groups else "no groups yet",
        },
        "questions": {
            "ready": bool(labelled),
            "blocked_by": None if labelled else "no group has a label yet",
        },
        "outline": {
            "ready": bool(question),
            "blocked_by": None if question else "no research question chosen",
        },
        "section": {
            "ready": any(s["evidence_count"] for s in sections),
            "blocked_by": (
                None
                if any(s["evidence_count"] for s in sections)
                else "no section has evidence assigned"
            ),
        },
    }
