"""The argument layer, the prompts it produces, and what comes back. §12.12–16"""

from __future__ import annotations

import pytest

from tests.conftest import FakeZotero
from zkj.citekeys import base_key, citekeys
from zkj.compose import (
    add_claim,
    add_question,
    add_section,
    adopt_groups_as_sections,
    assign_card,
    choose_question,
    list_sections,
    move_section,
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


def test_nothing_but_an_empty_project_blocks_an_export(project):
    """Specifying is how you take control of a decision, not a toll gate."""
    conn, p = project
    state = available(conn, p["id"])
    assert state["themes"]["ready"] is True
    assert state["questions"]["ready"] is True
    assert state["outline"]["ready"] is True
    assert state["paper"]["ready"] is True
    assert all(state[k]["blocked_by"] is None for k in ("themes", "questions", "outline", "paper"))


def test_what_each_export_will_work_out_for_itself_is_named(project):
    conn, p = project
    state = available(conn, p["id"])
    assert "argument" in state["paper"]["infers"]
    assert state["outline"]["specified"] == "no question chosen"
    assert state["themes"]["specified"] == "no groups yet; your folders will be used"


def test_an_empty_project_says_so(tmp_path):
    from zkj.store import insert

    conn = connect(tmp_path / "empty.sqlite3")
    pid = insert(conn, "project",
                 {"name": "e", "root_collection_key": "R", "created_at": now_iso()})
    state = available(conn, pid)
    assert state["paper"]["ready"] is False
    assert "no cards" in state["paper"]["blocked_by"]


def test_the_themes_prompt_works_without_any_grouping_at_all(project):
    """Before anything is dragged in Zotero, the folders the sources sit in are
    the researcher's grouping — the tool works from where they actually are."""
    conn, p = project
    prompt = build(conn, p, "themes")
    assert "Agentic Governance/02 Oversight" in prompt.content
    assert "put these together but has not said why" in flat(prompt.content)
    assert "No group is labelled" in prompt.note


def test_an_unlabelled_group_is_offered_for_the_model_to_read(project):
    conn, p = project
    grouped(conn, p["id"])
    content = build(conn, p, "questions").content
    assert "the theme is yours to work out" in flat(content)


def test_the_outline_prompt_proposes_the_question_when_none_is_chosen(project):
    conn, p = project
    prompt = build(conn, p, "outline")
    assert "no research question has been chosen" in flat(prompt.content)
    assert "mark the question as your proposal" in flat(prompt.content)
    assert "The researcher has fixed nothing yet" in prompt.content


def test_what_the_researcher_did_fix_is_marked_as_not_the_model_s(project):
    conn, p = project
    question = add_question(conn, p["id"], "Does capacity bind?")
    choose_question(conn, p["id"], question["id"])
    add_claim(conn, p["id"], "Capacity binds before law does.", claim_type="thesis")
    add_section(conn, p["id"], "Institutional capacity")
    content = build(conn, p, "outline").content
    assert "Research question (fixed): Does capacity bind?" in content
    assert "is not yours to revise" in flat(content)


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


def test_a_section_with_no_evidence_offers_every_card_and_says_so(project):
    """Unassigned does not mean unwritable: the whole project is the allowed
    set, so nothing can be invented — only chosen."""
    conn, p = project
    section = add_section(conn, p["id"], "Empty")
    prompt = build(conn, p, "section", section_id=section["id"])
    assert "has not said which of these the section uses" in flat(prompt.content)
    assert "No evidence is assigned" in prompt.note
    every = [r["human_id"] for r in conn.execute(
        "SELECT human_id FROM card WHERE kind != 'image' AND origin != 'group_label'")]
    assert all(h in prompt.content for h in every)


def test_a_section_with_no_purpose_is_asked_to_work_one_out(project):
    conn, p = project
    section = add_section(conn, p["id"], "Untitled work")
    content = build(conn, p, "section", section_id=section["id"]).content
    assert "work out what this section has to establish" in flat(content)


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


def test_a_passage_containing_quotation_marks_is_still_recognised(section):
    """A quotation with a quotation inside it cannot be pulled out as one
    span, so the source's words are looked for in the draft as a whole."""
    conn, p, s, q, _other, _idea = section
    conn.execute(
        "UPDATE card SET text = ?, text_raw = ? WHERE id = ?",
        (
            'The third frame is the “competition frame,” which borrows its '
            "urgency from security language.",
            None,
            q["id"],
        ),
    )
    card = quote(conn)
    draft = f'She writes: "{card["text"]}" [[CITE:{card["human_id"]}]].'
    result = validate(conn, p["id"], s["id"], draft)
    assert not [f for f in result.findings if f.kind == "quotation_altered"]

    altered = card["text"].replace("borrows", "steals")
    bad = f'She writes: "{altered}" [[CITE:{card["human_id"]}]].'
    finding = next(
        f
        for f in validate(conn, p["id"], s["id"], bad).findings
        if f.kind == "quotation_altered"
    )
    # the diff is against the stretch the draft was quoting, not the whole card
    assert "borrows" in finding.detail and "steals" in finding.detail
    assert finding.detail.count("\n") < 8


# -- the whole paper, from the clusters alone -----------------------------


def test_the_paper_prompt_asks_for_a_paper_not_an_assessment(project):
    """Handed the material with no task, a model reasonably reports on it. The
    prompt has to say plainly that prose in sections is what is wanted."""
    conn, p = project
    prompt = build(conn, p, "paper")
    assert prompt.kind == "paper"
    assert "prose in sections" in prompt.content
    assert "not an assessment of the material" in prompt.content
    assert "not a set to be exhausted" in flat(prompt.content)
    assert "The researcher has fixed nothing yet" in prompt.content
    assert "the argument, the sections and their claims are all the model's" in prompt.note


def test_a_gap_is_written_through_rather_than_stopped_at(project):
    """A draft that halts at every missing step is not a draft. The model may
    supply the reasoning — never a source — and must mark where it did."""
    conn, p = project
    content = build(conn, p, "paper").content
    assert "[UNSUPPORTED:" in content
    assert "never do is attribute it to anybody" in flat(content)
    assert "invent a source, an author, a date, a page number or a quotation" in flat(content)


def test_the_assess_mode_is_still_there_for_when_it_is_wanted(project):
    conn, p = project
    prompt = build(conn, p, "paper", mode="assess")
    assert "Do not draft anything" in prompt.content
    assert "a gap here is a gap in what they have read" in flat(prompt.content)
    assert prompt.title == "What this material can answer"


def test_how_passages_are_used_can_be_left_open_or_decided(project):
    conn, p = project
    default = build(conn, p, "paper").content
    assert "choose one and commit to it" in flat(default)

    quoting = build(conn, p, "paper", quoting="quote").content
    assert "wants the sources' own words on the page" in flat(quoting)
    assert "do not paraphrase the passages" in flat(quoting)

    ideas = build(conn, p, "paper", quoting="ideas").content
    assert "do not quote" in flat(ideas)
    assert "no phrase of the original carried over" in flat(ideas)


def test_an_unknown_mode_or_quoting_choice_is_refused(project):
    conn, p = project
    with pytest.raises(ValueError, match="mode"):
        build(conn, p, "paper", mode="wing it")
    with pytest.raises(ValueError, match="quoting"):
        build(conn, p, "paper", quoting="somehow")


def test_the_paper_prompt_keeps_whatever_the_researcher_did_fix(project):
    conn, p = project
    question = add_question(conn, p["id"], "Does capacity bind?")
    choose_question(conn, p["id"], question["id"])
    add_section(conn, p["id"], "Institutional capacity", purpose="Establish it binds.")
    prompt = build(conn, p, "paper")
    assert "Research question (fixed): Does capacity bind?" in prompt.content
    assert "Institutional capacity — Establish it binds." in prompt.content
    assert "your question and sections will be kept" in available(conn, p["id"])["paper"]["specified"]


def test_every_card_reaches_the_paper_prompt_grouped_or_not(project):
    conn, p = project
    # only some cards are filed; the rest must not vanish from the prompt
    quote_one = quote(conn)
    conn.execute(
        "UPDATE card SET kj_path = 'P/_KJ/Oversight', zotero_note_key = 'N', "
        "materialized_at = ? WHERE id = ?", (now_iso(), quote_one["id"]))
    prompt = build(conn, p, "paper")
    assert "not yet grouped" in prompt.content
    every = [r["human_id"] for r in conn.execute(
        "SELECT human_id FROM card WHERE kind != 'image' AND origin != 'group_label'")]
    assert all(h in prompt.content for h in every)


def test_a_whole_paper_draft_is_checked_against_every_card(project):
    """No section means no whitelist of one section — the project is the set."""
    conn, p = project
    card = quote(conn)
    draft = (
        f'Oversight fails at the boundary. "{card["text"]}" [[CITE:{card["human_id"]}]]. '
        f'A later study agrees [[CITE:KJ-9999]].'
    )
    result = validate(conn, p["id"], None, draft)
    assert result.unknown == ["KJ-9999"]
    assert not [f for f in result.findings if f.kind == "quotation_altered"]


def test_with_no_mode_fixed_the_draft_s_own_choice_decides_the_check(project):
    conn, p = project
    card = quote(conn, "annotation:ANN4:quote")

    quoted_exactly = f'They write "{card["text"]}" [[CITE:{card["human_id"]}]].'
    assert validate(conn, p["id"], None, quoted_exactly).clean

    quoted_wrongly = (
        f'They write "{card["text"].replace("lags", "sprints")}" '
        f'[[CITE:{card["human_id"]}]].'
    )
    assert any(
        f.kind == "quotation_altered"
        for f in validate(conn, p["id"], None, quoted_wrongly).findings
    )

    echoed_without_marks = f'{card["text"]} [[CITE:{card["human_id"]}]].'
    assert any(
        f.kind == "paraphrase_too_close"
        for f in validate(conn, p["id"], None, echoed_without_marks).findings
    )


def test_whole_paper_drafts_are_versioned_separately_from_sections(project):
    conn, p = project
    section = add_section(conn, p["id"], "A section")
    save_draft(conn, p["id"], section["id"], "section draft")
    first = save_draft(conn, p["id"], None, "paper draft")
    second = save_draft(conn, p["id"], None, "paper draft again")
    assert (first["version"], second["version"]) == (1, 2)


def test_a_whole_paper_draft_exports_with_citekeys(project):
    conn, p = project
    card = quote(conn)
    markdown = to_markdown(conn, p["id"], None, f"x [[CITE:{card['human_id']}]]")
    assert "[@smith2025, p. 132]" in markdown


def test_an_invented_citation_is_named_for_the_scope_it_broke(project):
    """"Not in this section's evidence" is the wrong thing to say about a
    draft of the whole paper — there was no section."""
    conn, p = project
    section = add_section(conn, p["id"], "S")
    assign_card(conn, section["id"], quote(conn)["id"])

    scoped = validate(conn, p["id"], section["id"], "x [[CITE:KJ-9999]]")
    assert "this section's evidence" in scoped.findings[0].message
    assert scoped.stats["scope"] == "section"

    whole = validate(conn, p["id"], None, "x [[CITE:KJ-9999]]")
    assert "is not one of your cards" in whole.findings[0].message
    assert whole.stats["scope"] == "project"


def test_group_order_is_not_an_argument_and_says_so(project):
    """A group says these passages belong together. It says nothing about what
    comes first — that is the order the folders happened to be in."""
    conn, p = project
    content = build(conn, p, "paper").content
    assert "the order their folders happened to be in, and means nothing" in flat(content)
    assert "order the sections as the argument requires" in flat(content)
    assert "split a group across two sections" in flat(content)


def test_once_the_researcher_names_sections_their_order_is_kept(project):
    conn, p = project
    add_section(conn, p["id"], "Second thing")
    content = build(conn, p, "paper").content
    assert "in the order the researcher set. Keep it." in content


# -- groups become an outline you can edit --------------------------------


def test_groups_can_be_adopted_as_sections_carrying_their_cards(project):
    """The passages you put together are already your claim about what belongs
    with what. This makes that into an outline, rather than asking for one."""
    conn, p = project
    grouped(conn, p["id"], "P/_KJ/Oversight")
    made = adopt_groups_as_sections(conn, p["id"])
    assert [s["title"] for s in made] == ["Oversight"]
    evidence = section_evidence(conn, made[0]["id"])
    assert len(evidence) == 6  # every card in the group came with it
    assert {e["citation_mode"] for e in evidence} == {"paraphrase", "reference_only"}


def test_a_labelled_group_becomes_a_section_named_by_its_proposition(project):
    conn, p = project
    path = grouped(conn, p["id"], "P/_KJ/Oversight")
    save_label(conn, p["id"], path, "Oversight is organisational, not individual.")
    made = adopt_groups_as_sections(conn, p["id"])
    assert made[0]["title"] == "Oversight is organisational, not individual"


def test_adopting_twice_does_not_duplicate(project):
    conn, p = project
    grouped(conn, p["id"], "P/_KJ/Oversight")
    adopt_groups_as_sections(conn, p["id"])
    assert adopt_groups_as_sections(conn, p["id"]) == []
    assert len(list_sections(conn, p["id"])) == 1


def test_sections_can_be_reordered(project):
    conn, p = project
    first = add_section(conn, p["id"], "One")
    second = add_section(conn, p["id"], "Two")
    third = add_section(conn, p["id"], "Three")
    assert [s["title"] for s in list_sections(conn, p["id"])] == ["One", "Two", "Three"]

    move_section(conn, third["id"], -1)
    assert [s["title"] for s in list_sections(conn, p["id"])] == ["One", "Three", "Two"]

    move_section(conn, first["id"], +1)
    assert [s["title"] for s in list_sections(conn, p["id"])] == ["Three", "One", "Two"]

    # the ends hold
    move_section(conn, list_sections(conn, p["id"])[0]["id"], -1)
    assert [s["title"] for s in list_sections(conn, p["id"])] == ["Three", "One", "Two"]
    assert second and first


# -- the export stands on its own -----------------------------------------


def test_an_undrafted_paper_still_carries_every_quotation_in_full(project):
    """A file that says "not drafted yet — 0 cards assigned" is no use to
    anybody: not to the researcher, and not to a model handed it."""
    conn, p = project
    markdown = paper_markdown(conn, p["id"])
    card = quote(conn)
    assert card["text"] in markdown
    assert card["human_id"] in markdown
    assert "Smith 2025, p. 132" in markdown
    assert "not drafted yet" not in markdown
    assert "The material, as the researcher grouped it" in markdown


def test_the_researcher_s_own_words_are_marked_as_theirs_in_the_export(project):
    conn, p = project
    markdown = paper_markdown(conn, p["id"])
    idea = dict(conn.execute(
        "SELECT * FROM card WHERE origin = 'annotation_comment'").fetchone())
    assert "the researcher's own words" in markdown
    assert idea["text"] in markdown


def test_a_section_with_evidence_carries_it_when_undrafted(project):
    conn, p = project
    section = add_section(conn, p["id"], "Institutional capacity")
    card = quote(conn)
    assign_card(conn, section["id"], card["id"], citation_mode="direct_quote")
    markdown = paper_markdown(conn, p["id"])
    assert "## Institutional capacity" in markdown
    assert "Not drafted. Its evidence, in full:" in markdown
    assert card["text"] in markdown
    assert "direct_quote · evidence" in markdown


def test_material_already_used_by_a_section_is_not_repeated(project):
    conn, p = project
    section = add_section(conn, p["id"], "S")
    card = quote(conn)
    assign_card(conn, section["id"], card["id"])
    markdown = paper_markdown(conn, p["id"])
    assert markdown.count(card["text"]) == 1


def test_the_export_says_when_no_question_was_chosen(project):
    conn, p = project
    assert "Not chosen" in paper_markdown(conn, p["id"])


def test_a_section_and_a_group_of_the_same_name_are_one_thing(project):
    """A researcher who labels a group "AI is politics" and then makes a
    section called "AI is politics" meant one thing, not two."""
    conn, p = project
    path = grouped(conn, p["id"], "P/_KJ/Sec 3")
    save_label(conn, p["id"], path, "AI is politics")
    empty = add_section(conn, p["id"], "AI is politics")

    made = adopt_groups_as_sections(conn, p["id"])
    assert [s["id"] for s in made] == [empty["id"]]
    assert len(list_sections(conn, p["id"])) == 1
    assert len(section_evidence(conn, empty["id"])) == 6


def test_a_section_that_already_holds_evidence_is_left_alone(project):
    conn, p = project
    path = grouped(conn, p["id"], "P/_KJ/Sec 3")
    save_label(conn, p["id"], path, "AI is politics")
    section = add_section(conn, p["id"], "AI is politics")
    assign_card(conn, section["id"], quote(conn)["id"], citation_mode="direct_quote")

    assert adopt_groups_as_sections(conn, p["id"]) == []
    evidence = section_evidence(conn, section["id"])
    assert len(evidence) == 1
    assert evidence[0]["citation_mode"] == "direct_quote"


def test_an_unwritten_label_is_said_out_loud_in_the_export(project):
    """Silence reads as "no label was possible" — and a folder name is not a
    claim about the passages under it."""
    conn, p = project
    grouped(conn, p["id"], "P/_KJ/Sec 2")
    markdown = paper_markdown(conn, p["id"])
    assert "No label written" in markdown
    assert "the folder name is not a claim" in markdown


def test_a_source_that_cannot_be_cited_normally_is_named(project):
    """A model asked for "(Author, year)" where the record has neither will
    supply something. Naming the gap is the difference between a citation to
    check and a citation to discover."""
    conn, p = project
    conn.execute("UPDATE source SET creators_short = NULL, year = NULL "
                 "WHERE zotero_item_key = 'SRC1'")
    content = build(conn, p, "paper").content
    assert "SOURCES WITH INCOMPLETE RECORDS" in content
    assert "Human oversight of autonomous agents" in content
    assert "no author, no date" in content
    assert "not even a likely one" in content


def test_a_complete_library_gets_no_such_block(project):
    conn, p = project
    conn.execute("UPDATE source SET creators_short = 'Someone', year = '2020'")
    assert "INCOMPLETE RECORDS" not in build(conn, p, "paper").content


def test_the_export_marks_the_records_to_fix(project):
    conn, p = project
    conn.execute("UPDATE source SET year = NULL WHERE zotero_item_key = 'SRC1'")
    markdown = paper_markdown(conn, p["id"])
    assert "this Zotero record has no date" in markdown
    assert "refer to it by title until it is fixed" in markdown


# -- a note written under a book keeps the book ---------------------------


def test_a_note_written_under_a_source_carries_it_into_the_prompt(project):
    """"My note" and "my note on Smith" are different things, and only one of
    them tells a reader what the researcher was thinking about."""
    conn, p = project
    content = build(conn, p, "paper").content
    child = dict(conn.execute(
        "SELECT * FROM card WHERE origin = 'child_note'").fetchone())
    assert child["text"] in content
    assert "written while reading Smith 2025" in content


def test_it_is_still_the_researcher_s_thought_not_the_source_s(project):
    conn, p = project
    content = build(conn, p, "paper").content
    assert "does not make the thought that author's" in flat(content)
    assert "it is not a citation to them" in flat(content)


def test_a_comment_on_a_highlight_names_the_page_it_was_written_at(project):
    conn, p = project
    content = build(conn, p, "paper").content
    assert "written while reading Smith 2025, p. 132" in content


def test_a_standalone_note_has_no_source_and_claims_none(project):
    conn, p = project
    content = build(conn, p, "paper").content
    standalone = dict(conn.execute(
        "SELECT * FROM card WHERE origin = 'standalone_note'").fetchone())
    index = content.index(standalone["text"])
    preamble = content[index - 120 : index]
    assert "(the researcher's own words)" in preamble


def test_the_export_carries_it_too(project):
    conn, p = project
    markdown = paper_markdown(conn, p["id"])
    assert "the researcher's own words, written while reading Smith 2025" in markdown
