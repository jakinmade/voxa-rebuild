"""
api/routes/fix.py — POST /api/fix (Full Spec Section 3.5.2, Engineering
Architecture Section 11.2).

The credit-consuming counterpart to check_draft.py's free Voice Check
(Section 2.1): calls render_pipeline.run_voice_render — the exact same
core app.py's Streamlit wrapper calls — with this route as its second
shell (Section 4.1's "api/routes/fix.py is the second shell, calling
the exact same core with its own concerns [render-credit accounting,
evidence sealing, telemetry] layered on top instead"). No engine logic
lives here; every scoring/correction/generation decision is made
inside run_voice_render, unchanged from what voicova.com's own Fix-it
flow already does.

Two documented spec gaps, both flagged rather than silently patched
over — see api/schemas/fix.py's module docstring for the request_id
naming collision, and its dimensions_to_address field for the
accepted-but-not-yet-engine-supported scoping request.

Credit accounting order mirrors app.py's own _run_render exactly,
because this is a genuine safety property, not just a style choice:
lifetime entitlement (lifetime_cap.py) is checked and reserved BEFORE
the daily site-wide spend guard (render_cap.py), which is itself
checked and reserved BEFORE the one Anthropic-calling function in this
codebase, run_voice_render, ever runs. render_cap.py is not mentioned
anywhere in this document's Section 4/11, but its own module docstring
frames it as a product-wide cost control, not a Streamlit-only one —
every code path that can trigger an LLM call needs to respect the same
shared daily budget, and this route is now a second such path.
Skipping it here would let the extension silently bypass a safety
rail voicova.com itself cannot bypass.

A failed generation call releases the reserved lifetime render, same
as app.py's release-on-failure path and for the same reason: an
API failure should never cost the person one of their 15 free renders
(or one of their surfaced credits, once metered billing exists —
Section 2.6 is unchanged by this route).
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from logging_config import get_logger
from render_pipeline import run_voice_render
from render_cap import check_and_reserve_render
from lifetime_cap import check_and_reserve_lifetime_render, release_reserved_lifetime_render
from api.auth.middleware import Identity, resolve_identity
from api.auth import rate_limit
from api.db.profile_lookup import get_profile_bundle
from api.db.evidence_seals import create_seal
from api.evidence import seal as evidence
from api.telemetry import events as telemetry
from api import formatting
from api.schemas.fix import FixRequest, FixResponse, ContentLockResult

log = get_logger(__name__)
router = APIRouter()

# Fails closed on this route deliberately, unlike check-draft's own
# api_key resolution (score_draft_check makes no LLM call at all, so
# check_draft.py never needs a key). run_voice_render always calls the
# LLM, so a missing key here must reject every request up front rather
# than let each one reach run_voice_render only to fail there and
# still cost a reserved lifetime render — same api_key source app.py
# already reads (os.environ; st.secrets doesn't exist outside a
# running Streamlit script, see render_pipeline.py's own
# _generate_voice_profile_summary docstring for the same point).
_ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"


@router.post("/api/fix", response_model=FixResponse)
def fix(req: FixRequest, identity: Identity = Depends(resolve_identity)):
    rate_limit.enforce(identity.installation_id)

    api_key = os.environ.get(_ANTHROPIC_API_KEY_ENV)
    if not api_key:
        log.error("fix_failed", reason="api_key_missing")
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})

    profile = get_profile_bundle(identity.profile_id)
    if profile is None:
        # Same "shouldn't be reachable in practice" case check_draft.py
        # documents — a token can't exist for a profile without a
        # usable baseline (routes/extension_auth.py refuses to mint
        # one). Mapped the same way: engine_error, not a new code.
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})

    # Entitlement gate BEFORE the daily spend guard, before any API
    # call — see module docstring. Section 11.7 defines exactly one
    # error_code for this state (render_cap_exhausted, 402); reused
    # for both this device's lifetime cap and the site-wide daily cap
    # below rather than inventing a second code the extension's fixed
    # state machine doesn't know how to handle.
    lifetime_allowed, lifetime_used, lifetime_limit = check_and_reserve_lifetime_render(
        identity.profile_id
    )
    if not lifetime_allowed:
        log.info(
            "fix_blocked", reason="lifetime_cap_reached",
            used=lifetime_used, limit=lifetime_limit,
        )
        raise HTTPException(status_code=402, detail={"error_code": "render_cap_exhausted"})

    daily_allowed, daily_used, daily_limit = check_and_reserve_render()
    if not daily_allowed:
        log.info("fix_blocked", reason="daily_cap_reached", used=daily_used, limit=daily_limit)
        release_reserved_lifetime_render(identity.profile_id)
        raise HTTPException(status_code=402, detail={"error_code": "render_cap_exhausted"})

    request_id = str(uuid.uuid4())
    started = time.monotonic()
    log.info(
        "fix_start", request_id=request_id,
        check_request_id=req.check_request_id, surface=req.surface,
    )

    result = run_voice_render(
        input_text=req.original_draft,
        api_key=api_key,
        raw_text=profile["raw_text"],
        sample2_completions=profile["sample2_completions"],
        baseline=profile["baseline_fingerprint"],
        baseline_texts=profile["baseline_texts"],
        voice_profile_summary=profile.get("voice_profile_summary"),
        starter_baseline=profile.get("starter_baseline"),
        baseline_fingerprints_by_format=profile.get("baseline_fingerprints_by_format"),
        render_context=req.user_context or "",
        # platform_format ("social" | "email") is a distinct,
        # voicova.com-only opt-in (app.py's "elevate" line-editing
        # toggle) — NOT a mapping from req.surface (linkedin | gmail,
        # Section 3.2). Section 11.2's Fix-it contract has no field
        # for it, so it's left at its default (None) here rather than
        # guessed from the composer surface.
    )

    if not result.success:
        # Release-on-failure — see module docstring. Never a daily-cap
        # release: that guard is a product-wide spend limit unrelated
        # to this device's own entitlement, and a failed call still
        # consumed the API attempt it was reserved for.
        release_reserved_lifetime_render(identity.profile_id)
        log.error("fix_failed", reason="render_failed", request_id=request_id)
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})

    report = result.voice_report or {}
    content_lock = formatting.content_lock_result(
        report, result.insertion_check, result.content_integrity_hard_fail
    )

    seal_result = formatting.seal_result_from_voice_report(report)
    receipt = evidence.seal(
        request_id=request_id,
        profile_id=identity.profile_id,
        action="fix",
        input_text=req.original_draft,
        result=seal_result,
        content_lock=content_lock,
    )
    create_seal(receipt)

    latency_ms = int((time.monotonic() - started) * 1000)
    classified_verdict = formatting.classify_result(
        seal_result["verdict"], seal_result["ai_tells_clean"]
    )
    telemetry.emit(
        installation_id=identity.installation_id,
        profile_id=identity.profile_id,
        surface=req.surface,
        action="fix",
        request_id=request_id,
        scoring_version=evidence.SCORING_VERSION,
        extension_version=req.client_version,
        result=classified_verdict,
        fix_requested=True,
        render_credit_consumed=True,
        latency_ms=latency_ms,
        draft_length=len(req.original_draft),
    )

    return FixResponse(
        request_id=request_id,
        corrected_text=result.output_text,
        what_changed=report.get("biggest_changes", []),
        post_fix_predicted_score=round(report.get("voice_match", 0)),
        content_lock_result=ContentLockResult(**content_lock),
        render_consumed=True,
        scoring_version=evidence.SCORING_VERSION,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
