"""
Regression coverage for the "Start over" bug fixed 7 Sept 2026 (see
storage.reset_all() and the top-level restore check in app.py for the
full write-up).

Real bug: reset_all() only ever cleared local session_state, never the
saved Supabase profile. restore_profile_if_available() runs
unconditionally on every page load, so the very next rerun after
"Start over" silently pulled the old profile straight back and dropped
the person on Screen 4 again — "Start over" looked like it worked for
an instant, then snapped back, with no way to actually reach fresh
onboarding again once a profile existed.

Fix: reset_all() sets a session-only _skip_profile_restore flag; the
top-level restore check honours it; _add_writing_sample_to_fingerprint
clears it the moment fresh calibration data is actually added. A
two-step confirm (_start_over_control) was added alongside this, since
an accidental click now has a real cost it didn't have when the button
was silently broken.

Reuses the fake-Supabase-table fixture and onboarding-drive helper from
test_persistence_flow.py rather than duplicating them.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from .test_persistence_flow import (
    _fake_supabase_client,
    _cookie_controller_returning,
    _context_cookies_returning,
    _click,
    _complete_onboarding_through_screen3,
)

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


def _ss_get(at: AppTest, key: str, default=None):
    """at.session_state (AppTest's inspection proxy) doesn't support
    .get() the way real st.session_state does inside the app itself -
    it only supports item/attribute access, and raises on a missing
    key. Safe lookup for keys that may genuinely be absent (like
    _skip_profile_restore, which only exists after reset_all() sets it
    and is popped once consumed)."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def _seed_completed_render(at: AppTest):
    """Screen 4's 'Write again'/'Start over' row only renders once a
    render has actually completed - a real render needs a live
    Anthropic call this suite deliberately avoids, so this seeds the
    minimal state that same row's visibility actually depends on
    (confirmed empirically against the real button tree, not guessed
    from source indentation)."""
    at.session_state["render_output"] = "Some rendered text here that is long enough."
    at.session_state["render_input_text"] = "Some input text."
    at.session_state["voice_report"] = {
        "risk": "Low", "content_integrity_hard_fail": False,
        "ai_tell_clean": True, "ai_tell_flags": [],
    }
    at.session_state["refinement_used"] = False


@pytest.fixture()
def fake_db():
    return {}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake-key")


def test_start_over_first_click_only_asks_for_confirmation(fake_db):
    client = _fake_supabase_client(fake_db)
    no_cookie = _cookie_controller_returning(None)

    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=no_cookie):
            with patch("persistence.st.context", _context_cookies_returning(None)):
                at = AppTest.from_file(_APP_PATH)
                at.session_state["screen"] = 1
                at = _complete_onboarding_through_screen3(at)
                _seed_completed_render(at)
                at.run(timeout=15)
    assert at.session_state["screen"] == 4
    baseline_before = at.session_state["baseline_fingerprint"]

    assert _click(at, "Start over")
    at.run(timeout=15)

    assert not at.exception, f"First Start-over click raised: {at.exception}"
    assert at.session_state["screen"] == 4, "First click should only confirm, not reset"
    assert at.session_state["baseline_fingerprint"] == baseline_before, (
        "Profile must be untouched before confirmation"
    )
    labels = [b.label for b in at.button]
    assert any("Yes, start over" in l for l in labels)
    assert any("Cancel" in l for l in labels)


def test_start_over_cancel_leaves_profile_untouched(fake_db):
    client = _fake_supabase_client(fake_db)
    no_cookie = _cookie_controller_returning(None)

    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=no_cookie):
            with patch("persistence.st.context", _context_cookies_returning(None)):
                at = AppTest.from_file(_APP_PATH)
                at.session_state["screen"] = 1
                at = _complete_onboarding_through_screen3(at)
                _seed_completed_render(at)
                at.run(timeout=15)
    baseline_before = at.session_state["baseline_fingerprint"]

    assert _click(at, "Start over")
    at.run(timeout=15)
    assert _click(at, "Cancel")
    at.run(timeout=15)

    assert not at.exception, f"Cancel raised: {at.exception}"
    assert at.session_state["screen"] == 4
    assert at.session_state["baseline_fingerprint"] == baseline_before


def test_confirmed_start_over_does_not_snap_back_to_old_profile(fake_db):
    """The actual regression: confirming Start over must NOT get
    silently overridden by the auto-restore on the very next rerun,
    even though the device cookie and the saved Supabase row are both
    still present and would normally trigger it."""
    client = _fake_supabase_client(fake_db)
    no_cookie = _cookie_controller_returning(None)

    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=no_cookie):
            with patch("persistence.st.context", _context_cookies_returning(None)):
                at = AppTest.from_file(_APP_PATH)
                at.session_state["screen"] = 1
                at = _complete_onboarding_through_screen3(at)
                _seed_completed_render(at)
                at.run(timeout=15)
    assert at.session_state["screen"] == 4
    saved_id, saved_row = next(iter(fake_db.items()))
    assert len(fake_db) == 1

    # Same device cookie resolves to the saved profile throughout this
    # whole interaction - exactly the condition that used to make
    # "Start over" silently fail.
    returning_cookie = _cookie_controller_returning(saved_id)
    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=returning_cookie):
            with patch("persistence.st.context", _context_cookies_returning(saved_id)):
                assert _click(at, "Start over")
                at.run(timeout=15)
                assert _click(at, "Yes, start over")
                at.run(timeout=15)

    assert not at.exception, f"Confirmed Start-over raised: {at.exception}"
    assert at.session_state["screen"] == 1, (
        f"Expected fresh onboarding (Screen 1), got screen "
        f"{at.session_state['screen']} - old profile was silently restored"
    )
    assert not _ss_get(at, "baseline_fingerprint"), (
        "Old baseline must not be present - this is the exact bug: "
        "restore_profile_if_available() pulling it straight back"
    )
    # The old row is untouched in Supabase - reset_all() alone never
    # deletes it, only a completed fresh calibration overwrites it.
    assert fake_db[saved_id] == saved_row


def test_fresh_calibration_clears_the_skip_restore_flag():
    """Narrower, more robust version of the full-loop check: confirms
    _add_writing_sample_to_fingerprint (the single choke point every
    fresh-calibration and Learn-from-edit path goes through) clears
    _skip_profile_restore, without redriving the full onboarding UI a
    second time in one process - that fuller drive is exercised
    manually and is flakier here for reasons unrelated to this fix
    (stale widget state across a second Screen 1 pass), not worth
    chasing at the cost of a solid, targeted unit check."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import streamlit as st
    import app as voicova_app
    from storage import init_state

    st.session_state.clear()
    init_state()
    st.session_state["_skip_profile_restore"] = True

    voicova_app._add_writing_sample_to_fingerprint(
        "I think we should move fast on this and I want the team focused."
    )

    assert not st.session_state.get("_skip_profile_restore"), (
        "Flag must be cleared once fresh calibration data is actually added"
    )
    assert st.session_state.get("baseline_fingerprint"), (
        "Sanity check: the sample should have actually built a baseline"
    )
