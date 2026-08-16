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
