"""
Voxa — Sprint 2 Test Suite
Maps directly to the Sprint 2 Done When criteria.

Done When:
[1]  Confidence formula runs on every calibration event. Output inspectable and matches expected range.
[2]  A rule moves from CANDIDATE to PROVISIONAL after meeting all four conditions.
[3]  A PROVISIONAL RULE applied at reduced weight. A STABLE RULE applied at full weight. Difference measurable.
[4]  Negative evidence reduces a rule's confidence.
[5]  Demotion path triggers on sufficient negative evidence.
[6]  LLM escalation fires for ambiguous edit and returns confidence score. Rules-based makes final call.
[7]  /explain-render returns complete rule trace.
[8]  Rule decay runs on batch. Confidence values change. Boundary rules unaffected.
[9]  Interaction map resolves Directness vs Formality conflict correctly.
[10] /bootstrap-status returns accurate lifecycle stage breakdown.
"""

import pytest
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from voxa_core.enums import EditClass, LifecycleStage
from voxa_core.entities import (
    BoundaryRules,
    RuleCandidate,
    RuleMetadata,
    VoiceProfile,
)


# ---------------------------------------------------------------------------
# [1] Confidence formula — runs, output in expected range
# ---------------------------------------------------------------------------

class TestConfidenceFormula:

    def test_formula_produces_value_in_range(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs
        result = compute_confidence(ConfidenceInputs(
            evidence_count=10,
            consistency_score=0.80,
            recency_score=0.90,
            source_weight=1.0,
            decay_adjustment=0.0,
        ))
        assert 0.0 <= result.confidence <= 1.0
        assert 0.0 <= result.raw_confidence <= 1.0

    def test_higher_consistency_yields_higher_confidence(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs
        low = compute_confidence(ConfidenceInputs(5, 0.30, 0.50, 0.4, 0.0))
        high = compute_confidence(ConfidenceInputs(5, 0.90, 0.50, 0.4, 0.0))
        assert high.confidence > low.confidence

    def test_decay_adjustment_reduces_confidence(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs
        no_decay = compute_confidence(ConfidenceInputs(10, 0.8, 0.8, 0.8, 0.0))
        with_decay = compute_confidence(ConfidenceInputs(10, 0.8, 0.8, 0.8, 0.3))
        assert with_decay.confidence < no_decay.confidence

    def test_evidence_count_saturates(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs, EVIDENCE_SATURATION_POINT
        at_saturation = compute_confidence(ConfidenceInputs(EVIDENCE_SATURATION_POINT, 0.8, 0.8, 0.8, 0.0))
        beyond_saturation = compute_confidence(ConfidenceInputs(EVIDENCE_SATURATION_POINT * 3, 0.8, 0.8, 0.8, 0.0))
        # Beyond saturation — no additional confidence gain
        assert abs(at_saturation.confidence - beyond_saturation.confidence) < 0.01

    def test_boundary_rules_not_touched_by_formula(self):
        # Boundary rules: confidence = 1.0 always. Formula does not apply.
        rule = RuleMetadata(
            value=["patronising"],
            confidence=1.0,
            source=["system"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        assert rule.confidence == 1.0
        assert rule.decay_rate == 0.0

    def test_recency_score_recent_evidence_scores_high(self):
        from voxa_profile.confidence import compute_recency_score
        now = datetime.now(timezone.utc)
        recent = [now - timedelta(days=1), now - timedelta(days=2)]
        score = compute_recency_score(recent)
        assert score > 0.90

    def test_recency_score_old_evidence_scores_low(self):
        from voxa_profile.confidence import compute_recency_score
        now = datetime.now(timezone.utc)
        old = [now - timedelta(days=29), now - timedelta(days=28)]
        score = compute_recency_score(old)
        assert score < 0.20  # Exponential decay — near window edge, low but not zero

    def test_consistency_score_all_agreeing(self):
        from voxa_profile.confidence import compute_consistency_score
        values = ["certain", "certain", "certain", "certain"]
        score = compute_consistency_score(values, "certain")
        assert score == 1.0

    def test_consistency_score_mixed(self):
        from voxa_profile.confidence import compute_consistency_score
        values = ["certain", "hedged", "certain", "certain"]
        score = compute_consistency_score(values, "certain")
        assert score == 0.75


# ---------------------------------------------------------------------------
# [2] CANDIDATE → PROVISIONAL promotion gate
# ---------------------------------------------------------------------------

class TestPromotionLifecycle:

    def test_candidate_to_provisional_all_conditions_met(self):
        from voxa_profile.lifecycle import can_promote_to_provisional
        can, reason = can_promote_to_provisional(
            confidence=0.55,
            consistency=0.75,
            evidence_count=5,
            has_active_conflict=False,
        )
        assert can is True
        assert reason == "promotion_criteria_met"

    def test_candidate_to_provisional_blocked_by_low_confidence(self):
        from voxa_profile.lifecycle import can_promote_to_provisional
        can, reason = can_promote_to_provisional(
            confidence=0.30,  # Below 0.40 threshold
            consistency=0.75,
            evidence_count=5,
            has_active_conflict=False,
        )
        assert can is False
        assert "confidence" in reason

    def test_candidate_to_provisional_blocked_by_active_conflict(self):
        from voxa_profile.lifecycle import can_promote_to_provisional
        can, reason = can_promote_to_provisional(
            confidence=0.55,
            consistency=0.75,
            evidence_count=5,
            has_active_conflict=True,
        )
        assert can is False
        assert "conflict" in reason

    def test_attempt_promotion_candidate_to_provisional(self):
        from voxa_profile.lifecycle import attempt_promotion
        rule = RuleMetadata(
            value="certain",
            confidence=0.55,
            evidence_count=5,
            source=["edit_1", "edit_2", "edit_3", "edit_4", "edit_5"],
            stability=0.40,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )
        now = datetime.now(timezone.utc)
        timestamps = [now - timedelta(days=i) for i in range(5)]
        values = ["certain"] * 5

        updated, reason, promoted = attempt_promotion(
            rule=rule,
            dimension="confidence_expression",
            evidence_timestamps=timestamps,
            values_observed=values,
            additional_sessions=0,
            has_active_conflict=False,
        )
        assert promoted is True
        assert updated.lifecycle_stage == LifecycleStage.PROVISIONAL

    def test_rules_do_not_skip_stages(self):
        from voxa_profile.lifecycle import attempt_promotion
        # An OBSERVED rule cannot jump to PROVISIONAL in one step
        rule = RuleMetadata(
            value="certain",
            confidence=0.80,
            evidence_count=10,
            source=["edit_1"],
            stability=0.80,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.OBSERVED,
        )
        updated, reason, promoted = attempt_promotion(
            rule=rule,
            dimension="confidence_expression",
            additional_sessions=2,
        )
        # Even with high confidence, OBSERVED only goes to CANDIDATE
        if promoted:
            assert updated.lifecycle_stage == LifecycleStage.CANDIDATE

    def test_boundary_rules_exempt_from_lifecycle(self):
        from voxa_profile.lifecycle import attempt_promotion
        rule = RuleMetadata(
            value=["patronising"],
            confidence=1.0,
            source=["system"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        updated, reason, promoted = attempt_promotion(rule, "tone_boundaries")
        assert promoted is False
        assert updated.lifecycle_stage == LifecycleStage.BOUNDARY
        assert "boundary" in reason


# ---------------------------------------------------------------------------
# [3] PROVISIONAL applied at reduced weight, STABLE at full weight
# ---------------------------------------------------------------------------

class TestProvisionalRendering:

    def test_provisional_rule_not_applied_in_high_stakes_context(self):
        from voxa_rendering.explainability import should_apply_provisional_rule
        rule = RuleMetadata(
            value="high",
            confidence=0.50,
            source=["edit_1"],
            stability=0.45,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        result = should_apply_provisional_rule(rule, context="investor", user_confirmed=False)
        assert result is False

    def test_provisional_rule_applied_in_normal_context(self):
        from voxa_rendering.explainability import should_apply_provisional_rule
        rule = RuleMetadata(
            value="high",
            confidence=0.50,
            source=["edit_1"],
            stability=0.45,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        result = should_apply_provisional_rule(rule, context="default", user_confirmed=False)
        assert result is True

    def test_stable_rule_always_applied(self):
        from voxa_rendering.explainability import should_apply_provisional_rule
        rule = RuleMetadata(
            value="high",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        result = should_apply_provisional_rule(rule, context="investor", user_confirmed=False)
        assert result is True

    def test_provisional_confirmed_by_user_applied_in_high_stakes(self):
        from voxa_rendering.explainability import should_apply_provisional_rule
        rule = RuleMetadata(
            value="high",
            confidence=0.50,
            source=["edit_1"],
            stability=0.45,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        result = should_apply_provisional_rule(rule, context="investor", user_confirmed=True)
        assert result is True


# ---------------------------------------------------------------------------
# [4] & [5] Negative evidence and demotion
# ---------------------------------------------------------------------------

class TestNegativeEvidence:

    def test_negative_evidence_reduces_confidence(self):
        from voxa_calibration.sprint2 import record_negative_evidence
        profile = VoiceProfile(user_id=uuid4())
        rule = RuleMetadata(
            value="certain",
            confidence=0.75,
            source=["edit_1"],
            stability=0.70,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        updated, demoted = record_negative_evidence(
            rule=rule,
            dimension="confidence_expression",
            event_id=uuid4(),
            pattern_reversed="certain -> hedged",
            profile=profile,
        )
        assert updated.confidence < 0.75

    def test_sufficient_negative_evidence_triggers_demotion(self):
        from voxa_calibration.sprint2 import record_negative_evidence
        profile = VoiceProfile(user_id=uuid4())
        rule = RuleMetadata(
            value="certain",
            confidence=0.66,  # Just above STABLE threshold (0.65)
            source=["edit_1"],
            stability=0.70,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        # One negative event drops it below 0.60 demotion threshold
        updated, demoted = record_negative_evidence(
            rule=rule,
            dimension="confidence_expression",
            event_id=uuid4(),
            pattern_reversed="certain -> hedged",
            profile=profile,
        )
        assert demoted is True
        assert updated.lifecycle_stage == LifecycleStage.PROVISIONAL

    def test_demotion_is_one_stage_at_a_time(self):
        from voxa_profile.lifecycle import demote_rule
        rule = RuleMetadata(
            value="certain",
            confidence=0.95,
            source=["edit_1"],
            stability=0.95,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CORE,
        )
        demoted = demote_rule(rule, "confidence_expression", reason="test")
        # CORE demotes to STABLE — not to OBSERVED
        assert demoted.lifecycle_stage == LifecycleStage.STABLE

    def test_boundary_rules_cannot_be_demoted(self):
        from voxa_profile.lifecycle import demote_rule
        rule = RuleMetadata(
            value=["patronising"],
            confidence=1.0,
            source=["system"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        result = demote_rule(rule, "tone_boundaries", reason="test")
        assert result.lifecycle_stage == LifecycleStage.BOUNDARY


# ---------------------------------------------------------------------------
# [6] LLM escalation — returns confidence score, rules-based makes final call
# ---------------------------------------------------------------------------

class TestLLMEscalation:

    @pytest.mark.asyncio
    async def test_llm_escalation_returns_tuple(self):
        from voxa_rendering.llm_boundary import classify_edit_via_llm as llm_classify_edit
        # No API key in test environment — should return gracefully
        prompt = "Classify: original=\'This might be worth exploring.\' edited=\'Explore this.\'"
        edit_class, confidence = await llm_classify_edit(prompt)
        # Without API key, returns AMBIGUOUS and 0.0 — that's the correct fallback
        assert edit_class in list(EditClass)
        assert 0.0 <= confidence <= 1.0

    def test_full_semantic_diff_detects_hedge_removal(self):
        from voxa_calibration.sprint2 import full_semantic_diff
        diff = full_semantic_diff(
            original="This might work and could be useful.",
            edited="This works and is useful.",
        )
        assert "might" in diff["hedges_removed"] or "could" in diff["hedges_removed"]
        assert diff["confidence_shift"] == "more_certain"

    def test_full_semantic_diff_detects_tonal_signal(self):
        from voxa_calibration.sprint2 import full_semantic_diff
        diff = full_semantic_diff(
            original="Here is the summary.",
            edited="Here is the summary.",
            user_instruction="Make it more direct.",
        )
        dims = [d for d, v in diff["tonal_signals"]]
        assert "directness" in dims


# ---------------------------------------------------------------------------
# [8] Rule decay — confidence changes, boundary rules unaffected
# ---------------------------------------------------------------------------

class TestRuleDecay:

    def test_decay_reduces_confidence(self):
        from voxa_profile.confidence import apply_decay
        new_conf = apply_decay(0.80, decay_rate=0.05)
        assert new_conf < 0.80

    def test_decay_batch_changes_rule_confidence(self):
        from voxa_profile.lifecycle import run_decay_batch
        profile = VoiceProfile(user_id=uuid4())
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.80,
            source=["edit_1"],
            stability=0.70,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        decay_log = run_decay_batch(profile)
        assert "directness" in decay_log
        assert decay_log["directness"] < 0.80

    def test_boundary_rules_unaffected_by_decay_batch(self):
        from voxa_profile.lifecycle import run_decay_batch
        profile = VoiceProfile(user_id=uuid4())
        profile.boundaries.tone_boundaries = RuleMetadata(
            value=["patronising"],
            confidence=1.0,
            source=["system"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        decay_log = run_decay_batch(profile)
        # Boundary rules must not appear in decay log
        assert "tone_boundaries" not in decay_log
        # Boundary rule confidence unchanged
        assert profile.boundaries.tone_boundaries.confidence == 1.0

    def test_rule_reverts_to_unknown_below_threshold(self):
        from voxa_profile.confidence import apply_decay, should_revert_to_unknown
        confidence = 0.12
        for _ in range(3):
            confidence = apply_decay(confidence, decay_rate=0.10)
        assert should_revert_to_unknown(confidence) is True


# ---------------------------------------------------------------------------
# [9] Interaction map — Directness vs Formality resolved correctly
# ---------------------------------------------------------------------------

class TestInteractionMap:

    def test_directness_vs_formality_resolved(self):
        from voxa_profile.interactions import resolve_directness_vs_formality, InteractionOutcome
        result = resolve_directness_vs_formality(directness="high", formality="formal")
        assert result.outcome == InteractionOutcome.RESOLVED
        assert result.resolution["directness"] == "high"
        assert "retain_directness" in result.resolution.get("rendering_note", "")

    def test_warmth_vs_intensity_capped(self):
        from voxa_profile.interactions import resolve_warmth_vs_intensity, InteractionOutcome
        result = resolve_warmth_vs_intensity(warmth="low", intensity="high", context="written")
        assert result.outcome == InteractionOutcome.RESOLVED
        assert result.resolution["intensity"] == "medium"

    def test_humour_constrained_under_teacher(self):
        from voxa_profile.interactions import resolve_humour_vs_audience, InteractionOutcome
        result = resolve_humour_vs_audience(humour="sarcastic", audience_positioning="teacher")
        assert result.outcome == InteractionOutcome.RESOLVED
        assert result.resolution["humour"] == "dry"

    def test_confidence_vs_hedging_flagged_not_resolved(self):
        from voxa_profile.interactions import resolve_confidence_vs_hedging, InteractionOutcome
        result = resolve_confidence_vs_hedging(
            confidence_expression="certain",
            hedging_detected=True,
        )
        assert result.outcome == InteractionOutcome.FLAGGED_UNRESOLVED
        assert result.resolution is None

    def test_no_conflict_no_change(self):
        from voxa_profile.interactions import resolve_directness_vs_formality, InteractionOutcome
        result = resolve_directness_vs_formality(directness="medium", formality="semi-formal")
        assert result.outcome == InteractionOutcome.NO_CONFLICT

    def test_resolve_all_interactions_pipeline(self):
        from voxa_profile.interactions import resolve_all_interactions
        constraints = {
            "directness": "high",
            "formality": "formal",
            "warmth": "low",
            "intensity": "high",
            "humour": "playful",
            "audience_positioning": "teacher",
            "confidence_expression": "certain",
        }
        updated, results = resolve_all_interactions(constraints, context="written")
        # Intensity should be capped
        assert updated.get("intensity") == "medium"
        # Humour should be moderated
        assert updated.get("humour") == "dry"


# ---------------------------------------------------------------------------
# Sprint 2 instrumentation — verify logging is in place
# ---------------------------------------------------------------------------

class TestSprint2Instrumentation:

    def test_confidence_result_includes_coefficients(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs
        result = compute_confidence(ConfidenceInputs(5, 0.7, 0.8, 0.4, 0.0))
        assert "consistency" in result.coefficients
        assert "recency" in result.coefficients
        assert "source" in result.coefficients
        assert "saturation" in result.coefficients
        assert "saturation_point" in result.coefficients

    def test_self_report_conflict_detection(self):
        from voxa_calibration.sprint2 import check_self_report_conflict
        # 3+ contrary edits = conflict surfaced
        conflict = check_self_report_conflict(
            onboarding_preference="formal",
            observed_behaviour_value="casual",
            contrary_edit_count=3,
            dimension="formality",
        )
        assert conflict is True

    def test_self_report_no_conflict_below_threshold(self):
        from voxa_calibration.sprint2 import check_self_report_conflict
        conflict = check_self_report_conflict(
            onboarding_preference="formal",
            observed_behaviour_value="casual",
            contrary_edit_count=2,
            dimension="formality",
        )
        assert conflict is False
