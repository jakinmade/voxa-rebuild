"""
Tests for voice_engine.detect_lexical_fidelity_breaks and its wiring
into score_semantic_drift / build_voice_report.

Root cause this exists to catch: the LEXICAL FIDELITY prompt
instruction (32b93ca, prompts.py) tells the model not to substitute
synonyms without a voice-target reason, but a prompt instruction has
no code-level backstop. A live render swapped "surfaces" -> "brings
up" in "It surfaces when someone finally asks..." - "brings up" is
transitive and needs an object, so used intransitively it breaks
grammar, while "surfaces" was already correct and needed no change.
These tests confirm the small curated watchlist that catches known-bad
pairs like this, and that it stays informational-only (unlike
attribution_swaps/dropped_entities it must NOT force High risk or gate
delivery - JA, 19 Aug 2026: "flag it for review rather than block").
"""
import voice_engine as ve


# ---------------------------------------------------------------------------
# detect_lexical_fidelity_breaks - the core detector
# ---------------------------------------------------------------------------

def test_surfaces_to_brings_up_swap_is_detected():
    input_text = "It surfaces when someone finally asks who decided this."
    output_text = "It brings up when someone finally asks who decided this."
    flags = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    assert len(flags) == 1
    assert "surfaces" in flags[0] and "brings up" in flags[0]


def test_no_flag_when_original_word_kept():
    input_text = "It surfaces when someone finally asks who decided this."
    output_text = "It surfaces when someone finally asks who decided this."
    flags = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    assert flags == []


def test_no_flag_when_original_absent_from_input():
    # "brings up" appearing in the output means nothing on its own if
    # the input never said "surfaces" in the first place.
    input_text = "It comes up when someone finally asks who decided this."
    output_text = "It brings up when someone finally asks who decided this."
    flags = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    assert flags == []


def test_no_flag_when_replacement_not_present():
    # Original word changed to something outside the watchlist entirely
    # - not this detector's job to catch every possible rewrite.
    input_text = "It surfaces when someone finally asks who decided this."
    output_text = "It appears when someone finally asks who decided this."
    flags = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    assert flags == []


def test_deterministic():
    input_text = "It surfaces when someone finally asks who decided this."
    output_text = "It brings up when someone finally asks who decided this."
    r1 = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    r2 = ve.detect_lexical_fidelity_breaks(input_text, output_text)
    assert r1 == r2


def test_no_watchlist_words_no_flags():
    flags = ve.detect_lexical_fidelity_breaks(
        "This is a plain sentence with no watchlist words.",
        "This is a plain sentence with no watchlist words."
    )
    assert flags == []


# ---------------------------------------------------------------------------
# score_semantic_drift - carries the new field through
# ---------------------------------------------------------------------------

def test_semantic_drift_carries_lexical_fidelity_breaks():
    input_text = "It surfaces when someone finally asks who decided this, and I mean that."
    output_text = "It brings up when someone finally asks who decided this, and I mean that."
    result = ve.score_semantic_drift(input_text, output_text)
    assert result["lexical_fidelity_breaks"]
    assert "surfaces" in result["lexical_fidelity_breaks"][0]


def test_semantic_drift_empty_lexical_fidelity_breaks_when_clean():
    input_text = "This is a plain sentence with no watchlist words, and I mean that."
    output_text = "This is a plain sentence with no watchlist words, and I mean that."
    result = ve.score_semantic_drift(input_text, output_text)
    assert result["lexical_fidelity_breaks"] == []


# ---------------------------------------------------------------------------
# compute_risk - a watchlist hit must NOT force High risk (informational
# only, unlike attribution_swaps/dropped_entities/ai_tells)
# ---------------------------------------------------------------------------

def test_lexical_fidelity_break_does_not_force_high_risk():
    delta = {}
    semantic = {
        "semantic_match": 97,
        "attribution_swaps": [],
        "dropped_entities": [],
        "lexical_fidelity_breaks": ["'surfaces' became 'brings up' - ..."],
    }
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "Low"


def test_lexical_fidelity_break_does_not_trip_content_integrity_hard_fail():
    semantic = {
        "attribution_swaps": [],
        "dropped_entities": [],
        "lexical_fidelity_breaks": ["'surfaces' became 'brings up' - ..."],
    }
    assert ve.has_content_integrity_hard_fail(semantic, ai_tells={"clean": True}) is False


# ---------------------------------------------------------------------------
# build_voice_report - flags surface in the report dict for the UI
# ---------------------------------------------------------------------------

def test_voice_report_carries_lexical_fidelity_breaks_through():
    delta = {}
    semantic = {
        "semantic_match": 97, "dropped_entities": [], "attribution_swaps": [],
        "lexical_fidelity_breaks": ["'surfaces' became 'brings up' - ..."],
    }
    report = ve.build_voice_report(delta, semantic, confidence="High", risk="Low")
    assert report["lexical_fidelity_breaks"] == ["'surfaces' became 'brings up' - ..."]


def test_voice_report_empty_lexical_fidelity_breaks_when_none_found():
    delta = {}
    semantic = {
        "semantic_match": 97, "dropped_entities": [], "attribution_swaps": [],
        "lexical_fidelity_breaks": [],
    }
    report = ve.build_voice_report(delta, semantic, confidence="High", risk="Low")
    assert report["lexical_fidelity_breaks"] == []
