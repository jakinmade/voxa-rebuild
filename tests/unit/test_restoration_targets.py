"""
Tests for _build_restoration_targets — the system-prompt block that
tells the initial voice-transformation render what ownership/hedging/
directness rate to aim for. No prior test coverage existed for this
function at all before this file.

The ownership guardrail (18 Aug 2026) exists because of a real, live
failure: a render converted "Nobody finds it through monitoring..."
(a general, impersonal analytical statement) into "I would never find
it through monitoring..." — treating a general claim as fair game for
first-person conversion just to hit the target rate. Every existing
deterministic fixer declined to touch this (it doesn't match any
known pattern: not a recognised opener verb, not a mid-sentence "I
think" injection), and _matching_original_sentence correctly confirms
this is a genuine defect, not preserved ownership (the aligned
original sentence has no first-person marker at all).

IMPORTANT — what these tests can and cannot prove: they confirm the
instruction text is correctly constructed and contains the intended
guardrail language. They CANNOT prove a live model actually obeys it
— that requires a real render, which this sandbox has no API access
to run. Flagged explicitly, not silently implied, per the standing
rule on structural changes to the render/prompt path.
"""
from prompts import _build_restoration_targets


def _baseline(**overrides):
    b = {
        "hedge_density": 0.04, "sentence_length_sd": 7.0,
        "first_person_ratio": 0.15, "directive_ratio": 0.02,
        "word_count": 500,
    }
    b.update(overrides)
    return b


# ------------------------------------------------------------------
# Baseline behaviour, no prior coverage — locking in what already works
# ------------------------------------------------------------------

def test_includes_hedge_density_target():
    result = _build_restoration_targets(_baseline())
    # max(baseline['hedge_density'], 0.5) floor -- 0.04 is well below
    # that floor, so 0.5 is the value actually used, not the raw input.
    assert "Hedge density: 0.5% per 100 words" in result


def test_includes_sentence_rhythm_target():
    result = _build_restoration_targets(_baseline())
    assert "Sentence rhythm: SD 7.0 words" in result


def test_includes_word_count_and_confidence_note():
    result = _build_restoration_targets(_baseline())
    assert "Based on 500 words" in result


def test_ends_with_the_specification_framing_line():
    result = _build_restoration_targets(_baseline())
    assert "Treat these as specifications you are being measured against, not style suggestions." in result


def test_directive_target_included_when_rate_meaningful():
    result = _build_restoration_targets(_baseline(directive_ratio=0.1), input_has_directive_content=True)
    assert "Directness: 10% of sentences are action statements" in result


def test_directive_target_omitted_when_rate_low():
    result = _build_restoration_targets(_baseline(directive_ratio=0.02), input_has_directive_content=True)
    assert "low imperative rate in baseline" in result


def test_directive_instructed_not_to_be_invented_when_input_lacks_it():
    result = _build_restoration_targets(_baseline(directive_ratio=0.1), input_has_directive_content=False)
    assert "purely descriptive" in result
    assert "Do not invent any" in result


def test_ownership_instructed_not_to_be_invented_when_input_lacks_it():
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=False)
    assert "no first-person claims, opinions, or reactions" in result
    assert "Do not add any" in result


# ------------------------------------------------------------------
# The new impersonal-statement guardrail (18 Aug 2026)
# ------------------------------------------------------------------

def test_ownership_target_forbids_converting_impersonal_statements():
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=True)
    assert "Do NOT convert a general, impersonal statement" in result


def test_ownership_guardrail_uses_the_real_failing_example_verbatim():
    """Not a generic example — the literal sentence from the live
    failure this guardrail was built against. Deliberately verbatim,
    not paraphrased, so the model sees the exact case it got wrong."""
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=True)
    assert '"Nobody finds X"' in result


def test_ownership_guardrail_explicitly_permits_undershooting():
    """The other half of the fix: without explicit permission to fall
    short of the target, an instruction that only says "don't fabricate"
    still leaves the model with no acceptable way to reconcile a
    target it can't reach honestly -- it has to choose between
    violating the rate or violating the "don't convert" rule. Making
    undershoot explicitly correct resolves that tension."""
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=True)
    assert "undershoot it" in result
    assert "that is correct behaviour, not a miss" in result


def test_ownership_guardrail_only_applies_when_input_has_opinion_content():
    """The guardrail is a refinement of the 'input HAS opinion content'
    branch specifically -- the separate 'input has NO opinion content'
    branch already has its own, stricter 'do not add any' instruction
    and must not also carry this guardrail's wording (which would be
    redundant/confusing in that branch)."""
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=False)
    assert "Do NOT convert a general, impersonal statement" not in result


def test_attribution_reassignment_warning_still_present_alongside_new_guardrail():
    """Regression guard: the new guardrail was inserted into the
    middle of the existing ownership instruction -- the pre-existing
    attribution-swap warning (a distinct, already-tested-elsewhere
    concern) must survive intact, not get silently dropped or
    overwritten by the insertion."""
    result = _build_restoration_targets(_baseline(), input_has_opinion_content=True)
    assert 'do not turn "your point" into "my point"' in result
    assert "this is a meaning change, not a voice adjustment" in result
