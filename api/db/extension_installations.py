"""
api/db/extension_installations.py — CRUD for the extension_installations
table (Engineering Architecture Section 5.1).

Same fail-open-vs-fail-closed split as the rest of the persistence
layer, but drawn differently here on purpose: identity resolution is a
security boundary (Section 2.2), so lookups fail CLOSED (a Supabase
error means "cannot verify", not "proceed anyway") — the opposite of
persistence.py's fail-open reads, which only ever gate a UX
convenience (skip onboarding), never an auth decision.
"""
from __future__ import annotations

from datetime import datetime, timezone

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)

_TABLE = "extension_installations"


def create_installation(profile_id: str, refresh_handle_hash: str) -> dict | None:
    """Called by /api/extension/link. Returns the new row (with its
    generated installation_id) or None on failure — the caller cannot
    issue tokens without an installation_id, so None must be treated
    as a hard failure of the link ceremony, not a fallback path."""
    client = get_supabase_client()
    if client is None:
        log.error("installation_create_unavailable", reason="no_supabase_client")
        return None
    try:
        result = (
            client.table(_TABLE)
            .insert({
                "profile_id": profile_id,
                "refresh_handle_hash": refresh_handle_hash,
                "refresh_handle_version": 1,
            })
            .execute()
        )
    except Exception:
        log.error("installation_create_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None


def get_installation(installation_id: str) -> dict | None:
    """Fail closed: any error or absence returns None, and every
    caller (verify_access_token, revoke_installation) must treat that
    as 'cannot resolve this identity' rather than 'no row, proceed'."""
    client = get_supabase_client()
    if client is None:
        log.error("installation_lookup_unavailable", reason="no_supabase_client")
        return None
    try:
        result = (
            client.table(_TABLE)
            .select("*")
            .eq("installation_id", installation_id)
            .limit(1)
            .execute()
        )
    except Exception:
        log.error("installation_lookup_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None


def rotate_refresh_handle(installation_id: str, presented_hash: str, new_hash: str) -> dict | None:
    """Atomic compare-and-swap + version increment per Section 4.3's
    concurrency rule, via the rotate_refresh_handle Postgres function
    (one UPDATE, keyed on installation_id AND the presented hash,
    row-count-checked — not read-then-write). Postgres row-level
    locking makes this safe under concurrent requests without an
    application-level lock.

    Returns the updated row if this call won (matched and rotated), or
    None if it matched zero rows — either a lost race against a
    near-simultaneous duplicate call, or a stale/reused/revoked handle.
    Callers needing to tell those apart should call
    check_reuse_or_race() below with the same arguments.
    """
    client = get_supabase_client()
    if client is None:
        log.error("installation_rotate_unavailable", reason="no_supabase_client")
        return None
    try:
        result = client.rpc("rotate_refresh_handle", {
            "p_installation_id": installation_id,
            "p_presented_hash": presented_hash,
            "p_new_hash": new_hash,
        }).execute()
    except Exception:
        log.error("installation_rotate_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None


# Race-loss vs genuine reuse can't be told apart from the CAS result
# alone (both return zero rows). This window is the practical
# implementation of Section 4.3's "later, separate request" vs "a
# losing request from the same near-simultaneous pair" distinction —
# a real duplicate call from the client's own retry logic lands within
# low single-digit seconds of the winning call; anything older
# presenting an already-superseded hash is reuse, not a race.
_REUSE_DETECTION_WINDOW_SECONDS = 10


def check_reuse_or_race(installation_id: str) -> str:
    """Call this only after rotate_refresh_handle() returns None, to
    classify why. Returns one of:
      'race'     — last_refreshed_at is very recent; treat as a lost
                   race, per Section 4.3 (silent-refresh-and-retry
                   handles it, no chain revocation).
      'reuse'    — an already-rotated-away handle presented well after
                   the last successful rotation; Section 4.3 treats
                   this as compromise (revoke the whole chain).
      'revoked'  — installation was already revoked; same
                   caller-facing outcome as 'reuse' (token_revoked).
      'not_found'— installation_id doesn't exist at all.
    """
    row = get_installation(installation_id)
    if row is None:
        return "not_found"
    if row.get("revoked_at"):
        return "revoked"
    last_refreshed_at = row.get("last_refreshed_at")
    if last_refreshed_at:
        try:
            last = datetime.fromisoformat(last_refreshed_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - last).total_seconds()
            if age <= _REUSE_DETECTION_WINDOW_SECONDS:
                return "race"
        except (ValueError, AttributeError):
            pass
    return "reuse"


def revoke_installation(installation_id: str) -> bool:
    """Called by /api/extension/disconnect. Idempotent — revoking an
    already-revoked or nonexistent installation is not an error from
    the caller's point of view (Section 4.3: 'all future verify/
    refresh calls for it fail', which is equally true whether this
    call actually changed a row or the row was already in that
    state)."""
    client = get_supabase_client()
    if client is None:
        log.error("installation_revoke_unavailable", reason="no_supabase_client")
        return False
    try:
        client.table(_TABLE).update({
            "revoked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("installation_id", installation_id).execute()
        return True
    except Exception:
        log.error("installation_revoke_failed", exc_info=True)
        return False
