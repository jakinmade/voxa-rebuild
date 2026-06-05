"""
Voxa — Rule Promotion Lifecycle
Layer 2 addition — Sprint 2.

Architecture Spec v9.2.0, Section 5.3.

Five stages: OBSERVED → CANDIDATE → PROVISIONAL → STABLE → CORE

Rules do not skip stages.
Demotion is one stage at a time.
Boundary rules: lifecycle does not apply.

All promotion criteria are starting hypotheses — instrumented for adjustment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog

from voxa_core.entities import RuleCandidate, RuleMetadata, VoiceProfile
from voxa_core.enums import LifecycleStage
from voxa_profile.confidence import (
    ConfidenceInputs,
    compute_confidence,
    compute_consistency_score,
    compute_recency_score,
    apply_decay,
    should_revert_to_unknown,
    MINIMUM_CONFIDENCE_THRESHOLD,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Promotion thresholds — starting hypotheses, not fixed
# All instrumented so they can be adjusted from real data
# ---------------------------------------------------------------------------

# OBSERVED → CANDIDATE
CANDIDATE_MIN_OBSERVATIONS = 2
CANDIDATE_MIN_SESSIONS = 1  # Must span at least this many sessions

# CANDIDATE → PROVISIONAL
PROVISIONAL_MIN_CONFIDENCE = 0.40
PROVISIONAL_MIN_CONSISTENCY = 0.60
PROVISIONAL_MIN_EVIDENCE = 3
PROVISIONAL_NO_ACTIVE_CONFLICT = True

# PROVISIONAL → STABLE
STABLE_MIN_CONFIDENCE = 0.65
STABLE_MIN_CONSISTENCY = 0.70
STABLE_MIN_ADDITIONAL_SESSIONS = 3
STABLE_NO_SUSTAINED_NEGATIVE = True

# STABLE → CORE
CORE_MIN_CONFIDENCE = 0.90
CORE_MIN_STABILITY = 0.90
CORE_MIN_ACTIVE_DAYS = 30
CORE_MIN_CONTEXTS = 3


# ---------------------------------------------------------------------------
# Promotion gate functions
# ---------------------------------------------------------------------------

def can_promote_to_candidate(
    observation_count: int,
    session_count: int,
) -> tuple[bool, str]:
    """OBSERVED → CANDIDATE gate."""
    if observation_count < CANDIDATE_MIN_OBSERVATIONS:
        return False, f"insufficient observations: {observation_count} < {CANDIDATE_MIN_OBSERVATIONS}"
    if session_count < CANDIDATE_MIN_SESSIONS:
        return False, f"insufficient sessions: {session_count} < {CANDIDATE_MIN_SESSIONS}"
    return True, "promotion_criteria_met"


def can_promote_to_provisional(
    confidence: float,
    consistency: float,
    evidence_count: int,
    has_active_conflict: bool,
) -> tuple[bool, str]:
    """CANDIDATE → PROVISIONAL gate. All four conditions must be met."""
    if confidence < PROVISIONAL_MIN_CONFIDENCE:
        return False, f"confidence too low: {confidence:.3f} < {PROVISIONAL_MIN_CONFIDENCE}"
    if consistency < PROVISIONAL_MIN_CONSISTENCY:
        return False, f"consistency too low: {consistency:.3f} < {PROVISIONAL_MIN_CONSISTENCY}"
    if evidence_count < PROVISIONAL_MIN_EVIDENCE:
        return False, f"insufficient evidence: {evidence_count} < {PROVISIONAL_MIN_EVIDENCE}"
    if has_active_conflict:
        return False, "active conflict present — resolution required before promotion"
    return True, "promotion_criteria_met"


def can_promote_to_stable(
    confidence: float,
    consistency: float,
    additional_sessions: int,
    has_sustained_negative: bool,
) -> tuple[bool, str]:
    """PROVISIONAL → STABLE gate."""
    if confidence < STABLE_MIN_CONFIDENCE:
        return False, f"confidence too low: {confidence:.3f} < {STABLE_MIN_CONFIDENCE}"
    if consistency < STABLE_MIN_CONSISTENCY:
        return False, f"consistency too low: {consistency:.3f} < {STABLE_MIN_CONSISTENCY}"
    if additional_sessions < STABLE_MIN_ADDITIONAL_SESSIONS:
        return False, f"insufficient sessions since provisional: {additional_sessions} < {STABLE_MIN_ADDITIONAL_SESSIONS}"
    if has_sustained_negative:
        return False, "sustained negative evidence — promotion blocked"
    return True, "promotion_criteria_met"


def can_promote_to_core(
    confidence: float,
    stability: float,
    active_days: int,
    context_count: int,
) -> tuple[bool, str]:
    """STABLE → CORE gate."""
    if confidence < CORE_MIN_CONFIDENCE:
        return False, f"confidence too low: {confidence:.3f} < {CORE_MIN_CONFIDENCE}"
    if stability < CORE_MIN_STABILITY:
        return False, f"stability too low: {stability:.3f} < {CORE_MIN_STABILITY}"
    if active_days < CORE_MIN_ACTIVE_DAYS:
        return False, f"insufficient active days: {active_days} < {CORE_MIN_ACTIVE_DAYS}"
    if context_count < CORE_MIN_CONTEXTS:
        return False, f"insufficient contexts: {context_count} < {CORE_MIN_CONTEXTS}"
    return True, "promotion_criteria_met"


# ---------------------------------------------------------------------------
# Promotion executor
# ---------------------------------------------------------------------------

def attempt_promotion(
    rule: RuleMetadata,
    dimension: str,
    evidence_timestamps: list[datetime] | None = None,
    values_observed: list[str] | None = None,
    additional_sessions: int = 0,
    has_active_conflict: bool = False,
    has_sustained_negative: bool = False,
    active_days: int = 0,
    context_count: int = 1,
) -> tuple[RuleMetadata, str, bool]:
    """
    Attempts to promote a rule to the next lifecycle stage.
    Returns (updated_rule, reason, promoted).
    Rules do not skip stages — promotion is one step at a time.
    Boundary rules are never passed to this function.
    """
    current_stage = rule.lifecycle_stage

    if current_stage == LifecycleStage.BOUNDARY:
        return rule, "boundary_rules_exempt_from_lifecycle", False

    if current_stage == LifecycleStage.CORE:
        return rule, "already_at_core", False

    # Recompute confidence before attempting promotion
    timestamps = evidence_timestamps or []
    values = values_observed or []

    recency = compute_recency_score(timestamps)
    consistency = compute_consistency_score(values, str(rule.value)) if values else 0.5

    confidence_result = compute_confidence(ConfidenceInputs(
        evidence_count=rule.evidence_count,
        consistency_score=consistency,
        recency_score=recency,
        source_weight=0.4,  # Default — caller should pass actual source weight
        decay_adjustment=0.0,
    ))

    rule.confidence = confidence_result.confidence

    # Attempt promotion based on current stage
    if current_stage == LifecycleStage.OBSERVED:
        can, reason = can_promote_to_candidate(
            observation_count=rule.evidence_count,
            session_count=additional_sessions + 1,
        )
        if can:
            rule.lifecycle_stage = LifecycleStage.CANDIDATE
            logger.info("rule_promoted", dimension=dimension,
                        from_stage="observed", to_stage="candidate",
                        confidence=rule.confidence)
            return rule, reason, True

    elif current_stage == LifecycleStage.CANDIDATE:
        can, reason = can_promote_to_provisional(
            confidence=rule.confidence,
            consistency=consistency,
            evidence_count=rule.evidence_count,
            has_active_conflict=has_active_conflict,
        )
        if can:
            rule.lifecycle_stage = LifecycleStage.PROVISIONAL
            rule.stability = 0.45  # Provisional starts at reduced weight
            logger.info("rule_promoted", dimension=dimension,
                        from_stage="candidate", to_stage="provisional",
                        confidence=rule.confidence)
            return rule, reason, True

    elif current_stage == LifecycleStage.PROVISIONAL:
        can, reason = can_promote_to_stable(
            confidence=rule.confidence,
            consistency=consistency,
            additional_sessions=additional_sessions,
            has_sustained_negative=has_sustained_negative,
        )
        if can:
            rule.lifecycle_stage = LifecycleStage.STABLE
            rule.stability = max(rule.stability, 0.70)
            logger.info("rule_promoted", dimension=dimension,
                        from_stage="provisional", to_stage="stable",
                        confidence=rule.confidence)
            return rule, reason, True

    elif current_stage == LifecycleStage.STABLE:
        can, reason = can_promote_to_core(
            confidence=rule.confidence,
            stability=rule.stability,
            active_days=active_days,
            context_count=context_count,
        )
        if can:
            rule.lifecycle_stage = LifecycleStage.CORE
            logger.info("rule_promoted", dimension=dimension,
                        from_stage="stable", to_stage="core",
                        confidence=rule.confidence)
            return rule, reason, True

    logger.info(
        "rule_promotion_blocked",
        dimension=dimension,
        stage=current_stage.value,
        reason=reason if 'reason' in dir() else "gate_not_passed",
    )
    return rule, "gate_not_passed", False


# ---------------------------------------------------------------------------
# Demotion — one stage at a time
# ---------------------------------------------------------------------------

DEMOTION_ORDER = {
    LifecycleStage.CORE: LifecycleStage.STABLE,
    LifecycleStage.STABLE: LifecycleStage.PROVISIONAL,
    LifecycleStage.PROVISIONAL: LifecycleStage.CANDIDATE,
    LifecycleStage.CANDIDATE: LifecycleStage.OBSERVED,
}


def demote_rule(rule: RuleMetadata, dimension: str, reason: str) -> RuleMetadata:
    """
    Demotes a rule one stage.
    A Core Rule does not drop to Observed in a single calibration event.
    Boundary rules are never demoted.
    """
    if rule.lifecycle_stage == LifecycleStage.BOUNDARY:
        logger.warning("demotion_attempted_on_boundary_rule", dimension=dimension)
        return rule

    if rule.lifecycle_stage == LifecycleStage.OBSERVED:
        logger.info("rule_at_minimum_stage_cannot_demote", dimension=dimension)
        return rule

    next_stage = DEMOTION_ORDER[rule.lifecycle_stage]
    prev_stage = rule.lifecycle_stage

    rule.lifecycle_stage = next_stage
    rule.stability = max(0.0, rule.stability - 0.20)

    logger.info(
        "rule_demoted",
        dimension=dimension,
        from_stage=prev_stage.value,
        to_stage=next_stage.value,
        reason=reason,
        new_stability=rule.stability,
    )

    return rule


# ---------------------------------------------------------------------------
# Rule decay batch — applied per calibration batch
# ---------------------------------------------------------------------------

DIMENSION_DECAY_RATES: dict[str, float] = {
    # These are starting hypotheses — empirical rates TBD
    # Instrumented so rates can be measured and adjusted
    "cadence": 0.01,
    "compression": 0.01,
    "directness": 0.02,
    "warmth": 0.03,          # Warmth expected to decay faster
    "formality": 0.01,
    "reasoning_style": 0.01,
    "decision_style": 0.02,
    "confidence_expression": 0.02,
    "preferred_verbs": 0.03,  # Linguistic rules may evolve faster
    "forbidden_phrases": 0.01,
    "sentence_shapes": 0.02,
    "paragraph_structure": 0.01,
    "metaphor_usage": 0.02,
    "humour": 0.03,
    "intensity": 0.02,
    "emotional_range": 0.02,
    "audience_positioning": 0.01,
    "instruction_style": 0.01,
    "question_usage": 0.01,
}


def run_decay_batch(profile: VoiceProfile) -> dict[str, float]:
    """
    Applies decay to all non-boundary rules in the profile.
    Returns a dict of dimension -> new_confidence for instrumentation.
    Boundary rules exempt.
    """
    decay_log: dict[str, float] = {}

    dimension_rule_map = {
        "cadence": (profile.identity, "cadence"),
        "compression": (profile.identity, "compression"),
        "directness": (profile.identity, "directness"),
        "warmth": (profile.identity, "warmth"),
        "formality": (profile.identity, "formality"),
        "reasoning_style": (profile.cognitive, "reasoning_style"),
        "decision_style": (profile.cognitive, "decision_style"),
        "confidence_expression": (profile.cognitive, "confidence_expression"),
        "preferred_verbs": (profile.linguistic, "preferred_verbs"),
        "forbidden_phrases": (profile.linguistic, "forbidden_phrases"),
        "sentence_shapes": (profile.linguistic, "sentence_shapes"),
        "paragraph_structure": (profile.linguistic, "paragraph_structure"),
        "metaphor_usage": (profile.linguistic, "metaphor_usage"),
        "humour": (profile.stylistic, "humour"),
        "intensity": (profile.stylistic, "intensity"),
        "emotional_range": (profile.stylistic, "emotional_range"),
        "audience_positioning": (profile.interaction, "audience_positioning"),
        "instruction_style": (profile.interaction, "instruction_style"),
        "question_usage": (profile.interaction, "question_usage"),
    }

    for dimension, (category, field) in dimension_rule_map.items():
        rule = getattr(category, field)
        if rule is None:
            continue
        if rule.lifecycle_stage == LifecycleStage.BOUNDARY:
            continue  # Boundary rules exempt

        rate = DIMENSION_DECAY_RATES.get(dimension, 0.02)
        old_confidence = rule.confidence
        new_confidence = apply_decay(old_confidence, rate)
        rule.confidence = new_confidence
        decay_log[dimension] = new_confidence

        # Revert to unknown if below minimum threshold
        if should_revert_to_unknown(new_confidence):
            setattr(category, field, None)
            logger.info(
                "rule_reverted_to_unknown",
                dimension=dimension,
                final_confidence=new_confidence,
            )
        else:
            logger.info(
                "decay_applied",
                dimension=dimension,
                old_confidence=old_confidence,
                new_confidence=new_confidence,
                decay_rate=rate,
            )

    return decay_log
