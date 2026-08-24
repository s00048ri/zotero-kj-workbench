"""Locators are claims about a source. A wrong one is worse than none."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from zkj.locators import EpubIndex, Locator, cfi_spine_index, resolve_locator
from zkj.zotero.models import Annotation, Attachment

PDF = Attachment(key="ATT1", itemType="attachment", contentType="application/pdf")
EPUB = Attachment(key="ATT2", itemType="attachment", contentType="application/epub+zip")


def ann(**kw) -> Annotation:
    kw.setdefault("key", "ANN")
    kw.setdefault("itemType", "annotation")
    return Annotation(**kw)


# -- PDF ------------------------------------------------------------------


def test_pdf_prefers_the_displayed_page_label():
    loc = resolve_locator(ann(annotationPageLabel="xiv", annotationPosition={"pageIndex": 13}), PDF)
    assert (loc.type, loc.value, loc.source) == ("page", "xiv", "page_label")
    assert loc.estimated is False


def test_pdf_falls_back_to_the_index_and_says_so():
    """A zero-based index is a different claim from a printed label."""
    loc = resolve_locator(ann(annotationPosition={"pageIndex": 131}), PDF)
    assert (loc.type, loc.value, loc.source) == ("page", "132", "page_index")


def test_pdf_with_no_position_gets_no_locator():
    assert resolve_locator(ann(), PDF).type == "none"


# -- CFI parsing ----------------------------------------------------------


@pytest.mark.parametrize(
    "cfi,expected",
    [
        ("epubcfi(/6/14[chap05ref]!/4/10/2/1:3)", 6),
        ("epubcfi(/6/2!/4/2)", 0),
        ("/6/8!/4", 3),
        ("epubcfi(/6/7!/4)", None),  # odd step is not an itemref
        ("epubcfi(!/4/2)", None),
        ("", None),
        (None, None),
    ],
)
def test_cfi_spine_index(cfi, expected):
    assert cfi_spine_index(cfi) == expected


# -- EPUB -----------------------------------------------------------------


def make_epub(path: Path, *, page_list: bool = False) -> Path:
    chapters = [
        ("c1.xhtml", "<title>Front matter</title>", "short"),
        ("c2.xhtml", "<title>Regulatory design</title>", "middle " * 200),
        ("c3.xhtml", "", "<h1>Fiscal capacity</h1>" + "late " * 400),
    ]
    manifest = "".join(
        f'<item id="c{i}" href="{href}" media-type="application/xhtml+xml"/>'
        for i, (href, _t, _b) in enumerate(chapters, start=1)
    )
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(1, len(chapters) + 1))
    nav_body = (
        '<nav epub:type="page-list"><ol><li>1</li></ol></nav>' if page_list else "<nav/>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "META-INF/container.xml",
            '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        z.writestr(
            "OEBPS/content.opf",
            '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
            f'{manifest}<item id="nav" href="nav.xhtml" properties="nav" '
            'media-type="application/xhtml+xml"/>'
            f"</manifest><spine>{spine}</spine></package>",
        )
        z.writestr("OEBPS/nav.xhtml", f"<html><body>{nav_body}</body></html>")
        for href, title, body in chapters:
            z.writestr(f"OEBPS/{href}", f"<html><head>{title}</head><body><p>{body}</p></body></html>")
    return path


@pytest.fixture
def epub(tmp_path) -> EpubIndex:
    return EpubIndex(str(make_epub(tmp_path / "book.epub")))


def test_epub_spine_is_read_in_order_with_titles(epub):
    assert len(epub.spine) == 3
    assert epub.titles[1] == "Regulatory design"
    assert epub.titles[2] == "Fiscal capacity"  # falls back to the first heading


def test_epub_without_a_page_list_is_recognised(epub):
    assert epub.has_page_list is False


def test_epub_with_a_page_list_is_recognised(tmp_path):
    idx = EpubIndex(str(make_epub(tmp_path / "paged.epub", page_list=True)))
    assert idx.has_page_list is True


def test_a_position_becomes_a_chapter_never_a_page(epub):
    loc = resolve_locator(
        ann(annotationPosition={"value": "epubcfi(/6/6[c3]!/4/2/1:0)"}), EPUB, epub=epub
    )
    assert loc.type == "chapter"
    assert loc.value == "Fiscal capacity"
    assert loc.source == "epub_spine"
    assert "p." not in loc.render()


def test_a_chapter_locator_is_not_flagged_estimated(epub):
    """The spine position is exact. Only a derived page is a guess."""
    loc = resolve_locator(
        ann(annotationPosition={"value": "epubcfi(/6/4[c2]!/4)"}), EPUB, epub=epub
    )
    assert loc.estimated is False
    assert loc.detail["spine_index"] == 1
    assert 0.0 < loc.detail["fraction"] < 1.0


def test_an_estimated_page_is_opt_in_and_always_flagged(epub):
    loc = resolve_locator(
        ann(annotationPosition={"value": "epubcfi(/6/6[c3]!/4)"}),
        EPUB,
        epub=epub,
        page_count=300,
    )
    assert loc.type == "chapter"  # the citable claim is still the chapter
    assert loc.estimated is True
    assert 1 <= loc.estimated_page <= 300
    assert loc.detail["page_count_source"] == "google_books"


def test_an_epub_page_label_is_a_real_page(epub):
    loc = resolve_locator(ann(annotationPageLabel="132"), EPUB, epub=epub)
    assert (loc.type, loc.source, loc.estimated) == ("page", "epub_page_list", False)


def test_an_unresolvable_cfi_says_the_location_is_unknown():
    loc = resolve_locator(
        ann(annotationPosition={"value": "epubcfi(/6/14!/4/2)"}), EPUB, epub=None
    )
    assert loc.type == "cfi"
    assert loc.render() == "location unknown"


def test_rendering():
    assert Locator("page", "132", "page_label").render() == "p. 132"
    assert Locator("page", "132", "epub_spine", estimated=True).render() == "p. 132 (est.)"
    assert Locator("chapter", "Regulatory design").render() == "ch. “Regulatory design”"
    assert Locator("none").render() == ""
