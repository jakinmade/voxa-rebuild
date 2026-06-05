"""
Voxa — Rule Interaction Map
Layer 2 addition — Sprint 2.

Architecture Spec v9.2.0, Section 10.9.

Defines resolution for high-conflict pairs only.
LIVE ARCHITECTURE AREA — extended from evidence, never designed speculatively.

Current pairs (four defined):
1. Directness vs Formality: retain directness, elevate register
2. Warmth vs Intensity: cap intensity at medium in written contexts
3. Humour vs Audience Positioning: apply lightly under teacher/challenger
4. Confidence Expression vs Hedging: flag as unresolved, surface to governance

Additional pairs expected but not yet formally defined — not added here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class InteractionOutcome(str, Enum):
    RESOLVED = "resolved"
    FLAGGED_UNRESOLVED = "flagged_unresolved"
    NO_CONFLICT = "no_conflict"


@dataclass
class InteractionResult:
    pair: str
    outcome: InteractionOutcome
    resolution: dict[str, str] | None
    reason: str


def resolve_directness_vs_formality(
    directness: str | None,
    formality: str | None,
) -> InteractionResult:
    """
    Directness vs Formality.
    Resolution: retain directness, elevate register.
    Do not soften directness to achieve formality.
    """
    if directness is None or formality is None:
        return InteractionResult(
            pair="directness_vs_formality",
            outcome=InteractionOutcome.NO_CONFLICT,
            resolution=None,
            reason="one_or_both_rules_unknown",
        )

    # High directness + formal: keep both, but express directly in formal register
    if directness == "high" and formality == "formal":
        logger.info(
            "interaction_resolved",
            pair="directness_vs_formality",
            resolution="retain_directness_elevate_register",
        )
        return InteractionResult(
            pair="directness_vs_formality",
            outcome=InteractionOutcome.RESOLVED,
            resolution={
                "directness": "high",
                "formality": "formal",
                "rendering_note": "direct_formal: retain_directness_elevate_register",
            },
            reason="directness_retained_register_elevated",
        )

    return InteractionResult(
        pair="directness_vs_formality",
        outcome=InteractionOutcome.NO_CONFLICT,
        resolution=None,
        reason="no_conflict_detected",
    )


def resolve_warmth_vs_intensity(
    warmth: str | None,
    intensity: str | None,
    context: str = "written",
) -> InteractionResult:
    """
    Warmth vs Intensity.
    Cap intensity at medium in written contexts.
    Intensity without warmth is aggression.
    """
    if warmth is None or intensity is None:
        return InteractionResult(
            pair="warmth_vs_intensity",
            outcome=InteractionOutcome.NO_CONFLICT,
            resolution=None,
            reason="one_or_both_rules_unknown",
        )

    # High intensity + low warmth in written context = aggression risk
    if intensity == "high" and warmth == "low" and context == "written":
        logger.warning(
            "interaction_resolved",
            pair="warmth_vs_intensity",
            resolution="intensity_capped_at_medium",
        )
        return InteractionResult(
            pair="warmth_vs_intensity",
            outcome=InteractionOutcome.RESOLVED,
            resolution={
                "warmth": "low",
                "intensity": "medium",  # Capped
                "rendering_note": "intensity_capped_at_medium_written_context",
            },
            reason="intensity_without_warmth_is_aggression_in_written_context",
        )

    return InteractionResult(
        pair="warmth_vs_intensity",
        outcome=InteractionOutcome.NO_CONFLICT,
        resolution=None,
        reason="no_conflict_detected",
    )


def resolve_humour_vs_audience(
    humour: str | None,
    audience_positioning: str | None,
) -> InteractionResult:
    """
    Humour vs Audience Positioning.
    Apply lightly under teacher or challenger positioning.
    """
    if humour is None or audience_positioning is None:
        return InteractionResult(
            pair="humour_vs_audience_positioning",
            outcome=InteractionOutcome.NO_CONFLICT,
            resolution=None,
            reason="one_or_both_rules_unknown",
        )

    constrained_positions = {"teacher", "challenger"}
    heavy_humour = {"playful", "sarcastic", "absurdist"}

    if audience_positioning in constrained_positions and humour in heavy_humour:
        logger.info(
            "interaction_resolved",
            pair="humour_vs_audience_positioning",
            resolution="humour_applied_lightly",
        )
        return InteractionResult(
            pair="humour_vs_audience_positioning",
            outcome=InteractionOutcome.RESOLVED,
            resolution={
                "humour": "dry",  # Reduced to lightest non-none form
                "audience_positioning": audience_positioning,
                "rendering_note": f"humour_constrained_under_{audience_positioning}_positioning",
            },
            reason=f"heavy_humour_moderated_for_{audience_positioning}_positioning",
        )

    return InteractionResult(
        pair="humour_vs_audience_positioning",
        outcome=InteractionOutcome.NO_CONFLICT,
        resolution=None,
        reason="no_conflict_detected",
    )


def resolve_confidence_vs_hedging(
    confidence_expression: str | None,
    hedging_detected: bool = False,
) -> InteractionResult:
    """
    Confidence Expression vs Hedging.
    If conflicting: flag as unresolved, surface to governance.
    Do not pick a winner.
    """
    if confidence_expression is None or not hedging_detected:
        return InteractionResult(
            pair="confidence_expression_vs_hedging",
            outcome=InteractionOutcome.NO_CONFLICT,
            resolution=None,
            reason="no_conflict_detected",
        )

    if confidence_expression == "certain" and hedging_detected:
        logger.warning(
            "interaction_unresolved",
            pair="confidence_expression_vs_hedging",
            reason="certain_profile_but_hedging_detected_in_edit",
        )
        return InteractionResult(
            pair="confidence_expression_vs_hedging",
            outcome=InteractionOutcome.FLAGGED_UNRESOLVED,
            resolution=None,
            reason="conflict_surfaced_to_governance_do_not_pick_winner",
        )

    return InteractionResult(
        pair="confidence_expression_vs_hedging",
        outcome=InteractionOutcome.NO_CONFLICT,
        resolution=None,
        reason="no_conflict_detected",
    )


# ---------------------------------------------------------------------------
# Main interaction resolver — runs all four pairs
# ---------------------------------------------------------------------------

def resolve_all_interactions(
    constraints: dict[str, object],
    context: str = "written",
) -> tuple[dict[str, object], list[InteractionResult]]:
    """
    Runs all four defined interaction pairs against a constraints dict.
    Returns updated constraints and a list of interaction results.
    Unresolved conflicts are flagged — not silently picked.
    """
    results: list[InteractionResult] = []
    updated = dict(constraints)

    # 1. Directness vs Formality
    r1 = resolve_directness_vs_formality(
        directness=str(updated.get("directness", "")),
        formality=str(updated.get("formality", "")),
    )
    results.append(r1)
    if r1.outcome == InteractionOutcome.RESOLVED and r1.resolution:
        updated.update(r1.resolution)

    # 2. Warmth vs Intensity
    r2 = resolve_warmth_vs_intensity(
        warmth=str(updated.get("warmth", "")),
        intensity=str(updated.get("intensity", "")),
        context=context,
    )
    results.append(r2)
    if r2.outcome == InteractionOutcome.RESOLVED and r2.resolution:
        updated.update(r2.resolution)

    # 3. Humour vs Audience Positioning
    r3 = resolve_humour_vs_audience(
        humour=str(updated.get("humour", "")),
        audience_positioning=str(updated.get("audience_positioning", "")),
    )
    results.append(r3)
    if r3.outcome == InteractionOutcome.RESOLVED and r3.resolution:
        updated.update(r3.resolution)

    # 4. Confidence Expression vs Hedging
    hedging_detected = any(
        w in str(updated.get("rendering_input", "")).lower()
        for w in ["might", "could", "perhaps", "possibly", "maybe"]
    )
    r4 = resolve_confidence_vs_hedging(
        confidence_expression=str(updated.get("confidence_expression", "")),
        hedging_detected=hedging_detected,
    )
    results.append(r4)

    return updated, results
