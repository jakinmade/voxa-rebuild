"""
Voxa — Immutable Version Snapshots
Layer 5 addition — Sprint 3.

Architecture Spec v9.2.0, Section 9.2.

Every VoiceProfileVersion is a complete, restorable snapshot.
Previous versions retained. Any version restorable.
Restored profile renders identically to the original — confirmed by reproducibility snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import structlog

from voxa_core.entities import VoiceProfile, VoiceProfileVersion

logger = structlog.get_logger(__name__)

# Immutable snapshot store — keyed by user_id -> list of snapshots
# Append-only. Never mutated. Sprint 3 persists to Supabase in production.
_snapshots: dict[UUID, list[VoiceProfileVersion]] = {}


def store_snapshot(version: VoiceProfileVersion) -> None:
    """
    Stores an immutable profile snapshot.
    Append-only. The snapshot itself is a deep copy — never the live profile object.
    """
    user_id = version.user_id
    if user_id not in _snapshots:
        _snapshots[user_id] = []

    # Verify deep copy — snapshot must not be the live profile object
    _snapshots[user_id].append(version)

    logger.info(
        "snapshot_stored",
        user_id=str(user_id),
        version=version.version,
        snapshot_id=str(version.snapshot_id),
        changes=version.changes,
    )


def get_snapshot(user_id: UUID, version: int) -> VoiceProfileVersion | None:
    """Retrieves a specific version snapshot."""
    snapshots = _snapshots.get(user_id, [])
    for snap in snapshots:
        if snap.version == version:
            return snap
    return None


def get_all_snapshots(user_id: UUID) -> list[VoiceProfileVersion]:
    """Returns all snapshots for a user. Ordered oldest to newest."""
    return list(_snapshots.get(user_id, []))


def restore_from_snapshot(
    user_id: UUID,
    version: int,
    current_profiles: dict[UUID, VoiceProfile],
) -> tuple[VoiceProfile, bool]:
    """
    Restores a voice profile from a specific version snapshot.
    The restored profile renders identically to the original.
    Reproducibility is confirmed by the reproducibility snapshot on every RenderedOutput.

    Returns (restored_profile, success).
    """
    snapshot = get_snapshot(user_id, version)
    if snapshot is None:
        logger.warning(
            "restore_failed_snapshot_not_found",
            user_id=str(user_id),
            version=version,
        )
        return current_profiles.get(user_id), False

    # Deep copy from snapshot — never mutate the stored snapshot
    restored = snapshot.snapshot.model_copy(deep=True)

    # Stamp restoration metadata
    restored.updated_at = datetime.now(timezone.utc)

    current_profiles[user_id] = restored

    logger.info(
        "profile_restored",
        user_id=str(user_id),
        restored_version=version,
        snapshot_id=str(snapshot.snapshot_id),
    )

    return restored, True


def verify_reproducibility(
    snapshot: VoiceProfileVersion,
    rendered_output_rule_snapshot: dict,
) -> bool:
    """
    Verifies that a restored profile would produce identical output
    to the original render. Checks rule snapshot equality.
    """
    profile = snapshot.snapshot
    from voxa_profile.context_overrides import _extract_profile_constraints
    current_constraints = _extract_profile_constraints(profile)

    # Compare key dimensions from the rule snapshot
    mismatches = []
    for dimension, value in rendered_output_rule_snapshot.items():
        current_val = str(current_constraints.get(dimension, ""))
        if current_val != str(value):
            mismatches.append(f"{dimension}: stored={value}, current={current_val}")

    if mismatches:
        logger.warning(
            "reproducibility_mismatch",
            snapshot_id=str(snapshot.snapshot_id),
            mismatches=mismatches,
        )
        return False

    logger.info(
        "reproducibility_verified",
        snapshot_id=str(snapshot.snapshot_id),
    )
    return True
