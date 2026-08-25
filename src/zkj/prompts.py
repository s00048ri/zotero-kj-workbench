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

Nothing here requires the researcher to have specified anything. A question
they have not chosen, a group they have not labelled, a section whose evidence
they have not picked — each of those is something the model is asked to
propose, and to mark as its own proposal. Specifying is how you take control
of a decision, never a toll gate you pass before the tool will work.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .cards import CARD_SELECT, citation_of
from .compose import chosen_question, list_claims, list_sections, section_evidence
from .groups import list_groups
from .store import insert, now_iso

SOFT_LIMIT_CHARS = 150_000

KINDS = ("themes", "questions", "outline", "section", "paper")

# What a model is asked to work out for itself when the researcher has not
# said. Shown in the interface so the automatic choice is never a surprise.
INFERRED = {
    "themes": "what each unlabelled group is claiming",
    "questions": "the themes, from the groups themselves where you have not "
    "labelled them",
    "outline": "the research question, if you have not chosen one",
    "section": "which passages the section uses, if you have not assigned any",
    "paper": "the argument, the sections, and what each one claims",
}


def _plural(n: int, one: str) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {one}s"


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
    note: str | None = None

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
            "note": self.note,
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


def _all_cards(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Every card that could be evidence, grouped or not."""
    rows = [
        dict(r)
        for r in conn.execute(
            CARD_SELECT
            + " WHERE c.project_id = ? AND c.status = 'active' AND c.kind != 'image' "
            "AND c.origin != 'group_label' "
            "ORDER BY COALESCE(c.kj_path, c.prior_path), c.human_id",
            (project_id,),
        )
    ]
    for row in rows:
        row["citation"] = citation_of(row)
    return rows


def _buckets(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """The researcher's own grouping, whatever form it has reached.

    Cards they dragged into subcollections if they have; otherwise the folders
    the sources already sat in; otherwise one undifferentiated pile. A tool
    that refuses to work until the material is tidy is a tool that never gets
    used on the day the thinking is unfinished — which is every day until the
    last one.
    """
    groups = list_groups(conn, project_id)
    if groups:
        placed = {c["id"] for g in groups for c in g.cards}
        buckets = [
            {
                "name": g.name,
                "label": g.label["text"] if g.label else None,
                "cards": g.cards,
                "source": "group",
            }
            for g in groups
        ]
        loose = [c for c in _all_cards(conn, project_id) if c["id"] not in placed]
        if loose:
            buckets.append(
                {
                    "name": "not yet grouped",
                    "label": None,
                    "cards": loose,
                    "source": "ungrouped",
                }
            )
        return buckets

    cards = _all_cards(conn, project_id)
    by_path: dict[str, list[dict[str, Any]]] = {}
    for card in cards:
        by_path.setdefault(card["prior_path"] or "not in any collection", []).append(card)
    return [
        {"name": path, "label": None, "cards": group, "source": "folder"}
        for path, group in sorted(by_path.items())
    ]


def _bucket_block(bucket: dict[str, Any]) -> str:
    lines = [f"GROUP: {bucket['name']}"]
    if bucket["label"]:
        lines.append(f"the researcher's label: {bucket['label']}")
    elif bucket["source"] == "ungrouped":
        lines.append(
            "the researcher has not sorted these anywhere yet — say whether "
            "they belong with a group above, or are a group of their own"
        )
    else:
        lines.append(
            "the researcher put these together but has not said why — that is "
            "yours to work out"
        )
    lines.append("")
    lines += [_card_block(card) for card in bucket["cards"]]
    return "\n".join(lines)


def _fixed_block(conn: sqlite3.Connection, project_id: str) -> tuple[str, bool]:
    """What the researcher has decided, and whether they have decided anything."""
    question = chosen_question(conn, project_id)
    claims = list_claims(conn, project_id)
    sections = list_sections(conn, project_id)
    lines: list[str] = []
    if question:
        lines.append(f"Research question (fixed): {question['text']}")
        if question.get("rationale"):
            lines.append(f"  why it matters: {question['rationale']}")
    if claims:
        lines.append("Claims the researcher wants to make (fixed):")
        lines += [f"  - [{c['claim_type']}] {c['text']}" for c in claims]
    if sections:
        lines.append("Sections the researcher has already named (fixed):")
        for section in sections:
            detail = f" — {section['purpose']}" if section["purpose"] else ""
            lines.append(f"  - {section['title']}{detail}")
    if not lines:
        return (
            "The researcher has fixed nothing yet. The question, the sections "
            "and what each one claims are all yours to propose.",
            False,
        )
    lines.append("")
    lines.append(
        "Everything above is the researcher's decision and is not yours to "
        "revise. Anything not listed is yours to propose, and must be marked "
        "as your proposal."
    )
    return ("\n".join(lines), True)


# -- 1. groups → themes and tensions --------------------------------------


def _no_cards() -> ValueError:
    return ValueError(
        "There are no cards yet. Read a collection first — everything here is "
        "made out of the passages you highlighted."
    )


def themes_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    buckets = _buckets(conn, project["id"])
    if not buckets:
        raise _no_cards()
    unlabelled = [b for b in buckets if not b["label"]]

    task = (
        "Below are groups of passages a researcher put together while reading.\n"
        "Some carry a proposition they wrote; some do not.\n\n"
        "Tell them:\n"
        "  1. what each group is really claiming — in one sentence. Where they\n"
        "     wrote a label, say plainly if it does not match its members;\n"
        "     where they wrote none, propose one and mark it as yours;\n"
        "  2. which groups are in tension with each other, and where the\n"
        "     tension actually lies;\n"
        "  3. what is conspicuously absent, given what is here.\n\n"
        "Do not summarise the passages back. Do not introduce sources,\n"
        "authors or facts that are not below. A gap in this collection is\n"
        "not a gap in the literature — say which you think it is."
    )
    parts = [_wrap("TASK", task), _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip())]
    parts += [_wrap(f"GROUP — {b['name']}", _bucket_block(b)) for b in buckets]

    note = None
    if len(unlabelled) == len(buckets):
        note = (
            "No group is labelled, so every proposition below will be the "
            "model's reading rather than yours."
        )
    return Prompt(
        kind="themes",
        content="\n".join(parts),
        title="Groups → themes and tensions",
        note=note,
    )


# -- 2. themes → research questions ---------------------------------------


def questions_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    buckets = _buckets(conn, project["id"])
    if not buckets:
        raise _no_cards()

    task = (
        "Below is what a researcher has collected, in the groupings they made.\n"
        "Where they wrote a proposition about a group it is given; where they\n"
        "did not, the passages are given instead and the theme is yours to\n"
        "work out.\n\n"
        "Propose 3–5 research questions this material could actually answer.\n"
        "For each one:\n"
        "  - name the groups that support it;\n"
        "  - say what would count as an answer;\n"
        "  - say what is missing before it could be answered.\n\n"
        "A question this collection cannot answer is not a good question here,\n"
        "however interesting. And a gap in this collection is not a gap in the\n"
        "literature: the researcher has read what they have read, and nothing\n"
        "below tells you what exists elsewhere."
    )
    parts = [_wrap("TASK", task), _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip())]

    labelled = [b for b in buckets if b["label"]]
    if labelled:
        parts.append(
            _wrap(
                "THE RESEARCHER'S OWN PROPOSITIONS",
                "\n".join(f"- {b['name']}: {b['label']}" for b in labelled),
            )
        )
    parts += [_wrap(f"GROUP — {b['name']}", _bucket_block(b)) for b in buckets]
    return Prompt(
        kind="questions",
        content="\n".join(parts),
        title="Themes → research questions",
        note=None
        if labelled
        else "No group is labelled, so the themes will be inferred from the "
        "passages themselves.",
    )


# -- 3. outline ------------------------------------------------------------


def outline_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    buckets = _buckets(conn, project["id"])
    if not buckets:
        raise _no_cards()
    question = chosen_question(conn, project["id"])
    fixed, has_fixed = _fixed_block(conn, project["id"])

    task = (
        "Propose a section structure for a paper built from the material below.\n\n"
        + (
            "The research question is fixed; the structure is yours.\n\n"
            if question
            else "No research question has been chosen. Propose the one this\n"
            "material can best answer, say why, and then build the structure\n"
            "for it. Mark the question as your proposal.\n\n"
        )
        + "For each section give: a title, what it has to establish, which\n"
        "groups supply its evidence, and a rough length. Say which claims have\n"
        "no evidence behind them yet, rather than inventing a section that\n"
        "would need some."
    )
    parts = [
        _wrap("TASK", task),
        _wrap("WHAT THE RESEARCHER HAS FIXED", fixed),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
    ]
    parts += [
        _wrap(
            f"GROUP — {b['name']}",
            f"GROUP: {b['name']}"
            + (f"\nthe researcher's label: {b['label']}" if b["label"] else "")
            + f"\n{len(b['cards'])} cards\n\n"
            + "\n".join(_card_block(c) for c in b["cards"]),
        )
        for b in buckets
    ]
    return Prompt(
        kind="outline",
        content="\n".join(parts),
        title="Outline",
        note=None if has_fixed else "Nothing is fixed, so the question and the "
        "structure will both be proposals.",
    )


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
- Where a card carries no citation_mode, choose one and use it consistently.
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
    chose_evidence = bool(evidence)
    if not evidence:
        # Nothing assigned: everything is allowed and the model picks. Still a
        # closed set, so no source can be invented — only chosen badly.
        evidence = _all_cards(conn, project["id"])
        if not evidence:
            raise _no_cards()

    question = chosen_question(conn, project["id"])
    head = [f"Title:   {section['title']}"]
    if section.get("purpose"):
        head.append(f"Purpose: {section['purpose']}")
    else:
        head.append(
            "Purpose: not stated — work out what this section has to establish, "
            "and say so before you draft it."
        )
    if question:
        head.append(f"Research question: {question['text']}")
    if section.get("thesis"):
        head.append(f"Thesis: {section['thesis']}")
    if section.get("target_words"):
        head.append(f"Target length: {section['target_words']} words")

    blocks = []
    for card in evidence:
        index = (
            f"{card['citation_mode']} | {card['argument_role']}"
            if chose_evidence
            else "you choose how to use it"
        )
        blocks.append(_card_block(card, index=index))

    estimated = [c["human_id"] for c in evidence if c["locator_estimated"]]
    parts = [
        _wrap("TASK", SECTION_RULES),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
        _wrap("SECTION", "\n".join(head)),
        _wrap(
            "ALLOWED EVIDENCE",
            (
                ""
                if chose_evidence
                else "The researcher has not said which of these the section "
                "uses. Use what the section needs and leave the rest; say at "
                "the end which you left and why.\n\n"
            )
            + "\n\n".join(blocks),
        ),
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
        note=None
        if chose_evidence
        else f"No evidence is assigned to this section, so all "
        f"{len(evidence)} cards are offered and the model chooses.",
    )


# -- 5. the whole paper ----------------------------------------------------

PAPER_RULES = """Rules, in force for every section you write:
- Every source-dependent claim carries a marker: [[CITE:KJ-0042]]
- Never invent a source, author, date, page number, or quotation. The cards
  below are the only evidence that exists.
- Reproduce a quotation exactly, in quotation marks, or restate it entirely in
  your own words. Do not half-do either: a restatement that tracks the
  original's wording is the failure this researcher will be checking for.
- If the argument needs something the evidence does not have, write
  [EVIDENCE NEEDED: what is missing]. Do not fill the gap.
- Distinguish what a source states, what the researcher takes it to mean, and
  what this paper argues."""


def paper_prompt(conn: sqlite3.Connection, project: dict[str, Any]) -> Prompt:
    buckets = _buckets(conn, project["id"])
    if not buckets:
        raise _no_cards()
    fixed, has_fixed = _fixed_block(conn, project["id"])
    total = sum(len(b["cards"]) for b in buckets)

    task = (
        "Write a paper out of the passages below.\n\n"
        "A researcher collected and grouped these while reading. Work out what\n"
        "they add up to, and write the paper that argument deserves.\n\n"
        "Do it in this order, and show each step:\n"
        "  1. Say what you take the argument to be, in one paragraph, before\n"
        "     you draft anything. If the evidence supports more than one, give\n"
        "     the strongest two and say which you are taking.\n"
        "  2. Propose the sections, and what each one has to establish.\n"
        "  3. Draft the paper.\n"
        "  4. End with two lists: what you inferred rather than were told, and\n"
        "     what the evidence could not carry.\n\n"
        + PAPER_RULES
    )

    parts = [
        _wrap("TASK", task),
        _wrap("WHAT THE RESEARCHER HAS FIXED", fixed),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
    ]
    parts += [_wrap(f"GROUP — {b['name']}", _bucket_block(b)) for b in buckets]

    return Prompt(
        kind="paper",
        content="\n".join(parts),
        title="The whole paper",
        note=(
            f"{total} cards in {len(buckets)} groups."
            + ("" if has_fixed else " Nothing is fixed, so the argument, the "
               "sections and their claims are all the model's to propose.")
        ),
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
    if kind == "paper":
        return paper_prompt(conn, project)
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
    """What can be exported, and what each one will work out for itself.

    Only one thing blocks anything here: having no cards at all. Everything
    else the researcher might have specified is optional, and what they leave
    unspecified is named so the automatic choice is never a surprise.
    """
    cards = conn.execute(
        "SELECT COUNT(*) FROM card WHERE project_id = ? AND status = 'active' "
        "AND kind != 'image' AND origin != 'group_label'",
        (project_id,),
    ).fetchone()[0]
    groups = list_groups(conn, project_id)
    labelled = [g for g in groups if g.label]
    question = chosen_question(conn, project_id)
    sections = list_sections(conn, project_id)
    assigned = [s for s in sections if s["evidence_count"]]

    blocked = None if cards else "no cards yet — read a collection first"
    specified = {
        "themes": f"{len(labelled)} of {_plural(len(groups), 'group')} labelled"
        if groups
        else "no groups yet; your folders will be used",
        "questions": _plural(len(labelled), "label") + " written"
        if labelled
        else "no labels written",
        "outline": "a question is chosen" if question else "no question chosen",
        "section": f"{len(assigned)} of {_plural(len(sections), 'section')} "
        f"with evidence assigned"
        if sections
        else "no sections yet",
        "paper": "nothing fixed"
        if not (question or sections)
        else "your question and sections will be kept",
    }
    return {
        kind: {
            "ready": bool(cards) and (kind != "section" or bool(sections)),
            "blocked_by": (
                blocked
                if blocked
                else "add a section first"
                if kind == "section" and not sections
                else None
            ),
            "infers": INFERRED[kind],
            "specified": specified[kind],
        }
        for kind in KINDS
    }
