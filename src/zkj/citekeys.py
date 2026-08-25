"""Citation keys, so a draft can go into pandoc or Zotero without being redone.

A draft whose citations are pre-rendered strings — “(Smith, 2025, p. 132)” —
is a dead end: every one has to be re-linked by hand before the paper can be
typeset or the bibliography regenerated. So the Markdown this app writes uses
Better BibTeX-style keys instead.

Where a name has no Latin letters at all, a transliterated key would be a
guess. The Zotero item key is used instead: unambiguous, and resolvable in the
library the citation came from.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

_NON_WORD = re.compile(r"[^a-z0-9]+")
_STOPWORDS = {"the", "a", "an", "of", "on", "in", "and", "for", "to"}


def _asciify(text: str) -> str:
    """Latin letters only, accents folded. Empty when there are none."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return _NON_WORD.sub("", folded.lower())


def _first_word(title: str | None) -> str:
    for word in re.split(r"\s+", (title or "").strip()):
        candidate = _asciify(word)
        if candidate and candidate not in _STOPWORDS:
            return candidate
    return ""


def base_key(source: dict[str, Any]) -> str:
    name = (source.get("creators_short") or "").split(" ")[0].split("&")[0].strip()
    stem = _asciify(name) or _first_word(source.get("title"))
    year = (source.get("year") or "").strip()
    if not stem:
        # Nothing Latin to build from — say which item, rather than invent a
        # romanisation the researcher's bibliography will not match.
        return f"zotero-{source.get('zotero_item_key', 'unknown')}"
    return f"{stem}{year}" if year else stem


def citekeys(conn: sqlite3.Connection, project_id: str) -> dict[str, str]:
    """One key per source in the project, made unique within it."""
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, zotero_item_key, creators_short, title, year FROM source "
            "WHERE project_id = ? ORDER BY creators_short, year, title",
            (project_id,),
        )
    ]
    used: dict[str, int] = {}
    keys: dict[str, str] = {}
    for row in rows:
        stem = base_key(row)
        count = used.get(stem, 0)
        used[stem] = count + 1
        # smith2025, smith2025a, smith2025b — the shape BibTeX users expect
        keys[row["id"]] = stem if count == 0 else f"{stem}{chr(ord('a') + count - 1)}"
    return keys


def cite_marker(citekey: str, locator: str = "") -> str:
    """``[@smith2025, p. 132]`` — a citation a typesetter can resolve."""
    return f"[@{citekey}, {locator}]" if locator else f"[@{citekey}]"
