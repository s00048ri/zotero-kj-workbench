"""Repairing extracted text without altering what the source said.

Everything here is conservative on purpose. A quotation is evidence: the
cleaned form exists so a passage reads properly on screen and matches when a
draft quotes it back, and the untouched original is always kept beside it.

The rule that matters most: **never NFKC**. NFKC rewrites full-width
characters, so ＡＩ becomes AI and 「 becomes a plain bracket, silently
altering every Japanese quotation in the library. NFC composes accents and
changes nothing else.
"""

from __future__ import annotations

import html
import re
import unicodedata

LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "ﬅ": "st",
    "ﬆ": "st",
}

SOFT_HYPHEN = "­"

# Scripts written without spaces between words. A line break inside one of
# these is a typesetting artefact and joining across it must not insert a
# space, or every Japanese quotation acquires holes.
_CJK = (
    r"　-〿"      # CJK punctuation, ideographic space
    r"぀-ヿ"      # kana
    r"㐀-䶿"      # CJK extension A
    r"一-鿿"      # CJK unified ideographs
    r"豈-﫿"      # compatibility ideographs
    r"＀-￯"      # full-width and half-width forms
    r"가-힯"      # hangul syllables
)
_CJK_CLASS = f"[{_CJK}]"
_NOT_CJK_LETTER = f"[^\\W\\d_{_CJK}]"

_HYPHEN_BREAK = re.compile(rf"({_NOT_CJK_LETTER})-[ \t]*\n[ \t]*({_NOT_CJK_LETTER})")
_CJK_BREAK = re.compile(rf"({_CJK_CLASS})[ \t]*\n[ \t]*({_CJK_CLASS})")
_ANY_BREAK = re.compile(r"[ \t]*\n[ \t]*")
_RUN_OF_SPACES = re.compile(r"[ \t ]{2,}")

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|blockquote|tr|pre)\s*>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_BLANK_RUN = re.compile(r"\n{3,}")


def normalise_quote(s: str | None) -> str:
    """Make an extracted passage readable without changing what it says.

    Repairs end-of-line hyphenation, expands ligatures, drops soft hyphens and
    unwraps the line breaks a PDF extractor leaves behind. Full-width
    characters, typographic quotation marks and em dashes are left exactly as
    they were: normalising those is the job of the draft validator, which
    compares against the raw text and must not have been pre-empted here.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    for lig, repl in LIGATURES.items():
        s = s.replace(lig, repl)
    s = s.replace(SOFT_HYPHEN, "")
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = _HYPHEN_BREAK.sub(r"\1\2", s)
    s = _CJK_BREAK.sub(r"\1\2", s)
    s = _ANY_BREAK.sub(" ", s)
    s = _RUN_OF_SPACES.sub(" ", s)
    return s.strip()


def html_to_text(s: str | None) -> str:
    """Flatten a Zotero note to text, keeping paragraph breaks."""
    if not s:
        return ""
    s = _BR.sub("\n", s)
    s = _BLOCK_END.sub("\n\n", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = unicodedata.normalize("NFC", s)
    s = _BLANK_RUN.sub("\n\n", s)
    return "\n".join(line.rstrip() for line in s.split("\n")).strip()


def collapse(s: str | None) -> str:
    """One line, single-spaced. For previews and log lines only."""
    return re.sub(r"\s+", " ", s or "").strip()


def escape_html(s: str | None) -> str:
    """For building note HTML. User text is data, never markup."""
    return html.escape(s or "", quote=False).replace("\n", "<br/>")
