"""
Tests for score_restructure_fidelity (18 Aug 2026) — the deterministic
check that catches a platform_format correction call rewriting rather
than merely rearranging. Built after the wording-only guardrail in
build_correction_prompt was found live to be insufficient: a real
render restructured "A governance failure is loud. An agent does
something..." into a "When X... When Y..." conditional, introducing
"when" and "occurs" — words that don't exist anywhere in the
pre-correction text — despite an explicit instruction against exactly
that. See voice_engine.py's docstring for the full incident.
"""
import voice_engine as ve


def test_catches_the_real_fabrication_incident():
    pre = (
        "A governance failure is loud. An agent does something it should not "
        "have, and there is an incident, a trace and someone to ask. A "
        "qualification failure is silent. The system runs correctly for "
        "eighteen months, every action inside policy, every log clean, and it "
        "should never have been deployed for that purpose in the first place."
    )
    post = (
        "When an agent does something it should not have, there is an "
        "incident, a trace and someone to ask. When a qualification failure "
        "occurs, the system runs correctly for eighteen months, every action "
        "inside policy, every log clean, and it should never have been "
        "deployed for that purpose in the first place."
    )
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is False
    assert "when" in result["fabricated_words"]
    assert "occurs" in result["fabricated_words"]


def test_pure_reordering_is_not_flagged():
    """Whole-sentence and paragraph-level reordering — exactly what
    platform_format is meant to permit — must never be flagged."""
    pre = "Distinct stage, and I think you have found the gap. My test is simple."
    post = "My test is simple.\n\nDistinct stage, and I think you have found the gap."
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is True
    assert result["fabricated_words"] == {}


def test_word_removal_is_not_flagged():
    """Economy-mode cutting happens in the same correction call —
    removing words must never be flagged, only adding them."""
    pre = "This is a somewhat needlessly verbose sentence that could be tightened."
    post = "This is a verbose sentence that could be tightened."
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is True


def test_case_and_punctuation_changes_are_not_flagged():
    """Recapitalising a word that moved to a new sentence-initial
    position, or changing a comma to a period at a new paragraph
    break, must not count as a fabricated word — comparison is
    case-insensitive and punctuation-stripped."""
    pre = "the plan works. the team agrees."
    post = "The team agrees. The plan works."
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is True


def test_word_count_matters_not_just_word_presence():
    """The real bug class this guards against: a naive 'does this
    word appear ANYWHERE in pre_text' check would miss a word being
    used MORE often in post_text than pre_text had it — e.g. "the"
    trivially exists in both texts, so presence-only checking would
    never flag an extra "the" being inserted. Count-based comparison
    catches this; presence-based comparison would not."""
    pre = "The plan works well for the team."
    post = "The plan works well for the wider team the leadership approved."
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is False
    assert "the" in result["fabricated_words"]
    assert "wider" in result["fabricated_words"]
    assert "leadership" in result["fabricated_words"]
    assert "approved" in result["fabricated_words"]


def test_empty_texts_are_trivially_clean():
    result = ve.score_restructure_fidelity("", "")
    assert result["clean"] is True


def test_post_shorter_than_pre_is_clean_if_no_new_words():
    """A restructuring pass that also cuts a whole sentence (allowed —
    economy) while introducing nothing new must still pass."""
    pre = "This sentence stays. This sentence gets cut entirely from the output."
    post = "This sentence stays."
    result = ve.score_restructure_fidelity(pre, post)
    assert result["clean"] is True
