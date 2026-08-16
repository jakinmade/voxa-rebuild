"""
Tests for _fix_entity_casing (deterministic_fixers.py) — the fixer that
restores case-only entity drift (e.g. "CLEARANCE" -> "Clearance")
without touching genuinely dropped content.

Confirmed live against a real render (see the function's own docstring):
JA's brand name "CLEARANCE" survived a rewrite as "Clearance" and was
flagged identically to a genuinely vanished word ("Curious", reworded
away entirely) by score_semantic_drift's case-sensitive set comparison.
These tests pin the distinction the fixer is responsible for drawing.
"""
from deterministic_fixers import _fix_entity_casing
from voice_engine import _entities_and_numbers, score_semantic_drift


def test_restores_case_only_drift():
    input_text = "The CLEARANCE report is ready for Scott."
    output_text = "The Clearance report is ready for Scott."

    fixed, restored, still_dropped = _fix_entity_casing(output_text, input_text)

    assert "CLEARANCE" in fixed
    assert "Clearance" not in fixed
    assert restored == ["CLEARANCE"]
    assert still_dropped == []


def test_leaves_genuine_drop_untouched():
    input_text = "Curious if you got a chance to run it."
    output_text = "Did you get a chance to run it?"

    fixed, restored, still_dropped = _fix_entity_casing(output_text, input_text)

    # "Curious" has no case-insensitive match anywhere in the output —
    # the fixer must not invent a placement for it. That's the LLM
    # correction path's job (build_correction_prompt's "add it back
    # in naturally" instruction), not this fixer's.
    assert fixed == output_text
    assert restored == []
    assert "Curious" in still_dropped


def test_mixed_case_only_and_genuine_drop_in_same_render():
    """The exact shape of JA's live render: one case-only drift, one
    genuine drop, in the same input/output pair."""
    input_text = (
        "Scott, the CLEARANCE test link is ready. Curious if you had "
        "a chance to look."
    )
    output_text = (
        "Scott, the Clearance test link is ready. Did you have a "
        "chance to look?"
    )

    fixed, restored, still_dropped = _fix_entity_casing(output_text, input_text)

    assert "CLEARANCE" in fixed
    assert restored == ["CLEARANCE"]
    assert still_dropped == ["Curious"]


def test_restores_all_occurrences_not_just_first():
    input_text = "CLEARANCE is live. CLEARANCE covers SEC and FINRA."
    output_text = "Clearance is live. Clearance covers SEC and FINRA."

    fixed, restored, _ = _fix_entity_casing(output_text, input_text)

    assert fixed.count("CLEARANCE") == 2
    assert "Clearance" not in fixed


def test_no_entities_dropped_returns_text_unchanged():
    input_text = "Scott, the report is ready."
    output_text = "Scott, the report is ready for review."

    fixed, restored, still_dropped = _fix_entity_casing(output_text, input_text)

    assert fixed == output_text
    assert restored == []
    assert still_dropped == []


def test_does_not_touch_unrelated_words_sharing_a_substring():
    """A whole-word, case-insensitive match only — must not corrupt a
    longer word that happens to contain the dropped entity's letters."""
    input_text = "SEC issued new guidance."
    output_text = "The section on guidance was updated. SEC rules apply."

    fixed, restored, still_dropped = _fix_entity_casing(output_text, input_text)

    # "SEC" is present verbatim in output already (case-sensitive
    # match), so it's not even in the dropped set to begin with —
    # confirming the whole-word boundary means "section" was never at
    # risk of being mistaken for a case variant of "SEC".
    assert "section" in fixed
    assert restored == []
    assert still_dropped == []


def test_integration_with_score_semantic_drift_on_real_render_pair():
    """End-to-end: applying the fixer before re-scoring should remove
    the case-only entity from dropped_entities entirely, leaving only
    the genuine drop — reproducing JA's actual live render."""
    original = (
        'Scott — following up on the CLEARANCE test link from a while '
        'back. Curious if you got a chance to run it or if it fell off '
        'the desk with everything going on.'
    )
    rewrite = (
        'Scott, following up on the Clearance test link I sent a while '
        'back. Did you get a chance to run it, or did it fall off the '
        'desk with everything going on?'
    )

    before = score_semantic_drift(original, rewrite)
    assert "CLEARANCE" in before["dropped_entities"]
    assert "Curious" in before["dropped_entities"]

    fixed, restored, still_dropped = _fix_entity_casing(rewrite, original)
    after = score_semantic_drift(original, fixed)

    assert "CLEARANCE" not in after["dropped_entities"]
    assert "Curious" in after["dropped_entities"]
    # Entity preservation, and therefore semantic_match, should improve
    # once the false case-only drop is no longer counted against it.
    assert after["semantic_match"] >= before["semantic_match"]
