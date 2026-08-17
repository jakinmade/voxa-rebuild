"""
Tests for _fix_entity_casing (deterministic_fixers.py) — the fixer that
restores case-only entity drift (e.g. "CLEARANCE" -> "Clearance")
without touching genuinely dropped content — plus score_semantic_drift's
own, more general fix for the same underlying blind spot.

Original incident (confirmed live): JA's brand name "CLEARANCE"
survived a rewrite as "Clearance" and was flagged identically to a
genuinely vanished word ("Curious", reworded away entirely) by
score_semantic_drift's case-sensitive set comparison. _fix_entity_casing
was built to draw that distinction and repair the text.

SECOND incident (17 Aug 2026, live): the same blind spot surfaced
differently. "Curious" as a sentence-initial word in the input was
relocated to mid-sentence and correctly lowercased in the rewrite -
the word itself survived, just moved and recapitalised, which is
correct English, not a drop. But _entities_and_numbers only ever
extracts CAPITALISED words, so lowercase "curious" was invisible to
both score_semantic_drift's set comparison AND _fix_entity_casing's
own restoration logic (which also depends on that same extraction).
Neither could see it. Fixed by changing score_semantic_drift to check
literal case-insensitive word presence in the output TEXT directly,
not membership in the output's extracted entity set - which as a side
effect also makes it catch case-only drift like CLEARANCE -> Clearance
on its own, without needing _fix_entity_casing to run first.
_fix_entity_casing keeps its own job: actually restoring correct
casing in the text a person reads, a text-mutation concern separate
from whether the drop-detection score counts a word as preserved.
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


def test_score_semantic_drift_now_catches_case_drift_without_the_fixer():
    """score_semantic_drift was strengthened (17 Aug 2026, following a
    live false-positive where "Curious" - genuinely present in a
    rewrite, just relocated and correctly lowercased mid-sentence -
    was flagged as dropped) to check literal case-insensitive word
    presence in the output TEXT directly, not just membership in the
    output's extracted (capitalised-only) entity set. A side effect:
    it now also catches case-only drift like CLEARANCE -> Clearance
    on its own, without needing _fix_entity_casing to run first -
    the old approach could only ever restore/recognise a case
    variant if it happened to still be extractable (capitalised) in
    the output, the exact same blind spot that caused the Curious
    false positive. This is a strict improvement in coverage, not a
    regression - see test_leaves_genuine_drop_untouched and
    test_mixed_case_only_and_genuine_drop_in_same_render above for
    _fix_entity_casing's own (still valid, still necessary) job:
    actually restoring the correct casing in the text the person
    reads, which is a text-mutation concern score_semantic_drift
    doesn't and shouldn't take on."""
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

    # CLEARANCE is now recognised as preserved by score_semantic_drift
    # alone - "Clearance" (case-insensitive) is literally present in
    # the output text, so it's no longer counted as dropped even
    # before _fix_entity_casing does anything.
    before = score_semantic_drift(original, rewrite)
    assert "CLEARANCE" not in before["dropped_entities"]
    # "Curious" genuinely doesn't survive anywhere in THIS rewrite
    # (reworded away entirely, unlike the live render where the word
    # itself survived relocated) - correctly still flagged.
    assert "Curious" in before["dropped_entities"]

    # _fix_entity_casing still does its own job: actually restoring
    # CLEARANCE's correct casing in the text a person reads, which is
    # a separate concern from whether score_semantic_drift's count
    # treats the word as preserved.
    fixed, restored, still_dropped = _fix_entity_casing(rewrite, original)
    assert "CLEARANCE" in fixed
    assert restored == ["CLEARANCE"]

    after = score_semantic_drift(original, fixed)
    assert "CLEARANCE" not in after["dropped_entities"]
    assert "Curious" in after["dropped_entities"]


def test_curious_relocated_and_lowercased_is_not_a_false_positive():
    """The actual live incident this fix closes: "Curious" as the
    first word of a sentence in the input, correctly relocated to
    mid-sentence and lowercased in the rewrite ("...I am curious,
    worth another look..."). The word survives - this must not be
    flagged as dropped, unlike the case above where it's genuinely
    reworded away with no trace."""
    original = (
        "Curious if you got a chance to run it or if it fell off the "
        "desk with everything going on."
    )
    rewrite = (
        "I am curious, worth another look, or should I just send you "
        "a fresh report so you are not chasing the old link?"
    )
    result = score_semantic_drift(original, rewrite)
    assert "Curious" not in result["dropped_entities"]
