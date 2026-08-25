"""Highlights a page break cut in half."""

from __future__ import annotations

import copy

import pytest

from tests.conftest import FakeZotero, load_fixture
from zkj.continuations import attach, is_continuation, join, label_of, quotation_of
from zkj.importer import run_import
from zkj.store import connect

SPLIT = [
    {
        "data": {
            "key": "SPLITA", "itemType": "annotation", "parentItem": "ATT1",
            "annotationType": "highlight",
            "annotationText": "What conditions opened windows for bipartisan "
            "action, and what forces may ultimately close these",
            "annotationComment": "", "annotationPageLabel": "1",
            "annotationSortIndex": "00000|002825|00623",
            "annotationPosition": {"pageIndex": 0},
        }
    },
    {
        "data": {
            "key": "SPLITB", "itemType": "annotation", "parentItem": "ATT1",
            "annotationType": "highlight",
            "annotationText": "windows? Finally, what lessons from this earlier "
            "moment can help actors seeking to navigate?",
            "annotationComment": "", "annotationPageLabel": "2",
            "annotationSortIndex": "00001|000000|00034",
            "annotationPosition": {"pageIndex": 1},
        }
    },
]


def library_with_split():
    data = copy.deepcopy(load_fixture())
    data["annotations"] += copy.deepcopy(SPLIT)
    return data


@pytest.fixture
def project(tmp_path):
    fake = FakeZotero(library_with_split())
    conn = connect(tmp_path / "c.sqlite3")
    client = fake.client()
    project_id, stats = run_import(conn, client, "p", "ROOT")
    return fake, conn, client, project_id, stats


def card(conn, origin_key):
    return dict(
        conn.execute("SELECT * FROM card WHERE origin_key = ?", (origin_key,)).fetchone()
    )


# -- the rule -------------------------------------------------------------


def test_a_sentence_running_across_a_page_break_is_recognised():
    a, b = ({k[10:].lower() if k.startswith("annotation") else k: v
             for k, v in s["data"].items()} for s in SPLIT)
    first = {"text": SPLIT[0]["data"]["annotationText"], "comment": "",
             "sort_index": SPLIT[0]["data"]["annotationSortIndex"]}
    second = {"text": SPLIT[1]["data"]["annotationText"], "comment": "",
              "sort_index": SPLIT[1]["data"]["annotationSortIndex"]}
    assert is_continuation(first, second)
    assert a and b


def test_a_finished_sentence_is_not_continued():
    first = {"text": "Regulatory capacity lags behind deployment.", "sort_index": "00000|1|1"}
    second = {"text": "windows? Finally", "sort_index": "00001|0|0"}
    assert not is_continuation(first, second)


def test_a_fragment_that_starts_a_sentence_is_not_a_continuation():
    first = {"text": "and what forces may close these", "sort_index": "00000|1|1"}
    second = {"text": "Finally, what lessons remain", "sort_index": "00001|0|0"}
    assert not is_continuation(first, second)


def test_pages_far_apart_are_not_continued():
    first = {"text": "and what forces may close these", "sort_index": "00000|1|1"}
    second = {"text": "windows? Finally", "sort_index": "00009|0|0"}
    assert not is_continuation(first, second)


def test_a_comment_on_either_half_means_the_researcher_kept_them_apart():
    first = {"text": "and what forces may close these", "comment": "my note",
             "sort_index": "00000|1|1"}
    second = {"text": "windows? Finally", "comment": "", "sort_index": "00001|0|0"}
    assert not is_continuation(first, second)


def test_japanese_halves_close_up_with_no_space():
    assert join(["監督を個人の能力として", "扱ってきたことが問題である。"]) == (
        "監督を個人の能力として扱ってきたことが問題である。"
    )
    assert join(["close these", "windows?"]) == "close these windows?"


# -- through the import ---------------------------------------------------


def test_the_import_links_the_halves_and_counts_them(project):
    _fake, conn, _client, _pid, stats = project
    head = card(conn, "annotation:SPLITA:quote")
    tail = card(conn, "annotation:SPLITB:quote")
    assert tail["continues_card_id"] == head["id"]
    assert head["continues_card_id"] is None
    assert stats.joined_highlights == 1


def test_both_halves_keep_their_own_locator_and_text(project):
    """Nothing is merged away: each card is still the annotation it came from."""
    _fake, conn, _client, _pid, _stats = project
    head, tail = card(conn, "annotation:SPLITA:quote"), card(conn, "annotation:SPLITB:quote")
    assert head["locator_value"] == "1"
    assert tail["locator_value"] == "2"
    assert head["text"].endswith("close these")
    assert tail["text"].startswith("windows?")


def test_the_joined_quotation_is_whole(project):
    _fake, conn, _client, _pid, _stats = project
    head = card(conn, "annotation:SPLITA:quote")
    tail = card(conn, "annotation:SPLITB:quote")
    cards = [head, tail]
    attach(conn, cards)
    assert "close these windows? Finally" in quotation_of(cards[0])
    assert label_of(cards[0]) == f"{head['human_id']} + {tail['human_id']}"
    assert cards[1]["is_continuation"] is True


def test_a_link_is_dropped_when_the_highlight_is_edited(project):
    """Extend the first half to a full sentence in Zotero and they are two
    passages again."""
    _fake, conn, client, _pid, _stats = project
    data = library_with_split()
    for annotation in data["annotations"]:
        if annotation["data"]["key"] == "SPLITA":
            annotation["data"]["annotationText"] += " windows."
    other = FakeZotero(data)
    with other.client() as c:
        run_import(conn, c, "p", "ROOT")
    assert card(conn, "annotation:SPLITB:quote")["continues_card_id"] is None


# -- what the researcher and the model actually see -----------------------


def test_the_prompt_offers_the_whole_passage_once(project):
    from zkj.prompts import build

    _fake, conn, _client, pid, _stats = project
    p = dict(conn.execute("SELECT * FROM project WHERE id = ?", (pid,)).fetchone())
    content = build(conn, p, "paper").content

    head = card(conn, "annotation:SPLITA:quote")
    tail = card(conn, "annotation:SPLITB:quote")
    assert f"[{head['human_id']} + {tail['human_id']}]" in content
    assert "close these windows? Finally" in content
    assert "split across a page break" in content
    # the tail is not offered on its own, so it cannot be quoted on its own
    assert f"[{tail['human_id']}] quote" not in content


def test_the_export_writes_the_whole_passage(project):
    from zkj.export import paper_markdown

    _fake, conn, _client, pid, _stats = project
    markdown = paper_markdown(conn, pid)
    assert "close these windows? Finally" in markdown
    assert "one passage, split across a page break" in markdown


def test_quoting_the_joined_passage_validates(project):
    """It is what the prompt handed over, so it has to be accepted."""
    from zkj.validate import validate

    _fake, conn, _client, pid, _stats = project
    head = card(conn, "annotation:SPLITA:quote")
    joined = (
        "What conditions opened windows for bipartisan action, and what forces "
        "may ultimately close these windows? Finally, what lessons from this "
        "earlier moment can help actors seeking to navigate?"
    )
    draft = f'They ask: "{joined}" [[CITE:{head["human_id"]}]].'
    result = validate(conn, pid, None, draft)
    assert not [f for f in result.findings if f.kind == "quotation_altered"]


def test_the_cards_screen_shows_the_whole_passage(project):
    from zkj.cards import CardFilters, list_cards

    _fake, conn, _client, pid, _stats = project
    page = list_cards(conn, pid, CardFilters(search="close these"))
    joined = next(c for c in page.cards if c["origin_key"] == "annotation:SPLITA:quote")
    assert "close these windows?" in joined["joined_text"]
    assert len(joined["joined_ids"]) == 2


def test_a_joined_passage_cites_both_pages(project):
    """The quotation is on both pages; citing only the first is wrong for half
    of what is being quoted."""
    from zkj.cards import CardFilters, citation_of, list_cards

    _fake, conn, _client, pid, _stats = project
    page = list_cards(conn, pid, CardFilters(search="close these"))
    joined = next(c for c in page.cards if c["origin_key"] == "annotation:SPLITA:quote")
    assert joined["joined_locator"] == "pp. 1–2"
    assert citation_of(joined).endswith("pp. 1–2")


def test_two_halves_on_the_same_page_still_cite_one_page():
    from zkj.continuations import joined_locator

    same = [
        {"locator_type": "page", "locator_value": "7"},
        {"locator_type": "page", "locator_value": "7"},
    ]
    assert joined_locator(same) == "p. 7"


def test_a_span_of_roman_numerals_works():
    from zkj.continuations import joined_locator

    assert joined_locator([
        {"locator_type": "page", "locator_value": "xiv"},
        {"locator_type": "page", "locator_value": "xv"},
    ]) == "pp. xiv–xv"


def test_a_span_is_not_invented_where_the_locators_are_not_pages():
    from zkj.continuations import joined_locator

    assert joined_locator([
        {"locator_type": "chapter", "locator_value": "Regulatory design"},
        {"locator_type": "chapter", "locator_value": "Regulatory design"},
    ]) is None
    assert joined_locator([
        {"locator_type": "page", "locator_value": "1"},
        {"locator_type": "none", "locator_value": ""},
    ]) is None


def test_an_estimated_span_says_so():
    from zkj.continuations import joined_locator

    assert joined_locator([
        {"locator_type": "page", "locator_value": "10", "locator_estimated": 1},
        {"locator_type": "page", "locator_value": "11", "locator_estimated": 1},
    ]) == "pp. 10–11 (est.)"


def test_the_markdown_export_spans_the_pages_too(project):
    from zkj.validate import to_markdown

    _fake, conn, _client, pid, _stats = project
    head = card(conn, "annotation:SPLITA:quote")
    markdown = to_markdown(conn, pid, None, f"x [[CITE:{head['human_id']}]]")
    assert "pp. 1–2]" in markdown
