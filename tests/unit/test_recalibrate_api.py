"""
Tests for voxa_api.recalibrate._regex_sweep.

No dedicated test coverage existed for this function before this file —
found during the August 2026 guardrail-consolidation audit. The function
used to be a local, stale port (14-entry Claude-construction list vs the
live app's 46, no hedge stripping, no plausibility-shield removal, no
AI-tell verification) that had silently drifted since being "ported
verbatim" on 25 July 2026. It now delegates to voxa_core.text_guardrail,
the canonical implementation — these tests confirm the delegation
actually happened and behaves correctly, not just that the function
runs without raising.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from voxa_api.recalibrate import _regex_sweep


def test_delegates_to_canonical_sweep_em_dash():
    result = _regex_sweep("This is one thing—and this is another.")
    assert "\u2014" not in result
    assert " - " not in result


def test_delegates_to_canonical_sweep_construction_replaced():
    result = _regex_sweep("We will leverage this to deliver a seamless result.")
    assert "leverage" not in result.lower()
    assert "seamless" not in result.lower()


def test_delegates_to_canonical_sweep_plausibility_shield():
    # The specific gap the old 14-entry local copy had: no shield
    # stripping at all. Confirms it's now actually wired through.
    result = _regex_sweep("I see it as the deterministic proof layer.")
    assert result == "It is the deterministic proof layer."


def test_delegates_to_canonical_sweep_hedge_on_absolute_claim():
    # Also entirely absent from the old local copy.
    result = _regex_sweep("This is unmatched, though it might vary in some regions.")
    assert "though it might vary" not in result


def test_matches_voxa_core_text_guardrail_directly():
    from voxa_core.text_guardrail import sweep as core_sweep

    text = "We will leverage this seamless approach, in my view."
    assert _regex_sweep(text) == core_sweep(text)
