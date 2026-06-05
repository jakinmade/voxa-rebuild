"""
Voxa — Policy Enforcement Layer
Layer 5 addition — Sprint 3.

Architecture Spec v9.2.0, Section 9.4.

Org-level constraints as org-level ContextOverrides.
Same data structure, higher precedence.
Cannot be modified by individual users.
Takes precedence over all user-level rules including boundaries.

Enterprise audit trail: full who/what/when/why. Append-only.
Admin notified on drift threshold breach.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

logger = structlog.get_logger(__name__)

# Enterprise audit trail — append-only, never mutated
_enterprise_audit: list[dict] = []


def enforce_org_policy(
    rendered_text: str,
    org_id: str,
    org_policies: dict,
    user_id: UUID,
    output_id: UUID,
) -> tuple[str | None, list[str]]:
    """
    Enforces org-level policy against a rendered output.
    If a forbidden phrase is present: output is rejected, not degraded.
    Org policy takes precedence over user boundary — both present, org policy wins.

    Returns (text_or_none, violations).
    """
    if org_id not in org_policies:
        return rendered_text, []

    policy = org_policies[org_id]
    violations: list[str] = []

    # Check forbidden phrases
    forbidden = policy.rules.get("forbidden_phrases", [])
    for phrase in forbidden:
        if phrase.lower() in rendered_text.lower():
            violations.append(f"org_policy_forbidden_phrase: '{phrase}'")

    # Check tone constraints
    tone_forbidden = policy.rules.get("tone_boundaries", [])
    for tone in tone_forbidden:
        if tone.lower() in rendered_text.lower():
            violations.append(f"org_policy_tone_violation: '{tone}'")

    if violations:
        _record_policy_violation(
            user_id=user_id,
            org_id=org_id,
            output_id=output_id,
            violations=violations,
        )
        logger.warning(
            "org_policy_violation_output_rejected",
            user_id=str(user_id),
            org_id=org_id,
            violations=violations,
        )
        return None, violations

    _record_policy_audit(
        user_id=user_id,
        org_id=org_id,
        output_id=output_id,
        result="passed",
    )
    return rendered_text, []


def _record_policy_violation(
    user_id: UUID,
    org_id: str,
    output_id: UUID,
    violations: list[str],
) -> None:
    _enterprise_audit.append({
        "audit_id": str(uuid4()),
        "type": "policy_violation",
        "user_id": str(user_id),
        "org_id": org_id,
        "output_id": str(output_id),
        "violations": violations,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "output_rejected",
    })


def _record_policy_audit(
    user_id: UUID,
    org_id: str,
    output_id: UUID,
    result: str,
) -> None:
    _enterprise_audit.append({
        "audit_id": str(uuid4()),
        "type": "policy_check",
        "user_id": str(user_id),
        "org_id": org_id,
        "output_id": str(output_id),
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def get_enterprise_audit_trail(
    user_id: UUID | None = None,
    org_id: str | None = None,
) -> list[dict]:
    """
    Returns the full enterprise audit trail.
    Filtered by user_id or org_id if provided.
    Append-only. Never mutated.
    """
    results = list(_enterprise_audit)
    if user_id:
        results = [e for e in results if e.get("user_id") == str(user_id)]
    if org_id:
        results = [e for e in results if e.get("org_id") == org_id]
    return results
