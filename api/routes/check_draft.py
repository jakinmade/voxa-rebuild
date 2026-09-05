"""
api/routes/check_draft.py — POST /api/check-draft (Full Spec Section
3.5.1, Engineering Architecture Section 4.4).

Free, no render credit consumed — Voice Check is a read-only score
against the existing engine, not a rewrite (Full Spec Section 2.6).

Deliberately does NOT call a content-lock check, and the response
deliberately omits a content_lock field: score_draft_check's own
docstring is explicit that it skips content-lock/attribution checks on
purpose, since those measure fidelity between an input and a
REWRITTEN output, and there is no rewrite in a Voice Check (draft_text
is the only text in play). Both the Engineering Architecture doc's
Section 4.4 pseudocode and the Full Spec's Section 3.5.1 field list
assume a content_lock result that this action cannot produce — this is
a correction to those documents (tracked for the next spec revision),
not a gap in this implementation.

verdict in the response is the three-value good | borderline | failed
Section 3.5.1 documents — mapped from the engine's native two-value
PASS | REVIEW verdict plus the separate ai_tells_clean signal, via
_classify_result below. recommended_action instead follows the
engine's native PASS/REVIEW distinction directly, since that's the
exact condition screen_check_draft() on voicova.com already uses to
decide whether to offer "Fix it" — the two mappings serve different
purposes and are kept independent on purpose.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from logging_config import get_logger
from voice_engine import score_draft_check
from lifetime_cap import get_lifetime_render_count
from api.auth.middleware import Identity, resolve_identity
from api.auth import rate_limit
from api.db.profile_lookup import get_profile_bundle
from api.db.evidence_seals import create_seal
from api.evidence import seal as evidence
from api.telemetry import events as telemetry
from api import formatting
from api.schemas.check_draft import (
    CheckDraftRequest,
    CheckDraftResponse,
    BurrowsDelta,
)

log = get_logger(__name__)
router = APIRouter()


@router.post("/api/check-draft", response_model=CheckDraftResponse)
def check_draft(req: CheckDraftRequest, identity: Identity = Depends(resolve_identity)):
    rate_limit.enforce(identity.installation_id)
    request_id = str(uuid.uuid4())
    started = time.monotonic()

    profile = get_profile_bundle(identity.profile_id)
    if profile is None:
        # No usable voice profile for this identity — Section 4.6 has
        # no dedicated code for this case since Flow A (linking)
        # already refuses to mint a token without a usable profile
        # (routes/extension_auth.py), so it shouldn't be reachable in
        # practice. Mapped to engine_error (500) rather than inventing
        # a new code the extension's state machine doesn't know how
        # to handle.
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})

    result = score_draft_check(
        profile["baseline_fingerprint"],
        req.draft_text,
        baseline_texts=profile["baseline_texts"],
    )

    receipt = evidence.seal(
        request_id=request_id,
        profile_id=identity.profile_id,
        action="check",
        input_text=req.draft_text,
        # Independent architecture review, finding #4: the seal must
        # cover the actual displayed match percentage and per-dimension
        # scores, not just the verdict/tier/evidence summary — see
        # seal.py's own comment. score_draft_check's native result
        # already carries match_pct and delta separately (used below
        # to build the response); dimension_scores is added here as a
        # non-invasive merge, matching the exact shape
        # seal_result_from_voice_report's own fix.py call produces via
        # this same formatting.dimension_scores(), rather than
        # reshaping score_draft_check's return contract in
        # voice_engine.py just for this.
        result={**result, "dimension_scores": formatting.dimension_scores(result.get("delta") or {})},
    )
    create_seal(receipt)

    used, limit = get_lifetime_render_count(identity.profile_id)
    remaining = max(limit - used, 0)

    classified_verdict = formatting.classify_result(result["verdict"], result["ai_tells_clean"])
    latency_ms = int((time.monotonic() - started) * 1000)
    telemetry.emit(
        installation_id=identity.installation_id,
        profile_id=identity.profile_id,
        surface=req.surface,
        action="check",
        request_id=request_id,
        scoring_version=evidence.SCORING_VERSION,
        extension_version=req.client_version,
        result=classified_verdict,
        fix_requested=False,
        render_credit_consumed=False,
        latency_ms=latency_ms,
        draft_length=len(req.draft_text),
    )

    burrows = result.get("burrows_delta") or {}
    delta = result.get("delta") or {}

    return CheckDraftResponse(
        request_id=request_id,
        overall_match=result["match_pct"],
        dimension_scores=formatting.dimension_scores(delta),
        dimension_explanations=formatting.dimension_explanations(delta),
        burrows_delta=BurrowsDelta(
            tier=burrows.get("tier"),
            delta=burrows.get("delta"),
            biggest_divergences=burrows.get("biggest_divergences", []),
        ) if burrows else None,
        verdict=classified_verdict,
        remaining_allowance=remaining,
        # Follows the engine's native PASS/REVIEW verdict directly —
        # this is exactly the condition screen_check_draft() on
        # voicova.com already uses to decide whether to show "Fix it"
        # (app.py, screen_check_draft), kept as its own independent
        # mapping from classified_verdict on purpose (Section 3.5.1
        # documents these as two separate fields serving different UI
        # decisions).
        recommended_action="fix_available" if result["verdict"] == "REVIEW" else "none",
        scoring_version=evidence.SCORING_VERSION,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
