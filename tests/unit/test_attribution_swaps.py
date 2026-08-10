"""
Tests for voice_engine.detect_attribution_swaps and its wiring into
score_semantic_drift / compute_risk / build_voice_report.

Root cause this exists to catch: score_semantic_drift's content-word
overlap comparison has 'your' and 'my' in _STOPWORDS (correctly, for
ordinary style-vs-content scoring), which means 'your point' -> 'my
point' is invisible to it - 'point' survives, nothing dropped, 97%
semantic match reported. But that swap changes who a point is credited
to, which is a meaning change wearing a style change's clothes. These
tests confirm the dedicated, narrow check that exists specifically to
catch this class of error, and that it's treated as seriously as a
surviving em dash once found.
"""
import voice_engine as ve


# ---------------------------------------------------------------------------
# detect_attribution_swaps — the core detector
# ---------------------------------------------------------------------------

def test_your_to_my_swap_is_detected():
    input_text = "Banking half-proves your point already."
    output_text = "Banking half-proves my point already."
    swaps = ve.detect_attribution_swaps(input_text, output_text)
    assert len(swaps) == 1
    assert "point" in swaps[0]


def test_my_to_your_swap_is_detected():
    input_text = "I think my argument holds up here."
    output_text = "I think your argument holds up here."
    swaps = ve.detect_attribution_swaps(input_text, output_text)
    assert len(swaps) == 1


def test_no_swap_when_pronoun_unchanged():
    input_text = "Banking half-proves your point already."
    output_text = "Banking already half-proves your point."
    swaps = ve.detect_attribution_swaps(input_text, output_text)
    assert swaps == []


def test_no_swap_when_noun_not_present_on_both_sides():
    # 'your point' in input, nothing about 'point' at all in output -
    # that's a dropped entity/content concern, not an attribution swap.
    input_text = "Banking half-proves your point already."
    output_text = "Banking backs this up nicely."
    swaps = ve.detect_attribution_swaps(input_text, output_text)
    assert swaps == []


def test_both_your_and_my_present_for_same_noun_is_not_flagged():
    # Ambiguous case - if the output still contains 'your point'
    # anywhere alongside a new 'my point', this isn't a clean swap,
    # it's more nuanced text change. Deliberately conservative: only
    # flag the clear-cut case where the original pronoun is gone
    # entirely for that noun.
    input_text = "Your point is clear."
    output_text = "Your point is clear, though my point matters too."
    swaps = ve.detect_attribution_swaps(input_text, output_text)
    assert swaps == []


def test_no_possessives_no_swaps():
    swaps = ve.detect_attribution_swaps(
        "This is a plain sentence with no ownership language.",
        "This is a plain sentence with no ownership language."
    )
    assert swaps == []


def test_deterministic():
    input_text = "Banking half-proves your point already."
    output_text = "Banking half-proves my point already."
    r1 = ve.detect_attribution_swaps(input_text, output_text)
    r2 = ve.detect_attribution_swaps(input_text, output_text)
    assert r1 == r2


# ---------------------------------------------------------------------------
# score_semantic_drift — swap detected but headline score stays high
# (documents the exact blind spot this feature closes)
# ---------------------------------------------------------------------------

def test_semantic_drift_reports_high_match_despite_swap():
    input_text = "Banking half-proves your point already, and I mean that."
    output_text = "Banking half-proves my point already, and I mean that."
    result = ve.score_semantic_drift(input_text, output_text)
    # The headline number is misleadingly high - that's the bug this
    # feature is compensating for, not something this change fixes on
    # its own. attribution_swaps is what actually catches it.
    assert result["semantic_match"] >= 90
    assert result["attribution_swaps"]


# ---------------------------------------------------------------------------
# compute_risk — attribution swap is a hard fail, same tier as an AI tell
# ---------------------------------------------------------------------------

def test_attribution_swap_forces_high_risk_even_with_perfect_scores():
    delta = {}  # no voice-dimension misses
    semantic = {"semantic_match": 97, "attribution_swaps": ["'your point' became 'my point'"]}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "High"


def test_no_swap_no_forced_high_risk():
    delta = {}
    semantic = {"semantic_match": 97, "attribution_swaps": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "Low"


def test_ai_tell_still_forces_high_even_without_swap():
    # Confirms the new check is additive, not a replacement for the
    # existing hard-fail path.
    delta = {}
    semantic = {"semantic_match": 97, "attribution_swaps": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": False})
    assert risk == "High"


# ---------------------------------------------------------------------------
# build_voice_report — swaps surface in the report dict for the UI
# ---------------------------------------------------------------------------

def test_voice_report_carries_attribution_swaps_through():
    delta = {}
    semantic = {
        "semantic_match": 97, "dropped_entities": [],
        "attribution_swaps": ["'your point' became 'my point'"],
    }
    report = ve.build_voice_report(delta, semantic, confidence="Low", risk="High")
    assert report["attribution_swaps"] == ["'your point' became 'my point'"]


def test_voice_report_empty_swaps_when_none_found():
    delta = {}
    semantic = {"semantic_match": 97, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, confidence="High", risk="Low")
    assert report["attribution_swaps"] == []


# ---------------------------------------------------------------------------
# build_correction_prompt (prompts.py) — swap becomes the first, hardest
# instruction in the one automatic refinement pass
# ---------------------------------------------------------------------------

def test_correction_prompt_fires_on_swap_alone_with_no_voice_misses():
    import prompts as pr
    delta = {}  # nothing else needs correcting
    semantic = {"semantic_match": 97, "dropped_entities": [], "attribution_swaps": ["'your point' became 'my point'"]}
    prompt = pr.build_correction_prompt(delta, semantic)
    assert prompt is not None
    assert "CREDIT ERROR" in prompt
    assert "your point" in prompt and "my point" in prompt


def test_correction_prompt_none_when_nothing_to_fix():
    import prompts as pr
    delta = {}
    semantic = {"semantic_match": 100, "dropped_entities": [], "attribution_swaps": []}
    assert pr.build_correction_prompt(delta, semantic) is None


def test_correction_prompt_lists_swap_before_other_instructions():
    import prompts as pr
    delta = {
        "hedge_density": {"verdict": "MISSED", "baseline": 3.0, "output": 0.0, "delta": -3.0, "pct_diff": 1.0},
    }
    semantic = {"semantic_match": 97, "dropped_entities": [], "attribution_swaps": ["'my argument' became 'your argument'"]}
    prompt = pr.build_correction_prompt(delta, semantic)
    assert prompt is not None
    credit_pos = prompt.find("CREDIT ERROR")
    hedge_pos = prompt.find("Hedge density")
    assert credit_pos != -1 and hedge_pos != -1
    assert credit_pos < hedge_pos
