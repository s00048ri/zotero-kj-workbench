"""Cleaning must repair typesetting damage and nothing else."""

from __future__ import annotations

from zkj.text import collapse, escape_html, html_to_text, normalise_quote


def test_end_of_line_hyphenation_is_repaired():
    raw = "Human oversight becomes increasingly dif-\nficult as agents operate."
    assert "difficult as agents" in normalise_quote(raw)
    assert "-" not in normalise_quote(raw)


def test_wrapped_english_lines_join_with_a_space():
    assert normalise_quote("regulatory capacity\nlags behind") == (
        "regulatory capacity lags behind"
    )


def test_wrapped_japanese_lines_join_without_a_space():
    """A space inserted mid-sentence is damage, not cleaning."""
    raw = "監督を個人の能力として\n扱ってきたことが問題である。"
    assert normalise_quote(raw) == "監督を個人の能力として扱ってきたことが問題である。"


def test_full_width_characters_survive():
    """NFKC would rewrite every one of these. NFC leaves them alone."""
    raw = "ＡＩガバナンスは「監督」を個人の能力として扱ってきた。"
    assert normalise_quote(raw) == raw


def test_typographic_marks_are_not_normalised_away():
    raw = "He called it “oversight” — a word doing too much work."
    assert normalise_quote(raw) == raw


def test_ligatures_and_soft_hyphens():
    assert normalise_quote("deﬁnitely ﬂawed") == "definitely flawed"
    assert normalise_quote("over­sight") == "oversight"


def test_accents_are_composed_but_letters_unchanged():
    decomposed = "cité"  # e + combining acute
    assert normalise_quote(decomposed) == "cité"


def test_empty_input_is_empty_output():
    assert normalise_quote(None) == ""
    assert normalise_quote("   \n  ") == ""


def test_note_html_becomes_text_with_paragraphs_intact():
    note = "<p>First claim.</p><p>Second claim.</p>"
    assert html_to_text(note) == "First claim.\n\nSecond claim."


def test_note_html_unescapes_entities_and_breaks():
    assert html_to_text("<p>a &amp; b<br/>c</p>") == "a & b\nc"


def test_note_html_drops_markup_but_keeps_japanese():
    assert html_to_text("<p><strong>監督</strong>の問題</p>") == "監督の問題"


def test_collapse_and_escape():
    assert collapse(" a \n b  c ") == "a b c"
    assert escape_html("<script>x</script>") == "&lt;script&gt;x&lt;/script&gt;"
    assert escape_html("a\nb") == "a<br/>b"
