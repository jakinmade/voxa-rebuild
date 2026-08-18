"""
Tests for the AI-Slop Firewall feature (18 Aug 2026 roadmap item 3) —
the visible 'N found' phrase list and one-click 'Clean it up' action
on the render screen.

Two things under test:
1. ai_tell_phrases actually reaching build_voice_report's output
   (voice_engine.py) — the raw, individual phrase list the UI needs,
   distinct from ai_tell_flags' pre-joined display-string list.
2. _clean_ai_tells_and_rescore (app.py) — the button's handler, which
   re-runs the already-trusted _regex_sweep mechanism and re-scores,
   mutating session_state in place. No new removal logic is being
   tested here — that's _regex_sweep's own, already-covered job; this
   guards the wiring around it (state read/write correctness).

Session-state fixture pattern matches test_persistence.py's
established convention — a clean slate between tests, since
session_state otherwise leaks across the suite.
"""
import streamlit as st
import pytest

import app
import voice_engine as ve


@pytest.fixture(autouse=True)
def _reset_session_state():
    st.session_state.clear()
    yield
    st.session_state.clear()


# ------------------------------------------------------------------
# ai_tell_phrases reaching build_voice_report
# ------------------------------------------------------------------

def test_ai_tell_phrases_present_when_tells_flagged():
    ai_tells = ve.score_ai_tells("We will leverage this holistic synergy.")
    report = ve.build_voice_report(
        {}, {"semantic_match": 90, "dropped_entities": [], "attribution_swaps": []},
        "Low", "Low", ai_tells,
    )
    assert report["ai_tell_phrases"] == ["holistic", "leverage", "synergy"]


def test_ai_tell_phrases_empty_when_clean():
    ai_tells = ve.score_ai_tells("This is plain, ordinary text.")
    report = ve.build_voice_report(
        {}, {"semantic_match": 100, "dropped_entities": [], "attribution_swaps": []},
        "Low", "Low", ai_tells,
    )
    assert report["ai_tell_phrases"] == []


def test_ai_tell_phrases_empty_when_ai_tells_is_none():
    """build_voice_report already handles ai_tells=None elsewhere
    (defaults ai_tell_clean to True) -- the new field must degrade
    the same way, not raise."""
    report = ve.build_voice_report(
        {}, {"semantic_match": 100, "dropped_entities": [], "attribution_swaps": []},
        "Low", "Low", None,
    )
    assert report["ai_tell_phrases"] == []


# ------------------------------------------------------------------
# _clean_ai_tells_and_rescore
# ------------------------------------------------------------------

def _seed_dirty_render(keep_contractions=False):
    st.session_state["render_output"] = (
        "We will leverage this holistic synergy to deliver a seamless result."
    )
    st.session_state["render_input_text"] = "placeholder input"
    st.session_state["render_keep_contractions"] = keep_contractions
    st.session_state["voice_report"] = {
        "voice_match": 80, "voice_match_tier": "Good", "voice_match_badge": "badge-green",
        "voice_match_evidence": "", "semantic_match": 90, "confidence": "Low", "risk": "High",
        "ai_tell_clean": False,
        "ai_tell_flags": ["AI-typical phrasing found: holistic, leverage, seamless, synergy"],
        "ai_tell_phrases": ["holistic", "leverage", "seamless", "synergy"],
        "biggest_changes": [], "dropped_entities": [], "attribution_swaps": [],
    }


def test_clean_reduces_flagged_phrases_to_empty():
    _seed_dirty_render()
    app._clean_ai_tells_and_rescore()
    assert st.session_state["voice_report"]["ai_tell_phrases"] == []


def test_clean_sets_ai_tell_clean_true():
    _seed_dirty_render()
    app._clean_ai_tells_and_rescore()
    assert st.session_state["voice_report"]["ai_tell_clean"] is True


def test_clean_updates_render_output_in_place():
    _seed_dirty_render()
    original = st.session_state["render_output"]
    app._clean_ai_tells_and_rescore()
    assert st.session_state["render_output"] != original
    # Confirm the flagged words are actually gone from the text itself,
    # not just from the score -- the score changing without the text
    # changing would be a worse bug than either alone.
    cleaned_lower = st.session_state["render_output"].lower()
    assert "leverage" not in cleaned_lower
    assert "holistic" not in cleaned_lower
    assert "seamless" not in cleaned_lower
    assert "synergy" not in cleaned_lower


def test_clean_uses_persisted_keep_contractions_not_a_fresh_default():
    """render_keep_contractions must be read from session_state (set
    at render time), not silently defaulted -- confirms the value
    actually reaches _regex_sweep by checking a contraction survives
    when keep_contractions=True was persisted."""
    st.session_state["render_output"] = "We can't leverage this, it's holistic."
    st.session_state["render_input_text"] = "placeholder"
    st.session_state["render_keep_contractions"] = True
    st.session_state["voice_report"] = {
        "ai_tell_clean": False,
        "ai_tell_flags": [], "ai_tell_phrases": ["leverage", "holistic"],
        "voice_match_tier": "Good", "voice_match_badge": "badge-green",
        "voice_match_evidence": "", "semantic_match": 90, "confidence": "Low", "risk": "High",
        "biggest_changes": [], "dropped_entities": [], "attribution_swaps": [],
    }
    app._clean_ai_tells_and_rescore()
    assert "can't" in st.session_state["render_output"] or "it's" in st.session_state["render_output"]


def test_clean_does_nothing_when_render_output_empty():
    st.session_state["render_output"] = ""
    st.session_state["voice_report"] = {"ai_tell_phrases": ["leverage"]}
    app._clean_ai_tells_and_rescore()
    # Must not raise, and must not fabricate a report from nothing.
    assert st.session_state["render_output"] == ""
    assert st.session_state["voice_report"]["ai_tell_phrases"] == ["leverage"]


def test_clean_does_nothing_when_no_voice_report_exists():
    """A defensive path -- render_output present but voice_report
    missing entirely (shouldn't normally happen, but must not crash)."""
    st.session_state["render_output"] = "We will leverage this."
    st.session_state["render_input_text"] = ""
    st.session_state["render_keep_contractions"] = False
    # No voice_report key set at all.
    app._clean_ai_tells_and_rescore()
    # Should not raise. render_output may still update (the sweep runs
    # regardless), but there's no report to update.
    assert "voice_report" not in st.session_state or st.session_state.get("voice_report") is None


def test_clean_respects_original_input_text_exemption():
    """The cleaned re-score must still honour the original_input_text
    exemption (18 Aug 2026 fix) -- a genuine phrase from the person's
    own original input must not get flagged again after cleaning
    removes the fabricated ones around it."""
    st.session_state["render_output"] = "Curious whether your clients solved that, and we will leverage this."
    st.session_state["render_input_text"] = "Curious whether your clients solved that."
    st.session_state["render_keep_contractions"] = False
    st.session_state["voice_report"] = {
        "ai_tell_clean": False,
        "ai_tell_flags": [], "ai_tell_phrases": ["curious whether", "leverage"],
        "voice_match_tier": "Good", "voice_match_badge": "badge-green",
        "voice_match_evidence": "", "semantic_match": 90, "confidence": "Low", "risk": "High",
        "biggest_changes": [], "dropped_entities": [], "attribution_swaps": [],
    }
    app._clean_ai_tells_and_rescore()
    # "leverage" (genuinely fabricated) must be gone; "curious whether"
    # was never a real problem in the first place (present in original).
    assert "curious whether" not in st.session_state["voice_report"]["ai_tell_phrases"]
    assert "leverage" not in st.session_state["voice_report"]["ai_tell_phrases"]
