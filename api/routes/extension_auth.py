"""
api/routes/extension_auth.py — /api/extension/link, /refresh,
/disconnect (Full Spec Section 3.3.3, Engineering Architecture
Section 4.3).

Error-code mapping for refresh (Section 4.6): a lost race and a
genuine reuse/already-revoked handle both map to the SAME two
error_codes middleware.py's resolve_identity already uses —
token_expired for the transient race case (the extension's own
silent-refresh-and-retry path already handles this, no special
response needed) and token_revoked for every other failure (surfaces
"Reconnect this extension"). 403 installation_mismatch is deliberately
NOT used here — that code is scoped to resolve_identity's own case (a
structurally valid access token whose installation_id no longer
resolves), a different situation from "this refresh handle didn't
work."
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from logging_config import get_logger
from api.auth import tokens
from api.auth.middleware import Identity, resolve_identity
from api.db.profile_lookup import get_profile_bundle
from api.schemas.extension_auth import (
    LinkRequest,
    LinkResponse,
    RefreshRequest,
    RefreshResponse,
    DisconnectResponse,
)

log = get_logger(__name__)
router = APIRouter()


@router.post("/api/extension/link", response_model=LinkResponse)
def link_extension(req: LinkRequest):
    # Confirms device_identity corresponds to a real, usable voice
    # profile before minting anything bound to it — Flow A step 1
    # assumes onboarding is already complete ("logged into voicova.com
    # with an active device-cookie session"); minting an installation
    # for a device_id with no baseline would just produce a confusing
    # engine_error on every later check-draft call instead of a clear
    # failure right here at link time.
    if get_profile_bundle(req.device_identity) is None:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "invalid_device_identity"},
        )

    result = tokens.issue_installation(req.device_identity)
    if result is None:
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})

    return LinkResponse(**result)


@router.post("/api/extension/refresh", response_model=RefreshResponse)
def refresh_extension_token(req: RefreshRequest):
    try:
        result = tokens.refresh_access_token(req.installation_id, req.refresh_handle)
    except tokens.TokenExpired:
        # Section 4.3: a lost race against a near-simultaneous refresh
        # call — same shape as a plain expired access token, so the
        # extension's existing silent-refresh-and-retry path handles
        # it with no special case.
        raise HTTPException(status_code=401, detail={"error_code": "token_expired"})
    except tokens.TokenInvalid:
        raise HTTPException(status_code=401, detail={"error_code": "token_revoked"})

    return RefreshResponse(
        access_token=result["access_token"],
        refresh_handle=result["refresh_handle"],
    )


@router.post("/api/extension/disconnect", response_model=DisconnectResponse)
def disconnect_extension(identity: Identity = Depends(resolve_identity)):
    # Behind resolve_identity like check-draft/fix, for consistency —
    # same auth path everywhere rather than a special case for one
    # endpoint. Idempotent: revoking an already-revoked installation
    # is not an error (Section 4.3's revoke_installation docstring).
    ok = tokens.revoke_installation(identity.installation_id)
    if not ok:
        raise HTTPException(status_code=500, detail={"error_code": "engine_error"})
    return DisconnectResponse(disconnected=True)
