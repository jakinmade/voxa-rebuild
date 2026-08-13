"""
Tests for voxa_core.text_guardrail — the canonical guardrail sweep for
the packages/ ecosystem (voxa-rendering, voxa-api).

Root cause this exists to catch: an August 2026 audit found four
independent copies of this guardrail at four different levels of
completeness (app.py: 12/12 steps + verification; voxa_rendering/
cleaner.py: 2/12, no verification; voxa_api/recalibrate.py: ~4/12,
stale, no verification; voxa_api/rewrite.py: 0/12, no verification).
Any consumer hitting the FastAPI layer instead of the Streamlit app got
materially worse output with no warning, because nothing checked.

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
