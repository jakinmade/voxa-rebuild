"""
api/auth/middleware.py — resolve_identity, the FastAPI dependency that
runs before every /api/check-draft and /api/fix call (Section 4.2).

Layered on top of tokens.verify_access_token: that function only
checks signature and expiry (no DB round trip); this function adds the
installation lookup and the revoked/mismatch checks that do need one.
Split this way so a plain expired-token response — the overwhelmingly
common case, handled silently by the extension's background worker —
never costs a database query, only a genuinely-presented, structurally
valid token does.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from logging_config import get_logger
from api.auth import tokens
from api.db import extension_installations as installations_db

log = get_logger(__name__)


@dataclass(frozen=True)
class Identity:
    installation_id: str
    profile_id: str


def _error(status_code: int, error_code: str) -> HTTPException:
    # Shape matches Section 4.6 exactly: HTTP status + error_code,
    # nothing else required — the extension's state machine branches
    # on error_code, not on the HTTP status text.
    return HTTPException(status_code=status_code, detail={"error_code": error_code})


def resolve_identity(authorization: str = Header(default="")) -> Identity:
    if not authorization.startswith("Bearer "):
        raise _error(401, "token_revoked")
    token = authorization[len("Bearer "):]

    try:
        claims = tokens.verify_access_token(token)
    except tokens.TokenExpired:
        raise _error(401, "token_expired")
    except tokens.TokenInvalid:
        raise _error(401, "token_revoked")

    installation_id = claims["installation_id"]
    row = installations_db.get_installation(installation_id)
    if row is None:
        # A structurally valid, correctly-signed token for an
        # installation_id that no longer resolves — the installation
        # was deleted, or (Section 4.3) profile_id was re-pointed and
        # this old token should never have kept working. Treated as
        # 403 per Section 4.6, not 401: this is the "should not
        # happen for a legitimate client" case, an early intrusion
        # signal, not a routine auth error.
        log.error("identity_mismatch", installation_id=installation_id, severity="high")
        raise _error(403, "installation_mismatch")

    if row.get("revoked_at"):
        raise _error(401, "token_revoked")

    return Identity(installation_id=installation_id, profile_id=row["profile_id"])
