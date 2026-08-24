"""Where a passage is — and never a page number that was invented.

A locator is a claim about a source that a reader may go and check, so each
one records not just a value but where the value came from. A displayed page
label and a zero-based page index are different claims about the same
annotation, and a page derived from a character offset through an EPUB is not
a claim at all — it is a hint, and it is marked as one.

PDF
    ``annotationPageLabel`` → ``position.pageIndex + 1`` → nothing.

EPUB
    EPUBs have no pages. ``annotationPageLabel`` is populated only when the
    book ships an EPUB 3 page-list, in which case it is a real page. Otherwise
    the CFI is resolved to a spine document and the **chapter** becomes the
    locator: CSL supports a chapter locator, so it is properly citable, and it
    is honest. Only if the researcher explicitly turns the feature on is an
    estimated print page attached, always flagged.
"""

from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree as ET

from .text import html_to_text

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = {"o": "http://www.idpf.org/2007/opf"}


@dataclass(frozen=True)
class Locator:
    type: str  # page | chapter | cfi | none
    value: str = ""
    source: str = "none"  # page_label | page_index | epub_page_list | epub_spine | cfi | none
    estimated: bool = False
    detail: dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        if self.type == "none":
            return ""
        if self.type == "page":
            return f"p. {self.value}" + (" (est.)" if self.estimated else "")
        if self.type == "chapter":
            return f"ch. “{self.value}”" if self.value else ""
        if self.type == "cfi":
            return "location unknown"
        return self.value

    @property
    def estimated_page(self) -> int | None:
        page = self.detail.get("estimated_page")
        return page if isinstance(page, int) else None


NO_LOCATOR = Locator("none")

_SPINE_STEP = re.compile(r"/(\d+)")


def cfi_spine_index(cfi: str | None) -> int | None:
    """The spine position an EPUB CFI points into, or None.

    ``epubcfi(/6/14[chap05ref]!/4/10/2/1:3)`` — the part before ``!`` walks the
    package document. CFI element steps are even and doubled, so the last step
    of the package path gives ``index = step / 2 - 1``.
    """
    if not cfi:
        return None
    m = re.search(r"epubcfi\((.*)\)", cfi)
    body = m.group(1) if m else cfi
    steps = _SPINE_STEP.findall(body.split("!")[0])
    if not steps:
        return None
    try:
        last = int(steps[-1])
    except ValueError:
        return None
    if last < 2 or last % 2 != 0:
        return None
    return last // 2 - 1


class EpubIndex:
    """The spine of one EPUB: which chapter a position falls in, and how far in.

    Built from the file on disk, which the local API hands over as a
    ``file://`` URL. Nothing here is written back.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self.spine: list[tuple[str, str]] = []  # (idref, href)
        self.titles: dict[int, str] = {}
        self.char_counts: list[int] = []
        self.total_chars = 1
        self.has_page_list = False
        self._load()

    # -- construction ------------------------------------------------------

    def _load(self) -> None:
        with zipfile.ZipFile(self.path) as z:
            opf_path = self._opf_path(z)
            opf = ET.fromstring(z.read(opf_path))
            opf_dir = os.path.dirname(opf_path)

            manifest: dict[str, tuple[str, str]] = {}
            for item in opf.findall(".//o:manifest/o:item", OPF_NS):
                manifest[item.attrib.get("id", "")] = (
                    self._join(opf_dir, item.attrib.get("href", "")),
                    item.attrib.get("properties", ""),
                )

            for itemref in opf.findall(".//o:spine/o:itemref", OPF_NS):
                idref = itemref.attrib.get("idref", "")
                href = manifest.get(idref, ("", ""))[0]
                self.spine.append((idref, href))

            self.has_page_list = self._detect_page_list(z, manifest)

            for idx, (_idref, href) in enumerate(self.spine):
                try:
                    doc = z.read(href).decode("utf-8", "ignore")
                except KeyError:
                    self.char_counts.append(0)
                    continue
                title = self._title_of(doc)
                if title:
                    self.titles[idx] = title
                self.char_counts.append(len(html_to_text(doc)))

        self.total_chars = sum(self.char_counts) or 1

    @staticmethod
    def _opf_path(z: zipfile.ZipFile) -> str:
        root = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = root.find(".//c:rootfile", CONTAINER_NS)
        if rootfile is None:
            raise ValueError("container.xml names no package document")
        return rootfile.attrib["full-path"]

    @staticmethod
    def _join(directory: str, href: str) -> str:
        href = href.split("#")[0]
        return os.path.normpath(os.path.join(directory, href)).replace("\\", "/")

    @staticmethod
    def _detect_page_list(z: zipfile.ZipFile, manifest: dict) -> bool:
        """True when the book carries printed-page anchors of its own."""
        for href, properties in manifest.values():
            if "nav" not in properties:
                continue
            try:
                nav = z.read(href).decode("utf-8", "ignore")
            except KeyError:
                continue
            if "page-list" in nav:
                return True
        return False

    @staticmethod
    def _title_of(doc: str) -> str:
        m = re.search(r"<title[^>]*>(.*?)</title>", doc, re.S | re.I)
        if m and html_to_text(m.group(1)):
            return html_to_text(m.group(1))[:120]
        m = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", doc, re.S | re.I)
        if m and html_to_text(m.group(1)):
            return html_to_text(m.group(1))[:120]
        return ""

    # -- use ---------------------------------------------------------------

    def locate(self, spine_index: int) -> tuple[str, float]:
        """(chapter label, fraction of the book before that chapter starts)."""
        if not self.spine:
            return "", 0.0
        spine_index = max(0, min(spine_index, len(self.spine) - 1))
        before = sum(self.char_counts[:spine_index])
        label = (
            self.titles.get(spine_index)
            or self.spine[spine_index][0]
            or f"section {spine_index + 1}"
        )
        return label, before / self.total_chars


def resolve_locator(
    annotation: Any,
    attachment: Any,
    *,
    epub: EpubIndex | None = None,
    page_count: int | None = None,
) -> Locator:
    """Turn one annotation's position into a citable locator, or into nothing."""
    page_label = (annotation.page_label or "").strip()
    position = annotation.position or {}

    if not attachment.is_epub:
        if page_label:
            return Locator("page", page_label, "page_label")
        index = position.get("pageIndex")
        if isinstance(index, int):
            return Locator(
                "page", str(index + 1), "page_index", detail={"page_index": index}
            )
        return NO_LOCATOR

    # EPUB
    if page_label:
        # Present only when the book ships an EPUB 3 page-list, so this is a
        # real printed page and not a reader's own pagination.
        return Locator("page", page_label, "epub_page_list")

    cfi = position.get("value") or ""
    spine_index = cfi_spine_index(cfi)
    if epub is not None and spine_index is not None:
        label, fraction = epub.locate(spine_index)
        detail: dict[str, Any] = {
            "cfi": cfi,
            "spine_index": spine_index,
            "fraction": round(fraction, 4),
        }
        if page_count:
            # A hint for finding the passage again. Google Books page counts
            # are frequently the ebook's own pagination, so this is never a
            # citation and the flag says so.
            detail["estimated_page"] = max(1, min(page_count, round(fraction * page_count) or 1))
            detail["page_count"] = page_count
            detail["page_count_source"] = "google_books"
            return Locator("chapter", label, "epub_spine", estimated=True, detail=detail)
        return Locator("chapter", label, "epub_spine", detail=detail)

    if cfi:
        # The position is known to Zotero but means nothing to a reader.
        return Locator("cfi", cfi, "cfi", detail={"cfi": cfi})
    return NO_LOCATOR
