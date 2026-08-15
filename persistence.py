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
the starter completions, and the derived baselines. Render output and
in-progress refinement state are NOT persisted — cheap to regenerate,
no reason to carry them across a session boundary.
"""

import os
import uuid

import streamlit as st
from streamlit_cookies_controller import CookieController

from logging_config import get_logger

log = get_logger(__name__)

_COOKIE_NAME = "voicova_device_id"
_TABLE = "voice_profiles"

def _get_cookie_controller() -> CookieController:
    """Cached in st.session_state, not a module-level global — Streamlit
    serves multiple users from the same Python process, so a
    module-level cache would leak one user's CookieController instance
    into another user's request. session_state is correctly scoped per
    browser session, which is what this actually needs."""
    if "_cookie_controller" not in st.session_state:
        st.session_state["_cookie_controller"] = CookieController()
    return st.session_state["_cookie_controller"]


def _get_supabase_client():
    """Returns a Supabase client, or None if not configured. Never
    raises — callers treat None as 'persistence unavailable, proceed
    without it', matching the fail-open design."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception:
        log.error("supabase_client_init_failed", exc_info=True)
        return None


def get_or_create_device_id() -> str:
    """Reads the device cookie if present; otherwise generates one and
    sets it. Always returns a usable ID — this never blocks onboarding
    even if cookie read/write fails for some reason (private browsing,
    cookie-blocking extension, etc.), it just means that visit won't
    persist."""
    controller = _get_cookie_controller()
    try:
        existing = controller.get(_COOKIE_NAME)
    except Exception:
        existing = None

    if existing:
        return existing

    new_id = str(uuid.uuid4())
    try:
        controller.set(_COOKIE_NAME, new_id, max_age=60 * 60 * 24 * 365)
    except Exception:
        log.error("device_cookie_set_failed", exc_info=True)
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
        return False

    client = _get_supabase_client()
    if client is None:
        return False

    device_id = get_or_create_device_id()
    st.session_state["_device_id"] = device_id

    try:
        result = client.table(_TABLE).select("*").eq("device_id", device_id).limit(1).execute()
    except Exception:
        log.error("profile_restore_query_failed", exc_info=True)
        return False

    rows = result.data if result and result.data else []
    if not rows:
        return False

    row = rows[0]
    try:
        st.session_state["raw_text"] = row.get("raw_text") or ""
        st.session_state["sample2_completions"] = row.get("sample2_completions") or ["", "", "", ""]
        st.session_state["baseline_fingerprint"] = row.get("baseline_fingerprint")
        st.session_state["starter_baseline"] = row.get("starter_baseline")
    except Exception:
        log.error("profile_restore_apply_failed", exc_info=True)
        return False

    if not st.session_state.get("baseline_fingerprint"):
        # Row existed but had no usable baseline — treat as absent
        # rather than sending someone to Screen 4 with nothing to
        # render against.
        return False

    log.info("profile_restored", device_id_present=True)
    return True


def save_profile_if_available() -> None:
    """Call once, right after the baseline is finalised (end of
    Screen 3). Silent — no UI, no error surfaced to the user if this
    fails. A failed save just means this device won't be recognised
    next visit; it must never interrupt the render flow the user is
    actually waiting on."""
    client = _get_supabase_client()
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
    }

    try:
        client.table(_TABLE).upsert(payload, on_conflict="device_id").execute()
        log.info("profile_saved", device_id_present=True)
    except Exception:
        log.error("profile_save_failed", exc_info=True)
