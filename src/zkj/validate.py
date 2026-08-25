"""Checking a draft that came back from a model.

Four things can go wrong between handing a model your evidence and getting
prose back, and only one of them is obvious:

1. it cites a card that was never in the section's evidence — obvious once
   checked, invisible otherwise;
2. it alters a quotation. Small alterations are the dangerous ones;
3. it "paraphrases" by tracking the original's wording. **This is the higher
   risk of the two**, because there are no quotation marks to draw the eye to
   it, and nothing about the sentence looks wrong;
4. it fills a gap in the evidence with something plausible instead of saying
   the gap is there.

So all four are checked, and the paraphrase check is not optional.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from .cards import CARD_SELECT, citation_of
from .citekeys import cite_marker, citekeys
from .compose import section_evidence
from .store import insert, now_iso

CITE_RE = re.compile(r"\[\[CITE:\s*([A-Za-z0-9\-]+)\s*\]\]")
EVIDENCE_NEEDED_RE = re.compile(r"\[EVIDENCE NEEDED:?\s*([^\]]*)\]", re.I)
# A quotation nests: a passage quoted with straight marks often contains
# curly ones, and vice versa. Matching each kind against its own closing mark
# keeps the outer span whole instead of stopping at the first inner quote.
QUOTE_SPAN_RE = re.compile(
    r"\"([^\"]{12,})\""            # straight double quotes
    r"|“([^“”]{12,})”"              # curly double quotes
    r"|「([^「」]{6,})」"             # Japanese corner brackets
    r"|『([^『』]{6,})』"
)

# How far from a marker a quotation may sit and still be its quotation.
NEAR = 200

# Paraphrase thresholds. Deliberately cautious: a false flag costs a glance,
# a missed one costs the paper.
WORD_NGRAM = 5
CJK_NGRAM = 10
CONTAINMENT_LIMIT = 0.18
RUN_LIMIT_WORDS = 8
RUN_LIMIT_CJK = 12

_CURLY = str.maketrans({"“": '"', "”": '"', "„": '"', "‟": '"', "＂": '"',
                        "‘": "'", "’": "'", "‚": "'", "‛": "'"})
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿＀-￯가-힯]")


def normalise_for_comparison(text: str) -> str:
    """Whitespace and quotation marks only.

    Nothing else is folded. An em dash turned into a hyphen, a changed word,
    a dropped clause — those are alterations of the quotation and must show up
    as ones.
    """
    text = unicodedata.normalize("NFC", text or "")
    text = text.translate(_CURLY)
    return re.sub(r"\s+", " ", text).strip()


def is_cjk(text: str) -> bool:
    letters = [c for c in text if not c.isspace()]
    if not letters:
        return False
    return sum(1 for c in letters if _CJK.match(c)) / len(letters) > 0.3


@dataclass
class Finding:
    kind: str
    severity: str  # "stop" | "look"
    message: str
    human_id: str | None = None
    detail: str | None = None


@dataclass
class Validation:
    cited: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    unused: list[dict[str, Any]] = field(default_factory=list)
    evidence_needed: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    rendered: str = ""
    stats: dict[str, Any] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return not self.unknown and not any(f.severity == "stop" for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cited": self.cited,
            "unknown": self.unknown,
            "unused": self.unused,
            "evidence_needed": self.evidence_needed,
            "findings": [asdict(f) for f in self.findings],
            "rendered": self.rendered,
            "stats": self.stats,
            "clean": self.clean,
        }


# -- pieces ---------------------------------------------------------------


def markers(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(1), m.start(), m.end()) for m in CITE_RE.finditer(text)]


def quoted_spans(text: str) -> list[tuple[str, int, int]]:
    """Every quoted stretch, longest first where two overlap."""
    spans: list[tuple[str, int, int]] = []
    for match in QUOTE_SPAN_RE.finditer(text):
        body = next(g for g in match.groups() if g is not None)
        spans.append((body, match.start(), match.end()))
    spans.sort(key=lambda s: (s[1], -(s[2] - s[1])))
    kept: list[tuple[str, int, int]] = []
    for span in spans:
        if any(k[1] <= span[1] and span[2] <= k[2] for k in kept):
            continue  # nested inside one already taken
        kept.append(span)
    return kept


def _ngrams(text: str) -> tuple[set[str], list[str]]:
    """N-grams and the token list they came from, sized to the script."""
    if is_cjk(text):
        tokens = [c for c in re.sub(r"\s+", "", text)]
        n = CJK_NGRAM
    else:
        tokens = re.findall(r"\w+", text.lower())
        n = WORD_NGRAM
    if len(tokens) < n:
        return set(), tokens
    joiner = "" if is_cjk(text) else " "
    return {joiner.join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}, tokens


def longest_shared_run(source: str, draft: str) -> tuple[int, str]:
    """The longest stretch of the original that survives verbatim in the draft."""
    if is_cjk(source):
        a = re.sub(r"\s+", "", source)
        b = re.sub(r"\s+", "", draft)
    else:
        a = re.findall(r"\w+", source.lower())
        b = re.findall(r"\w+", draft.lower())
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)
    )
    piece = a[match.a : match.a + match.size]
    return match.size, (piece if isinstance(piece, str) else " ".join(piece))


def align(source: str, span: str) -> str:
    """The stretch of the source the draft's quotation was trying to be.

    A draft usually quotes part of a passage. Diffing a seven-word quotation
    against a sixty-word source reports fifty words "dropped", which is true
    and useless; diffing it against the stretch it aligns to shows the actual
    alteration.
    """
    a = source.split()
    b = span.split()
    if len(a) <= len(b):
        return source
    match = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(
        0, len(a), 0, len(b)
    )
    start = max(0, match.a - match.b)
    return " ".join(a[start : start + len(b)])


def quote_diff(expected: str, found: str) -> str:
    """A compact account of how the quotation was altered."""
    a = expected.split()
    b = found.split()
    lines: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            lines.append(f"  “{' '.join(a[i1:i2])}” became “{' '.join(b[j1:j2])}”")
        elif tag == "delete":
            lines.append(f"  dropped: “{' '.join(a[i1:i2])}”")
        elif tag == "insert":
            lines.append(f"  added: “{' '.join(b[j1:j2])}”")
    return "\n".join(lines[:6]) or "  (differs only in spacing or quotation marks)"


# -- the check ------------------------------------------------------------


def evidence_for(
    conn: sqlite3.Connection, project_id: str, section_id: str | None
) -> dict[str, dict[str, Any]]:
    """What a draft was allowed to use.

    With a section, the cards assigned to it and what each was to do. Without
    one — a draft of the whole paper — every card in the project, with no mode
    fixed, because the researcher did not fix one.
    """
    if section_id:
        return {c["human_id"]: c for c in section_evidence(conn, section_id)}
    cards: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
        CARD_SELECT
        + " WHERE c.project_id = ? AND c.status = 'active' AND c.kind != 'image' "
        "AND c.origin != 'group_label'",
        (project_id,),
    ):
        card = dict(row)
        card["citation"] = citation_of(card)
        card["citation_mode"] = None
        card["argument_role"] = None
        card["user_instruction"] = None
        cards[card["human_id"]] = card
    return cards


def validate(
    conn: sqlite3.Connection,
    project_id: str,
    section_id: str | None,
    draft: str,
) -> Validation:
    evidence = evidence_for(conn, project_id, section_id)
    scoped = bool(section_id)
    result = Validation()

    found = markers(draft)
    result.cited = sorted({human_id for human_id, _s, _e in found})
    result.unknown = sorted({h for h in result.cited if h not in evidence})
    for human_id in result.unknown:
        result.findings.append(
            Finding(
                kind="unknown_citation",
                severity="stop",
                human_id=human_id,
                message=(
                    f"{human_id} is cited but was not in this section's evidence. "
                    f"Either the model invented it, or it belongs to another "
                    f"section."
                    if scoped
                    else f"{human_id} is cited but is not one of your cards. "
                    f"Nothing with that number exists in this project — the "
                    f"model made it up."
                ),
            )
        )

    result.evidence_needed = [
        m.group(1).strip() or "(unspecified)" for m in EVIDENCE_NEEDED_RE.finditer(draft)
    ]

    spans = quoted_spans(draft)
    for human_id in result.cited:
        card = evidence.get(human_id)
        if card is None:
            continue
        if card["kind"] == "idea":
            _check_idea(result, card, draft, found)
        elif card["citation_mode"] == "direct_quote":
            _check_direct_quote(result, card, draft, found, spans)
        elif card["citation_mode"] == "paraphrase":
            _check_paraphrase(result, card, draft, spans)
        elif card["citation_mode"] == "reference_only":
            _check_reference_only(result, card, draft, spans)
        else:
            # No mode was fixed, so the draft's own choice decides which check
            # applies: quoted text must be exact, unquoted text must not track
            # the original.
            if _spans_near(spans, _near_markers(card["human_id"], found)):
                _check_direct_quote(result, card, draft, found, spans)
            else:
                _check_paraphrase(result, card, draft, spans)

    result.unused = [
        {
            "human_id": card["human_id"],
            "kind": card["kind"],
            "citation_mode": card["citation_mode"] or "not fixed",
            "argument_role": card["argument_role"] or "not fixed",
            "text": card["text"][:200],
        }
        for human_id, card in evidence.items()
        if human_id not in result.cited
    ]

    result.rendered = render_for_reading(draft, evidence)
    words = len(re.findall(r"\w+", draft))
    result.stats = {
        "scope": "section" if scoped else "project",
        "words": words,
        "chars": len(draft),
        "citations": len(found),
        "cards_cited": len(result.cited),
        "cards_available": len(evidence),
        "evidence_needed": len(result.evidence_needed),
    }
    return result


def _near_markers(
    human_id: str, found: list[tuple[str, int, int]]
) -> list[tuple[int, int]]:
    return [(s, e) for h, s, e in found if h == human_id]


def _spans_near(
    spans: list[tuple[str, int, int]], positions: list[tuple[int, int]]
) -> list[str]:
    close = []
    for body, start, end in spans:
        for marker_start, marker_end in positions:
            if abs(marker_start - end) <= NEAR or abs(start - marker_end) <= NEAR:
                close.append(body)
                break
    return close


def _check_direct_quote(
    result: Validation,
    card: dict[str, Any],
    draft: str,
    found: list[tuple[str, int, int]],
    spans: list[tuple[str, int, int]],
) -> None:
    # Two forms count as the source's words: what the prompt handed over (the
    # cleaned passage) and what the page literally shows (the raw extraction,
    # hyphenation and all). A model can only reproduce the first, and a
    # researcher typing from the page produces the second.
    forms = [
        normalise_for_comparison(card["text"]),
        normalise_for_comparison(card["text_raw"] or card["text"]),
    ]
    original = forms[0]
    candidates = _spans_near(spans, _near_markers(card["human_id"], found))
    if not candidates:
        result.findings.append(
            Finding(
                kind="quotation_missing",
                severity="look",
                human_id=card["human_id"],
                message=(
                    f"{card['human_id']} was assigned as a direct quotation, but "
                    f"no quoted text sits near where it is cited."
                ),
            )
        )
        return

    for candidate in candidates:
        quoted = normalise_for_comparison(candidate)
        if any(quoted in form or form in quoted for form in forms):
            return  # an exact quotation, whole or in part

    # A passage that itself contains quotation marks cannot be extracted as one
    # span, so check the draft as a whole: if the source's words are in it
    # unaltered, the quotation is intact however the marks nest.
    whole = normalise_for_comparison(draft)
    if any(form in whole for form in forms):
        return

    closest = normalise_for_comparison(
        max(
            candidates,
            key=lambda c: difflib.SequenceMatcher(
                None, normalise_for_comparison(c), original
            ).ratio(),
        )
    )
    aligned = align(original, closest)
    result.findings.append(
        Finding(
            kind="quotation_altered",
            severity="stop",
            human_id=card["human_id"],
            message=(
                f"The quotation attributed to {card['human_id']} is not what the "
                f"source says."
            ),
            detail=(
                f"source: “{aligned}”\n"
                f"draft:  “{closest}”\n"
                f"{quote_diff(aligned, closest)}"
            ),
        )
    )


def _check_paraphrase(
    result: Validation,
    card: dict[str, Any],
    draft: str,
    spans: list[tuple[str, int, int]],
) -> None:
    """The check the earlier plan had no equivalent of.

    A paraphrase that tracks its original is plagiarism wearing no quotation
    marks, and nothing in the sentence looks wrong.

    Compared against the cleaned passage, because that is the form the prompt
    handed over and therefore the form a draft can echo.
    """
    original = card["text"]
    card_ngrams, _tokens = _ngrams(original)
    draft_ngrams, _ = _ngrams(draft if not is_cjk(original) else draft)
    if card_ngrams:
        shared = card_ngrams & draft_ngrams
        containment = len(shared) / len(card_ngrams)
    else:
        containment = 0.0
    size, run = longest_shared_run(original, draft)
    limit = RUN_LIMIT_CJK if is_cjk(original) else RUN_LIMIT_WORDS

    quoted = any(
        normalise_for_comparison(body) in normalise_for_comparison(original)
        for body, _s, _e in spans
    )
    if quoted:
        result.findings.append(
            Finding(
                kind="paraphrase_quoted",
                severity="look",
                human_id=card["human_id"],
                message=(
                    f"{card['human_id']} was assigned as a paraphrase but appears "
                    f"quoted. Either quote it deliberately, or restate it."
                ),
            )
        )
        return

    if containment >= CONTAINMENT_LIMIT or size >= limit:
        result.findings.append(
            Finding(
                kind="paraphrase_too_close",
                severity="stop",
                human_id=card["human_id"],
                message=(
                    f"The passage citing {card['human_id']} follows the original's "
                    f"wording too closely for a paraphrase. There are no quotation "
                    f"marks around it, so nothing marks it as the source's words."
                ),
                detail=(
                    f"{int(containment * 100)}% of the original's phrasing survives; "
                    f"longest unchanged stretch: “{run}”\n"
                    f"source: “{original}”"
                ),
            )
        )


def _check_reference_only(
    result: Validation,
    card: dict[str, Any],
    draft: str,
    spans: list[tuple[str, int, int]],
) -> None:
    original = normalise_for_comparison(card["text"])
    for body, _s, _e in spans:
        if normalise_for_comparison(body) in original:
            result.findings.append(
                Finding(
                    kind="reference_only_quoted",
                    severity="look",
                    human_id=card["human_id"],
                    message=(
                        f"{card['human_id']} was assigned as a reference only, but "
                        f"the draft quotes it."
                    ),
                )
            )
            return


def _check_idea(
    result: Validation,
    card: dict[str, Any],
    draft: str,
    found: list[tuple[str, int, int]],
) -> None:
    """An idea card is the researcher's own note, not a source.

    If the draft treats it as one, that is a fabricated citation with a real
    card behind it — harder to spot than an invented author.
    """
    for _start, end in _near_markers(card["human_id"], found):
        after = draft[end : end + 60]
        before = draft[max(0, end - 160) : end]
        if re.search(r"\b(argues?|writes?|claims?|states?|according to|notes?)\b",
                     before, re.I) and re.search(r"\(\s*\d{4}", before):
            result.findings.append(
                Finding(
                    kind="idea_cited_as_source",
                    severity="stop",
                    human_id=card["human_id"],
                    message=(
                        f"{card['human_id']} is your own note, but the draft "
                        f"appears to attribute it to a source."
                    ),
                    detail=f"…{before[-120:]}{after[:40]}…",
                )
            )
            return


# -- reading and exporting ------------------------------------------------


def render_for_reading(draft: str, evidence: dict[str, Any]) -> str:
    """Markers become readable citations; the stored draft keeps its markers."""

    def replace(match: re.Match[str]) -> str:
        human_id = match.group(1)
        card = evidence.get(human_id)
        if card is None:
            return f"[[UNKNOWN: {human_id}]]"
        if card["kind"] == "idea":
            return f"(my own note, {human_id})"
        citation = card.get("citation") or citation_of(card)
        return f"({citation})" if citation else f"({human_id})"

    return CITE_RE.sub(replace, draft)


def to_markdown(
    conn: sqlite3.Connection,
    project_id: str,
    section_id: str | None,
    draft: str,
) -> str:
    """Markdown with citekeys, not with pre-rendered author-year strings.

    A draft whose citations are plain text cannot go into pandoc or Zotero's
    word processor plugin without every one being redone by hand.
    """
    evidence = evidence_for(conn, project_id, section_id)
    keys = citekeys(conn, project_id)

    def replace(match: re.Match[str]) -> str:
        human_id = match.group(1)
        card = evidence.get(human_id)
        if card is None:
            return f"<!-- unknown citation {human_id} -->"
        if card["kind"] == "idea":
            return f"<!-- {human_id}: my own note, not a source -->"
        citekey = keys.get(card["source_id"])
        if not citekey:
            return f"<!-- {human_id}: no source -->"
        locator = ""
        if card["locator_type"] == "page" and not card["locator_estimated"]:
            locator = f"p. {card['locator_value']}"
        elif card["locator_type"] == "chapter":
            locator = f"ch. {card['locator_value']}"
        return cite_marker(citekey, locator)

    return CITE_RE.sub(replace, draft)


# -- storing drafts -------------------------------------------------------


def save_draft(
    conn: sqlite3.Connection,
    project_id: str,
    section_id: str | None,
    content: str,
    *,
    prompt_export_id: str | None = None,
    validation: Validation | None = None,
) -> dict[str, Any]:
    """Append-only. A draft is never overwritten, only followed."""
    # NULL never equals NULL in SQL, so drafts of the whole paper need their
    # own branch or every one of them would be version 1.
    row = (
        conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM draft "
            "WHERE project_id = ? AND section_id IS NULL",
            (project_id,),
        )
        if section_id is None
        else conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM draft WHERE section_id = ?",
            (section_id,),
        )
    ).fetchone()
    draft_id = insert(
        conn,
        "draft",
        {
            "project_id": project_id,
            "section_id": section_id,
            "prompt_export_id": prompt_export_id,
            "version": row["v"],
            "content": content,
            "validation_json": json.dumps(validation.as_dict(), ensure_ascii=False)
            if validation
            else None,
            "created_at": now_iso(),
        },
    )
    return dict(conn.execute("SELECT * FROM draft WHERE id = ?", (draft_id,)).fetchone())
