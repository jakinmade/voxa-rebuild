"""
Voxa — Context Overrides
Layer 2 addition — Sprint 3.

Architecture Spec v9.2.0, Section 10.10.

Context overrides layer on top of the global profile.
Any rule not specified in a context override falls back to global.

Supported contexts: email_investor, email_customer, internal, public.
Org-level policy: same data structure, higher precedence.
Cannot be modified by individual users.
Takes precedence over all user-level rules including boundaries.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

from voxa_core.entities import RuleMetadata, VoiceProfile
from voxa_core.enums import LifecycleStage

logger = structlog.get_logger(__name__)

SUPPORTED_CONTEXTS = {"email_investor", "email_customer", "internal", "public", "default"}


class ContextOverride:
    """
    A set of rule overrides for a specific context.
    Layers on top of the global profile.
    Rules not present fall back to global.
    """

    def __init__(self, context: str, rules: dict[str, object], is_org_policy: bool = False):
        if context not in SUPPORTED_CONTEXTS and not context.startswith("org_"):
            raise ValueError(f"Unsupported context: {context}. Supported: {SUPPORTED_CONTEXTS}")
        self.context = context
        self.rules = rules  # dimension -> value
        self.is_org_policy = is_org_policy
        self.created_at = datetime.now(timezone.utc)
        self.override_id = uuid4()

    def get(self, dimension: str) -> object | None:
        return self.rules.get(dimension)


# In-memory context override store (Sprint 3 — persisted in Supabase in production)
_user_overrides: dict[UUID, dict[str, ContextOverride]] = {}
_org_policies: dict[str, ContextOverride] = {}  # org_id -> policy


def set_context_override(
    user_id: UUID,
    context: str,
    rules: dict[str, object],
) -> ContextOverride:
    """
    Sets a context override for a user.
    System-detected with user confirmation (architecture spec Section 15).
    """
    override = ContextOverride(context=context, rules=rules, is_org_policy=False)
    if user_id not in _user_overrides:
        _user_overrides[user_id] = {}
    _user_overrides[user_id][context] = override

    logger.info(
        "context_override_set",
        user_id=str(user_id),
        context=context,
        dimensions=list(rules.keys()),
    )
    return override


def set_org_policy(org_id: str, rules: dict[str, object]) -> ContextOverride:
    """
    Sets an org-level policy. Admin role only.
    Takes precedence over all user-level rules including boundaries.
    """
    policy = ContextOverride(context=f"org_{org_id}", rules=rules, is_org_policy=True)
    _org_policies[org_id] = policy

    logger.info(
        "org_policy_set",
        org_id=org_id,
        dimensions=list(rules.keys()),
    )
    return policy


def get_effective_constraints(
    profile: VoiceProfile,
    context: str,
    org_id: str | None = None,
) -> dict[str, object]:
    """
    Resolves the effective constraints for rendering.

    Precedence (highest to lowest):
    1. Org-level policy (if org_id provided)
    2. User context override (if context has an override)
    3. Global profile rules
    4. Neutral defaults

    Any rule not in a higher layer falls back to the next.
    """
    from voxa_core.defaults import get_neutral_default

    # Build base constraints from global profile
    base_constraints = _extract_profile_constraints(profile)

    # Apply user context override
    user_overrides = _user_overrides.get(profile.user_id, {})
    context_override = user_overrides.get(context)

    if context_override:
        for dimension, value in context_override.rules.items():
            base_constraints[dimension] = value
            logger.info(
                "context_override_applied",
                user_id=str(profile.user_id),
                context=context,
                dimension=dimension,
                value=str(value),
            )

    # Apply org policy — highest precedence, cannot be overridden
    if org_id and org_id in _org_policies:
        policy = _org_policies[org_id]
        for dimension, value in policy.rules.items():
            base_constraints[dimension] = value
            logger.info(
                "org_policy_applied",
                org_id=org_id,
                dimension=dimension,
                value=str(value),
            )

    return base_constraints


def get_user_overrides(user_id: UUID) -> dict[str, dict]:
    """Returns all context overrides for a user."""
    overrides = _user_overrides.get(user_id, {})
    return {
        ctx: {
            "context": o.context,
            "rules": o.rules,
            "created_at": o.created_at.isoformat(),
            "override_id": str(o.override_id),
        }
        for ctx, o in overrides.items()
    }


def _extract_profile_constraints(profile: VoiceProfile) -> dict[str, object]:
    """Extracts all current rule values from the global profile."""
    from voxa_core.defaults import get_neutral_default

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

    constraints: dict[str, object] = {}
    for dimension, rule in dimension_rule_map.items():
        if rule is not None:
            constraints[dimension] = rule.value
        else:
            try:
                constraints[dimension] = get_neutral_default(dimension)
            except KeyError:
                constraints[dimension] = None

    return constraints
