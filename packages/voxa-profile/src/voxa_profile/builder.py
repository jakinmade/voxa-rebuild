"""
Voxa — Canonical Voice Profile (Layer 2)
Builds and manages the VoiceProfile from HumanisedProfile output.

Architecture Spec v9.2.0, Section 5.

Sprint 1 scope:
- Full rule metadata schema enforced
- All rule categories schema-valid
- Bootstrap state check
- Two lifecycle stages active: OBSERVED and CANDIDATE
- Basic versioning (full immutable history Sprint 3)
- Confidence via simple heuristic (formula in Sprint 2)
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as _tz
_UTC = _tz.utc
from uuid import UUID, uuid4

import structlog

from voxa_core.bootstrap import BootstrapStatus, check_bootstrap
from voxa_core.entities import (
    BoundaryRules,
    HumanisedProfile,
    IdentityRules,
    LinguisticRules,
    RuleMetadata,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import (
    LifecycleStage,
    SemanticDomain,
    SourceType,
)

logger = structlog.get_logger(__name__)

# Sprint 1 — simple heuristic confidence assignment
# Sprint 2 replaces this with the full confidence derivation formula
ONBOARDING_INITIAL_CONFIDENCE = 0.25
ONBOARDING_INITIAL_STABILITY = 0.10


def _make_rule(value: object, source_label: str, source_type: SourceType) -> RuleMetadata:
    """
    Creates a RuleMetadata with Sprint 1 heuristic confidence.
    Sprint 2 replaces the confidence value with the derivation formula.
    """
    confidence = ONBOARDING_INITIAL_CONFIDENCE
    stability = ONBOARDING_INITIAL_STABILITY

    return RuleMetadata(
        value=value,
        confidence=confidence,
        evidence_count=1,
        last_updated=datetime.now(_UTC),
        source=[source_label],
        stability=stability,
        decay_rate=0.02,
        lifecycle_stage=LifecycleStage.OBSERVED,
    )


# ---------------------------------------------------------------------------
# Fact → Rule mapping
# Maps extracted fact preferences to rule dimensions on the VoiceProfile
# ---------------------------------------------------------------------------

TONE_KEYWORDS_TO_RULES: list[tuple[str, str, object]] = [
    ("avoid corporate", "forbidden_phrases", ["corporate language"]),
    ("direct", "directness", "high"),
    ("blunt", "directness", "high"),
    ("plain", "directness", "high"),
    ("short", "cadence", "short"),
    ("brief", "cadence", "short"),
    ("concise", "compression", "high"),
    ("tight", "compression", "high"),
    ("formal", "formality", "formal"),
    ("informal", "formality", "casual"),
    ("casual", "formality", "casual"),
    ("do not hedge", "confidence_expression", "certain"),
    ("do not waffle", "compression", "high"),
]


def _map_fact_to_rules(
    preference: str,
    source_label: str,
    source_type: SourceType,
) -> dict[str, RuleMetadata]:
    """
    Maps a normalised preference string to one or more rule dimensions.
    Returns a dict of dimension -> RuleMetadata.
    """
    rules: dict[str, RuleMetadata] = {}
    pref_lower = preference.lower()

    for keyword, dimension, value in TONE_KEYWORDS_TO_RULES:
        if keyword in pref_lower:
            rules[dimension] = _make_rule(value, source_label, source_type)

    return rules


# ---------------------------------------------------------------------------
# Profile builder
# ---------------------------------------------------------------------------

def build_profile(humanised: HumanisedProfile) -> VoiceProfile:
    """
    Builds a VoiceProfile from a HumanisedProfile.
    Applies system default boundary if none detected from facts.
    """
    logger.info("profile_build_started", user_id=str(humanised.user_id))

    profile = VoiceProfile(
        user_id=humanised.user_id,
        version=1,
    )

    # Apply a system default boundary — architecture requires at least one
    profile.boundaries = BoundaryRules(
        tone_boundaries=RuleMetadata(
            value=["patronising", "aggressive", "salesy"],
            confidence=1.0,
            evidence_count=0,
            source=["system_default"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
    )

    # Map facts to rules
    collected_rules: dict[str, RuleMetadata] = {}
    for fact in humanised.facts:
        source_label = f"onboarding_{fact.fact_id}"
        mapped = _map_fact_to_rules(fact.preference, source_label, humanised.source_type)
        for dimension, rule in mapped.items():
            if dimension in collected_rules:
                # Merge — increment evidence count, append source
                existing = collected_rules[dimension]
                existing.evidence_count += 1
                existing.source.append(source_label)
                existing.confidence = min(existing.confidence + 0.05, 0.65)
            else:
                collected_rules[dimension] = rule

    # Apply collected rules to profile
    _apply_rules_to_profile(profile, collected_rules)

    # Check bootstrap state
    status = check_bootstrap(profile, calibration_session_count=0)
    profile.is_bootstrap = not status.is_bootstrap_complete

    logger.info(
        "profile_build_complete",
        user_id=str(humanised.user_id),
        rules_applied=len(collected_rules),
        is_bootstrap=profile.is_bootstrap,
        is_renderable=status.is_renderable,
    )

    return profile


def _apply_rules_to_profile(
    profile: VoiceProfile,
    rules: dict[str, RuleMetadata],
) -> None:
    """Writes collected rules into the correct category on the VoiceProfile."""
    dimension_to_category = {
        # Identity
        "cadence": ("identity", "cadence"),
        "compression": ("identity", "compression"),
        "directness": ("identity", "directness"),
        "warmth": ("identity", "warmth"),
        "formality": ("identity", "formality"),
        # Cognitive
        "reasoning_style": ("cognitive", "reasoning_style"),
        "decision_style": ("cognitive", "decision_style"),
        "confidence_expression": ("cognitive", "confidence_expression"),
        # Linguistic
        "preferred_verbs": ("linguistic", "preferred_verbs"),
        "forbidden_phrases": ("linguistic", "forbidden_phrases"),
        "sentence_shapes": ("linguistic", "sentence_shapes"),
        "paragraph_structure": ("linguistic", "paragraph_structure"),
        "metaphor_usage": ("linguistic", "metaphor_usage"),
        # Stylistic
        "humour": ("stylistic", "humour"),
        "intensity": ("stylistic", "intensity"),
        "emotional_range": ("stylistic", "emotional_range"),
        # Interaction
        "audience_positioning": ("interaction", "audience_positioning"),
        "instruction_style": ("interaction", "instruction_style"),
        "question_usage": ("interaction", "question_usage"),
    }

    for dimension, rule in rules.items():
        if dimension not in dimension_to_category:
            logger.warning("unknown_dimension", dimension=dimension)
            continue
        category_name, field_name = dimension_to_category[dimension]
        category_obj = getattr(profile, category_name)
        setattr(category_obj, field_name, rule)


# ---------------------------------------------------------------------------
# Profile versioning (Sprint 1 — basic; Sprint 3 — immutable snapshots)
# ---------------------------------------------------------------------------

def increment_version(profile: VoiceProfile, changes: list[str]) -> VoiceProfileVersion:
    """
    Creates a version snapshot and increments the profile version counter.
    Sprint 1: basic versioning. Sprint 3: immutable snapshots with restore.
    """
    profile.version += 1
    profile.updated_at = datetime.now(_UTC)

    snapshot = VoiceProfileVersion(
        user_id=profile.user_id,
        version=profile.version,
        snapshot=profile.model_copy(deep=True),
        changes=changes,
    )

    logger.info(
        "profile_version_incremented",
        user_id=str(profile.user_id),
        version=profile.version,
        change_count=len(changes),
    )

    return snapshot


def get_bootstrap_status(profile: VoiceProfile, session_count: int = 0) -> BootstrapStatus:
    return check_bootstrap(profile, calibration_session_count=session_count)
