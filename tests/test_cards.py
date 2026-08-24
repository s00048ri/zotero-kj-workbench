"""Filters, search and the counters on the reading surface."""

from __future__ import annotations

import pytest

from zkj.cards import CardFilters, citation_of, facets, list_cards, summary
from zkj.importer import run_import
from zkj.store import connect


@pytest.fixture
def project(tmp_path, client):
    conn = connect(tmp_path / "cards.sqlite3")
    project_id, _ = run_import(conn, client, "p", "ROOT")
    yield conn, project_id
    conn.close()


def texts(page) -> list[str]:
    return [c["human_id"] for c in page.cards]


def test_default_listing_is_in_reading_order(project):
    conn, pid = project
    page = list_cards(conn, pid)
    assert page.total == len(page.cards)
    paths = [c["prior_path"] for c in page.cards]
    assert paths == sorted(paths, key=lambda p: (p is None, p or ""))


def test_a_quote_carries_the_note_written_on_it(project):
    conn, pid = project
    page = list_cards(conn, pid, CardFilters(kind="quote"))
    with_note = [c for c in page.cards if c["linked_ideas"]]
    assert len(with_note) == 1
    assert with_note[0]["linked_ideas"][0]["origin"] == "annotation_comment"


def test_an_idea_card_points_back_at_its_quote(project):
    conn, pid = project
    page = list_cards(conn, pid, CardFilters(origin="annotation_comment"))
    assert page.cards[0]["parent"]["kind"] == "quote"


def test_has_comment_filter_cuts_both_ways(project):
    conn, pid = project
    yes = list_cards(conn, pid, CardFilters(kind="quote", has_comment=True))
    no = list_cards(conn, pid, CardFilters(kind="quote", has_comment=False))
    assert yes.total == 1
    assert no.total == 2
    assert set(texts(yes)).isdisjoint(texts(no))


def test_filters_narrow(project):
    conn, pid = project
    assert list_cards(conn, pid, CardFilters(kind="image")).total == 1
    assert list_cards(conn, pid, CardFilters(color="#a28ae5")).total == 1
    assert list_cards(conn, pid, CardFilters(locator_type="none")).total >= 1
    assert list_cards(
        conn, pid, CardFilters(prior_path="Agentic Governance/03 Capacity")
    ).total >= 1


def test_excluded_cards_are_hidden_unless_asked_for(project):
    conn, pid = project
    before = list_cards(conn, pid).total
    conn.execute("UPDATE card SET status = 'excluded' WHERE human_id = 'KJ-0001'")
    assert list_cards(conn, pid).total == before - 1
    assert list_cards(conn, pid, CardFilters(status=None)).total == before


def test_search_finds_english(project):
    conn, pid = project
    page = list_cards(conn, pid, CardFilters(search="oversight"))
    assert page.total >= 1
    assert all("oversight" in c["text"].lower() for c in page.cards)


def test_search_finds_a_short_japanese_query(project):
    """Two characters is below the trigram floor, and is a normal query in
    Japanese. It must still find the card."""
    conn, pid = project
    assert list_cards(conn, pid, CardFilters(search="監督")).total == 1
    assert list_cards(conn, pid, CardFilters(search="ガバナンス")).total == 1


def test_search_for_something_absent_finds_nothing(project):
    conn, pid = project
    assert list_cards(conn, pid, CardFilters(search="zzzz")).total == 0
    assert list_cards(conn, pid, CardFilters(search="猫")).total == 0


def test_a_quotation_mark_in_the_query_does_not_break_search(project):
    conn, pid = project
    assert list_cards(conn, pid, CardFilters(search='"oversight')).total >= 0


def test_paging(project):
    conn, pid = project
    first = list_cards(conn, pid, CardFilters(limit=2))
    second = list_cards(conn, pid, CardFilters(limit=2, offset=2))
    assert len(first.cards) == 2
    assert first.total == second.total
    assert set(texts(first)).isdisjoint(texts(second))


def test_the_counter_says_how_many_quotes_carry_your_note(project):
    conn, pid = project
    counts = summary(conn, pid)
    assert counts["quotes"] == 3
    assert counts["quotes_with_my_note"] == 1
    assert counts["ideas"] == 3
    assert counts["images"] == 1


def test_facets_only_offer_what_exists(project):
    conn, pid = project
    f = facets(conn, pid)
    assert {v["value"] for v in f["kinds"]} == {"quote", "idea", "image"}
    assert all(v["count"] > 0 for values in f.values() for v in values)
    assert f["groups"] == []  # nothing has been filed under _KJ yet


def test_citation_renders_author_year_and_locator(project):
    conn, pid = project
    page = list_cards(conn, pid, CardFilters(search="oversight becomes"))
    assert citation_of(page.cards[0]) == "Smith 2025, p. 132"


def test_a_source_without_a_date_cites_author_only(project):
    conn, pid = project
    conn.execute("UPDATE source SET year = NULL WHERE creators_short = 'Smith'")
    page = list_cards(conn, pid, CardFilters(search="oversight becomes"))
    assert citation_of(page.cards[0]) == "Smith, p. 132"


def test_an_item_naming_no_author_cites_its_title(project):
    """“Anon. 2026, p. 1” claims something. The title claims nothing."""
    conn, pid = project
    conn.execute("UPDATE source SET creators_short = NULL WHERE creators_short = 'Smith'")
    page = list_cards(conn, pid, CardFilters(search="oversight becomes"))
    assert citation_of(page.cards[0]) == "Human oversight of autonomous agents, 2025, p. 132"


def test_a_subtitle_is_dropped_from_a_title_citation():
    from zkj.cards import short_title

    assert short_title("Governance at a Crossroads: AI and the Future") == (
        "Governance at a Crossroads"
    )
    assert short_title(None) == ""
    assert short_title("A" * 60).endswith("…")
