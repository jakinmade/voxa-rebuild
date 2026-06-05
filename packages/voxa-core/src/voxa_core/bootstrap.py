"""
Voxa — Bootstrap State
Determines whether a VoiceProfile meets the minimum renderable threshold.

Architecture Spec v9.2.0, Section 6.1 and 6.4.

Bootstrap state ends when:
- 5 or more rules at STABLE RULE stage or above
- At least 3 distinct rule categories represented
- Minimum 3 calibration sessions completed

Minimum renderable profile requires:
- At least one Identity Rule at PROVISIONAL or above
- At least one Linguistic Rule at any stage including CANDIDATE
- No active conflicts in the profile
- At least one Boundary defined (can be system default)
"""

from dataclasses import dataclass

from voxa_core.entities import VoiceProfile
from voxa_core.enums import LifecycleStage, RuleCategory


STABLE_AND_ABOVE = {LifecycleStage.STABLE, LifecycleStage.CORE}
PROVISIONAL_AND_ABOVE = {LifecycleStage.PROVISIONAL, LifecycleStage.STABLE, LifecycleStage.CORE}
CANDIDATE_AND_ABOVE = {
    LifecycleStage.CANDIDATE,
    LifecycleStage.PROVISIONAL,
    LifecycleStage.STABLE,
    LifecycleStage.CORE,
}

BOOTSTRAP_EXIT_STABLE_RULE_COUNT = 5
BOOTSTRAP_EXIT_CATEGORY_COUNT = 3
BOOTSTRAP_EXIT_SESSION_COUNT = 3


@dataclass
class BootstrapStatus:
    is_renderable: bool
    is_bootstrap_complete: bool
    stable_rule_count: int
    categories_represented: list[str]
    missing_requirements: list[str]
    rules_by_stage: dict[str, int]


def check_bootstrap(profile: VoiceProfile, calibration_session_count: int = 0) -> BootstrapStatus:
    """
    Evaluates the bootstrap state of a VoiceProfile.
    Returns a BootstrapStatus with full detail — surfaced to the user as a feature.
    """
    missing: list[str] = []
    rules_by_stage: dict[str, int] = {stage.value: 0 for stage in LifecycleStage}

    # Collect all rules across all categories
    all_rules = _collect_all_rules(profile)
    for rule in all_rules:
        rules_by_stage[rule.lifecycle_stage.value] += 1

    # --- Minimum renderable profile checks ---

    # 1. At least one Identity Rule at PROVISIONAL or above
    identity_rules = _collect_identity_rules(profile)
    has_identity = any(
        r.lifecycle_stage in PROVISIONAL_AND_ABOVE for r in identity_rules
    )
    if not has_identity:
        missing.append("No Identity Rule at PROVISIONAL or above")

    # 2. At least one Linguistic Rule at CANDIDATE or above
    linguistic_rules = _collect_linguistic_rules(profile)
    has_linguistic = any(
        r.lifecycle_stage in CANDIDATE_AND_ABOVE for r in linguistic_rules
    )
    if not has_linguistic:
        missing.append("No Linguistic Rule at CANDIDATE or above")

    # 3. At least one Boundary defined
    has_boundary = (
        profile.boundaries.tone_boundaries is not None
        or profile.boundaries.content_boundaries is not None
    )
    if not has_boundary:
        missing.append("No Boundary defined")

    is_renderable = len(missing) == 0

    # --- Bootstrap completion checks ---

    stable_count = sum(
        1 for r in all_rules if r.lifecycle_stage in STABLE_AND_ABOVE
    )

    categories_represented = _categories_with_stable_rules(profile)

    bootstrap_complete = (
        stable_count >= BOOTSTRAP_EXIT_STABLE_RULE_COUNT
        and len(categories_represented) >= BOOTSTRAP_EXIT_CATEGORY_COUNT
        and calibration_session_count >= BOOTSTRAP_EXIT_SESSION_COUNT
    )

    return BootstrapStatus(
        is_renderable=is_renderable,
        is_bootstrap_complete=bootstrap_complete,
        stable_rule_count=stable_count,
        categories_represented=categories_represented,
        missing_requirements=missing,
        rules_by_stage=rules_by_stage,
    )


def _collect_all_rules(profile: VoiceProfile) -> list:
    rules = []
    for category in [
        profile.identity,
        profile.cognitive,
        profile.linguistic,
        profile.stylistic,
        profile.interaction,
    ]:
        for field_value in category.model_dump().values():
            if field_value is not None:
                # Re-fetch as RuleMetadata object
                pass
    # Use model fields directly
    for category_obj in [
        profile.identity,
        profile.cognitive,
        profile.linguistic,
        profile.stylistic,
        profile.interaction,
    ]:
        for field_name in category_obj.model_fields:
            rule = getattr(category_obj, field_name)
            if rule is not None:
                rules.append(rule)
    return rules


def _collect_identity_rules(profile: VoiceProfile) -> list:
    rules = []
    for field_name in profile.identity.model_fields:
        rule = getattr(profile.identity, field_name)
        if rule is not None:
            rules.append(rule)
    return rules


def _collect_linguistic_rules(profile: VoiceProfile) -> list:
    rules = []
    for field_name in profile.linguistic.model_fields:
        rule = getattr(profile.linguistic, field_name)
        if rule is not None:
            rules.append(rule)
    return rules


def _categories_with_stable_rules(profile: VoiceProfile) -> list[str]:
    categories = []
    category_map = {
        RuleCategory.IDENTITY.value: profile.identity,
        RuleCategory.COGNITIVE.value: profile.cognitive,
        RuleCategory.LINGUISTIC.value: profile.linguistic,
        RuleCategory.STYLISTIC.value: profile.stylistic,
        RuleCategory.INTERACTION.value: profile.interaction,
    }
    for cat_name, category_obj in category_map.items():
        for field_name in category_obj.model_fields:
            rule = getattr(category_obj, field_name)
            if rule is not None and rule.lifecycle_stage in STABLE_AND_ABOVE:
                categories.append(cat_name)
                break
    return categories
