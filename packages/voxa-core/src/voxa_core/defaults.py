"""
Voxa — Neutral Defaults
Applied by the renderer when a rule is unknown.
These are rendering fallbacks only — never stored in the voice profile.
They leave no evidence trail.

Architecture Spec v9.2.0, Section 6.2.
"""

from voxa_core.enums import (
    AudiencePositioning,
    Cadence,
    Compression,
    ConfidenceExpression,
    Directness,
    Formality,
    Humour,
    Intensity,
    ReasoningStyle,
    Warmth,
)

NEUTRAL_DEFAULTS: dict[str, object] = {
    "cadence": Cadence.MEDIUM,
    "compression": Compression.MEDIUM,
    "directness": Directness.MEDIUM,
    "warmth": Warmth.MEDIUM,
    "formality": Formality.SEMI_FORMAL,
    "reasoning_style": ReasoningStyle.LINEAR,
    "confidence_expression": ConfidenceExpression.BALANCED,
    "humour": Humour.NONE,
    "intensity": Intensity.MEDIUM,
    "audience_positioning": AudiencePositioning.PEER,
}


def get_neutral_default(dimension: str) -> object:
    """
    Returns the neutral default for a given rule dimension.
    Raises KeyError if the dimension is not recognised.
    """
    if dimension not in NEUTRAL_DEFAULTS:
        raise KeyError(f"No neutral default defined for dimension: {dimension}")
    return NEUTRAL_DEFAULTS[dimension]
