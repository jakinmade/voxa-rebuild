"""
api/auth/tokens.py — access + refresh token issuance, verification,
rotation (Engineering Architecture Section 4.3).

Access tokens are JWTs (RFC 7519) via PyJWT — the established industry
pattern for signed bearer tokens with expiry claims, not a hand-rolled
format. Algorithm is pinned explicitly to HS256 on both encode and
decode: JWT's well-known "alg" attacks (algorithm confusion, "none"
algorithm acceptance) come specifically from trusting the token's own
declared algorithm — pinning it server-side closes that regardless of
what a malicious or malformed token claims about itself.

Refresh handles are a separate, non-JWT credential by design (Section
3.3.2): opaque, high-entropy random tokens, hashed before storage
(extension_installations.refresh_handle_hash) — the same pattern
GitHub/Stripe use for API keys. This is deliberately different from
password hashing (bcrypt/argon2/scrypt): those defend against
brute-forcing a LOW-entropy secret a human chose, which needs
deliberately-slow key stretching; a refresh handle is 256 bits of
os.urandom, already unguessable, so a single fast SHA-256 is the
correct and standard tool here, not a mismatched heavier one.

TOKEN_SIGNING_SECRET is the one secret this whole access-token model
rests on (Section 2.2: "Trusted, server-only... never shipped to the
extension bundle in any form").
"""
from __future__ import annotations

import hashlib
import os
import secrets
import time

import jwt

from logging_config import get_logger
from api.db import extension_installations as installations_db

log = get_logger(__name__)

# Section 3.3.2: "recommend 1 hour" for the access token.
ACCESS_TOKEN_TTL_SECONDS = 60 * 60

_ALGORITHM = "HS256"


class TokenError(Exception):
    """Base for every verification failure below. Routes catch this
    and map it to the Section 4.6 error-code table — this module
    itself never knows about HTTP status codes."""


class TokenExpired(TokenError):
    """Maps to 401 token_expired (Section 4.6) — the background
    worker should silently refresh and retry, not surface anything."""


class TokenInvalid(TokenError):
    """Maps to 401 token_revoked (Section 4.6) — malformed, badly
    signed, or otherwise not a token this service issued."""


def _signing_secret() -> str:
    secret = os.environ.get("TOKEN_SIGNING_SECRET")
    if not secret:
        # Fails loudly and immediately, not with a silent fallback —
        # an unset signing secret is a deploy-configuration bug, and
        # every token operation is a security decision (Section 2.2),
        # not a place to fail open.
        raise RuntimeError("TOKEN_SIGNING_SECRET is not set")
    return secret


def _mint_access_token(installation_id: str) -> str:
    now = int(time.time())
    claims = {
        "sub": installation_id,  # RFC 7519 standard claim, not a bespoke field
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(claims, _signing_secret(), algorithm=_ALGORITHM)


def verify_access_token(token: str) -> dict:
    """Checks signature and expiry; returns {"installation_id": ...}
    or raises TokenExpired / TokenInvalid. Does not resolve the
    installation against the database — that's middleware.py's job,
    layered on top so a merely-expired-but-otherwise-valid token can
    be told apart from a genuinely bad one (Section 4.6) without an
    extra DB round trip on every request just to check expiry.

    algorithms=[_ALGORITHM] is passed explicitly (never inferred from
    the token's own header) — this is what actually closes the
    algorithm-confusion class of JWT vulnerability, not just the
    choice of HS256 itself.
    """
    try:
        claims = jwt.decode(token, _signing_secret(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise TokenExpired("access token expired")
    except jwt.InvalidTokenError:
        raise TokenInvalid("invalid or malformed token")

    installation_id = claims.get("sub")
    if not installation_id:
        raise TokenInvalid("missing sub claim")

    return {"installation_id": installation_id}


def _new_refresh_handle() -> tuple[str, str]:
    """Returns (raw_handle, hash) — raw goes to the client once, hash
    is what's stored (Section 3.3.2, same principle as password
    storage)."""
    raw = secrets.token_urlsafe(32)
    handle_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return raw, handle_hash


def issue_installation(device_identity: str) -> dict | None:
    """Called by /api/extension/link. device_identity is the existing
    voicova.com device-cookie identity, read once at link time (see
    this module's docstring and Section 4.3's device_identity note —
    everything downstream of this call is a fresh identifier scoped to
    the extension, not a copy of the cookie value).

    Returns {"installation_id", "access_token", "refresh_handle"} or
    None if the installations table write failed — callers must treat
    None as a hard failure of the link ceremony, not a partial
    success."""
    raw_handle, handle_hash = _new_refresh_handle()
    row = installations_db.create_installation(
        profile_id=device_identity,
        refresh_handle_hash=handle_hash,
    )
    if row is None:
        log.error("issue_installation_failed", reason="db_create_failed")
        return None

    installation_id = row["installation_id"]
    return {
        "installation_id": installation_id,
        "access_token": _mint_access_token(installation_id),
        "refresh_handle": raw_handle,
    }


def refresh_access_token(installation_id: str, presented_refresh_handle: str) -> dict:
    """Called by /api/extension/refresh. Validates the refresh handle
    against extension_installations, rotates it (Section 4.3), and
    returns a new access token + new refresh handle.

    Raises TokenInvalid on any failure — the route layer maps this to
    token_revoked or, on a detected-reuse case, treats it as the
    compromise signal Section 4.3 and the 403 installation_mismatch
    row in Section 4.6 describe (logged at high severity, not a
    routine auth error).
    """
    presented_hash = hashlib.sha256(presented_refresh_handle.encode("utf-8")).hexdigest()
    new_raw, new_hash = _new_refresh_handle()

    row = installations_db.rotate_refresh_handle(installation_id, presented_hash, new_hash)
    if row is None:
        classification = installations_db.check_reuse_or_race(installation_id)
        if classification == "race":
            # Section 4.3: "no chain revocation" — same response shape
            # as a plain expired token, so the existing silent-
            # refresh-and-retry path handles it with no special case.
            raise TokenExpired("lost a concurrent refresh race")
        if classification == "reuse":
            log.error(
                "refresh_handle_reuse_detected",
                installation_id=installation_id,
                severity="high",
            )
            installations_db.revoke_installation(installation_id)
        raise TokenInvalid(f"refresh rejected: {classification}")

    return {
        "installation_id": installation_id,
        "access_token": _mint_access_token(installation_id),
        "refresh_handle": new_raw,
    }


def revoke_installation(installation_id: str) -> bool:
    """Called by /api/extension/disconnect."""
    return installations_db.revoke_installation(installation_id)
