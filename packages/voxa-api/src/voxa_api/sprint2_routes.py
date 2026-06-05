"""
Voxa — Sprint 2 API Additions
New endpoints added in Sprint 2.

POST /validate-calibration
POST /apply-calibration
GET  /explain-render/{output_id}
GET  /bootstrap-status

Architecture Spec v9.2.0, Section 12.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from voxa_core.entities import (
    RuleCandidate,
    RuleMetadata,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import LifecycleStage

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class ValidateCalibrateRequest(BaseModel):
    user_id: UUID
    candidate_ids: list[UUID]


class ValidateCalibrateResponse(BaseModel):
    approved_candidates: list[dict]
    rejected_candidates: list[dict]


class ApplyCalibrateRequest(BaseModel):
    user_id: UUID
    approved_candidate_ids: list[UUID]


class ApplyCalibrateResponse(BaseModel):
    rules_promoted: list[dict]
    profile_version: int


class BootstrapStatusResponse(BaseModel):
    is_renderable: bool
    is_bootstrap_complete: bool
    stable_rule_count: int
    categories_represented: list[str]
    missing_requirements: list[str]
    rules_by_stage: dict[str, int]
    calibration_session_count: int


# ---------------------------------------------------------------------------
# These endpoints share state with the main API.
# Injected at mount time.
# ---------------------------------------------------------------------------

def create_sprint2_router(
    profiles: dict,
    candidates_store: dict,
    rendered_outputs: dict,
    version_history: dict,
    rule_traces: dict,
    session_counts: dict,
) -> APIRouter:

    @router.post("/validate-calibration", response_model=ValidateCalibrateResponse)
    async def validate_calibration(request: ValidateCalibrateRequest) -> ValidateCalibrateResponse:
        """
        Runs the full validation gate on rule candidates.
        Returns approved candidates only.
        Does not update the profile directly.
        """
        from voxa_calibration.sprint2 import run_validation_gate

        user_candidates: list[RuleCandidate] = candidates_store.get(request.user_id, [])

        requested = [c for c in user_candidates if c.candidate_id in request.candidate_ids]
        if not requested:
            raise HTTPException(status_code=404, detail="No matching candidates found.")

        approved = []
        rejected = []

        for candidate in requested:
            # Build evidence timestamps from candidate creation time (Sprint 1 has limited data)
            timestamps = [candidate.created_at, candidate.last_seen]
            values = [str(candidate.candidate_value)] * candidate.evidence_count

            ok, reason, confidence = run_validation_gate(
                candidate=candidate,
                evidence_timestamps=timestamps,
                values_observed=values,
                has_active_conflict=False,
            )

            entry = {
                "candidate_id": str(candidate.candidate_id),
                "rule_dimension": candidate.rule_dimension,
                "candidate_value": candidate.candidate_value,
                "evidence_count": candidate.evidence_count,
                "computed_confidence": confidence,
                "reason": reason,
            }

            if ok:
                approved.append(entry)
            else:
                rejected.append(entry)

        return ValidateCalibrateResponse(
            approved_candidates=approved,
            rejected_candidates=rejected,
        )

    @router.post("/apply-calibration", response_model=ApplyCalibrateResponse)
    async def apply_calibration(request: ApplyCalibrateRequest) -> ApplyCalibrateResponse:
        """
        Applies approved candidates to the voice profile.
        Promotes to PROVISIONAL RULE. Creates new VoiceProfileVersion.
        """
        from voxa_profile.builder import increment_version
        from voxa_governance.engine import record_profile_version

        if request.user_id not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found.")

        profile: VoiceProfile = profiles[request.user_id]
        user_candidates = candidates_store.get(request.user_id, [])
        approved = [c for c in user_candidates if c.candidate_id in request.approved_candidate_ids]

        if not approved:
            raise HTTPException(status_code=404, detail="No matching approved candidates.")

        promoted = []
        changes = []

        dimension_map = {
            "cadence": (profile.identity, "cadence"),
            "compression": (profile.identity, "compression"),
            "directness": (profile.identity, "directness"),
            "warmth": (profile.identity, "warmth"),
            "formality": (profile.identity, "formality"),
            "reasoning_style": (profile.cognitive, "reasoning_style"),
            "decision_style": (profile.cognitive, "decision_style"),
            "confidence_expression": (profile.cognitive, "confidence_expression"),
            "preferred_verbs": (profile.linguistic, "preferred_verbs"),
            "forbidden_phrases": (profile.linguistic, "forbidden_phrases"),
            "humour": (profile.stylistic, "humour"),
            "intensity": (profile.stylistic, "intensity"),
            "audience_positioning": (profile.interaction, "audience_positioning"),
        }

        for candidate in approved:
            dim = candidate.rule_dimension
            if dim not in dimension_map:
                continue

            category, field = dimension_map[dim]
            existing_rule = getattr(category, field)

            if existing_rule is not None:
                # Promote existing rule
                existing_rule.lifecycle_stage = LifecycleStage.PROVISIONAL
                existing_rule.confidence = candidate.confidence
                existing_rule.evidence_count = candidate.evidence_count
                existing_rule.stability = 0.45
            else:
                # Create new rule at PROVISIONAL
                new_rule = RuleMetadata(
                    value=candidate.candidate_value,
                    confidence=candidate.confidence,
                    evidence_count=candidate.evidence_count,
                    source=[str(obs_id) for obs_id in candidate.supporting_observations],
                    stability=0.45,
                    decay_rate=0.02,
                    lifecycle_stage=LifecycleStage.PROVISIONAL,
                )
                setattr(category, field, new_rule)

            promoted.append({
                "dimension": dim,
                "value": str(candidate.candidate_value),
                "lifecycle_stage": LifecycleStage.PROVISIONAL.value,
                "confidence": candidate.confidence,
            })
            changes.append(f"promoted_to_provisional:{dim}")

        snapshot = increment_version(profile, changes=changes)
        version_history.setdefault(request.user_id, []).append(snapshot)
        record_profile_version(snapshot)

        return ApplyCalibrateResponse(
            rules_promoted=promoted,
            profile_version=profile.version,
        )

    @router.get("/explain-render/{output_id}")
    async def explain_render(output_id: UUID) -> dict:
        """
        Full rule trace for a rendered output.
        Rules applied, rules suppressed, boundary checks, neutral defaults,
        provisional rules flagged, profile version, engine version.
        The trust interface.
        """
        if output_id not in rule_traces:
            if output_id not in rendered_outputs:
                raise HTTPException(status_code=404, detail="Output not found.")
            # No trace stored — return basic reproducibility metadata
            output = rendered_outputs[output_id]
            return {
                "output_id": str(output_id),
                "reproducibility": output.reproducibility.model_dump(),
                "neutral_defaults_used": [d.model_dump() for d in output.neutral_defaults_used],
                "note": "Full rule trace available after Sprint 2 rendering pipeline upgrade.",
            }

        trace = rule_traces[output_id]
        return {
            "output_id": str(output_id),
            "profile_version": trace.profile_version,
            "engine_version": trace.engine_version,
            "context": trace.context,
            "rules_applied": trace.rules_applied,
            "rules_suppressed": trace.rules_suppressed,
            "boundary_checks": trace.boundary_checks,
            "neutral_defaults_used": trace.neutral_defaults_used,
            "provisional_rules_applied": trace.provisional_rules_applied,
            "interaction_resolutions": trace.interaction_resolutions,
        }

    @router.get("/bootstrap-status", response_model=BootstrapStatusResponse)
    async def bootstrap_status(user_id: UUID) -> BootstrapStatusResponse:
        """
        Current bootstrap completeness.
        Rules at each lifecycle stage. Remaining gaps.
        Progress toward minimum renderable profile.
        Surfaced to the user as a feature, not hidden as a system limitation.
        """
        from voxa_core.bootstrap import check_bootstrap

        if user_id not in profiles:
            raise HTTPException(status_code=404, detail="Profile not found.")

        profile = profiles[user_id]
        session_count = session_counts.get(user_id, 0)
        status_result = check_bootstrap(profile, calibration_session_count=session_count)

        return BootstrapStatusResponse(
            is_renderable=status_result.is_renderable,
            is_bootstrap_complete=status_result.is_bootstrap_complete,
            stable_rule_count=status_result.stable_rule_count,
            categories_represented=status_result.categories_represented,
            missing_requirements=status_result.missing_requirements,
            rules_by_stage=status_result.rules_by_stage,
            calibration_session_count=session_count,
        )

    return router
