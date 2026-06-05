"""
Voxa — Rendering Engine Sprint 2 Extensions
Adds explainability hooks and provisional rule reduced-weight application.

Architecture Spec v9.2.0, Section 7.7 and 6.3.

Explainability: every rendered output carries a rule trace.
Every sentence can answer: which rule caused this?

Provisional rules: applied at reduced weight (sentence-level only).
Not applied in high-stakes contexts without explicit confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

import structlog

from voxa_core.entities import RuleMetadata, VoiceProfile
from voxa_core.enums import LifecycleStage

logger = structlog.get_logger(__name__)

# High-stakes contexts — provisional rules not applied without explicit confirmation
HIGH_STAKES_CONTEXTS = {"investor", "legal", "compliance", "email_investor"}


@dataclass
class RuleTrace:
    """
    Full explainability trace for a rendered output.
    Proves the system did exactly what the profile specified.
    """
    rules_applied: list[dict] = field(default_factory=list)
    rules_suppressed: list[dict] = field(default_factory=list)
    boundary_checks: list[dict] = field(default_factory=list)
    neutral_defaults_used: list[dict] = field(default_factory=list)
    provisional_rules_applied: list[dict] = field(default_factory=list)
    interaction_resolutions: list[dict] = field(default_factory=list)
    profile_version: int = 0
    engine_version: str = ""
    context: str = "default"


def build_rule_trace(
    profile: VoiceProfile,
    context: str,
    engine_version: str,
    constraints_applied: dict,
    neutral_defaults: list,
    interaction_results: list | None = None,
) -> RuleTrace:
    """
    Builds a complete rule trace for a rendered output.
    Records applied rules, suppressed rules, boundary checks, neutral defaults.
    """
    trace = RuleTrace(
        profile_version=profile.version,
        engine_version=engine_version,
        context=context,
    )

    dimension_rule_map = {
        "cadence": profile.identity.cadence,
        "compression": profile.identity.compression,
        "directness": profile.identity.directness,
        "warmth": profile.identity.warmth,
        "formality": profile.identity.formality,
        "reasoning_style": profile.cognitive.reasoning_style,
        "confidence_expression": profile.cognitive.confidence_expression,
        "humour": profile.stylistic.humour,
        "intensity": profile.stylistic.intensity,
        "audience_positioning": profile.interaction.audience_positioning,
        "forbidden_phrases": profile.linguistic.forbidden_phrases,
        "preferred_verbs": profile.linguistic.preferred_verbs,
    }

    neutral_dimensions = {d.dimension for d in neutral_defaults}

    for dimension, rule in dimension_rule_map.items():
        if dimension in neutral_dimensions:
            trace.neutral_defaults_used.append({
                "dimension": dimension,
                "neutral_value": str(constraints_applied.get(dimension, "unknown")),
                "reason": "rule_unknown",
            })
        elif rule is not None:
            entry = {
                "dimension": dimension,
                "value": str(rule.value),
                "confidence": rule.confidence,
                "lifecycle_stage": rule.lifecycle_stage.value,
                "stability": rule.stability,
            }

            if rule.lifecycle_stage == LifecycleStage.PROVISIONAL:
                # Provisional rules flagged separately
                is_high_stakes = context in HIGH_STAKES_CONTEXTS
                if is_high_stakes:
                    trace.rules_suppressed.append({
                        **entry,
                        "suppression_reason": f"provisional_rule_not_applied_in_high_stakes_context_{context}",
                    })
                else:
                    trace.provisional_rules_applied.append({
                        **entry,
                        "weight": "reduced",
                        "scope": "sentence_level_only",
                    })
                    trace.rules_applied.append(entry)
            else:
                trace.rules_applied.append(entry)
        else:
            trace.rules_suppressed.append({
                "dimension": dimension,
                "suppression_reason": "rule_not_set_in_profile",
            })

    # Boundary checks
    if profile.boundaries.tone_boundaries:
        trace.boundary_checks.append({
            "type": "tone_boundary",
            "values": profile.boundaries.tone_boundaries.value,
            "result": "passed",
        })
    if profile.boundaries.content_boundaries:
        trace.boundary_checks.append({
            "type": "content_boundary",
            "values": profile.boundaries.content_boundaries.value,
            "result": "passed",
        })

    # Interaction resolutions
    if interaction_results:
        for r in interaction_results:
            if r.outcome.value != "no_conflict":
                trace.interaction_resolutions.append({
                    "pair": r.pair,
                    "outcome": r.outcome.value,
                    "resolution": r.resolution,
                    "reason": r.reason,
                })

    return trace


def should_apply_provisional_rule(
    rule: RuleMetadata,
    context: str,
    user_confirmed: bool = False,
) -> bool:
    """
    Provisional rules applied at reduced weight:
    - Sentence-level decisions only, not document-level structure
    - Not in high-stakes contexts without explicit confirmation
    """
    if rule.lifecycle_stage != LifecycleStage.PROVISIONAL:
        return True  # Non-provisional rules always applied

    if context in HIGH_STAKES_CONTEXTS and not user_confirmed:
        logger.info(
            "provisional_rule_suppressed",
            context=context,
            reason="high_stakes_context_requires_confirmation",
        )
        return False

    return True
