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
from .continuations import attach, label_of, quotation_of
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
    head = f"[{label_of(card)}] {kind}"
    if index:
        head += f" | {index}"
    lines = [head]
    if kind == "quote":
        citation = card.get("citation") or citation_of(card)
        if citation:
            estimated = " (locator estimated — verify)" if card.get("locator_estimated") else ""
            lines.append(f"  {citation}{estimated}")
        lines.append(f"  \"{quotation_of(card)}\"")
        if len(card.get("joined_ids") or []) > 1:
            lines.append(
                "  (one passage, split across a page break in the PDF — quote it whole)"
            )
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
    attach(conn, rows)
    # A half-sentence offered on its own would be quoted on its own. The whole
    # passage travels under the card it starts on.
    return [r for r in rows if not r["is_continuation"]]


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
        for group in groups:
            attach(conn, group.cards)
            group.cards[:] = [c for c in group.cards if not c["is_continuation"]]
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


def thin_sources(conn: sqlite3.Connection, project_id: str) -> list[dict[str, Any]]:
    """Sources whose Zotero record cannot produce a normal citation."""
    return [
        dict(r)
        for r in conn.execute(
            "SELECT zotero_item_key, title, creators_short, year FROM source "
            "WHERE project_id = ? AND (creators_short IS NULL OR year IS NULL) "
            "AND id IN (SELECT source_id FROM card WHERE project_id = ?)",
            (project_id, project_id),
        )
    ]


def _thin_block(conn: sqlite3.Connection, project_id: str) -> str | None:
    """Say which citations cannot be written, rather than let one be invented.

    A model asked for "(Author, year)" where the record has neither will supply
    something. Naming the gap first is the difference between a citation to
    check and a citation to discover.
    """
    thin = thin_sources(conn, project_id)
    if not thin:
        return None
    lines = [
        "These sources' Zotero records are incomplete. Refer to them by title.",
        "Do not supply an author or a year for them — not even a likely one.",
        "",
    ]
    for source in thin:
        missing = ", ".join(
            filter(
                None,
                [
                    "no author" if not source["creators_short"] else None,
                    "no date" if not source["year"] else None,
                ],
            )
        )
        lines.append(f"- “{source['title'] or '(untitled)'}” — {missing}")
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
    thin = _thin_block(conn, project["id"])
    if thin:
        parts.append(_wrap("SOURCES WITH INCOMPLETE RECORDS", thin))
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

PAPER_MODES = ("draft", "assess")

# How the passages are to be used. The default leaves it to the model, per
# passage, which is what a writer does; the other two are for a researcher who
# has decided.
QUOTING = {
    "model": "For each passage you use, choose one and commit to it: quote it\n"
    "exactly, in quotation marks, or take only its point and put it entirely in\n"
    "your own words. Do not half-do either — a restatement that tracks the\n"
    "original's wording is the failure this researcher will be checking for.",
    "quote": "Where you use a passage, quote it — exactly, in quotation marks,\n"
    "and only as much of it as the sentence needs. Do not paraphrase the\n"
    "passages: this researcher wants the sources' own words on the page.",
    "ideas": "Do not quote. Take the point of a passage and write it entirely in\n"
    "your own words — no phrase of the original carried over, no sentence shape\n"
    "borrowed. The marker still says which passage the claim rests on.",
}

DRAFT_TASK = """Write a paper from the passages below. A draft — prose in sections,
with an argument running through them — not an assessment of the material.

A researcher collected these passages and put them into the groups you see.
Those groups are what sets this paper going: they are not a set to be
exhausted. Use what serves the argument and leave the rest.

{quoting}

Where the passages do not carry a step the argument needs, write that step
anyway, in the paper's own voice: reasoning, a definition, a transition, a
qualification. Mark it [UNSUPPORTED: what you are asserting] so the researcher
can find it. What you must never do is attribute it to anybody, or invent a
source, an author, a date, a page number or a quotation. The cards below are
the only sources that exist.

Every claim that rests on a passage carries its marker: [[CITE:KJ-0042]]

End with three short lists, after the paper:
  - what you argued that the evidence does not carry
  - which passages you left out, and why
  - what the researcher would have to read next"""

ASSESS_TASK = """Read the passages below and tell the researcher what they have.

Do not draft anything. Say:
  - what clusters the material actually falls into, and where that differs
    from the groups they made;
  - which questions this collection could answer, and which it could not;
  - what is missing — and be clear that a gap here is a gap in what they have
    read, not necessarily a gap in the literature;
  - anything in the material that is not what it appears to be: a position
    paper standing among empirical studies, an argument standing among
    findings."""

PAPER_RULES = """Rules, in force throughout:
- Never invent a source, author, date, page number, or quotation.
- Distinguish what a source states, what the researcher takes it to mean, and
  what this paper argues.
- An idea card is the researcher's own note. Its content can become the
  paper's argument; it is never cited as though somebody published it."""


def paper_prompt(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    mode: str = "draft",
    quoting: str = "model",
) -> Prompt:
    if mode not in PAPER_MODES:
        raise ValueError(f"mode must be one of {PAPER_MODES}")
    if quoting not in QUOTING:
        raise ValueError(f"quoting must be one of {tuple(QUOTING)}")
    buckets = _buckets(conn, project["id"])
    if not buckets:
        raise _no_cards()
    fixed, has_fixed = _fixed_block(conn, project["id"])
    total = sum(len(b["cards"]) for b in buckets)
    sections = list_sections(conn, project["id"])

    # A group is a claim about what belongs with what. It is not a claim about
    # what comes first: that is the order the folders happened to be in.
    order_note = (
        "The sections below are in the order the researcher set. Keep it.\n"
        "What each group contains is their decision; how the argument runs\n"
        "through them is what you are writing."
        if sections
        else "Each group below is the researcher's claim that these passages\n"
        "belong together — nothing more. The order they appear in is the order\n"
        "their folders happened to be in, and means nothing. Order the sections\n"
        "as the argument requires, split a group across two sections if it holds\n"
        "two ideas, and put two groups in one section if they are one idea."
    )

    task = (
        (
            DRAFT_TASK.format(quoting=QUOTING[quoting])
            + "\n\n"
            + order_note
            + "\n\n"
            + PAPER_RULES
        )
        if mode == "draft"
        else ASSESS_TASK
    )

    parts = [
        _wrap("TASK", task),
        _wrap("WHAT THE RESEARCHER HAS FIXED", fixed),
        _wrap("WHOSE WORDS", WHOSE_WORDS.rstrip()),
    ]
    thin = _thin_block(conn, project["id"])
    if thin:
        parts.append(_wrap("SOURCES WITH INCOMPLETE RECORDS", thin))
    parts += [_wrap(f"GROUP — {b['name']}", _bucket_block(b)) for b in buckets]

    return Prompt(
        kind="paper",
        content="\n".join(parts),
        title="The whole paper" if mode == "draft" else "What this material can answer",
        note=(
            f"{total} cards in {len(buckets)} groups."
            + (
                ""
                if has_fixed
                else " Nothing is fixed, so the argument, the sections and their "
                "claims are all the model's to propose."
            )
            if mode == "draft"
            else f"{total} cards in {len(buckets)} groups. No drafting — an "
            "account of what you have."
        ),
    )


# -- building and storing --------------------------------------------------


def build(
    conn: sqlite3.Connection,
    project: dict[str, Any],
    kind: str,
    *,
    section_id: str | None = None,
    mode: str = "draft",
    quoting: str = "model",
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
        return paper_prompt(conn, project, mode=mode, quoting=quoting)
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
