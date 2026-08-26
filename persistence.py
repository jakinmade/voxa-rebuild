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
    """A fresh CookieController() every call, deliberately not cached.

    Bug this replaces (returning users always looking like brand-new
    visitors): the
    underlying component call inside CookieController.__init__ returns
    an empty default on the browser's first-ever round-trip in a
    session, and Streamlit auto-triggers a rerun once the real cookie
    value arrives — but only a freshly-constructed CookieController
    picks that up, via its own `else: self.__cookies =
    st.session_state[key]` branch. Caching the whole instance across
    reruns (as this function used to) froze it at the empty-default
    snapshot forever, so get_or_create_device_id() below always saw
    "no cookie" and silently overwrote the real one on every load,
    before the real value was ever read.

    Constructing fresh each call is safe, not just a workaround: the
    class's own internal session_state key ('cookies' by default) is
    already correctly scoped per browser session by Streamlit itself,
    which is what the original per-session caching here was trying to
    guarantee in the first place — it just did so at the wrong layer.
    """
    return CookieController()


def get_or_create_device_id() -> str:
    """Reads the device cookie if present; otherwise generates one and
    sets it. Always returns a usable ID — this never blocks onboarding
    even if cookie read/write fails for some reason (private browsing,
    cookie-blocking extension, etc.), it just means that visit won't
    persist.

    One-rerun deferral before treating an empty read as genuine: the
    underlying component returns its default ({}) on the very first
    script pass of any session, before the browser's real cookie value
    has round-tripped back — true regardless of the controller-caching
    fix above, since that's a *within-session* fix and this race exists
    on pass one of every fresh session (i.e. every hard page load, since
    st.session_state doesn't survive those). Committing to "no cookie"
    on that first pass, as this used to, meant writing and overwriting
    a fresh random ID before the real one was ever read — every time.
    Deferring once, via the same rerun-idiom used elsewhere in this
    codebase for identical component-timing races, gives the real
    value one full round trip to arrive before we decide.

    SECOND, WORSE INSTANCE OF THE SAME RACE FIXED (26 Aug 2026, live
    incident): the deferral above only protects the FIRST call within
    a session. Every other call site in this codebase (app.py, six of
    them) already guards against redundant calls with `st.session_
    state.get("_device_id") or get_or_create_device_id()` — but this
    function was still being reached unconditionally on EVERY rerun
    via restore_profile_if_available(), which is the very first thing
    that runs on every page load and did not use that guard. Confirmed
    live via DIAG prints: a single page load produced THREE different
    randomly-generated device IDs in under two seconds, each
    overwriting the last, because Streamlit's own automatic reruns
    (while the cookie component's async round-trip is still in
    flight) hit this function again before the previous write had
    landed — each premature read-before-write-lands looks identical
    to a genuinely absent cookie. The one-rerun deferral above cannot
    protect against this, because it only fires on the very first call
    of a session; by the second, third, etc. call, "cookies" is
    already in st.session_state (from the first call), so the deferral
    is skipped and a fresh read is attempted immediately — landing
    exactly in the race window.

    Fix: once this function has resolved a device_id for this session
    (however it got it), cache it and short-circuit every subsequent
    call within the same session — the cookie dance now only ever
    runs once per browser session, not once per rerun. This is the
    same "resolve once, reuse via session_state" pattern already used
    at every other call site; it was simply missing from the one
    function that actually does the resolving.
    """
    if st.session_state.get("_device_id"):
        return st.session_state["_device_id"]

    if "cookies" not in st.session_state and not st.session_state.get("_device_id_cookie_wait"):
        print("DIAG get_or_create_device_id: deferring one rerun for cookie to arrive", flush=True)
        st.session_state["_device_id_cookie_wait"] = True
        st.rerun()

    controller = _get_cookie_controller()
    try:
        existing = controller.get(_COOKIE_NAME)
    except Exception:
        existing = None
        print("DIAG get_or_create_device_id: controller.get() raised, treating as no cookie", flush=True)

    print(f"DIAG get_or_create_device_id: cookie read existing={existing!r}", flush=True)

    if existing:
        return existing

    new_id = str(uuid.uuid4())
    print(f"DIAG get_or_create_device_id: NO existing cookie found, generating new_id={new_id}", flush=True)
    try:
        # BUG FIXED (26 Aug 2026, live incident): streamlit_cookies_
        # controller's CookieController.set() silently defaults
        # `expires` to datetime.now()+1 day WHENEVER the caller
        # doesn't pass expires explicitly - confirmed by reading the
        # installed package source (__getOptions in
        # cookie_controller.py), regardless of what max_age is set to.
        # This call only ever passed max_age=365 days, so every device
        # cookie this app has ever set went out with BOTH a 365-day
        # Max-Age AND a competing, silently-injected 1-day Expires in
        # the same Set-Cookie instruction. Max-Age should win per RFC
        # 6265, but conflicting directives are exactly the kind of
        # thing that behaves inconsistently across browsers rather
        # than failing outright - consistent with "recognised
        # sometimes, not others" rather than a clean on/off failure.
        # Fix: pass an explicit, matching expires 365 days out so the
        # two directives agree instead of silently conflicting.
        controller.set(
            _COOKIE_NAME, new_id,
            max_age=60 * 60 * 24 * 365,
            expires=datetime.now(timezone.utc) + timedelta(days=365),
        )
        print(f"DIAG get_or_create_device_id: controller.set() call returned (fire-and-forget, no confirmation)", flush=True)
    except Exception:
        log.error("device_cookie_set_failed", exc_info=True)

    # SECOND WRITE-SIDE BUG (26 Aug 2026, same live incident, found
    # after the read-side fix above still didn't close it): the write
    # above is the exact same kind of async Streamlit-component call
    # as the read at the top of this function - it queues an
    # instruction for the browser's JS to actually execute
    # (document.cookie = ...), it does not confirm that instruction
    # has run. The read side already needed a full extra rerun before
    # its real value could be trusted (see the docstring above); the
    # write side needed the identical treatment and never got it -
    # every previous fix in this incident treated the write as
    # complete the instant controller.set() returned, then immediately
    # rendered the rest of the page and moved on. If the browser tears
    # the page down (a hard refresh) before that queued JS has had a
    # chance to actually mount and run, the cookie is never actually
    # written at all - which exactly matches every prior attempt
    # tonight showing existing=None on every single fresh visit, even
    # after the within-session id-cascade bug above was fixed. Forcing
    # one more rerun here, mirroring the read-side deferral exactly,
    # gives the browser a full round trip to actually execute the
    # write before this function (or anything downstream of it) is
    # allowed to consider the id settled. Guarded by its own flag so
    # this can only ever fire once per new id, not loop.
    wait_key = f"_device_id_write_wait_{new_id}"
    if not st.session_state.get(wait_key):
        print(f"DIAG get_or_create_device_id: deferring one rerun for the write to {new_id} to actually land", flush=True)
        st.session_state[wait_key] = True
        st.session_state["_device_id"] = new_id
        st.rerun()

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
