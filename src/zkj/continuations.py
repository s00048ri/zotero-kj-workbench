"""Highlights that a page break cut in half.

Zotero stores an annotation per page, so a passage highlighted across a page
boundary arrives as two, split mid-sentence: "…what forces may ultimately close
these" and "windows?". Quoted separately, both are wrong — the first is not a
sentence and the second is not a quotation.

Detection is deliberately timid. Joining two passages that were never one is a
worse error than leaving a split one split, because the result reads as a
single quotation and nothing marks it as assembled. So: the same attachment,
adjacent in reading order, at most one page apart, the first ending mid-clause,
and the second beginning where a sentence cannot.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from .text import normalise_quote

# A highlight that ends on any of these has finished its sentence.
_ENDS_SENTENCE = re.compile(r"[.!?…。！？」』\"”'’)）\]]\s*$")
# A fragment beginning with any of these cannot be the start of a sentence.
_CANNOT_START = re.compile(r"^\s*[a-zà-öø-ÿ,;:?!)）\]。、」』…]")
_CJK_START = re.compile(r"^\s*[぀-ヿ㐀-䶿一-鿿]")


def _page(sort_index: str | None) -> int | None:
    """Zotero's sort index leads with the page, zero-padded."""
    if not sort_index:
        return None
    head = sort_index.split("|")[0]
    try:
        return int(head)
    except ValueError:
        return None


def is_continuation(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Does the second highlight finish the sentence the first began?"""
    a = (first.get("text") or "").strip()
    b = (second.get("text") or "").strip()
    if not a or not b:
        return False
    if _ENDS_SENTENCE.search(a):
        return False
    if not (_CANNOT_START.match(b) or _CJK_START.match(b) or a.endswith("-")):
        return False

    page_a, page_b = _page(first.get("sort_index")), _page(second.get("sort_index"))
    if page_a is not None and page_b is not None and page_b - page_a not in (0, 1):
        return False
    # A comment on either half is the researcher treating them as separate
    # thoughts; that is their call, not ours to overrule.
    return not (first.get("comment") or second.get("comment"))


def join(texts: list[str]) -> str:
    """The halves, made whole.

    Joined through the newline rule rather than with a bare space: a passage in
    Japanese must close up, one in English must not.
    """
    return normalise_quote("\n".join(t.strip() for t in texts if t and t.strip()))


def chain(conn: sqlite3.Connection, card_id: str) -> list[dict[str, Any]]:
    """Every card that continues this one, in order."""
    out: list[dict[str, Any]] = []
    current = card_id
    seen = {card_id}
    while True:
        row = conn.execute(
            "SELECT * FROM card WHERE continues_card_id = ?", (current,)
        ).fetchone()
        if row is None or row["id"] in seen:
            return out
        out.append(dict(row))
        seen.add(row["id"])
        current = row["id"]


def attach(conn: sqlite3.Connection, cards: list[dict[str, Any]]) -> None:
    """Give each card its joined text, and mark the halves that are not starts.

    Mutates in place: ``joined_text`` is the whole quotation, ``joined_ids``
    names the cards it was assembled from, and ``is_continuation`` marks a card
    that should not be offered on its own.
    """
    for card in cards:
        card["is_continuation"] = bool(card.get("continues_card_id"))
        rest = chain(conn, card["id"]) if not card["is_continuation"] else []
        if rest:
            card["joined_ids"] = [card["human_id"]] + [r["human_id"] for r in rest]
            card["joined_text"] = join(
                [card.get("text_raw") or card["text"]]
                + [r["text_raw"] or r["text"] for r in rest]
            )
        else:
            card["joined_ids"] = [card["human_id"]]
            card["joined_text"] = card["text"]


def quotation_of(card: dict[str, Any]) -> str:
    return card.get("joined_text") or card["text"]


def label_of(card: dict[str, Any]) -> str:
    """“KJ-0013” or “KJ-0013 + KJ-0014”, so an assembled quotation says so."""
    ids = card.get("joined_ids") or [card["human_id"]]
    return " + ".join(ids)
