"""
api/schemas/fix.py — pydantic models for POST /api/fix, matching
Engineering Architecture Section 11.2's documented field set, with two
deliberate, flagged departures (same convention check_draft.py's own
schema module uses for its documented-but-unimplementable field):

  - dimensions_to_address is accepted but currently a true no-op:
    Section 11.2 documents it as "which flagged dimensions the user
    wants addressed (default: all flagged)", but run_voice_render (and
    the engine underneath it) has no parameter for scoping the
    correction pass to a subset of dimensions — it always corrects
    everything score_render_delta flags as MISSED, and this field is
    not logged, not forwarded to telemetry (telemetry.emit has no
    matching parameter), and not read anywhere in routes/fix.py.
    Accepted now only so the extension can start sending it without a
    later breaking schema change, not because anything downstream
    consumes it yet. (Checked against the actual code, not assumed:
    check_draft.py's analogous `context` field carries a similar
    claim — "passed through to telemetry only" — that turned out to
    be inaccurate on inspection; req.context is never referenced
    anywhere in that file either. Not fixed here since it's a
    pre-existing, separate file — flagged instead of silently copying
    the same unverified claim into this one.)

  - request_id appears in BOTH the documented request and response
    shapes (Section 11.2), but means two different things: the
    request's request_id is "the check_draft call this Fix-it
    follows, for traceability" (a client-supplied correlation id from
    an earlier call); the response's request_id is this fix action's
    OWN id, the one evidence_seals and telemetry_events key on
    (Section 5.4/5.5: "ties back to the API call and its evidence_
    seals row" — this call, not an earlier one). Reusing one field
    name for both would collide, so the request field here is named
    check_request_id on the wire-adjacent Python side while keeping
    the documented JSON key (via alias) for contract compatibility —
    routes/fix.py generates a fresh uuid for the response's own
    request_id, exactly as check_draft.py already does for its call.
    check_request_id itself is logged (log.info in routes/fix.py, see
    that module) for manual traceability, but not persisted into
    telemetry_events — that table's fixed schema (Section 5.5) has a
    single request_id column, documented as tying back to THIS call's
    own evidence_seals row, with no second column for a prior call's
    id.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from api.schemas.check_draft import DimensionScore, BurrowsDelta  # noqa: F401 (re-exported for callers)


class FixRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    original_draft: str = Field(..., min_length=1)
    # See module docstring — wire key stays request_id (Section 11.2),
    # Python side is named for what it actually is.
    check_request_id: str | None = Field(default=None, alias="request_id")
    # See module docstring — accepted, not yet honoured by the engine.
    dimensions_to_address: list[str] | None = None
    user_context: str | None = None
    client_version: str | None = None
    # The extension always knows which composer surface it's running
    # in — required on check-draft's schema for the same reason, kept
    # consistent here since a Fix-it call always follows a Check call
    # on the same surface.
    surface: str = Field(..., pattern="^(linkedin|gmail)$")


class ContentLockResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    passed: bool = Field(..., alias="pass")
    reasons: list[str] = Field(default_factory=list)


class FixResponse(BaseModel):
    request_id: str
    corrected_text: str
    what_changed: list[str] = Field(default_factory=list)
    post_fix_predicted_score: int
    content_lock_result: ContentLockResult
    render_consumed: bool
    scoring_version: str
    timestamp: str
