"""
Voxa — FastAPI Application
Mounts all layers. Exposes Sprint 1 endpoints.

Sprint 1 endpoints:
  POST /humanise
  POST /render
  POST /calibrate
  GET  /voice-profile
  GET  /voice-history

Architecture Spec v9.2.0, Section 12.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel

from voxa_core.entities import (
    CalibrationEvent,
    HumanisedProfile,
    RenderedOutput,
    RuleCandidate,
    RuleObservation,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import SourceType

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Voxa",
    description="Governed Communication Identity System — v9.2.0",
    version="9.2.0-sprint1",
)

# ---------------------------------------------------------------------------
# In-memory stores (Sprint 1)
# Sprint 2/3: Supabase persistence
# ---------------------------------------------------------------------------
_profiles: dict[UUID, VoiceProfile] = {}
_version_history: dict[UUID, list[VoiceProfileVersion]] = {}
_observations: dict[UUID, list[RuleObservation]] = {}
_rendered_outputs: dict[UUID, RenderedOutput] = {}
_calibration_events: list[CalibrationEvent] = []
_session_counts: dict[UUID, int] = {}
_candidates: dict[UUID, list] = {}
_rule_traces: dict[UUID, object] = {}
_org_policies: dict[str, object] = {}


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class HumaniseRequest(BaseModel):
    user_id: UUID
    raw_input: str
    source_type: SourceType = SourceType.ONBOARDING


class HumaniseResponse(BaseModel):
    humanised_profile: HumanisedProfile
    fact_count: int
    conflict_count: int


class RenderRequest(BaseModel):
    user_id: UUID
    input_text: str
    context: str = "default"


class RenderResponse(BaseModel):
    output: RenderedOutput | None
    boundary_blocked: bool
    is_bootstrap_output: bool


class CalibrateRequest(BaseModel):
    user_id: UUID
    rendered_output_id: UUID
    original_text: str
    edited_text: str
    user_instruction: str = ""


class CalibrateResponse(BaseModel):
    accepted: bool
    edit_class: str
    observations_created: int
    candidates_promoted: int
    profile_version: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/humanise", response_model=HumaniseResponse, status_code=status.HTTP_200_OK)
async def humanise(request: HumaniseRequest) -> HumaniseResponse:
    """
    Raw input → HumanisedProfile → VoiceProfile.
    Full four-step humanisation pipeline.
    Builds profile on first call for a user.
    """
    from voxa_humanisation.engine import humanise as run_humanise
    from voxa_profile.builder import build_profile, increment_version
    from voxa_governance.engine import record_profile_version

    humanised = run_humanise(
        raw_input=request.raw_input,
        user_id=request.user_id,
        source_type=request.source_type,
    )

    # Build or update profile
    if request.user_id not in _profiles:
        profile = build_profile(humanised)
        _profiles[request.user_id] = profile
        snapshot = VoiceProfileVersion(
            user_id=request.user_id,
            version=profile.version,
            snapshot=profile.model_copy(deep=True),
            changes=["initial_profile_build"],
        )
        _version_history.setdefault(request.user_id, []).append(snapshot)
        record_profile_version(snapshot)
    else:
        # Re-build from new input and merge (Sprint 1: rebuild; Sprint 2: merge logic)
        profile = build_profile(humanised)
        _profiles[request.user_id] = profile

    logger.info(
        "humanise_endpoint_complete",
        user_id=str(request.user_id),
        fact_count=len(humanised.facts),
    )

    return HumaniseResponse(
        humanised_profile=humanised,
        fact_count=len(humanised.facts),
        conflict_count=len(humanised.conflicts),
    )


@app.post("/render", response_model=RenderResponse, status_code=status.HTTP_200_OK)
async def render(request: RenderRequest) -> RenderResponse:
    """
    VoiceProfile + input → RenderedOutput.
    Checks bootstrap state first.
    Returns boundary_blocked=True with null output if boundary check fails.
    """
    from voxa_rendering.engine import render as run_render

    if request.user_id not in _profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No voice profile found. Call /humanise first.",
        )

    profile = _profiles[request.user_id]
    session_id = uuid4()
    session_count = _session_counts.get(request.user_id, 0)

    output = await run_render(
        input_text=request.input_text,
        profile=profile,
        session_id=session_id,
        context=request.context,
        calibration_session_count=session_count,
    )

    if output is not None:
        _rendered_outputs[output.output_id] = output

    return RenderResponse(
        output=output,
        boundary_blocked=(output is None),
        is_bootstrap_output=(output.is_bootstrap_output if output else False),
    )


@app.post("/calibrate", response_model=CalibrateResponse, status_code=status.HTTP_200_OK)
async def calibrate(request: CalibrateRequest) -> CalibrateResponse:
    """
    RenderedOutput + user edit → CalibrationEvent + RuleObservation or RuleCandidate.
    Voice edits proceed. All others discarded.
    """
    from voxa_calibration.engine import calibrate as run_calibrate
    from voxa_governance.engine import record_calibration_event, record_profile_version
    from voxa_profile.builder import increment_version

    if request.user_id not in _profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No voice profile found.",
        )

    if request.rendered_output_id not in _rendered_outputs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rendered output not found.",
        )

    profile = _profiles[request.user_id]
    rendered_output = _rendered_outputs[request.rendered_output_id]
    existing_observations = _observations.get(request.user_id, [])

    event, observations, candidates = run_calibrate(
        rendered_output=rendered_output,
        original_text=request.original_text,
        edited_text=request.edited_text,
        user_instruction=request.user_instruction,
        profile=profile,
        existing_observations=existing_observations,
    )

    if event is None:
        # Non-voice edit — discarded
        from voxa_calibration.engine import classify_edit
        edit_class = classify_edit(
            request.original_text,
            request.edited_text,
            request.user_instruction,
        )
        return CalibrateResponse(
            accepted=False,
            edit_class=edit_class.value,
            observations_created=0,
            candidates_promoted=0,
            profile_version=profile.version,
        )

    # Store observations
    _observations.setdefault(request.user_id, []).extend(observations)

    # Record calibration event
    event.profile_version_after = profile.version
    _calibration_events.append(event)
    record_calibration_event(event)

    # Increment profile version if candidates were promoted
    if candidates:
        snapshot = increment_version(
            profile=profile,
            changes=[f"candidate_promoted:{c.rule_dimension}" for c in candidates],
        )
        _version_history.setdefault(request.user_id, []).append(snapshot)
        record_profile_version(snapshot)

    # Increment session count
    _session_counts[request.user_id] = _session_counts.get(request.user_id, 0) + 1

    return CalibrateResponse(
        accepted=True,
        edit_class=event.edit_class.value,
        observations_created=len(observations),
        candidates_promoted=len(candidates),
        profile_version=profile.version,
    )


@app.get("/voice-profile", status_code=status.HTTP_200_OK)
async def get_voice_profile(user_id: UUID) -> VoiceProfile:
    """Current voice profile with all rules, metadata, and lifecycle stages."""
    if user_id not in _profiles:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No voice profile found.",
        )
    return _profiles[user_id]


@app.get("/voice-history", status_code=status.HTTP_200_OK)
async def get_voice_history(user_id: UUID) -> list[dict]:
    """Version history — list of versions with change summaries."""
    history = _version_history.get(user_id, [])
    return [
        {
            "version": v.version,
            "timestamp": v.timestamp.isoformat(),
            "changes": v.changes,
        }
        for v in history
    ]


# Sprint 3 routes
from voxa_api.sprint3_routes import create_sprint3_router as _s3r
_sprint3_router = _s3r(
    profiles=_profiles,
    version_history=_version_history,
    session_counts=_session_counts,
    org_policies=_org_policies,
)
app.include_router(_sprint3_router)


# Sprint 2 routes
from voxa_api.sprint2_routes import create_sprint2_router as _s2r
_sprint2_router = _s2r(
    profiles=_profiles,
    candidates_store=_candidates,
    rendered_outputs=_rendered_outputs,
    version_history=_version_history,
    rule_traces=_rule_traces,
    session_counts=_session_counts,
)
app.include_router(_sprint2_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": "9.2.0-sprint1"}
