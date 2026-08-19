"""
Regression: 19 Aug 2026. review_gate.requires_review() used to gate on
Risk being Medium or High (scoring_rules.REVIEW_REQUIRED_RISK_LEVELS =
{"Medium", "High"}). Risk went Medium the moment a render missed even
ONE of four tracked style dimensions
(RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST = 1) - real renders miss at
least one of four targets on nearly every render, so the rewritten
text was hidden behind a confirm-to-unlock wall almost constantly,
regardless of whether anything was actually wrong with it.

JA (19 Aug 2026): "user friction is front and centre of VOICOVA,
nothing should contribute to friction."

Fix: gating narrowed to voice_engine.has_content_integrity_hard_fail()
- a real content-integrity failure (surviving AI tell, attribution
swap, dropped entity, invented sentence) - extracted out of
compute_risk's existing hard-fail logic. compute_risk's own
Low/Medium/High badge is UNCHANGED (still informational, still folds
in style-drift severity); only what review_gate.requires_review()
listens to changed. Style drift, however severe, no longer gates
anything on its own.

These tests confirm the actual end-to-end behaviour, not just the
individual functions in isolation: a render that misses dimensions but
has no hard fail must never require review, and a render with a real
hard fail must always require review, matching compute_risk's badge
exactly (a hard fail always forces Risk="High").
"""
import voice_engine as ve
import review_gate


class TestHasContentIntegrityHardFail:
    def test_clean_render_no_hard_fail(self):
        semantic = {"attribution_swaps": [], "dropped_entities": []}
        ai_tells = {"clean": True}
        insertion_check = {"sentence_growth": 0}
        assert ve.has_content_integrity_hard_fail(semantic, ai_tells, insertion_check) is False

    def test_ai_tell_not_clean_is_hard_fail(self):
        semantic = {"attribution_swaps": [], "dropped_entities": []}
        ai_tells = {"clean": False}
        assert ve.has_content_integrity_hard_fail(semantic, ai_tells, None) is True

    def test_attribution_swap_is_hard_fail(self):
        semantic = {"attribution_swaps": ["your point -> my point"], "dropped_entities": []}
        ai_tells = {"clean": True}
        assert ve.has_content_integrity_hard_fail(semantic, ai_tells, None) is True

    def test_dropped_entity_is_hard_fail(self):
        semantic = {"attribution_swaps": [], "dropped_entities": ["Scott"]}
        ai_tells = {"clean": True}
        assert ve.has_content_integrity_hard_fail(semantic, ai_tells, None) is True

    def test_sentence_growth_is_hard_fail(self):
        semantic = {"attribution_swaps": [], "dropped_entities": []}
        ai_tells = {"clean": True}
        insertion_check = {"sentence_growth": 1}
        assert ve.has_content_integrity_hard_fail(semantic, ai_tells, insertion_check) is True

    def test_missing_optional_args_defaults_to_no_hard_fail(self):
        assert ve.has_content_integrity_hard_fail(None, None, None) is False


class TestComputeRiskBadgeUnchangedByRefactor:
    """
    compute_risk's own return value must be byte-identical to its
    pre-refactor behaviour - this refactor only changes what
    review_gate listens to, not what the Risk badge itself says.
    """

    def test_style_drift_only_still_shows_medium_badge(self):
        # 1 missed dimension, no hard fail - badge should still say
        # Medium (informational), even though this no longer gates.
        delta = {
            "hedging": {"verdict": "HIT"},
            "sentence_rhythm": {"verdict": "HIT"},
            "ownership": {"verdict": "MISSED"},
            "directness": {"verdict": "HIT"},
        }
        semantic = {"semantic_match": 90, "attribution_swaps": [], "dropped_entities": []}
        ai_tells = {"clean": True}
        risk = ve.compute_risk(delta, semantic, ai_tells, {"sentence_growth": 0})
        assert risk == "Medium"

    def test_hard_fail_still_forces_high_badge(self):
        delta = {"hedging": {"verdict": "HIT"}}
        semantic = {"semantic_match": 98, "attribution_swaps": [], "dropped_entities": ["Scott"]}
        ai_tells = {"clean": True}
        risk = ve.compute_risk(delta, semantic, ai_tells, {"sentence_growth": 0})
        assert risk == "High"


class TestEndToEndGatingBehaviour:
    """
    The actual product-level fix: style drift alone (any severity)
    must never require review; a real hard fail always must. Mirrors
    exactly what app.py now does — compute has_content_integrity_
    hard_fail alongside risk, and gate only on that.
    """

    def test_style_drift_only_never_requires_review_even_at_max_severity(self):
        # Worst-case style drift: 3+ missed dimensions AND low semantic
        # match, which independently pushes compute_risk's badge to
        # High - but with no actual hard fail present, this must still
        # not gate.
        delta = {
            "hedging": {"verdict": "MISSED"},
            "sentence_rhythm": {"verdict": "MISSED"},
            "ownership": {"verdict": "MISSED"},
            "directness": {"verdict": "MISSED"},
        }
        semantic = {"semantic_match": 60, "attribution_swaps": [], "dropped_entities": []}
        ai_tells = {"clean": True}
        insertion_check = {"sentence_growth": 0}

        risk = ve.compute_risk(delta, semantic, ai_tells, insertion_check)
        hard_fail = ve.has_content_integrity_hard_fail(semantic, ai_tells, insertion_check)

        assert risk == "High"  # badge still reflects real severity
        assert hard_fail is False  # but nothing here is a genuine error
        assert review_gate.requires_review(hard_fail) is False  # so it must not gate

    def test_single_hard_fail_requires_review_even_with_perfect_style_match(self):
        delta = {
            "hedging": {"verdict": "HIT"},
            "sentence_rhythm": {"verdict": "HIT"},
            "ownership": {"verdict": "HIT"},
            "directness": {"verdict": "HIT"},
        }
        semantic = {"semantic_match": 99, "attribution_swaps": ["your -> my"], "dropped_entities": []}
        ai_tells = {"clean": True}
        insertion_check = {"sentence_growth": 0}

        hard_fail = ve.has_content_integrity_hard_fail(semantic, ai_tells, insertion_check)
        assert review_gate.requires_review(hard_fail) is True
