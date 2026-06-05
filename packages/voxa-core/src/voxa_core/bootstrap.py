"""
Voxa — Bootstrap State
Architecture Spec v9.2.0, Section 6.1 and 6.4.
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


def _iter_category_rules(category_obj) -> list:
    rules = []
    for field_name in type(category_obj).model_fields:
        rule = getattr(category_obj, field_name)
        if rule is not None:
            rules.append(rule)
    return rules


def check_bootstrap(profile: VoiceProfile, calibration_session_count: int = 0) -> BootstrapStatus:
    missing: list[str] = []
    rules_by_stage: dict[str, int] = {stage.value: 0 for stage in LifecycleStage}

    all_rules = []
    for cat in [profile.identity, profile.cognitive, profile.linguistic,
                profile.stylistic, profile.interaction]:
        all_rules.extend(_iter_category_rules(cat))

    for rule in all_rules:
        rules_by_stage[rule.lifecycle_stage.value] += 1

    # 1. At least one Identity Rule at PROVISIONAL or above
    identity_rules = _iter_category_rules(profile.identity)
    if not any(r.lifecycle_stage in PROVISIONAL_AND_ABOVE for r in identity_rules):
        missing.append("No Identity Rule at PROVISIONAL or above")

    # 2. At least one Linguistic Rule at CANDIDATE or above
    linguistic_rules = _iter_category_rules(profile.linguistic)
    if not any(r.lifecycle_stage in CANDIDATE_AND_ABOVE for r in linguistic_rules):
        missing.append("No Linguistic Rule at CANDIDATE or above")

    # 3. At least one Boundary defined
    if (profile.boundaries.tone_boundaries is None
            and profile.boundaries.content_boundaries is None):
        missing.append("No Boundary defined")

    is_renderable = len(missing) == 0

    stable_count = sum(1 for r in all_rules if r.lifecycle_stage in STABLE_AND_ABOVE)

    categories_represented = []
    category_map = {
        RuleCategory.IDENTITY.value: profile.identity,
        RuleCategory.COGNITIVE.value: profile.cognitive,
        RuleCategory.LINGUISTIC.value: profile.linguistic,
        RuleCategory.STYLISTIC.value: profile.stylistic,
        RuleCategory.INTERACTION.value: profile.interaction,
    }
    for cat_name, cat_obj in category_map.items():
        rules = _iter_category_rules(cat_obj)
        if any(r.lifecycle_stage in STABLE_AND_ABOVE for r in rules):
            categories_represented.append(cat_name)

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
