"""
Voxa — Canonical Voice Profile (Layer 2)
Builds and merges the VoiceProfile from HumanisedProfile output.

Architecture Spec v9.2.0, Section 5.

Core principle: Voxa is built on accumulated evidence, not snapshots.
Every call to merge_profile() accumulates new evidence into the existing
profile — it never overwrites it. build_profile() is for first-time
creation only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

from voxa_core.bootstrap import BootstrapStatus, check_bootstrap
from voxa_core.entities import (
    BoundaryRules,
    HumanisedProfile,
    RuleMetadata,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import LifecycleStage, SourceType
from voxa_profile.confidence import (
    ConfidenceInputs,
    compute_confidence,
    compute_recency_score,
    compute_consistency_score,
)

logger = structlog.get_logger(__name__)

_UTC = timezone.utc

ONBOARDING_INITIAL_CONFIDENCE = 0.25
ONBOARDING_INITIAL_STABILITY = 0.10

DIMENSION_TO_CATEGORY: dict[str, tuple[str, str]] = {
    "cadence": ("identity", "cadence"),
    "compression": ("identity", "compression"),
    "directness": ("identity", "directness"),
    "warmth": ("identity", "warmth"),
    "formality": ("identity", "formality"),
    "reasoning_style": ("cognitive", "reasoning_style"),
    "decision_style": ("cognitive", "decision_style"),
    "confidence_expression": ("cognitive", "confidence_expression"),
    "preferred_verbs": ("linguistic", "preferred_verbs"),
    "forbidden_phrases": ("linguistic", "forbidden_phrases"),
    "sentence_shapes": ("linguistic", "sentence_shapes"),
    "paragraph_structure": ("linguistic", "paragraph_structure"),
    "metaphor_usage": ("linguistic", "metaphor_usage"),
    "humour": ("stylistic", "humour"),
    "intensity": ("stylistic", "intensity"),
    "emotional_range": ("stylistic", "emotional_range"),
    "audience_positioning": ("interaction", "audience_positioning"),
    "instruction_style": ("interaction", "instruction_style"),
    "question_usage": ("interaction", "question_usage"),
}

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
) -> dict[str, tuple[object, float]]:
    """Returns dict of dimension -> (value, source_weight)."""
    rules: dict[str, tuple[object, float]] = {}
    pref_lower = preference.lower()
    source_weight = 0.2 if source_type == SourceType.ONBOARDING else 0.4
    for keyword, dimension, value in TONE_KEYWORDS_TO_RULES:
        if keyword in pref_lower:
            rules[dimension] = (value, source_weight)
    return rules


def _system_boundary() -> RuleMetadata:
    return RuleMetadata(
        value=["patronising", "aggressive", "salesy"],
        confidence=1.0,
        evidence_count=0,
        source=["system_default"],
        stability=1.0,
        decay_rate=0.0,
        lifecycle_stage=LifecycleStage.BOUNDARY,
    )


# ---------------------------------------------------------------------------
# build_profile — first-time creation only
# ---------------------------------------------------------------------------

def build_profile(humanised: HumanisedProfile) -> VoiceProfile:
    """
    Creates a new VoiceProfile from a HumanisedProfile.
    Called once per user. Subsequent onboarding calls use merge_profile().
    """
    logger.info("profile_build_started", user_id=str(humanised.user_id))

    profile = VoiceProfile(user_id=humanised.user_id, version=1)
    profile.boundaries = BoundaryRules(tone_boundaries=_system_boundary())

    _accumulate_facts(profile, humanised)

    status = check_bootstrap(profile, calibration_session_count=0)
    profile.is_bootstrap = not status.is_bootstrap_complete

    logger.info(
        "profile_build_complete",
        user_id=str(humanised.user_id),
        is_bootstrap=profile.is_bootstrap,
        is_renderable=status.is_renderable,
    )
    return profile


# ---------------------------------------------------------------------------
# merge_profile — evidence accumulation, not overwrite
# ---------------------------------------------------------------------------

def merge_profile(
    profile: VoiceProfile,
    humanised: HumanisedProfile,
) -> list[str]:
    """
    Accumulates new evidence from a HumanisedProfile into an existing VoiceProfile.
    Never overwrites. Returns a list of change descriptions for versioning.

    Architecture principle: Voxa is built on accumulated evidence.
    Each new fact increases evidence_count, updates confidence via the formula,
    and may trigger lifecycle promotion.
    """
    logger.info("profile_merge_started", user_id=str(profile.user_id))

    changes = _accumulate_facts(profile, humanised)

    status = check_bootstrap(profile, calibration_session_count=0)
    profile.is_bootstrap = not status.is_bootstrap_complete
    profile.updated_at = datetime.now(_UTC)

    logger.info(
        "profile_merge_complete",
        user_id=str(profile.user_id),
        changes=changes,
    )
    return changes


def _accumulate_facts(profile: VoiceProfile, humanised: HumanisedProfile) -> list[str]:
    """
    Core accumulation logic shared by build and merge.
    For each fact:
      - If dimension has no rule: create at OBSERVED
      - If dimension has a rule with same value: accumulate evidence, recompute confidence
      - If dimension has a rule with different value: record conflict, lower confidence
    """
    changes: list[str] = []
    source_type = humanised.source_type

    for fact in humanised.facts:
        source_label = f"{source_type.value}_{fact.fact_id}"
        mapped = _map_fact_to_rules(fact.preference, source_label, source_type)

        for dimension, (value, source_weight) in mapped.items():
            if dimension not in DIMENSION_TO_CATEGORY:
                continue

            category_name, field_name = DIMENSION_TO_CATEGORY[dimension]
            category_obj = getattr(profile, category_name)
            existing: RuleMetadata | None = getattr(category_obj, field_name)

            if existing is None:
                # New rule — create at OBSERVED
                rule = RuleMetadata(
                    value=value,
                    confidence=ONBOARDING_INITIAL_CONFIDENCE,
                    evidence_count=1,
                    last_updated=datetime.now(_UTC),
                    source=[source_label],
                    stability=ONBOARDING_INITIAL_STABILITY,
                    decay_rate=0.02,
                    lifecycle_stage=LifecycleStage.OBSERVED,
                )
                setattr(category_obj, field_name, rule)
                changes.append(f"created:{dimension}={value}")

            elif str(existing.value) == str(value):
                # Same value — accumulate evidence, recompute confidence
                existing.evidence_count += 1
                existing.source.append(source_label)
                existing.last_updated = datetime.now(_UTC)

                # Recompute confidence with formula
                recency = compute_recency_score([existing.last_updated])
                consistency = compute_consistency_score(
                    [str(value)] * existing.evidence_count, str(value)
                )
                result = compute_confidence(ConfidenceInputs(
                    evidence_count=existing.evidence_count,
                    consistency_score=consistency,
                    recency_score=recency,
                    source_weight=source_weight,
                ))
                existing.confidence = result.confidence

                # Promote OBSERVED → CANDIDATE if evidence threshold met
                if (existing.lifecycle_stage == LifecycleStage.OBSERVED
                        and existing.evidence_count >= 2):
                    existing.lifecycle_stage = LifecycleStage.CANDIDATE
                    changes.append(f"promoted_to_candidate:{dimension}")
                else:
                    changes.append(f"evidence_accumulated:{dimension}(count={existing.evidence_count})")

            else:
                # Conflicting value — reduce confidence, do not overwrite
                existing.confidence = max(0.0, existing.confidence - 0.05)
                existing.source.append(f"conflict:{source_label}")
                existing.last_updated = datetime.now(_UTC)
                changes.append(f"conflict_detected:{dimension}(existing={existing.value},new={value})")
                logger.warning(
                    "fact_conflict_detected",
                    user_id=str(profile.user_id),
                    dimension=dimension,
                    existing_value=str(existing.value),
                    new_value=str(value),
                )

    return changes


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

def increment_version(profile: VoiceProfile, changes: list[str]) -> VoiceProfileVersion:
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
