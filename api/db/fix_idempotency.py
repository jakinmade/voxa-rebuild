"""
api/db/fix_idempotency.py — persistence for /api/fix's idempotency
protection (independent architecture review, finding #5). All three
operations are thin wrappers around the Postgres functions defined in
migrations/2026_09_05_fix_idempotency.sql (reserve_fix_idempotency_key,
complete_fix_idempotency_key, release_fix_idempotency_key) — same
RPC-calling convention as extension_installations.py's
rotate_refresh_handle, for the same reason: the atomicity lives in the
database function, this module only calls it and shapes the result.

Fail-closed, not fail-open, on reserve() specifically — same doctrine
lifetime_cap.py's own module docstring states explicitly for
entitlement checks ("failing open just means a few extra renders
happen during a rare outage, but an entitlement gate failing open
[...] is a materially different and worse failure for a paid
product"). If this table is unreachable, routes/fix.py must refuse the
request rather than silently skip the one check that exists
specifically to stop a duplicate paid render — the same reasoning,
applied to the same class of risk. complete()/release() are logged
loudly on failure but do not raise: by the time either is called, the
render has already happened (succeeded or failed) and the credit
accounting has already been decided elsewhere: a failure to persist
the outcome here would leave a 'pending' row that blocks a legitimate
retry with the same key, which is a real but strictly smaller failure
than either double-charging or losing a request outright.
"""
from __future__ import annotations

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)


def reserve(idempotency_key: str, profile_id: str) -> dict | None:
    """Returns {"is_new": bool, "status": str, "response_json": dict | None}.

    is_new=True: this call won the reservation — the caller should
    proceed with a fresh render and call complete()/release() with
    this same key when it's done.

    is_new=False, status="completed": a prior call with this key
    already finished — response_json is that call's exact response,
    to be returned as-is without touching credits or the LLM.

    is_new=False, status="pending": another call with this key is
    still in flight right now (a genuine simultaneous duplicate, not
    yet resolved either way) — the caller should reject this request
    rather than wait or guess.

    Returns None only on an actual failure to reach the reservation
    store — callers must treat that as fail-closed (see module
    docstring), not as "is_new".
    """
    client = get_supabase_client()
    if client is None:
        log.error("fix_idempotency_reserve_unavailable", reason="no_supabase_client")
        return None
    try:
        result = client.rpc("reserve_fix_idempotency_key", {
            "p_key": idempotency_key,
            "p_profile_id": profile_id,
        }).execute()
    except Exception:
        log.error("fix_idempotency_reserve_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None


def complete(idempotency_key: str, response_payload: dict) -> None:
    """Stores the successful response so a repeat request with this
    same key gets it back verbatim instead of a second render."""
    client = get_supabase_client()
    if client is None:
        log.error("fix_idempotency_complete_unavailable", reason="no_supabase_client")
        return
    try:
        client.rpc("complete_fix_idempotency_key", {
            "p_key": idempotency_key,
            "p_response": response_payload,
        }).execute()
    except Exception:
        log.error("fix_idempotency_complete_failed", exc_info=True)


def release(idempotency_key: str) -> None:
    """Releases a reservation after a failed render (mirrors
    lifetime_cap.py's own release_reserved_lifetime_render — a failed
    attempt must not permanently occupy the key). Guarded server-side
    to 'pending' rows only (see the SQL function's own comment), so
    calling this after a completed response was already stored is
    always safe and a no-op."""
    client = get_supabase_client()
    if client is None:
        log.error("fix_idempotency_release_unavailable", reason="no_supabase_client")
        return
    try:
        client.rpc("release_fix_idempotency_key", {"p_key": idempotency_key}).execute()
    except Exception:
        log.error("fix_idempotency_release_failed", exc_info=True)
