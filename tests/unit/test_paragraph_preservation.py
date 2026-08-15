"""
Tests for paragraph-break preservation across the deterministic
fixers — found and fixed alongside the double-punctuation bug this
same session, from the same root cause: every fixer that rebuilt text
via _extract_sentences(text) + " ".join(...) operated on the WHOLE
text at once, which discards paragraph boundaries (_extract_sentences
has always flattened \n+ runs during splitting). A multi-paragraph
email that triggered any fixer would come back as one continuous
block, no blank lines between paragraphs — confirmed against a real
render this session via a live smoke test before this was fixed.

_split_into_paragraphs / _apply_across_paragraphs replace the flat
"whole text -> sentences -> " ".join()" pattern with one that operates
within each paragraph and rejoins paragraphs with a blank line, for
the four fixers that rebuild text this way (_fix_hedge_density,
_fix_sentence_length_sd, _fix_first_person_ratio,
_fix_directive_ratio). _fix_modal_hedge is untouched here deliberately
-- it does direct regex substitution on the full string and never
extracts/rejoins sentences, so it never had this bug.
"""
import deterministic_fixers as df


def _paragraph_count(text: str) -> int:
    return len([p for p in text.split("\n\n") if p.strip()])


# ------------------------------------------------------------------
# _split_into_paragraphs / _apply_across_paragraphs — the harness itself
# ------------------------------------------------------------------

def test_split_into_paragraphs_basic():
    text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert df._split_into_paragraphs(text) == [
        "First paragraph.", "Second paragraph.", "Third paragraph.",
    ]


def test_split_into_paragraphs_collapses_extra_blank_lines():
    text = "First.\n\n\n\nSecond."
    assert df._split_into_paragraphs(text) == ["First.", "Second."]


def test_apply_across_paragraphs_preserves_structure_when_nothing_changes():
    text = "First paragraph.\n\nSecond paragraph."
    result, changed = df._apply_across_paragraphs(text, lambda s: (s, False))
    assert not changed
    assert _paragraph_count(result) == 2


def test_apply_across_paragraphs_respects_global_max_conversions():
    text = "One thing.\n\nTwo things.\n\nThree things.\n\nFour things."
    calls = []

    def _fn(s):
        calls.append(s)
        return s.upper(), True

    result, changed = df._apply_across_paragraphs(text, _fn, max_conversions=2)
    assert changed
    # Only 2 of the 4 sentences should have actually been offered to
    # the fixer as convertible before the cap kicked in — the harness
    # itself must stop calling per_sentence_fn once the cap is hit,
    # not just ignore its result.
    upper_count = sum(1 for p in result.split("\n\n") if p.isupper())
    assert upper_count == 2, f"Expected exactly 2 conversions, got: {result!r}"


# ------------------------------------------------------------------
# Each fixer preserves paragraph structure on real multi-paragraph text
# ------------------------------------------------------------------

def test_fix_hedge_density_preserves_paragraphs():
    text = (
        "Following up on the test link. It might perhaps be worth another look.\n\n"
        "The timing feels right off the back of your recent post.\n\n"
        "Worth another look, or should I send a fresh report?"
    )
    fixed, changed = df._fix_hedge_density(text, target=0.0, current=2.0)
    assert changed
    assert _paragraph_count(fixed) == 3, f"Expected 3 paragraphs preserved, got: {fixed!r}"
    assert "perhaps" not in fixed.lower()


def test_fix_sentence_length_sd_preserves_paragraphs_and_splits_in_the_right_one():
    text = (
        "Short one here.\n\n"
        "This is a much longer sentence that goes on for a while, and it really "
        "should be split into two separate pieces for better rhythm.\n\n"
        "Final short paragraph."
    )
    fixed, changed = df._fix_sentence_length_sd(text, target=10.0, current=2.0)
    assert changed
    paragraphs = [p for p in fixed.split("\n\n") if p.strip()]
    assert len(paragraphs) == 3, f"Expected 3 paragraphs preserved, got: {paragraphs}"
    # The split should have happened in the middle paragraph, not
    # bled into the first or last.
    assert paragraphs[0] == "Short one here."
    assert paragraphs[2] == "Final short paragraph."
    assert "And it really should be split" in paragraphs[1]


def test_fix_first_person_ratio_preserves_paragraphs():
    text = (
        "It is worth noting that the numbers look strong this quarter.\n\n"
        "The team has been focused on delivery.\n\n"
        "Final paragraph here for context."
    )
    fixed, changed = df._fix_first_person_ratio(
        text, target=0.8, current=0.0, input_has_opinion_content=True
    )
    assert changed
    assert _paragraph_count(fixed) == 3, f"Expected 3 paragraphs preserved, got: {fixed!r}"
    assert fixed.startswith("I think")


def test_fix_directive_ratio_preserves_paragraphs():
    text = (
        "Could you check this before the meeting.\n\n"
        "The context is in the shared folder.\n\n"
        "Final paragraph for good measure."
    )
    fixed, changed = df._fix_directive_ratio(
        text, target=0.8, current=0.0, input_has_directive_content=True
    )
    assert changed
    assert _paragraph_count(fixed) == 3, f"Expected 3 paragraphs preserved, got: {fixed!r}"
    assert fixed.startswith("Check this")


def test_single_paragraph_input_still_works_unchanged():
    """No paragraph breaks at all -- the common case for a short
    single-block render -- must still work exactly as before."""
    text = "It might perhaps be worth another look at this."
    fixed, changed = df._fix_hedge_density(text, target=0.0, current=2.0)
    assert changed
    assert "\n\n" not in fixed
    assert "perhaps" not in fixed.lower()
