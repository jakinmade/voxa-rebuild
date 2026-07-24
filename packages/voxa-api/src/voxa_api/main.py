"""
Voxa — FastAPI Application
Architecture Spec v9.2.0 | Build v9.5.0

All state is behind repository interfaces.
Swap VOXA_REPOSITORY=supabase for production persistence.
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
from voxa_api.repositories import get_repositories

logger = structlog.get_logger(__name__)

from fastapi import Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from voxa_api.middleware import check_rate_limit, check_api_key

app = FastAPI(
    title="Voxa",
    description="Governed Communication Identity System — v9.5.0",
    version="9.5.0",
)

@app.middleware("http")
async def voxa_middleware(request: StarletteRequest, call_next):
    try:
        check_rate_limit(request)
        check_api_key(request)
    except Exception as e:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
    return await call_next(request)

# ---------------------------------------------------------------------------
# Repository initialisation — swap backend via VOXA_REPOSITORY env var
# ---------------------------------------------------------------------------
profile_repo, calibration_repo, governance_repo = get_repositories()

# Org policies (lightweight — kept in memory, set by admin at startup)
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
    from voxa_humanisation.engine import humanise as run_humanise
    from voxa_profile.builder import build_profile, merge_profile, increment_version
    from voxa_governance.engine import record_profile_version

    humanised = run_humanise(
        raw_input=request.raw_input,
        user_id=request.user_id,
        source_type=request.source_type,
    )

    if not profile_repo.exists(request.user_id):
        profile = build_profile(humanised)
        profile_repo.save(profile)
        snapshot = VoiceProfileVersion(
            user_id=request.user_id,
            version=profile.version,
            snapshot=profile.model_copy(deep=True),
            changes=["initial_profile_build"],
        )
        profile_repo.save_version(snapshot)
        record_profile_version(snapshot)
    else:
        profile = profile_repo.get(request.user_id)
        changes = merge_profile(profile, humanised)
        if changes:
            snapshot = increment_version(profile, changes=changes)
            profile_repo.save(profile)
            profile_repo.save_version(snapshot)
            record_profile_version(snapshot)

    return HumaniseResponse(
        humanised_profile=humanised,
        fact_count=len(humanised.facts),
        conflict_count=len(humanised.conflicts),
    )


@app.post("/render", response_model=RenderResponse, status_code=status.HTTP_200_OK)
async def render(request: RenderRequest) -> RenderResponse:
    from voxa_rendering.engine import render as run_render

    if not profile_repo.exists(request.user_id):
        raise HTTPException(status_code=404, detail="No voice profile found. Call /humanise first.")

    profile = profile_repo.get(request.user_id)
    session_id = uuid4()
    session_count = profile_repo.get_session_count(request.user_id)

    output = await run_render(
        input_text=request.input_text,
        profile=profile,
        session_id=session_id,
        context=request.context,
        calibration_session_count=session_count,
    )

    if output is not None:
        calibration_repo.save_rendered_output(output)

    return RenderResponse(
        output=output,
        boundary_blocked=(output is None),
        is_bootstrap_output=(output.is_bootstrap_output if output else False),
    )


@app.post("/calibrate", response_model=CalibrateResponse, status_code=status.HTTP_200_OK)
async def calibrate(request: CalibrateRequest) -> CalibrateResponse:
    from voxa_calibration.engine import calibrate as run_calibrate
    from voxa_governance.engine import record_calibration_event, record_profile_version
    from voxa_profile.builder import increment_version

    if not profile_repo.exists(request.user_id):
        raise HTTPException(status_code=404, detail="No voice profile found.")

    rendered_output = calibration_repo.get_rendered_output(request.rendered_output_id)
    if rendered_output is None:
        raise HTTPException(status_code=404, detail="Rendered output not found.")

    profile = profile_repo.get(request.user_id)
    existing_observations = calibration_repo.list_observations(request.user_id)

    event, observations, candidates = run_calibrate(
        rendered_output=rendered_output,
        original_text=request.original_text,
        edited_text=request.edited_text,
        user_instruction=request.user_instruction,
        profile=profile,
        existing_observations=existing_observations,
    )

    if event is None:
        from voxa_calibration.engine import classify_edit
        edit_class = classify_edit(
            request.original_text, request.edited_text, request.user_instruction
        )
        return CalibrateResponse(
            accepted=False, edit_class=edit_class.value,
            observations_created=0, candidates_promoted=0,
            profile_version=profile.version,
        )

    for obs in observations:
        calibration_repo.save_observation(obs)

    event.profile_version_after = profile.version
    calibration_repo.save_event(event)
    record_calibration_event(event)

    if candidates:
        for c in candidates:
            calibration_repo.save_candidate(c)
        snapshot = increment_version(
            profile, changes=[f"candidate_promoted:{c.rule_dimension}" for c in candidates]
        )
        profile_repo.save(profile)
        profile_repo.save_version(snapshot)
        record_profile_version(snapshot)

    profile_repo.increment_session_count(request.user_id)

    return CalibrateResponse(
        accepted=True,
        edit_class=event.edit_class.value,
        observations_created=len(observations),
        candidates_promoted=len(candidates),
        profile_version=profile.version,
    )


@app.get("/voice-profile", status_code=status.HTTP_200_OK)
async def get_voice_profile(user_id: UUID) -> VoiceProfile:
    if not profile_repo.exists(user_id):
        raise HTTPException(status_code=404, detail="No voice profile found.")
    return profile_repo.get(user_id)


@app.get("/voice-history", status_code=status.HTTP_200_OK)
async def get_voice_history(user_id: UUID) -> list[dict]:
    history = profile_repo.list_versions(user_id)
    return [
        {"version": v.version, "timestamp": v.timestamp.isoformat(), "changes": v.changes}
        for v in history
    ]


@app.get("/health")
async def health() -> dict:
    import os
    return {
        "status": "ok",
        "version": "9.5.0",
        "repository": os.environ.get("VOXA_REPOSITORY", "memory"),
    }


# ---------------------------------------------------------------------------
# Sprint 2 routes
# ---------------------------------------------------------------------------
from voxa_api.sprint2_routes import create_sprint2_router as _s2r

_sprint2_router = _s2r(
    profile_repo=profile_repo,
    calibration_repo=calibration_repo,
    governance_repo=governance_repo,
)
app.include_router(_sprint2_router)


# ---------------------------------------------------------------------------
# Sprint 3 routes
# ---------------------------------------------------------------------------
from voxa_api.sprint3_routes import create_sprint3_router as _s3r

_sprint3_router = _s3r(
    profile_repo=profile_repo,
    org_policies=_org_policies,
)
app.include_router(_sprint3_router)

from voxa_api.check_routes import router as _check_router
app.include_router(_check_router)
