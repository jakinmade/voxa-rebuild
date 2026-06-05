"""
Voxa — Confidence Derivation Formula
Layer 2 addition — Sprint 2.

Architecture Spec v9.2.0, Section 5.2.

Formula:
  raw_confidence = (
      consistency_score * 0.40
    + recency_score      * 0.30
    + source_weight      * 0.20
    + min(evidence_count / saturation_point, 1.0) * 0.10
  )
  confidence = raw_confidence * (1 - decay_adjustment)

Coefficients (0.40 / 0.30 / 0.20 / 0.10) are starting architecture.
Must be validated against real user data. Instrumented for adjustment.

Boundary rules: confidence = 1.0 always. Formula does not apply.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

# Starting architecture coefficients — instrumented, not hardcoded as fixed
COEFF_CONSISTENCY = 0.40
COEFF_RECENCY = 0.30
COEFF_SOURCE = 0.20
COEFF_EVIDENCE = 0.10

# Saturation point hypothesis — TBD from empirical data
# Beyond this, additional evidence increases stability, not confidence
EVIDENCE_SATURATION_POINT = 20

# Recency decay — evidence older than this scores 0.0
RECENCY_WINDOW_DAYS = 30


class ConfidenceInputs(NamedTuple):
    evidence_count: int
    consistency_score: float      # 0.0 to 1.0 — proportion of agreeing edits across contexts
    recency_score: float          # 0.0 to 1.0 — time-weighted mean of evidence recency
    source_weight: float          # 1.0 behavioural | 0.4 implied onboarding | 0.2 explicit onboarding
    decay_adjustment: float = 0.0 # applied after raw confidence computed


class ConfidenceResult(NamedTuple):
    confidence: float
    raw_confidence: float
    inputs: ConfidenceInputs
    coefficients: dict[str, float]
    capped_at_boundary: bool = False


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    """
    Computes rule confidence from four inputs.
    Called on every calibration event.
    All inputs and coefficients are logged for empirical validation.
    """
    evidence_contribution = min(
        inputs.evidence_count / EVIDENCE_SATURATION_POINT, 1.0
    )

    raw_confidence = (
        inputs.consistency_score  * COEFF_CONSISTENCY
        + inputs.recency_score    * COEFF_RECENCY
        + inputs.source_weight    * COEFF_SOURCE
        + evidence_contribution   * COEFF_EVIDENCE
    )

    # Clamp to [0.0, 1.0] before decay
    raw_confidence = max(0.0, min(1.0, raw_confidence))

    confidence = raw_confidence * (1.0 - inputs.decay_adjustment)
    confidence = max(0.0, min(1.0, confidence))

    result = ConfidenceResult(
        confidence=round(confidence, 4),
        raw_confidence=round(raw_confidence, 4),
        inputs=inputs,
        coefficients={
            "consistency": COEFF_CONSISTENCY,
            "recency": COEFF_RECENCY,
            "source": COEFF_SOURCE,
            "evidence": COEFF_EVIDENCE,
            "saturation_point": EVIDENCE_SATURATION_POINT,
        },
    )

    # Instrument everything — these measurements validate the open questions
    logger.info(
        "confidence_computed",
        evidence_count=inputs.evidence_count,
        consistency_score=inputs.consistency_score,
        recency_score=inputs.recency_score,
        source_weight=inputs.source_weight,
        decay_adjustment=inputs.decay_adjustment,
        raw_confidence=result.raw_confidence,
        confidence=result.confidence,
    )

    return result


def compute_recency_score(evidence_timestamps: list[datetime]) -> float:
    """
    Time-weighted mean of evidence recency.
    Recent evidence scores higher. Evidence older than RECENCY_WINDOW_DAYS scores 0.0.
    """
    if not evidence_timestamps:
        return 0.0

    now = datetime.now(timezone.utc)
    scores = []

    for ts in evidence_timestamps:
        # Make timezone-aware if naive
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (now - ts).days
        score = max(0.0, 1.0 - (age_days / RECENCY_WINDOW_DAYS))
        scores.append(score)

    return round(sum(scores) / len(scores), 4)


def compute_consistency_score(
    values_observed: list[str],
    target_value: str,
) -> float:
    """
    Proportion of edits agreeing with the rule value across different contexts.
    Consistency carries the highest weight (0.40) because a rule observed
    across multiple contexts is more reliable than one observed in one context many times.
    """
    if not values_observed:
        return 0.0
    agreeing = sum(1 for v in values_observed if v == target_value)
    return round(agreeing / len(values_observed), 4)


def apply_decay(current_confidence: float, decay_rate: float) -> float:
    """
    new_confidence = current_confidence * (1 - decay_rate)
    If confidence falls below minimum threshold, rule reverts to unknown.
    Boundary rules exempt — never passed to this function.
    """
    new_confidence = current_confidence * (1.0 - decay_rate)
    return round(max(0.0, new_confidence), 4)


# Minimum confidence threshold — below this, rule reverts to unknown
MINIMUM_CONFIDENCE_THRESHOLD = 0.10


def should_revert_to_unknown(confidence: float) -> bool:
    return confidence < MINIMUM_CONFIDENCE_THRESHOLD
