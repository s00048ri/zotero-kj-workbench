"""The argument layer, the prompts it produces, and what comes back. §12.12–16"""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj.citekeys import base_key, citekeys
from zkj.compose import (
    add_question,
    add_section,
    assign_card,
    choose_question,
    section_evidence,
)
from zkj.export import paper_markdown
from zkj.groups import save_label
from zkj.importer import run_import
from zkj.prompts import available, build, estimate_tokens, store
from zkj.store import connect, now_iso
from zkj.validate import save_draft, to_markdown, validate


@pytest.fixture
def project(tmp_path):
    fake = FakeZotero()
    conn = connect(tmp_path / "c.sqlite3")
    with fake.client() as client:
        project_id, _ = run_import(conn, client, "p", "ROOT")
    project = dict(conn.execute("SELECT * FROM project").fetchone())
    yield conn, project
    conn.close()


def flat(text: str) -> str:
    """Line breaks in a prompt are typography, not meaning."""
    import re

    return re.sub(r"\s+", " ", text).lower()


def quote(conn, origin_key="annotation:ANN1:quote"):
    return dict(
        conn.execute("SELECT * FROM card WHERE origin_key = ?", (origin_key,)).fetchone()
    )


def grouped(conn, project_id, path="P/_KJ/Oversight"):
    conn.execute(
        "UPDATE card SET kj_path = ?, zotero_note_key = 'N', materialized_at = ? "
        "WHERE kind IN ('quote', 'idea') AND origin != 'group_label'",
        (path, now_iso()),
    )
    return path


# -- the argument layer ---------------------------------------------------


def test_one_question_at_a_time_is_the_paper_s(project):
    conn, p = project
    first = add_question(conn, p["id"], "Does public attention shape AI policy?")
    second = add_question(conn, p["id"], "Is oversight organisational?")
    choose_question(conn, p["id"], first["id"])
    choose_question(conn, p["id"], second["id"])
    chosen = [
        r["id"] for r in conn.execute(
            "SELECT id FROM research_question WHERE status = 'chosen'")
    ]
    assert chosen == [second["id"]]
    assert conn.execute(
        "SELECT research_question FROM project WHERE id = ?", (p["id"],)
    ).fetchone()[0] == "Is oversight organisational?"


def test_a_card_does_different_work_in_different_sections(project):
    """The role belongs to the pairing, not to the card."""
    conn, p = project
    card = quote(conn)
    one = add_section(conn, p["id"], "Institutional capacity")
    two = add_section(conn, p["id"], "The oversight frame")
    assign_card(conn, one["id"], card["id"], citation_mode="direct_quote",
                argument_role="evidence")
    assign_card(conn, two["id"], card["id"], citation_mode="paraphrase",
                argument_role="counterevidence")
    assert section_evidence(conn, one["id"])[0]["citation_mode"] == "direct_quote"
    assert section_evidence(conn, two["id"])[0]["argument_role"] == "counterevidence"


def test_assigning_the_same_card_twice_updates_rather_than_duplicates(project):
    conn, p = project
    card = quote(conn)
    section = add_section(conn, p["id"], "A section")
    assign_card(conn, section["id"], card["id"], citation_mode="paraphrase")
    assign_card(conn, section["id"], card["id"], citation_mode="direct_quote")
    evidence = section_evidence(conn, section["id"])
    assert len(evidence) == 1
    assert evidence[0]["citation_mode"] == "direct_quote"


def test_an_unknown_role_is_refused(project):
    conn, p = project
    section = add_section(conn, p["id"], "A section")
    with pytest.raises(ValueError):
        assign_card(conn, section["id"], quote(conn)["id"], argument_role="vibes")


# -- prompts --------------------------------------------------------------


def test_what_is_ready_to_export_is_reported_with_what_is_missing(project):
    conn, p = project
    state = available(conn, p["id"])
    assert state["themes"]["ready"] is False
    assert "no groups" in state["themes"]["blocked_by"]
    assert state["outline"]["blocked_by"] == "no research question chosen"


def test_the_themes_prompt_carries_the_groups_and_their_labels(project):
    conn, p = project
    path = grouped(conn, p["id"])
    save_label(conn, p["id"], path, "Oversight is organisational.")
    prompt = build(conn, p, "themes")
    assert "Oversight is organisational." in prompt.content
    assert "GROUP — Oversight" in prompt.content
    assert "in tension" in prompt.content
    assert "a gap in this collection is not a gap in the literature" in flat(prompt.content)


def test_a_prompt_says_which_words_are_the_researcher_s(project):
    conn, p = project
    path = grouped(conn, p["id"])
    save_label(conn, p["id"], path, "A proposition.")
    prompt = build(conn, p, "themes")
    assert "the researcher's own words" in prompt.content
    assert "not a source, never cited as one" in flat(prompt.content)


def test_the_section_prompt_contains_only_assigned_cards(project):
    conn, p = project
    section = add_section(conn, p["id"], "Institutional capacity",
                          purpose="Establish that capacity binds.")
    card = quote(conn)
    assign_card(conn, section["id"], card["id"], citation_mode="direct_quote")
    prompt = build(conn, p, "section", section_id=section["id"])

    assert card["human_id"] in prompt.content
    assert "Institutional capacity" in prompt.content
    # every other card in the project stays out
    others = [
        r["human_id"] for r in conn.execute(
            "SELECT human_id FROM card WHERE id != ?", (card["id"],))
    ]
    assert not any(o in prompt.content for o in others)


def test_the_section_prompt_states_the_rules_that_matter(project):
    conn, p = project
    section = add_section(conn, p["id"], "A section")
    assign_card(conn, section["id"], quote(conn)["id"])
    content = build(conn, p, "section", section_id=section["id"]).content
    assert "[EVIDENCE NEEDED:" in content
    assert "Never invent a source" in content
    assert "do not track the original's wording" in flat(content)


def test_an_estimated_locator_is_flagged_in_the_prompt(project):
    conn, p = project
    section = add_section(conn, p["id"], "A section")
    card = quote(conn)
    conn.execute(
        "UPDATE card SET locator_estimated = 1 WHERE id = ?", (card["id"],))
    assign_card(conn, section["id"], card["id"], citation_mode="direct_quote")
    content = build(conn, p, "section", section_id=section["id"]).content
    assert "LOCATORS TO VERIFY" in content
    assert card["human_id"] in content.split("LOCATORS TO VERIFY")[1]


def test_a_section_with_no_evidence_refuses_to_export(project):
    """A prompt with no cards is an invitation to invent some."""
    conn, p = project
    section = add_section(conn, p["id"], "Empty")
    with pytest.raises(ValueError, match="no evidence"):
        build(conn, p, "section", section_id=section["id"])


def test_the_exact_text_sent_is_kept(project):
    conn, p = project
    section = add_section(conn, p["id"], "A section")
    assign_card(conn, section["id"], quote(conn)["id"])
    prompt = build(conn, p, "section", section_id=section["id"])
    export_id = store(conn, p["id"], prompt)
    stored = conn.execute(
        "SELECT content, kind FROM prompt_export WHERE id = ?", (export_id,)
    ).fetchone()
    assert stored["content"] == prompt.content
    assert stored["kind"] == "section"


def test_token_estimate_counts_japanese_differently_from_english():
    assert estimate_tokens("hello there friend") < 10
    japanese = "監督は組織の性質である" * 5
    assert estimate_tokens(japanese) >= len(japanese) * 0.9


# -- paste-back validation, §12.13–15 -------------------------------------


@pytest.fixture
def section(project):
    """A section with one direct quote and one paraphrase assigned."""
    conn, p = project
    section = add_section(conn, p["id"], "Institutional capacity")
    q = quote(conn)  # "Human oversight becomes increasingly difficult…"
    idea = dict(
        conn.execute(
            "SELECT * FROM card WHERE origin_key = 'annotation:ANN1:idea'"
        ).fetchone()
    )
    other = quote(conn, "annotation:ANN4:quote")  # "Regulatory capacity lags…"
    assign_card(conn, section["id"], q["id"], citation_mode="direct_quote")
    assign_card(conn, section["id"], other["id"], citation_mode="paraphrase")
    assign_card(conn, section["id"], idea["id"], citation_mode="reference_only")
    return conn, p, section, q, other, idea


def test_an_unknown_citation_stops_the_draft(section):
    conn, p, s, q, _other, _idea = section
    result = validate(conn, p["id"], s["id"], "A claim [[CITE:KJ-9999]].")
    assert result.unknown == ["KJ-9999"]
    assert result.clean is False
    assert result.findings[0].kind == "unknown_citation"


def test_an_exact_quotation_passes(section):
    conn, p, s, q, _other, _idea = section
    draft = f'Smith writes that "{q["text"]}" [[CITE:{q["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    assert [f.kind for f in result.findings if f.severity == "stop"] == []


def test_a_quotation_altered_beyond_spacing_is_flagged_with_a_diff(section):
    """§12.14. The small alterations are the dangerous ones."""
    conn, p, s, q, _other, _idea = section
    altered = q["text"].replace("increasingly", "impossibly")
    draft = f'Smith writes that "{altered}" [[CITE:{q["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    finding = next(f for f in result.findings if f.kind == "quotation_altered")
    assert finding.severity == "stop"
    assert "increasingly" in finding.detail
    assert "impossibly" in finding.detail


def test_whitespace_and_curly_quotes_are_not_an_alteration(section):
    conn, p, s, q, _other, _idea = section
    respaced = "  ".join(q["text"].split()).replace("“", '"')
    draft = f'Smith: “{respaced}” [[CITE:{q["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    assert not [f for f in result.findings if f.kind == "quotation_altered"]


def test_quoting_only_part_of_a_passage_is_allowed(section):
    conn, p, s, q, _other, _idea = section
    fragment = " ".join(q["text"].split()[2:9])
    draft = f'oversight becomes "{fragment}" in practice [[CITE:{q["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    assert not [f for f in result.findings if f.kind == "quotation_altered"]


def test_a_paraphrase_that_tracks_the_original_is_flagged(section):
    """§12.15 — the risk with no quotation marks around it."""
    conn, p, s, _q, other, _idea = section
    draft = f"{other['text']} [[CITE:{other['human_id']}]]."
    result = validate(conn, p["id"], s["id"], draft)
    finding = next(f for f in result.findings if f.kind == "paraphrase_too_close")
    assert finding.severity == "stop"
    assert "longest unchanged stretch" in finding.detail


def test_a_real_paraphrase_passes(section):
    conn, p, s, _q, other, _idea = section
    draft = (
        "Enforcement has not kept pace with how quickly these systems reach the "
        f"public [[CITE:{other['human_id']}]]."
    )
    result = validate(conn, p["id"], s["id"], draft)
    assert not [f for f in result.findings if f.kind == "paraphrase_too_close"]


def test_a_japanese_paraphrase_is_checked_too(project):
    conn, p = project
    section = add_section(conn, p["id"], "日本語の節")
    card = quote(conn, "annotation:ANN6:quote")
    assign_card(conn, section["id"], card["id"], citation_mode="paraphrase")
    close = f"{card['text']}という点が重要である [[CITE:{card['human_id']}]]。"
    assert any(
        f.kind == "paraphrase_too_close"
        for f in validate(conn, p["id"], section["id"], close).findings
    )
    distant = f"監督の所在をどこに置くかが論点になる [[CITE:{card['human_id']}]]。"
    assert not any(
        f.kind == "paraphrase_too_close"
        for f in validate(conn, p["id"], section["id"], distant).findings
    )


def test_evidence_needed_markers_are_listed_as_open_work(section):
    conn, p, s, q, _other, _idea = section
    draft = f'A claim [[CITE:{q["human_id"]}]]. [EVIDENCE NEEDED: a case after 2020]'
    result = validate(conn, p["id"], s["id"], draft)
    assert result.evidence_needed == ["a case after 2020"]


def test_cards_that_went_unused_are_reported(section):
    conn, p, s, q, _other, _idea = section
    result = validate(conn, p["id"], s["id"], f"Only one [[CITE:{q['human_id']}]].")
    assert {c["human_id"] for c in result.unused} == {_other["human_id"], _idea["human_id"]}


def test_markers_render_for_reading_but_stay_in_the_stored_text(section):
    conn, p, s, q, _other, idea = section
    draft = f"A claim [[CITE:{q['human_id']}]] and my own note [[CITE:{idea['human_id']}]]."
    result = validate(conn, p["id"], s["id"], draft)
    assert "(Smith 2025, p. 132)" in result.rendered
    assert f"(my own note, {idea['human_id']})" in result.rendered
    saved = save_draft(conn, p["id"], s["id"], draft, validation=result)
    assert "[[CITE:" in saved["content"]


def test_drafts_are_versioned_and_never_overwritten(section):
    conn, p, s, q, _other, _idea = section
    first = save_draft(conn, p["id"], s["id"], "first attempt")
    second = save_draft(conn, p["id"], s["id"], "second attempt")
    assert (first["version"], second["version"]) == (1, 2)
    assert conn.execute("SELECT COUNT(*) FROM draft").fetchone()[0] == 2


# -- export for writing, §12.16 -------------------------------------------


def test_markdown_export_emits_citekeys_not_author_year_strings(section):
    conn, p, s, q, _other, _idea = section
    markdown = to_markdown(conn, p["id"], s["id"], f"A claim [[CITE:{q['human_id']}]].")
    assert "[@smith2025, p. 132]" in markdown
    assert "(Smith 2025" not in markdown


def test_an_idea_card_leaves_a_comment_rather_than_a_citation(section):
    conn, p, s, _q, _other, idea = section
    markdown = to_markdown(conn, p["id"], s["id"], f"My reading [[CITE:{idea['human_id']}]].")
    assert "my own note, not a source" in markdown
    assert "[@" not in markdown


def test_an_estimated_page_is_left_out_of_the_citekey(section):
    conn, p, s, q, _other, _idea = section
    conn.execute("UPDATE card SET locator_estimated = 1 WHERE id = ?", (q["id"],))
    markdown = to_markdown(conn, p["id"], s["id"], f"x [[CITE:{q['human_id']}]]")
    assert "[@smith2025]" in markdown
    assert "p. 132" not in markdown


def test_citekeys_are_unique_within_a_project(project):
    conn, p = project
    conn.execute(
        "INSERT INTO source (id, project_id, zotero_item_key, creators_short, year, title) "
        "VALUES ('s9', ?, 'ZZZ', 'Smith', '2025', 'Another paper')", (p["id"],))
    keys = sorted(citekeys(conn, p["id"]).values())
    assert len(keys) == len(set(keys))
    assert "smith2025" in keys and "smith2025a" in keys


def test_a_name_with_no_latin_letters_falls_back_to_the_zotero_key():
    assert base_key(
        {"creators_short": "田中", "title": "国家の能力", "year": "2024",
         "zotero_item_key": "ABCD1234"}
    ) == "zotero-ABCD1234"


def test_the_paper_export_names_which_sections_a_model_drafted(section):
    conn, p, s, q, _other, _idea = section
    add_question(conn, p["id"], "Does capacity bind?")
    choose_question(
        conn, p["id"],
        conn.execute("SELECT id FROM research_question").fetchone()[0],
    )
    prompt = build(conn, p, "section", section_id=s["id"])
    export_id = store(conn, p["id"], prompt)
    save_draft(
        conn, p["id"], s["id"],
        f"A claim [[CITE:{q['human_id']}]]. [EVIDENCE NEEDED: a later case]",
        prompt_export_id=export_id,
    )
    markdown = paper_markdown(conn, p["id"])
    assert "## Institutional capacity" in markdown
    assert "[@smith2025, p. 132]" in markdown
    assert "Appendix: how this draft was made" in markdown
    assert "Institutional capacity (draft v1)" in markdown
    assert "a later case" in markdown
    assert "`@smith2025`" in markdown


def test_a_paper_with_no_model_written_sections_says_so(project):
    conn, p = project
    add_section(conn, p["id"], "Written by hand")
    markdown = paper_markdown(conn, p["id"])
    assert "No section here was drafted by a model." in markdown


def test_a_quotation_typed_from_the_page_also_passes(section):
    """The raw extraction still carries the PDF's line-break hyphenation. A
    researcher quoting from the page produces that form; a model quoting from
    the prompt produces the cleaned one. Both are the source's words."""
    conn, p, s, q, _other, _idea = section
    draft = f'Smith writes that "{q["text_raw"]}" [[CITE:{q["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    assert not [f for f in result.findings if f.kind == "quotation_altered"]
