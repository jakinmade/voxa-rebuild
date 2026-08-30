"""
persistence.py — cross-session voice profile persistence.

Design, scoped and agreed before this was built: no accounts, no email,
no signup screen, not even an optional one. A persistent browser cookie
holds an opaque device ID (a UUID with no identity attached to it).
The moment Screen 3 completes and a baseline exists, that device ID and
the profile get silently upserted to Supabase. On a later visit, the
cookie is read on load; if it matches a saved row, the profile is
restored into session_state and the person lands straight on Screen 4
instead of redoing onboarding. Nothing about this is visible to the
user — no field, no click, no message.

Two failure modes are handled deliberately, not left to raise:
  - Cookie present but no matching Supabase row (deleted server-side,
    stale cookie) -> fall back to fresh onboarding silently.
  - Supabase unreachable -> fail open to fresh onboarding, log it,
    never block the app on a persistence-layer problem.

Only what's expensive to rebuild is persisted: the raw writing sample,
the starter completions, the derived baselines, and the distilled
voice-profile summary (itself the result of an API call — see
_generate_voice_profile_summary in app.py). Render output and
in-progress refinement state are NOT persisted — cheap to regenerate,
no reason to carry them across a session boundary.

READ PATH REWRITTEN (26 Aug 2026, live incident — see git log for the
full sequence of fixes attempted before this one). Root cause of the
whole incident: reading the device cookie went through streamlit_
cookies_controller, a third-party component that queues an async
round-trip to the browser and returns a default value until that
round-trip completes. Every previous attempt tonight patched a
specific symptom of that same underlying async race (a one-rerun
deferral, a session-level cache to stop re-triggering it, an extra
deferral on the write side) without removing the race itself, and
each fix closed one specific failure mode while the fundamental
timing problem remained.

The actual fix: Streamlit 1.61.1 (the exact version this app is
pinned to, confirmed via requirements.txt) ships a native, synchronous
st.context.cookies — a read-only dict of the real Cookie header sent
with the initial HTTP request. No iframe, no component round-trip, no
default-then-real-value-later timing, no rerun needed at all. This
replaces the entire async read path below. Writing a NEW cookie still
has no native Streamlit equivalent, so streamlit_cookies_controller
is kept for that one purpose only — and a write only needs to land
before some LATER visit, not synchronously within the current one, so
its own async nature is no longer on the critical path for returning-
user recognition.
"""

import uuid
from datetime import datetime, timedelta, timezone

import streamlit as st
from streamlit_cookies_controller import CookieController

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_COOKIE_NAME = "voicova_device_id"
_TABLE = "voice_profiles"

def _get_cookie_controller() -> CookieController:
    """A fresh CookieController() every call, deliberately not cached
    — this is now used for WRITING a new cookie only (see module
    docstring); reading goes through st.context.cookies instead."""
    return CookieController()


def _write_device_id_cookie(device_id: str) -> None:
    """Writes device_id to the cookie via the JS component - shared by
    get_or_create_device_id's own new-id path and by
    set_device_id_cookie (below), so there is one place that knows the
    expires/max_age quirk documented inline, not two copies that could
    drift apart."""
    try:
        # BUG FIXED (26 Aug 2026, live incident): streamlit_cookies_
        # controller's CookieController.set() silently defaults
        # `expires` to datetime.now()+1 day WHENEVER the caller
        # doesn't pass expires explicitly - confirmed by reading the
        # installed package source (__getOptions in
        # cookie_controller.py), regardless of what max_age is set to.
        # Pass an explicit, matching expires 365 days out so the two
        # directives agree instead of silently conflicting.
        _get_cookie_controller().set(
            _COOKIE_NAME, device_id,
            max_age=60 * 60 * 24 * 365,
            expires=datetime.now(timezone.utc) + timedelta(days=365),
        )
    except Exception:
        log.error("device_cookie_set_failed", exc_info=True)


def set_device_id_cookie(device_id: str) -> None:
    """Forces the device cookie (and this session's cached value) to a
    SPECIFIC device_id, rather than generating a new one.

    Added 27 Aug 2026 for the Stripe checkout-return race this session
    surfaced directly, confirmed in production logs: the cookie
    written by get_or_create_device_id is set via a JS component that
    needs a moment to actually run in the browser, not a server-set
    HTTP header. If checkout redirects to Stripe before that write
    lands, the browser genuinely never has the cookie yet - coming
    back from Stripe is a fresh page load, st.context.cookies sees
    nothing, and get_or_create_device_id mints a brand-new, unrelated
    random device_id with zero connection to the one Stripe actually
    has on file for that payment (embedded in the Checkout Session's
    metadata at creation time). verify_and_record_subscription (27
    Aug 2026 redesign, same pass) now returns the VERIFIED device_id
    from Stripe's own session metadata instead of a bare bool -
    app.py's checkout-success handler calls this with that value,
    re-establishing the correct device identity in this browser
    regardless of whether the original write ever completed."""
    st.session_state["_device_id"] = device_id
    _write_device_id_cookie(device_id)


def get_or_create_device_id() -> str:
    """Reads the device cookie via st.context.cookies — synchronous,
    populated from the real HTTP Cookie header on the initial request,
    no async component round-trip and so no rerun-timing race to get
    wrong (see module docstring for the full history of what this
    replaced). If genuinely absent, generates one and queues a write
    via the cookie-controller component for a future visit to pick up.
    Always returns a usable ID — this never blocks onboarding even if
    the write fails for some reason (private browsing, cookie-blocking
    extension, etc.), it just means that visit won't persist.

    Resolved once per session and cached in st.session_state["_device_
    id"] — every call site in this codebase (including this function
    itself, on any later call within the same session) reuses that
    cached value rather than re-reading, so a new id is generated at
    most once per browser session regardless of how many times this
    is called."""
    if st.session_state.get("_device_id"):
        return st.session_state["_device_id"]

    try:
        existing = st.context.cookies.get(_COOKIE_NAME)
    except Exception:
        existing = None
        print("DIAG get_or_create_device_id: st.context.cookies read raised, treating as no cookie", flush=True)

    print(f"DIAG get_or_create_device_id: st.context.cookies read existing={existing!r}", flush=True)

    if existing:
        st.session_state["_device_id"] = existing
        return existing

    new_id = str(uuid.uuid4())
    print(f"DIAG get_or_create_device_id: NO existing cookie found, generating new_id={new_id}", flush=True)
    st.session_state["_device_id"] = new_id
    _write_device_id_cookie(new_id)
    return new_id


def restore_profile_if_available() -> bool:
    """Call once on app load, after init_state(). If a saved profile
    exists for this device, restores it into session_state and returns
    True. Returns False (and leaves session_state untouched) on any
    absence or failure — fresh onboarding proceeds exactly as it does
    today. This is the fail-open boundary: nothing here should ever
    raise out to the caller."""
    if st.session_state.get("baseline_fingerprint"):
        # Already have a baseline this session (e.g. mid-flow rerun) —
        # don't overwrite with a stale saved one.
        print("DIAG restore_profile_if_available: baseline_fingerprint already in session_state, skipping restore", flush=True)
        return False

    client = get_supabase_client()
    if client is None:
        print("DIAG restore_profile_if_available: get_supabase_client() returned None, skipping restore", flush=True)
        return False

    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id
    print(f"DIAG restore_profile_if_available: querying voice_profiles for device_id={device_id}", flush=True)

    try:
        result = client.table(_TABLE).select("*").eq("device_id", device_id).limit(1).execute()
    except Exception as e:
        print(f"DIAG restore_profile_if_available: query RAISED: {e!r}", flush=True)
        log.error("profile_restore_query_failed", exc_info=True)
        return False

    rows = result.data if result and result.data else []
    print(f"DIAG restore_profile_if_available: query returned {len(rows)} row(s) for device_id={device_id}", flush=True)
    if not rows:
        return False

    row = rows[0]
    try:
        st.session_state["raw_text"] = row.get("raw_text") or ""
        st.session_state["sample2_completions"] = row.get("sample2_completions") or ["", "", "", ""]
        st.session_state["baseline_fingerprint"] = row.get("baseline_fingerprint")
        st.session_state["starter_baseline"] = row.get("starter_baseline")
        # Optional (30 Aug 2026) — same safe pattern as voice_profile_summary
        # below: a row saved before this feature existed simply won't have
        # it, and the app falls back to the single blended baseline exactly
        # as it always has.
        if row.get("baseline_fingerprints_by_format"):
            st.session_state["baseline_fingerprints_by_format"] = row["baseline_fingerprints_by_format"]
        # Optional (30 Aug 2026) — same safe pattern. A row saved before
        # this feature existed simply won't have it; compute_dimension_
        # confidence's demotion logic is a no-op with no evidence, same
        # as it's always behaved.
        if row.get("correction_evidence"):
            st.session_state["correction_evidence"] = row["correction_evidence"]
        # Optional — a row saved before this feature existed simply
        # won't have it, and a render proceeds exactly as it did
        # before (anchor sentences and numeric targets alone).
        if row.get("voice_profile_summary"):
            st.session_state["voice_profile_summary"] = row["voice_profile_summary"]
        st.session_state["_voice_profile_updated_at"] = row.get("updated_at")
    except Exception as e:
        print(f"DIAG restore_profile_if_available: apply RAISED: {e!r}", flush=True)
        log.error("profile_restore_apply_failed", exc_info=True)
        return False

    if not st.session_state.get("baseline_fingerprint"):
        # Row existed but had no usable baseline — treat as absent
        # rather than sending someone to Screen 4 with nothing to
        # render against.
        print("DIAG restore_profile_if_available: row found but baseline_fingerprint empty, treating as absent", flush=True)
        return False

    print("DIAG restore_profile_if_available: SUCCESS, restoring straight to screen 4", flush=True)
    log.info("profile_restored", device_id_present=True)
    return True


def save_profile_if_available() -> None:
    """Call once, right after the baseline is finalised (end of
    Screen 3). Silent — no UI, no error surfaced to the user if this
    fails. A failed save just means this device won't be recognised
    next visit; it must never interrupt the render flow the user is
    actually waiting on."""
    client = get_supabase_client()
    if client is None:
        return

    baseline = st.session_state.get("baseline_fingerprint")
    if not baseline:
        return

    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id

    payload = {
        "device_id": device_id,
        "raw_text": st.session_state.get("raw_text", ""),
        "sample2_completions": st.session_state.get("sample2_completions", ["", "", "", ""]),
        "baseline_fingerprint": baseline,
        "starter_baseline": st.session_state.get("starter_baseline"),
        "voice_profile_summary": st.session_state.get("voice_profile_summary"),
        "baseline_fingerprints_by_format": st.session_state.get("baseline_fingerprints_by_format"),
        "correction_evidence": st.session_state.get("correction_evidence"),
        # Explicit, not left to the column's DEFAULT now() — that
        # default only fires on INSERT. This table is written via
        # upsert, and an upsert that hits the existing-row path is an
        # UPDATE, which a column default never touches. Without this,
        # updated_at would freeze at first-ever save and silently lie
        # to the "Voice profile updated ..." status line after every
        # later recalibration.
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        client.table(_TABLE).upsert(payload, on_conflict="device_id").execute()
        log.info("profile_saved", device_id_present=True)
    except Exception:
        log.error("profile_save_failed", exc_info=True)
