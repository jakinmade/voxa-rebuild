"""
api/db/profile_recovery.py — CRUD for the two profile-recovery tables
(migrations/2026_09_05_profile_recovery.sql): profile_recovery_emails
(this module's own addition — see that migration's comment for why)
and profile_recovery_requests (Engineering Architecture Section 5.2).

Fail-closed throughout, same posture as extension_installations.py and
for the same reason: recovery is an identity-resolution path (Section
2.2's security boundary applies here too, not just to the bearer-token
flow), so a Supabase error must mean "cannot verify", never "proceed
anyway".
"""
from __future__ import annotations

from datetime import datetime, timezone

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)

_EMAILS_TABLE = "profile_recovery_emails"
_REQUESTS_TABLE = "profile_recovery_requests"


def register_recovery_email(profile_id: str, email: str) -> bool:
    """Upsert, same style as persistence.py's own profile save
    (client.table(...).upsert(payload, on_conflict="device_id")) — one
    row per profile, latest submission wins, no history kept (matches
    "provide at will": the person can change their mind about which
    email to use, any time, with no approval step)."""
    client = get_supabase_client()
    if client is None:
        log.error("recovery_email_register_unavailable", reason="no_supabase_client")
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        client.table(_EMAILS_TABLE).upsert(
            {"profile_id": profile_id, "email": email, "updated_at": now},
            on_conflict="profile_id",
        ).execute()
    except Exception:
        log.error("recovery_email_register_failed", exc_info=True)
        return False
    return True


def get_profile_id_for_email(email: str) -> str | None:
    """Exact-match lookup, same simplicity level as the existing
    request_subscription_restore precedent (stripe.Customer.list(email=email)
    is also a plain exact match, no normalisation) — a fair trade for
    a pilot, revisit only if real users report case-mismatch misses.
    Returns None on no match OR any error; callers must not distinguish
    the two (Section 2.5's non-enumeration posture — see this module's
    docstring)."""
    client = get_supabase_client()
    if client is None:
        log.error("recovery_email_lookup_unavailable", reason="no_supabase_client")
        return None
    try:
        result = (
            client.table(_EMAILS_TABLE)
            .select("profile_id")
            .eq("email", email)
            .limit(1)
            .execute()
        )
    except Exception:
        log.error("recovery_email_lookup_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0]["profile_id"] if rows else None


def create_recovery_request(
    request_id: str, profile_id: str, email: str, token_hash: str, expires_at: str
) -> dict | None:
    """Called only once a matching profile_id has already been found —
    see routes/profile_recovery.py. Returns the new row or None on
    failure; the caller must not send the recovery email if this
    fails, since the token it referenced would never be resolvable."""
    client = get_supabase_client()
    if client is None:
        log.error("recovery_request_create_unavailable", reason="no_supabase_client")
        return None
    try:
        result = (
            client.table(_REQUESTS_TABLE)
            .insert({
                "request_id": request_id,
                "profile_id": profile_id,
                "email": email,
                "token_hash": token_hash,
                "expires_at": expires_at,
            })
            .execute()
        )
    except Exception:
        log.error("recovery_request_create_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None


def consume_recovery_request(token_hash: str) -> dict | None:
    """Atomic single-use consumption via the consume_recovery_request
    Postgres function (migrations/2026_09_05_profile_recovery.sql) —
    same reasoning as extension_installations.rotate_refresh_handle's
    own RPC-based compare-and-swap: a plain read-then-write from
    Python has a real TOCTOU race two near-simultaneous clicks on the
    same link could hit; the RPC closes it in one atomic statement.

    Returns {"profile_id", "email"} if the token was valid, unused,
    and unexpired (and is now marked used), or None for every other
    case — bad token, already used, expired, or a Supabase error.
    Callers must not distinguish between these (Section 2.5's
    non-enumeration posture, same as this module's other lookup)."""
    client = get_supabase_client()
    if client is None:
        log.error("recovery_request_consume_unavailable", reason="no_supabase_client")
        return None
    try:
        result = client.rpc("consume_recovery_request", {"p_token_hash": token_hash}).execute()
    except Exception:
        log.error("recovery_request_consume_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None
