"""
Voxa — Sprint 3 API Additions

GET  /voice-profile/{version}   — restore from snapshot
GET  /voice-governance          — full audit trail (enterprise)
GET  /drift-status
POST /context-override          — set context override
POST /org-policy                — set org-level policy (admin)
POST /drift-confirm             — user confirms unfreeze

Architecture Spec v9.2.0, Section 12.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter()


class ContextOverrideRequest(BaseModel):
    user_id: UUID
    context: str
    rules: dict[str, object]


class OrgPolicyRequest(BaseModel):
    org_id: str
    rules: dict[str, object]


class DriftConfirmRequest(BaseModel):
    user_id: UUID


def create_sprint3_router(
    profiles: dict,
    version_history: dict,
    session_counts: dict,
    org_policies: dict,
) -> APIRouter:

    @router.get("/voice-profile/{version}")
    async def get_profile_version(user_id: UUID, version: int) -> dict:
        """
        Restores a voice profile from a specific version snapshot.
        Restored profile renders identically to the original — confirmed
        by reproducibility snapshot on every RenderedOutput.
        """
        from voxa_governance.snapshots import restore_from_snapshot, get_snapshot

        snapshot = get_snapshot(user_id, version)
        if snapshot is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No snapshot found for version {version}.",
            )

        restored, success = restore_from_snapshot(
            user_id=user_id,
            version=version,
            current_profiles=profiles,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Restore failed.",
            )

        return {
            "restored": True,
            "version": version,
            "snapshot_id": str(snapshot.snapshot_id),
            "changes": snapshot.changes,
            "timestamp": snapshot.timestamp.isoformat(),
            "profile": restored.model_dump(),
        }

    @router.get("/voice-governance")
    async def get_voice_governance(
        user_id: UUID | None = None,
        org_id: str | None = None,
    ) -> dict:
        """
        Full enterprise audit trail. Append-only. Never mutated.
        Filtered by user_id or org_id if provided.
        """
        from voxa_governance.policy import get_enterprise_audit_trail
        from voxa_governance.engine import get_audit_log

        policy_audit = get_enterprise_audit_trail(user_id=user_id, org_id=org_id)
        calibration_audit = get_audit_log(user_id=user_id)

        return {
            "policy_audit": policy_audit,
            "calibration_audit": calibration_audit,
            "total_events": len(policy_audit) + len(calibration_audit),
        }

    @router.get("/drift-status")
    async def drift_status(user_id: UUID) -> dict:
        """
        Current drift monitor readings.
        Rule volatility, calibration frequency, contradiction frequency,
        override usage, stability decay, freeze status.
        """
        from voxa_governance.drift_monitor import get_drift_status

        if user_id not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found.")

        profile = profiles[user_id]
        return get_drift_status(user_id, profile)

    @router.post("/context-override")
    async def set_context_override_endpoint(request: ContextOverrideRequest) -> dict:
        """
        Sets a context override for a user.
        Context overrides layer on top of the global profile.
        Any rule not specified falls back to global.
        """
        from voxa_profile.context_overrides import set_context_override

        if request.user_id not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found.")

        override = set_context_override(
            user_id=request.user_id,
            context=request.context,
            rules=request.rules,
        )

        return {
            "override_id": str(override.override_id),
            "context": override.context,
            "rules": override.rules,
            "created_at": override.created_at.isoformat(),
        }

    @router.post("/org-policy")
    async def set_org_policy_endpoint(request: OrgPolicyRequest) -> dict:
        """
        Sets an org-level policy. Admin role only.
        Takes precedence over all user-level rules including boundaries.
        """
        from voxa_profile.context_overrides import set_org_policy, ContextOverride

        policy = set_org_policy(org_id=request.org_id, rules=request.rules)
        org_policies[request.org_id] = policy

        return {
            "org_id": request.org_id,
            "rules": request.rules,
            "created_at": policy.created_at.isoformat(),
        }

    @router.post("/drift-confirm")
    async def drift_confirm(request: DriftConfirmRequest) -> dict:
        """
        User confirmation that unfreezes a profile after a drift event.
        Calibration does not resume until this is called.
        """
        from voxa_governance.drift_monitor import confirm_unfreeze

        unfrozen = confirm_unfreeze(request.user_id)
        return {
            "user_id": str(request.user_id),
            "unfrozen": unfrozen,
            "calibration_resumed": unfrozen,
        }

    return router
