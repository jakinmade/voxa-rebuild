"""
Tests for voxa_core.text_guardrail — the canonical guardrail sweep.

Root cause this exists to catch: an August 2026 audit found duplicate,
divergent copies of this guardrail at different levels of completeness
across the codebase (app.py: 12/12 steps + verification; a since-removed
FastAPI layer's copies were materially worse, at 0-4/12 with no
verification, and shipped no warning to any caller reaching them).
The FastAPI layer and its package (packages/voxa-api, packages/
voxa-rendering) were confirmed unreachable from the live Streamlit app
and removed entirely; this guardrail module and its tests remain because
app.py itself still uses it.

This file has two jobs:
  1. Prove the ported voxa_core.text_guardrail module produces byte-
     identical output to the current root-level prompts.py/voice_engine.py
     on the same inputs, catching any transcription drift introduced
     during the port itself.
  2. Cover the module's own behaviour directly, so a future change to
     either copy that isn't mirrored in the other shows up as a test
     failure, not silent drift six months later.

Parity tests import from the root-level modules directly, which only
works when tests run from the repo root (as the existing suite already
assumes throughout tests/unit/).
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from voxa_core.text_guardrail import sweep, score_ai_tells


# ---------------------------------------------------------------------
# Parity with root-level prompts.py / voice_engine.py — the whole point
# of this module existing is that it matches, not diverges.
# ---------------------------------------------------------------------

PARITY_CASES = [
    "",
    "This is one thing—and this is another.",
    "We will leverage this to deliver a seamless result.",
    "I see it as the deterministic proof layer underneath the governance "
    "point from our earlier thread.",
    "This, in my view, is right, and that, as I see it, is wrong.",
    "This works, in my view",
    "I think that this is the strongest angle we have.",
    "Don't worry, it isn't a problem. It's fine, we're on it.",
    "This is unmatched, though it might vary in some regions.",
    "The market drifts toward new equilibria, and I suspect this pattern "
    "surfaces again.",
    "A rising tide. A falling curve. A shifting balance.",
    "That framing might be too simple to capture the whole picture.",
    "in todays landscape, we must leverage our synergies to unlock the "
    "potential of our ecosystem.",
    "The pressures are not small, and this, in my opinion, will matter "
    "a lot going forward.",
    "Hi Josh,.",
    "It was fine, , actually good, e.g., in banking.",
]


def test_sweep_matches_root_prompts_on_every_case():
    from prompts import _regex_sweep as root_sweep

    for text in PARITY_CASES:
        assert sweep(text) == root_sweep(text), f"drift on: {text!r}"


def test_score_ai_tells_matches_root_voice_engine():
    from voice_engine import score_ai_tells as root_score_ai_tells

    cases = [
        "We will leverage this to deliver a seamless result.",
        "I see it as the deterministic proof layer.",
        "This, in my view, is worth another look.",
        "This is clean, plain text with no tells at all.",
    ]
    for text in cases:
        assert score_ai_tells(text) == root_score_ai_tells(text), f"drift on: {text!r}"


def test_score_ai_tells_original_input_text_exemption_matches_root():
    """Real drift this exact test-and-fix cycle caught (18 Aug 2026):
    the ORIGINAL parity test above only ever exercised the zero-
    argument case, so it never noticed original_input_text was
    entirely absent from this file's score_ai_tells while root had
    already added it. Extends parity coverage to the exemption path
    specifically, not just the base case, so the same gap can't recur
    silently a second time."""
    from voice_engine import score_ai_tells as root_score_ai_tells

    cases = [
        # (rendered_text, original_input_text)
        ("Curious whether your clients have solved that.",
         "Curious whether your clients have solved that."),
        ("So I suspect this is not quite right.",
         "So I suspect this is not quite right."),
        ("Curious whether that framing lands for you.",
         "Hi John, thanks for the update."),  # NOT in original -- must still flag
    ]
    for text, original in cases:
        assert score_ai_tells(text, original_input_text=original) == \
            root_score_ai_tells(text, original_input_text=original), \
            f"drift on: {text!r} / {original!r}"


def test_flagged_phrases_field_matches_root():
    """Real drift found while adding this field to root for the
    AI-Slop Firewall UI feature (18 Aug 2026): confirms the ported
    copy's new field matches structurally, not just that both files
    happen to compile."""
    from voice_engine import score_ai_tells as root_score_ai_tells

    text = "We will leverage this holistic, seamless synergy."
    result = score_ai_tells(text)
    root_result = root_score_ai_tells(text)
    assert "flagged_phrases" in result
    assert result["flagged_phrases"] == root_result["flagged_phrases"]
    assert isinstance(result["flagged_phrases"], list)
    assert len(result["flagged_phrases"]) > 0


# ---------------------------------------------------------------------
# Direct coverage — the module's own behaviour, independent of parity,
# so a change here that breaks something is caught even if the parity
# check is ever removed or the root copy changes first.
# ---------------------------------------------------------------------

def test_em_dash_split_into_sentence():
    result = sweep("This is one thing—and this is another.")
    assert "\u2014" not in result
    assert " - " not in result


def test_claude_construction_replaced():
    result = sweep("We will leverage this to deliver a seamless result.")
    assert "leverage" not in result.lower()
    assert "seamless" not in result.lower()


def test_plausibility_shield_replace_shape():
    result = sweep("I see it as the deterministic proof layer.")
    assert result == "It is the deterministic proof layer."


def test_plausibility_shield_drop_shape():
    result = sweep("I think that this is the strongest angle we have.")
    assert result == "This is the strongest angle we have."


def test_plausibility_shield_midsentence_no_orphan_punctuation():
    result = sweep("This, in my view, is right, and that, as I see it, is wrong.")
    assert result == "This is right, and that is wrong."


def test_contractions_expanded_by_default():
    result = sweep("It's fine, we're on it.")
    assert "it is fine" in result.lower()
    assert "we are on it" in result.lower()


def test_contractions_kept_when_flagged():
    result = sweep("It's fine, we're on it.", keep_contractions=True)
    assert "it's" in result.lower()


def test_absolute_claim_hedge_stripped():
    result = sweep("This is unmatched, though it might vary in some regions.")
    assert "though it might vary" not in result


def test_score_ai_tells_flags_surviving_construction():
    result = score_ai_tells("We will leverage this to deliver a seamless result.")
    assert result["clean"] is False


def test_score_ai_tells_clean_on_plain_text():
    result = score_ai_tells("This is clean, plain text with no tells at all.")
    assert result["clean"] is True


def test_sweep_handles_empty_string():
    assert sweep("") == ""


def test_sweep_is_deterministic():
    text = "We will leverage this seamless approach, in my view."
    assert sweep(text) == sweep(text)


# ---------------------------------------------------------------------
# Orphan/doubled punctuation cleanup — confirmed live: "Hi Josh,."
# shipped from the upstream LLM grammar stage (not this module)
# inserting a name before a salutation comma and mishandling the
# close. This sweep runs after that stage every time, so it's the
# general catch, not a patch for one salutation. Mirrored fix in
# prompts.py's _regex_sweep - parity test above already covers this
# case; these test the behaviour directly.
# ---------------------------------------------------------------------

def test_orphan_comma_period_collapsed():
    # UPDATED 29 Aug 2026: "Hi Josh,." used to collapse only as far as
    # "Hi Josh." (orphan ",." cleanup alone). Now that the salutation-
    # comma-restoration step (mirrors prompts.py's sweep step 12) runs
    # after it, the correct final output restores the comma - this
    # was the actual bug the whole feature existed to catch, and this
    # exact input is the historically-confirmed live case that
    # originally motivated the ",." cleanup in the first place.
    assert sweep("Hi Josh,.") == "Hi Josh,"


def test_orphan_comma_period_mid_sentence():
    result = sweep("The point stands,. It holds.")
    assert ",." not in result
    assert result == "The point stands. It holds."


def test_doubled_comma_collapsed():
    result = sweep("It was fine, , actually good.")
    assert ",," not in result
    assert result == "It was fine, actually good."


def test_space_before_terminal_punctuation_removed():
    assert sweep("The word , was misplaced.") == "The word, was misplaced."
    assert sweep("End of sentence . New one.") == "End of sentence. New one."


def test_abbreviation_period_comma_survives():
    """The reverse direction (".," -> ",") is deliberately NOT applied
    — 'e.g.,' 'i.e.,' 'etc.,' are correct abbreviation-plus-comma
    sequences that happen to contain '.,' and collapsing them would
    corrupt real abbreviations rather than fix an error."""
    for text in (
        "This is common, e.g., in banking.",
        "Check the docs, i.e., the handbook.",
        "Various tools, etc., were used.",
    ):
        result = sweep(text)
        assert "e.g.," in result or "i.e.," in result or "etc.," in result
        assert result == text
