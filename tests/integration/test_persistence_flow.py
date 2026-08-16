"""
Integration coverage for persistence.py's save/restore round trip,
driven through the real Screen 1 -> 2 -> 3 -> 4 onboarding UI (not
just the unit-level functions in tests/unit/test_persistence.py).

Three scenarios, matching the three states a real visit can be in:
  1. Fresh onboarding completes -> profile gets saved (silent, no UI).
  2. A later visit with a matching device cookie -> restores straight
     to Screen 4, skipping onboarding entirely.
  3. A visit with an unrecognised cookie -> falls back cleanly to
     fresh onboarding, no crash, no partial state.

Supabase and the cookie controller are both mocked throughout via a
shared in-memory fake table — no network access, no real credentials
needed to run this suite, matches the standing cost/dependency
discipline used for the Anthropic client elsewhere in this repo.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

SAMPLE_TEXT = (
    "Sarah, I have been going back and forth on the Meridian contract all week and "
    "honestly I still do not have a clean answer for the board. The renewal numbers "
    "Tom sent look fine on paper but something about the March timeline bothers me. "
    "We told the Meridian team Q4 in the June call, we meant Q4, and now Tom is "
    "acting like March was always on the table. It was not, and I have the email "
    "thread from June 14th to prove it. I would rather raise that plainly with Tom now "
    "than have it come up sideways in Thursday board meeting, especially with "
    "Priya from finance already asking pointed questions about the renewal margin. "
    "Please can you pull the June thread before the call. Also flagging that the "
    "Hartwell deal has the same shape, we quoted 90 days, they are now saying 120, "
    "and nobody on their side seems to remember agreeing to the shorter window. "
    "I have asked David twice for the signed order form and have not heard back."
)


def _fake_supabase_client(fake_db: dict):
    """A shared in-memory dict stands in for the voice_profiles table.
    Upsert writes to it, select-by-eq reads from it — enough to
    exercise persistence.py's real query shape without a real
    connection."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    def _upsert(payload, on_conflict=None):
        fake_db[payload["device_id"]] = payload
        return MagicMock(execute=lambda: MagicMock())
    table.upsert.side_effect = _upsert

    def _eq(field, value):
        m = MagicMock()
        m.limit.return_value.execute.side_effect = lambda: MagicMock(
            data=[fake_db[value]] if value in fake_db else []
        )
        return m
    table.select.return_value.eq.side_effect = _eq
    return client


def _cookie_controller_returning(value):
    controller = MagicMock()
    controller.get.return_value = value
    return controller


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def _complete_onboarding_through_screen3(at):
    """Drives the real UI through Screen 1 -> 2 -> 3, same path as
    test_streamlit_app_flow.py's screen3 test. Returns the AppTest
    instance positioned right after the Screen 3 Continue click, which
    is where save_profile_if_available() actually fires in app.py."""
    at.run(timeout=15)
    assert at.session_state["screen"] == 1

    at.text_area[0].set_value(SAMPLE_TEXT)
    at.button[0].click()
    at.run(timeout=15)
    assert at.session_state["screen"] == 2

    assert _click(at, "Continue")
    at.run(timeout=15)
    assert at.session_state["screen"] == 3

    completions = at.session_state["sample2_completions"]
    completions[0] = "This completely misses what I actually asked for, and I need to say so plainly."
    completions[3] = "Honestly this has been bothering me all afternoon and I can't quite let it go."
    at.session_state["sample2_completions"] = completions
    at.run(timeout=15)
    assert _click(at, "Continue")
    at.run(timeout=15)
    return at


@pytest.fixture()
def fake_db():
    return {}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "fake-key")


def test_completing_onboarding_saves_a_profile_silently(fake_db):
    client = _fake_supabase_client(fake_db)
    no_cookie = _cookie_controller_returning(None)

    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=no_cookie):
            at = AppTest.from_file(_APP_PATH)
            at = _complete_onboarding_through_screen3(at)

    assert not at.exception, f"Onboarding + save raised: {at.exception}"
    assert at.session_state["screen"] == 4
    assert len(fake_db) == 1, f"Expected exactly one saved profile, got {list(fake_db.keys())}"

    saved_row = next(iter(fake_db.values()))
    assert saved_row["baseline_fingerprint"], "Saved row has no baseline_fingerprint"
    assert saved_row["raw_text"], "Saved row has no raw_text"
    assert saved_row["starter_baseline"], "Saved row has no starter_baseline"


def test_returning_visit_with_matching_cookie_restores_straight_to_screen4(fake_db):
    client = _fake_supabase_client(fake_db)
    no_cookie = _cookie_controller_returning(None)

    # First visit: onboard and save.
    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=no_cookie):
            at = AppTest.from_file(_APP_PATH)
            at = _complete_onboarding_through_screen3(at)
    saved_id, saved_row = next(iter(fake_db.items()))

    # Second visit: a fresh AppTest instance (new browser session, same
    # device cookie) should skip onboarding entirely.
    returning_cookie = _cookie_controller_returning(saved_id)
    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=returning_cookie):
            at2 = AppTest.from_file(_APP_PATH)
            at2.run(timeout=15)

    assert not at2.exception, f"Returning-visit load raised: {at2.exception}"
    assert at2.session_state["screen"] == 4, (
        f"Expected silent auto-restore to Screen 4, got screen {at2.session_state['screen']}"
    )
    assert at2.session_state["raw_text"] == saved_row["raw_text"]
    assert at2.session_state["baseline_fingerprint"] == saved_row["baseline_fingerprint"]


def test_unrecognised_cookie_falls_back_to_fresh_onboarding(fake_db):
    client = _fake_supabase_client(fake_db)
    unknown_cookie = _cookie_controller_returning("some-device-id-never-saved")

    with patch("persistence.get_supabase_client", return_value=client):
        with patch("persistence.CookieController", return_value=unknown_cookie):
            at = AppTest.from_file(_APP_PATH)
            at.run(timeout=15)

    assert not at.exception, f"Unmatched-cookie load raised: {at.exception}"
    assert at.session_state["screen"] == 1, (
        "An unmatched device cookie must fall back to fresh onboarding, "
        f"got screen {at.session_state['screen']}"
    )


def test_supabase_unreachable_on_load_fails_open_to_fresh_onboarding(fake_db):
    """Fail-open, not fail-crash: if Supabase can't be reached at all
    on app load, the person should land on ordinary Screen 1 onboarding,
    never see an error, never get stuck."""
    broken_client = MagicMock()
    broken_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("network down")
    some_cookie = _cookie_controller_returning("device-id-doesnt-matter")

    with patch("persistence.get_supabase_client", return_value=broken_client):
        with patch("persistence.CookieController", return_value=some_cookie):
            at = AppTest.from_file(_APP_PATH)
            at.run(timeout=15)

    assert not at.exception, f"Supabase-down load raised instead of failing open: {at.exception}"
    assert at.session_state["screen"] == 1
