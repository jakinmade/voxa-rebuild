"""
scoring_rules.py is the single source of truth for every threshold
governing a render's Confidence/Risk verdict - extracted 16 Aug 2026
from inline magic numbers previously scattered across voice_engine.py
(HIT/CLOSE/MISSED bands, High/Medium risk cutoffs, semantic_match
weighting) and prompts.py (the AI-contamination path selector).

These tests exist to catch drift between the two, not to re-test the
scoring logic itself (already covered elsewhere, e.g.
test_burrows_delta.py, test_attribution_swaps.py) - if someone edits
a threshold back to an inline literal instead of through this module,
these should fail.
"""
import scoring_rules as sr
import voice_engine as ve
import prompts as pr


def test_version_is_a_semver_string():
    parts = sr.SCORING_RULES_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_version_bumped_for_reason_instrumentation():
    """1.1.0 added compute_risk_reason - a minor bump (new capability,
    no threshold changed), not a patch or a major. The live constant
    moves forward with each new entry (see the 1.2.0 test below for
    the current value) - this checks the CHANGELOG still documents
    1.1.0 rather than pinning it as the current version forever."""
    assert "1.1.0" in sr.__doc__


def test_version_bumped_for_review_gate_rule():
    """1.2.0 added REVIEW_REQUIRED_RISK_LEVELS - a minor bump (new
    business rule, no existing threshold changed), consumed by
    review_gate.py. The live constant moves forward with each new
    entry (see the 1.3.0 test below for the current value) - this
    checks the CHANGELOG still documents 1.2.0 rather than pinning it
    as the current version forever."""
    assert "1.2.0" in sr.__doc__
    assert hasattr(sr, "REVIEW_REQUIRED_RISK_LEVELS")
    assert sr.REVIEW_REQUIRED_RISK_LEVELS == {"Medium", "High"}


def test_version_bumped_for_firm_signal_rule():
    """1.3.0 added PERSONAL_EMAIL_DOMAINS - a minor bump (new business
    rule, no existing threshold changed), consumed by firm_signal.py.
    The live constant moves forward with each new entry (see the
    1.4.0 test below for the current value) - this checks the
    CHANGELOG still documents 1.3.0 rather than pinning it as the
    current version forever."""
    assert "1.3.0" in sr.__doc__
    assert hasattr(sr, "PERSONAL_EMAIL_DOMAINS")
    assert "gmail.com" in sr.PERSONAL_EMAIL_DOMAINS
    assert "yahoo.com" in sr.PERSONAL_EMAIL_DOMAINS


def test_version_bumped_for_delta_band_min_abs_diff():
    """1.4.0 added DELTA_BAND_MIN_ABS_DIFF - a minor bump (new rule
    alongside the existing percentage bands, not a retuned number
    within the same rule), consumed by voice_engine.score_render_delta."""
    assert sr.SCORING_RULES_VERSION == "1.4.0"
    assert hasattr(sr, "DELTA_BAND_MIN_ABS_DIFF")
    assert sr.DELTA_BAND_MIN_ABS_DIFF["hedge_density"] == 1.0
    assert sr.DELTA_BAND_MIN_ABS_DIFF["sentence_length_sd"] == 2.0
    assert sr.DELTA_BAND_MIN_ABS_DIFF["first_person_ratio"] == 0.10
    assert sr.DELTA_BAND_MIN_ABS_DIFF["directive_ratio"] == 0.10


def test_scoring_rules_version_function_matches_constant():
    assert sr.scoring_rules_version() == sr.SCORING_RULES_VERSION


def test_risk_thresholds_match_module_constants():
    delta = {}
    just_below_high = sr.RISK_HIGH_SEMANTIC_MATCH_BELOW - 1
    semantic = {"semantic_match": just_below_high, "attribution_swaps": [], "dropped_entities": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "High"


def test_risk_at_medium_boundary_matches_module_constant():
    delta = {}
    just_below_medium = sr.RISK_MEDIUM_SEMANTIC_MATCH_BELOW - 1
    semantic = {"semantic_match": just_below_medium, "attribution_swaps": [], "dropped_entities": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "Medium"


def test_ai_contamination_threshold_matches_module_constant():
    """At exactly the threshold, the AI-contaminated path (restoration
    block present) should be selected - confirms prompts.py reads
    AI_CONTAMINATION_PATH_THRESHOLD rather than a re-declared 0.25."""
    prompt = pr._build_system_prompt(
        voice_dna="THE STANDARD: sample.", mode_instruction="Rewrite.",
        word_count_input=20, ai_score=sr.AI_CONTAMINATION_PATH_THRESHOLD,
    )
    assert "STRIPPING INSTRUCTIONS" in prompt


# ---------------------------------------------------------------------------
# compute_risk_reason — instrumentation added in v1.1.0 so the aggregate
# bands (RISK_HIGH/MEDIUM_SEMANTIC_MATCH_BELOW) can eventually be
# recalibrated against real evidence of which check actually drives a
# verdict, rather than guessed at. Mirrors compute_risk's own check
# order exactly - if these two functions ever disagree on which check
# fired first, that's a real bug, so the tests below cross-check them
# on the same fixtures rather than only testing compute_risk_reason
# in isolation.
# ---------------------------------------------------------------------------

def test_reason_is_ai_tell_when_that_hard_fail_fires():
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": [], "dropped_entities": []}
    ai_tells = {"clean": False}
    assert ve.compute_risk(delta, semantic, ai_tells) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "ai_tell"


def test_reason_is_attribution_swap_when_that_hard_fail_fires():
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": ["'your point' became 'my point'"], "dropped_entities": []}
    ai_tells = {"clean": True}
    assert ve.compute_risk(delta, semantic, ai_tells) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "attribution_swap"


def test_reason_is_dropped_entity_when_that_hard_fail_fires():
    delta = {}
    semantic = {"semantic_match": 96, "attribution_swaps": [], "dropped_entities": ["Scott"]}
    ai_tells = {"clean": True}
    assert ve.compute_risk(delta, semantic, ai_tells) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "dropped_entity"


def test_reason_is_sentence_growth_when_that_hard_fail_fires():
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": [], "dropped_entities": []}
    ai_tells = {"clean": True}
    insertion_check = {"sentence_growth": 2}
    assert ve.compute_risk(delta, semantic, ai_tells, insertion_check) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells, insertion_check) == "sentence_growth"


def test_reason_is_aggregate_band_when_only_the_score_is_low():
    """The actual gap this instrumentation closes: confirms a verdict
    CAN be driven by the aggregate bands alone, with no hard-fail
    present - this is the case v1.0.0 had zero real examples of."""
    delta = {}
    semantic = {"semantic_match": 60, "attribution_swaps": [], "dropped_entities": []}
    ai_tells = {"clean": True}
    assert ve.compute_risk(delta, semantic, ai_tells) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "aggregate_band"


def test_reason_is_clean_when_nothing_fires():
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": [], "dropped_entities": []}
    ai_tells = {"clean": True}
    assert ve.compute_risk(delta, semantic, ai_tells) == "Low"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "clean"


def test_reason_checks_same_priority_order_as_compute_risk():
    """If both an AI tell AND a dropped entity are present, compute_risk
    returns High either way - but the REASON must match whichever
    check compute_risk's own if-chain hits first (ai_tell, since it's
    checked before dropped_entities), or the two functions would be
    silently describing different renders."""
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": [], "dropped_entities": ["Scott"]}
    ai_tells = {"clean": False}
    assert ve.compute_risk(delta, semantic, ai_tells) == "High"
    assert ve.compute_risk_reason(delta, semantic, ai_tells) == "ai_tell"


# ---------------------------------------------------------------------------
# DELTA_BAND_MIN_ABS_DIFF — added 1.4.0, prompted by a confirmed-live
# render (CLEARANCE outreach to Scott, 17 Aug 2026) that scored High
# risk largely because hedge_density's baseline sat near zero, turning
# a trivial absolute move into a 100% pct_diff. Pinned here as a
# regression test, not just documented in the CHANGELOG.
# ---------------------------------------------------------------------------

def test_near_zero_baseline_trivial_move_is_hit_not_missed():
    """The actual bug: hedge_density baseline 0.5, output 0.0 - an
    absolute move of half a hedge word per 100 words - used to score
    pct_diff=1.0 (100%, MISSED) purely because the baseline denominator
    was tiny. Below DELTA_BAND_MIN_ABS_DIFF, must be HIT regardless of
    pct_diff."""
    baseline = {
        "hedge_density": 0.5, "sentence_length_sd": 8.25,
        "first_person_ratio": 0.10, "directive_ratio": 0.0,
    }
    output_text = "Short direct sentence. Another one. No hedging here at all."
    delta = ve.score_render_delta(baseline, output_text)
    assert delta["hedge_density"]["output"] == 0.0
    assert delta["hedge_density"]["pct_diff"] == 1.0
    assert delta["hedge_density"]["verdict"] == "HIT"


def test_genuine_large_move_past_the_floor_still_misses():
    """The floor only protects trivial moves - a real structural shift
    (sentence rhythm collapsing from varied to uniform) must still
    register as MISSED, same as before this change."""
    baseline = {
        "hedge_density": 0.5, "sentence_length_sd": 8.25,
        "first_person_ratio": 0.10, "directive_ratio": 0.0,
    }
    output_text = (
        "Short one. Short two. Short three. Short four. Short five."
    )
    delta = ve.score_render_delta(baseline, output_text)
    assert delta["sentence_length_sd"]["verdict"] == "MISSED"


def test_semantic_match_weights_sum_to_one():
    """The entity/content weighting in score_semantic_drift should
    remain a proper weighted average - a bug here would silently
    scale the whole semantic_match number rather than change how it's
    balanced between the two signals."""
    assert sr.SEMANTIC_MATCH_ENTITY_WEIGHT + sr.SEMANTIC_MATCH_CONTENT_WEIGHT == 1.0


def test_semantic_match_weighting_matches_module_constants():
    """Confirms score_semantic_drift reads the module constants live,
    not a re-declared local 0.6/0.4 - swap the weights and the
    semantic_match number for an entity-heavy-loss case must move."""
    original = "Scott called about the report."
    output = "Someone called about the report."  # entity dropped, content mostly kept
    baseline_result = ve.score_semantic_drift(original, output)

    original_entity_w = sr.SEMANTIC_MATCH_ENTITY_WEIGHT
    original_content_w = sr.SEMANTIC_MATCH_CONTENT_WEIGHT
    try:
        sr.SEMANTIC_MATCH_ENTITY_WEIGHT = 0.0
        sr.SEMANTIC_MATCH_CONTENT_WEIGHT = 1.0
        ve.SEMANTIC_MATCH_ENTITY_WEIGHT = 0.0
        ve.SEMANTIC_MATCH_CONTENT_WEIGHT = 1.0
        reweighted_result = ve.score_semantic_drift(original, output)
    finally:
        sr.SEMANTIC_MATCH_ENTITY_WEIGHT = original_entity_w
        sr.SEMANTIC_MATCH_CONTENT_WEIGHT = original_content_w
        ve.SEMANTIC_MATCH_ENTITY_WEIGHT = original_entity_w
        ve.SEMANTIC_MATCH_CONTENT_WEIGHT = original_content_w

    # With entity loss zeroed out of the formula, a render that drops
    # a name but keeps content should score HIGHER, not the same -
    # proves the weighting constants are live, not hardcoded.
    assert reweighted_result["semantic_match"] > baseline_result["semantic_match"]
