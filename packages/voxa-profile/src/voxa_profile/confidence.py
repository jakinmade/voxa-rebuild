"""
Voxa — Confidence Model
A single coherent model for confidence, stability, consistency, recency, decay,
and negative evidence. These are not separate concepts — they compose.

Architecture Spec v9.2.0, Section 5.2.

Model:

  raw_confidence = (
      consistency  * 0.40   # How reliably this value appears across contexts
    + recency      * 0.30   # How recent the evidence is (exponential, not linear)
    + source       * 0.20   # How strong the source (behavioural > onboarding)
    + saturation   * 0.10   # Diminishing returns beyond saturation point
  )

  effective_confidence = raw_confidence * stability_weight * (1 - decay_adjustment)

Key relationships:
  - High consistency slows decay (consistent behaviour is more stable)
  - High recency weights more at low evidence counts (early evidence is fragile)
  - Negative evidence reduces stability first, confidence second
  - Stability tracks long-term reliability; confidence tracks current evidence strength
  - A rule can have high confidence but low stability (many recent edits, volatile)
  - A rule can have low confidence but high stability (sparse but consistent)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

# Starting coefficients — instrumented, adjustable from empirical data
COEFF_CONSISTENCY = 0.40
COEFF_RECENCY     = 0.30
COEFF_SOURCE      = 0.20
COEFF_SATURATION  = 0.10

EVIDENCE_SATURATION_POINT = 20   # Beyond this, more evidence doesn't increase confidence
RECENCY_WINDOW_DAYS = 30          # Evidence older than this scores 0.0 on recency
RECENCY_HALF_LIFE_DAYS = 10       # Exponential decay half-life for recency scoring

# Stability weight applied to raw confidence
# High stability = confidence is more reliable = small discount
# Low stability = confidence is more uncertain = larger discount
STABILITY_CONFIDENCE_WEIGHT_MIN = 0.75  # Minimum weight even at stability=0
STABILITY_CONFIDENCE_WEIGHT_MAX = 1.00  # Maximum weight at stability=1

# Negative evidence
NEGATIVE_CONFIDENCE_PENALTY = 0.08   # Per negative event
NEGATIVE_STABILITY_PENALTY  = 0.05   # Per negative event (stability is harder to rebuild)

# Stage-specific demotion confidence thresholds
STAGE_DEMOTION_THRESHOLDS = {
    "core":        0.85,
    "stable":      0.60,
    "provisional": 0.35,
    "candidate":   0.20,
}


class ConfidenceInputs(NamedTuple):
    evidence_count:    int
    consistency_score: float   # 0.0–1.0
    recency_score:     float   # 0.0–1.0
    source_weight:     float   # 0.0–1.0
    decay_adjustment:  float = 0.0
    stability:         float = 0.5


class ConfidenceResult(NamedTuple):
    confidence:        float
    raw_confidence:    float
    effective_confidence: float
    inputs:            ConfidenceInputs
    coefficients:      dict[str, float]
    stability_weight:  float


def compute_confidence(inputs: ConfidenceInputs) -> ConfidenceResult:
    """
    Computes effective confidence from the full model.
    All inputs and results are logged for empirical validation.
    """
    saturation = min(inputs.evidence_count / EVIDENCE_SATURATION_POINT, 1.0)

    raw_confidence = (
        inputs.consistency_score * COEFF_CONSISTENCY
        + inputs.recency_score   * COEFF_RECENCY
        + inputs.source_weight   * COEFF_SOURCE
        + saturation             * COEFF_SATURATION
    )
    raw_confidence = max(0.0, min(1.0, raw_confidence))

    # Stability modulates confidence — high stability = full confidence trusted
    stability_weight = (
        STABILITY_CONFIDENCE_WEIGHT_MIN
        + (STABILITY_CONFIDENCE_WEIGHT_MAX - STABILITY_CONFIDENCE_WEIGHT_MIN) * inputs.stability
    )

    effective = raw_confidence * stability_weight * (1.0 - inputs.decay_adjustment)
    effective = max(0.0, min(1.0, effective))

    result = ConfidenceResult(
        confidence=round(effective, 4),
        raw_confidence=round(raw_confidence, 4),
        effective_confidence=round(effective, 4),
        inputs=inputs,
        coefficients={
            "consistency": COEFF_CONSISTENCY,
            "recency":     COEFF_RECENCY,
            "source":      COEFF_SOURCE,
            "saturation":  COEFF_SATURATION,
            "saturation_point": EVIDENCE_SATURATION_POINT,
        },
        stability_weight=round(stability_weight, 4),
    )

    logger.info(
        "confidence_computed",
        evidence_count=inputs.evidence_count,
        consistency=inputs.consistency_score,
        recency=inputs.recency_score,
        source=inputs.source_weight,
        stability=inputs.stability,
        decay=inputs.decay_adjustment,
        raw=result.raw_confidence,
        effective=result.confidence,
        stability_weight=result.stability_weight,
    )

    return result


def compute_recency_score(evidence_timestamps: list[datetime]) -> float:
    """
    Exponential recency scoring.
    Recent evidence decays slowly; old evidence drops faster than linear.
    Half-life: RECENCY_HALF_LIFE_DAYS days.

    Relationship: at low evidence counts, recency matters more —
    a single very recent observation is more meaningful than a single old one.
    """
    if not evidence_timestamps:
        return 0.0

    now = datetime.now(timezone.utc)
    scores = []

    for ts in evidence_timestamps:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_days = (now - ts).total_seconds() / 86400

        if age_days > RECENCY_WINDOW_DAYS:
            scores.append(0.0)
        else:
            # Exponential decay: score = 0.5 ^ (age / half_life)
            import math
            score = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
            scores.append(score)

    return round(sum(scores) / len(scores), 4)


def compute_consistency_score(
    values_observed: list[str],
    target_value: str,
) -> float:
    """
    Consistency = proportion of observations agreeing with the rule value.

    High consistency slows decay — a rule observed reliably across contexts
    is more stable than one observed many times in one context.
    """
    if not values_observed:
        return 0.0
    agreeing = sum(1 for v in values_observed if v == target_value)
    return round(agreeing / len(values_observed), 4)


def apply_decay(
    current_confidence: float,
    decay_rate: float,
    consistency_score: float = 0.5,
) -> float:
    """
    Applies decay with consistency modulation.
    High consistency reduces the effective decay rate.
    A rule observed reliably across contexts decays more slowly.

    effective_decay = decay_rate * (1 - consistency_score * 0.5)
    At consistency=1.0: effective decay is halved
    At consistency=0.0: full decay rate applied
    """
    effective_decay = decay_rate * (1.0 - consistency_score * 0.5)
    new_confidence = current_confidence * (1.0 - effective_decay)
    return round(max(0.0, new_confidence), 4)


def apply_negative_evidence(
    confidence: float,
    stability: float,
    lifecycle_stage: str,
) -> tuple[float, float, bool]:
    """
    Applies a negative evidence event.
    Reduces stability first, then confidence.
    Returns (new_confidence, new_stability, should_demote).

    Stability is harder to rebuild than confidence.
    A single negative event should not collapse a stable rule —
    it takes sustained negative evidence.
    """
    new_stability = max(0.0, stability - NEGATIVE_STABILITY_PENALTY)
    new_confidence = max(0.0, confidence - NEGATIVE_CONFIDENCE_PENALTY)

    threshold = STAGE_DEMOTION_THRESHOLDS.get(lifecycle_stage, 0.0)
    should_demote = new_confidence < threshold

    logger.info(
        "negative_evidence_applied",
        old_confidence=confidence,
        new_confidence=new_confidence,
        old_stability=stability,
        new_stability=new_stability,
        lifecycle_stage=lifecycle_stage,
        should_demote=should_demote,
    )

    return new_confidence, new_stability, should_demote


MINIMUM_CONFIDENCE_THRESHOLD = 0.10

def should_revert_to_unknown(confidence: float) -> bool:
    return confidence < MINIMUM_CONFIDENCE_THRESHOLD
