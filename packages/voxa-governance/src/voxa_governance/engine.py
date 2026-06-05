"""
Voxa — Voice Governance Engine (Layer 5)
The long-term moat. Auditable, provenance-tracked, enterprise-grade.

Architecture Spec v9.2.0, Section 9.

Sprint 1 scope:
- Rule provenance — source list on every rule
- Basic version history — profile version increments on every change
- Audit log — append-only record of calibration events

Sprint 3 adds:
- Immutable version snapshots with restore
- Voice Drift Monitor
- Policy Enforcement Layer
- Enterprise audit trail with admin notifications
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from voxa_core.entities import (
    CalibrationEvent,
    VoiceProfile,
    VoiceProfileVersion,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# In-memory audit log (Sprint 1)
# Sprint 3: persisted, append-only, enterprise audit trail
# ---------------------------------------------------------------------------

_audit_log: list[dict] = []
_version_history: dict[UUID, list[VoiceProfileVersion]] = {}


def record_calibration_event(event: CalibrationEvent) -> None:
    """
    Appends a calibration event to the audit log.
    Append-only. Never mutated.
    """
    entry = {
        "event_id": str(event.event_id),
        "user_id": str(event.user_id),
        "session_id": str(event.session_id),
        "edit_class": event.edit_class.value,
        "direction": event.direction,
        "rule_dimension": event.rule_dimension,
        "timestamp": event.timestamp.isoformat(),
        "profile_version_before": event.profile_version_before,
        "profile_version_after": event.profile_version_after,
    }
    _audit_log.append(entry)

    logger.info(
        "audit_event_recorded",
        event_id=str(event.event_id),
        user_id=str(event.user_id),
        edit_class=event.edit_class.value,
    )


def record_profile_version(version: VoiceProfileVersion) -> None:
    """
    Stores a profile version snapshot in the version history.
    Sprint 3: immutable snapshots with full restore capability.
    """
    user_id = version.user_id
    if user_id not in _version_history:
        _version_history[user_id] = []
    _version_history[user_id].append(version)

    logger.info(
        "profile_version_recorded",
        user_id=str(user_id),
        version=version.version,
    )


def get_version_history(user_id: UUID) -> list[VoiceProfileVersion]:
    """Returns all stored versions for a user. Ordered oldest to newest."""
    return _version_history.get(user_id, [])


def get_audit_log(user_id: UUID | None = None) -> list[dict]:
    """
    Returns the audit log.
    If user_id is provided, filters to that user only.
    Enterprise endpoint in Sprint 3.
    """
    if user_id is None:
        return list(_audit_log)
    return [e for e in _audit_log if e["user_id"] == str(user_id)]


def get_rule_provenance(profile: VoiceProfile, dimension: str) -> list[str]:
    """
    Returns the source provenance list for a given rule dimension.
    Every rule carries source history — no rule exists without it.
    """
    category_map = {
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

    rule = category_map.get(dimension)
    if rule is None:
        return []
    return rule.source
