"""
api/routes/profile_recovery.py — profile recovery (Full Spec Section
2.5, Engineering Architecture Section 11.6), plus one endpoint neither
document specifies.

Three routes:

  POST /api/profile/recovery-email — THIS MODULE'S OWN ADDITION, not
  in either source document. Authenticated (resolve_identity, same as
  disconnect/check-draft/fix): lets an already-linked installation
  register or update the email a future recovery request should match
  against. Necessary because Section 11.6's documented POST /api/
  profile/recover takes only an email and must resolve it to a
  profile_id — but neither the Full Spec nor the Engineering
  Architecture specifies where that email is captured or stored
  beforehand. voice_profiles is explicitly unchanged (Architecture
  Section 5); the only existing precedent for "resolve identity from a
  bare email" in this codebase is request_subscription_restore
  (stripe_subscription.py), which works by querying Stripe — a real
  external source of truth for paying customers, but useless for the
  free-tier population this feature exists to help (Section 2.5's own
  motivating scenario: "a new laptop, a Chrome reinstall... a lost
  Voice Profile with no recourse", not "a lost paid subscription").
  Closing this gap needs a capture point somewhere; this is the
  minimal one — a single authenticated endpoint, not a new account or
  login mechanism (Section 2.5/6.2's explicit hard boundary is
  unaffected: this table has no password, no session, and is never
  checked for anything except "where do I send a recovery link").

  POST /api/profile/recover — Section 11.6 step 1, unauthenticated
  (this is precisely the "I have lost my device, I hold no token"
  case — Section 2.5: "no password, no account table, no login
  state"). Looks up the submitted email against profile_recovery_
  emails; if found, issues a one-time token and emails it. Always
  returns the same response shape regardless of match, and never on a
  different timing profile than the codebase's own established non-
  enumeration convention allows for — see request_subscription_
  restore's own docstring for the precedent this mirrors.

  GET /api/profile/recover — Section 11.6 step 2, "the magic-link
  click itself, a GET, not a JSON POST". Verifies the token and, on
  success, completes a fresh link ceremony — literally the same
  tokens.issue_installation() call POST /api/extension/link already
  uses, returning the identical LinkResponse shape, per 11.6's own
  "same response shape as 11.3, not a new shape." This is a JSON API
  response, not an HTML page: the emailed link's actual browser-facing
  landing page (part of the still-unbuilt Chrome extension's
  onboarding, analogous to link.html) is a separate, later piece of
  work — this endpoint is what that page would call, not a replacement
  for it. RECOVERY_LINK_BASE_URL below points the email's link at THIS
  endpoint directly for now; update it once that landing page exists.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from logging_config import get_logger
from api.auth import tokens
from api.auth.middleware import Identity, resolve_identity
from api.db import profile_recovery as recovery_db
from api.schemas.extension_auth import LinkResponse
from api.schemas.profile_recovery import (
    RegisterRecoveryEmailRequest,
    RegisterRecoveryEmailResponse,
    RecoverInitiateRequest,
    RecoverInitiateResponse,
)

log = get_logger(__name__)
router = APIRouter()

# Section 5.2: "Short-lived — recommend 1 hour."
_TOKEN_TTL = timedelta(hours=1)

# Points the emailed link at this same standalone API service's own
# base URL (VOICOVA_API_BASE_URL — Architecture Section 7, already
# documented for the extension's own api_client.js) rather than a new,
# undocumented env var. See module docstring: this is a placeholder
# destination until a real browser-facing landing page exists.
_API_BASE_URL_ENV = "VOICOVA_API_BASE_URL"
# Architecture Section 7's own documented var for this exact flow.
_RECOVERY_EMAIL_FROM_ENV = "RECOVERY_EMAIL_FROM"


def _hash_token(token: str) -> str:
    # Same primitive and reasoning as tokens.py's refresh-handle
    # hashing: a random, high-entropy token needs no deliberately-slow
    # key stretching, a single fast SHA-256 is the correct tool.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _send_recovery_email(to_email: str, token: str) -> None:
    """Same SendGrid account/pattern as stripe_subscription.py's own
    _send_restore_email — reused deliberately, not reinvented, per
    this codebase's standing cross-product email convention (that
    function's own docstring: "same account/pattern as CLEARANCE's
    send_report_email"). Only the destination URL, subject, and body
    copy differ; the kill switch, key-cleaning, and fail-open-and-log
    posture are identical on purpose, so this flow degrades exactly
    the same way the existing one already does under a misconfigured
    or down SendGrid."""
    if os.environ.get("EMAIL_ENABLED", "true").strip().lower() == "false":
        log.info("recovery_email_skipped", reason="EMAIL_ENABLED=false")
        return

    api_key = os.environ.get("SENDGRID_API_KEY")
    if api_key:
        api_key = "".join(c for c in api_key if 33 <= ord(c) <= 126).strip()
    if not api_key:
        log.error("recovery_email_unavailable", reason="sendgrid_key_not_set")
        return

    api_base_url = os.environ.get(_API_BASE_URL_ENV, "http://localhost:8000")
    recover_url = f"{api_base_url}/api/profile/recover?token={token}"

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#111827;">
  <div style="padding:2rem 1.5rem;">
    <p style="font-size:0.95rem;color:#374151;line-height:1.7;">
      Click below to recover your VOICOVA voice profile on this device.
    </p>
    <p style="margin:1.5rem 0;">
      <a href="{recover_url}"
         style="background:#111827;color:#ffffff;padding:0.75rem 1.5rem;
                border-radius:6px;text-decoration:none;font-size:0.95rem;">
        Recover my profile
      </a>
    </p>
    <p style="font-size:0.8rem;color:#6b7280;">
      This link expires in 1 hour and works once. If you didn't
      request this, you can ignore this email.
    </p>
  </div>
</div>
"""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        from_email = os.environ.get(_RECOVERY_EMAIL_FROM_ENV, "hello@voicova.com")
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject="Recover your VOICOVA voice profile",
            html_content=html_body,
        )
        sg = SendGridAPIClient(api_key)
        sg.send(message)
    except Exception:
        log.error("recovery_email_send_failed", exc_info=True)


@router.post("/api/profile/recovery-email", response_model=RegisterRecoveryEmailResponse)
def register_recovery_email(
    req: RegisterRecoveryEmailRequest, identity: Identity = Depends(resolve_identity)
):
    ok = recovery_db.register_recovery_email(identity.profile_id, req.email)
    if not ok:
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})
    return RegisterRecoveryEmailResponse(registered=True)


@router.post("/api/profile/recover", response_model=RecoverInitiateResponse)
def recover_initiate(req: RecoverInitiateRequest):
    # Always generate a request_id, whether or not the email matches
    # anything — see module docstring on non-enumeration. This one is
    # never persisted when there's no match; it exists only so the
    # response shape can never reveal which case occurred.
    fallback_request_id = str(uuid.uuid4())

    profile_id = recovery_db.get_profile_id_for_email(req.email)
    if profile_id is None:
        log.info("recovery_initiate_no_match")
        return RecoverInitiateResponse(request_id=fallback_request_id)

    request_id = str(uuid.uuid4())
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_token(raw_token)
    expires_at = (datetime.now(timezone.utc) + _TOKEN_TTL).isoformat()

    row = recovery_db.create_recovery_request(
        request_id=request_id,
        profile_id=profile_id,
        email=req.email,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    if row is None:
        # The request record itself couldn't be persisted — sending an
        # email whose link could never resolve would be worse than
        # sending nothing, so this stops here. Same generic response
        # either way; non-enumeration doesn't distinguish "no match"
        # from "matched but failed to persist" either.
        log.error("recovery_initiate_persist_failed")
        return RecoverInitiateResponse(request_id=fallback_request_id)

    _send_recovery_email(to_email=req.email, token=raw_token)
    log.info("recovery_initiate_sent", request_id=request_id)
    return RecoverInitiateResponse(request_id=request_id)


@router.get("/api/profile/recover", response_model=LinkResponse)
def recover_complete(response: Response, token: str = Query(...)):
    # This response body carries live bearer credentials (same as
    # POST /api/extension/link's own LinkResponse) — no-store stops
    # any browser, proxy, or CDN on the path from caching a response
    # that could later be replayed to an attacker sharing the same
    # cache. Doesn't address the larger, separate gap this module's
    # own docstring already flags (no browser landing page yet to
    # receive this and hand it to the extension) — just stops this
    # specific response from being retained anywhere it doesn't need
    # to be.
    response.headers["Cache-Control"] = "no-store"

    token_hash = _hash_token(token)
    row = recovery_db.consume_recovery_request(token_hash)
    if row is None:
        # Deliberately one error_code for "no such token", "already
        # used", and "expired" alike — Section 2.5's non-enumeration
        # posture applies here too, and this is a new code (Section
        # 4.6/11.7's fixed table is scoped to the bearer-token-
        # authenticated endpoints; this cold, unauthenticated GET
        # isn't one of them), flagged here rather than silently reused
        # from that unrelated table.
        raise HTTPException(status_code=400, detail={"error_code": "recovery_token_invalid"})

    result = tokens.issue_installation(row["profile_id"])
    if result is None:
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})
    return LinkResponse(**result)
