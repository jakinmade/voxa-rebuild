"""
Tests for persistence.py — cross-session voice profile persistence via
a device cookie + Supabase, no accounts.

Convention, matching the fail-open design in persistence.py itself:
every failure path (no credentials configured, Supabase unreachable,
cookie read/write failure, row exists but has no usable baseline) must
leave session_state exactly as fresh onboarding would, never raise,
never partially apply. That's what most of these tests check — the
happy path (successful save, successful restore) is the smaller half.

Supabase and the cookie controller are both mocked throughout — no
network access, matches the standing cost/dependency discipline used
for the Anthropic client elsewhere in this suite.
"""
import os
from unittest.mock import patch, MagicMock

import pytest
import streamlit as st

import persistence


@pytest.fixture(autouse=True)
def _reset_session_state():
    """Streamlit's session_state needs a clean slate between tests, or
    state (including the cached per-session cookie controller) leaks
    from one test into the next."""
    st.session_state.clear()
    yield
    st.session_state.clear()


def _mock_cookie_controller(existing_value=None):
    # existing_value kept as a parameter for call-site compatibility
    # across the test file, but no longer drives the READ path (that's
    # st.context.cookies now, mocked separately via _mock_context_
    # cookies below) - only relevant for tests exercising the WRITE
    # path, where this mock's .set() is still what's asserted against.
    mock = MagicMock()
    mock.get.return_value = existing_value
    return mock


def _mock_context_cookies(existing_value=None, raise_on_get=False):
    """Mocks st.context.cookies (a dict-like StreamlitCookies object)
    for the READ path, which persistence.py now reads directly and
    synchronously - see persistence.py's module docstring for why this
    replaced the old async CookieController-based read."""
    mock_cookies = MagicMock()
    if raise_on_get:
        mock_cookies.get.side_effect = Exception("cookies unavailable")
    else:
        mock_cookies.get.return_value = existing_value
    mock_context = MagicMock()
    mock_context.cookies = mock_cookies
    return mock_context


def _mock_supabase_client(select_rows=None, raise_on_select=False, raise_on_upsert=False):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    if raise_on_select:
        table.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("boom")
    else:
        result = MagicMock()
        result.data = select_rows or []
        table.select.return_value.eq.return_value.limit.return_value.execute.return_value = result

    if raise_on_upsert:
        table.upsert.return_value.execute.side_effect = Exception("boom")

    return client


# ------------------------------------------------------------------
# get_or_create_device_id
# ------------------------------------------------------------------

def test_returns_existing_cookie_value_if_present():
    with patch("persistence.st.context", _mock_context_cookies("existing-id")):
        assert persistence.get_or_create_device_id() == "existing-id"


def test_generates_and_sets_a_new_id_if_no_cookie():
    mock_controller = _mock_cookie_controller(existing_value=None)
    with patch("persistence.st.context", _mock_context_cookies(None)), \
         patch("persistence._get_cookie_controller", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id  # non-empty
        mock_controller.set.assert_called_once()
        assert mock_controller.set.call_args[0][0] == persistence._COOKIE_NAME
        assert mock_controller.set.call_args[0][1] == new_id


def test_cookie_read_failure_still_returns_a_usable_id():
    mock_controller = _mock_cookie_controller()
    with patch("persistence.st.context", _mock_context_cookies(raise_on_get=True)), \
         patch("persistence._get_cookie_controller", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id


def test_cookie_write_failure_does_not_raise():
    mock_controller = _mock_cookie_controller(existing_value=None)
    mock_controller.set.side_effect = Exception("cookie blocked")
    with patch("persistence.st.context", _mock_context_cookies(None)), \
         patch("persistence._get_cookie_controller", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id  # still returns something usable this visit


# ------------------------------------------------------------------
# set_device_id_cookie — 27 Aug 2026 hardening pass, live incident:
# added so a verified device_id from Stripe (which may differ from
# whatever local cookie/session_state existed before, if the original
# write raced the checkout redirect) can be forced as this browser's
# identity, rather than only ever being able to generate a fresh
# random one.
# ------------------------------------------------------------------

def test_set_device_id_cookie_writes_the_given_id_not_a_generated_one():
    mock_controller = _mock_cookie_controller()
    with patch("persistence._get_cookie_controller", return_value=mock_controller):
        persistence.set_device_id_cookie("stripe-verified-device-id")
    mock_controller.set.assert_called_once()
    assert mock_controller.set.call_args[0][0] == persistence._COOKIE_NAME
    assert mock_controller.set.call_args[0][1] == "stripe-verified-device-id"


def test_set_device_id_cookie_updates_cached_session_state_value():
    mock_controller = _mock_cookie_controller()
    with patch("persistence._get_cookie_controller", return_value=mock_controller):
        persistence.set_device_id_cookie("stripe-verified-device-id")
    assert st.session_state["_device_id"] == "stripe-verified-device-id"


def test_set_device_id_cookie_overwrites_a_different_existing_value():
    """The actual scenario this exists for: session_state/cookie
    already has SOME (wrong/unrelated) device_id, and this must
    replace it with the Stripe-verified one, not defer to what's
    already there."""
    st.session_state["_device_id"] = "some-other-unrelated-id"
    mock_controller = _mock_cookie_controller()
    with patch("persistence._get_cookie_controller", return_value=mock_controller):
        persistence.set_device_id_cookie("stripe-verified-device-id")
    assert st.session_state["_device_id"] == "stripe-verified-device-id"
    assert mock_controller.set.call_args[0][1] == "stripe-verified-device-id"


def test_set_device_id_cookie_write_failure_does_not_raise():
    mock_controller = _mock_cookie_controller()
    mock_controller.set.side_effect = Exception("cookie blocked")
    with patch("persistence._get_cookie_controller", return_value=mock_controller):
        persistence.set_device_id_cookie("stripe-verified-device-id")  # must not raise
    assert st.session_state["_device_id"] == "stripe-verified-device-id"


# ------------------------------------------------------------------
# restore_profile_if_available
# ------------------------------------------------------------------

def test_restore_returns_false_when_credentials_not_configured():
    with patch.dict(os.environ, {}, clear=True):
        assert persistence.restore_profile_if_available() is False
    assert "baseline_fingerprint" not in st.session_state


def test_restore_returns_false_and_does_not_raise_when_supabase_unreachable():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(raise_on_select=True)):
                assert persistence.restore_profile_if_available() is False


def test_restore_returns_false_when_no_matching_row():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[])):
                assert persistence.restore_profile_if_available() is False


def test_restore_returns_false_when_row_exists_but_has_no_baseline():
    row = {"device_id": "device-1", "raw_text": "hello", "baseline_fingerprint": None}
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is False


def test_restore_populates_session_state_on_a_real_match():
    row = {
        "device_id": "device-1",
        "raw_text": "I think we should move fast on this.",
        "sample2_completions": ["a", "b", "", ""],
        "baseline_fingerprint": {"hedge_density": 1.0},
        "starter_baseline": {"hedge_density": 1.2},
    }
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is True
    assert st.session_state["raw_text"] == "I think we should move fast on this."
    assert st.session_state["baseline_fingerprint"] == {"hedge_density": 1.0}
    assert st.session_state["starter_baseline"] == {"hedge_density": 1.2}


def test_restore_populates_voice_profile_summary_when_present():
    row = {
        "device_id": "device-1",
        "raw_text": "some writing",
        "baseline_fingerprint": {"hedge_density": 1.0},
        "voice_profile_summary": "Writes short, direct sentences. Rarely hedges.",
    }
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is True
    assert st.session_state["voice_profile_summary"] == "Writes short, direct sentences. Rarely hedges."


def test_restore_omits_voice_profile_summary_key_when_absent():
    """A row saved before this feature existed won't have the column —
    restore must not set the key at all in that case (not set it to
    None), so downstream code's simple truthiness checks behave the
    same as if the feature had just never generated a summary yet."""
    row = {
        "device_id": "device-1",
        "raw_text": "some writing",
        "baseline_fingerprint": {"hedge_density": 1.0},
    }
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is True
    assert "voice_profile_summary" not in st.session_state


def test_restore_does_not_overwrite_an_already_populated_session():
    """Guards against clobbering a baseline built earlier this same
    session (e.g. a mid-flow rerun) with a stale saved profile."""
    st.session_state["baseline_fingerprint"] = {"hedge_density": 9.9}
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        assert persistence.restore_profile_if_available() is False
    assert st.session_state["baseline_fingerprint"] == {"hedge_density": 9.9}


# ------------------------------------------------------------------
# save_profile_if_available
# ------------------------------------------------------------------

def test_save_no_ops_when_credentials_not_configured():
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    with patch.dict(os.environ, {}, clear=True):
        persistence.save_profile_if_available()  # must not raise


def test_save_no_ops_when_no_baseline_yet():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        mock_client = _mock_supabase_client()
        with patch("persistence.get_supabase_client", return_value=mock_client):
            persistence.save_profile_if_available()
    mock_client.table.return_value.upsert.assert_not_called()


def test_save_upserts_with_the_expected_payload():
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    st.session_state["raw_text"] = "some writing"
    st.session_state["starter_baseline"] = {"hedge_density": 1.1}
    st.session_state["_device_id"] = "device-1"
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        mock_client = _mock_supabase_client()
        with patch("persistence.get_supabase_client", return_value=mock_client):
            persistence.save_profile_if_available()

    mock_client.table.assert_called_with(persistence._TABLE)
    upsert_call = mock_client.table.return_value.upsert
    upsert_call.assert_called_once()
    payload = upsert_call.call_args[0][0]
    assert payload["device_id"] == "device-1"
    assert payload["raw_text"] == "some writing"
    assert payload["baseline_fingerprint"] == {"hedge_density": 1.0}
    assert payload["starter_baseline"] == {"hedge_density": 1.1}


def test_save_includes_voice_profile_summary_when_present():
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    st.session_state["_device_id"] = "device-1"
    st.session_state["voice_profile_summary"] = "Writes short, direct sentences."
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        mock_client = _mock_supabase_client()
        with patch("persistence.get_supabase_client", return_value=mock_client):
            persistence.save_profile_if_available()

    payload = mock_client.table.return_value.upsert.call_args[0][0]
    assert payload["voice_profile_summary"] == "Writes short, direct sentences."


def test_save_includes_none_for_voice_profile_summary_when_not_yet_generated():
    """Confirms the key is always present in the payload (as None if
    not yet generated), not silently omitted — an upsert with a
    missing key vs. an explicit None can behave differently depending
    on the client, so this pins the actual behaviour down."""
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    st.session_state["_device_id"] = "device-1"
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        mock_client = _mock_supabase_client()
        with patch("persistence.get_supabase_client", return_value=mock_client):
            persistence.save_profile_if_available()

    payload = mock_client.table.return_value.upsert.call_args[0][0]
    assert payload["voice_profile_summary"] is None


def test_save_failure_does_not_raise():
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(raise_on_upsert=True)):
                persistence.save_profile_if_available()  # must not raise


# ------------------------------------------------------------------
# Regression: repeated calls within one session must not each mint a
# new device id (26 Aug 2026 live incident)
# ------------------------------------------------------------------

def test_repeated_calls_within_one_session_do_not_mint_new_ids_each_time():
    """Live incident, 26 Aug 2026: confirmed via production DIAG logs
    that a single page load produced THREE different randomly-
    generated device ids in under two seconds, each overwriting the
    last, because get_or_create_device_id() had no memory of already
    having resolved an id earlier in the same session - every call
    re-ran the full cookie read/write dance, and Streamlit's own
    automatic reruns (while the cookie component's async round-trip
    is still in flight) meant a premature read-before-write-lands
    looked identical to a genuinely absent cookie every time.

    This simulates exactly that: the cookie controller's .get() never
    starts returning the written value (as it wouldn't, mid-race, in
    the real bug) - the fix must still only mint one id, by caching
    its own resolution in st.session_state and never touching the
    controller again once resolved, not by the mock happening to
    become consistent."""
    mock_controller = _mock_cookie_controller(existing_value=None)
    with patch("persistence.st.context", _mock_context_cookies(None)), \
         patch("persistence._get_cookie_controller", return_value=mock_controller):
        first_id = persistence.get_or_create_device_id()
        st.session_state["_device_id"] = first_id

        second_id = persistence.get_or_create_device_id()
        third_id = persistence.get_or_create_device_id()

    assert second_id == first_id
    assert third_id == first_id
    # The controller must only ever have been written to once - every
    # subsequent call should short-circuit on the cached session_state
    # value before it ever reaches the cookie controller again.
    mock_controller.set.assert_called_once()


def test_restore_profile_if_available_reuses_cached_device_id():
    """The one call site that was missing the same st.session_state.
    get("_device_id") or ... guard every other call site already uses
    - restore_profile_if_available() itself. A second call within the
    same session must not touch the cookie controller again."""
    mock_controller = _mock_cookie_controller(existing_value=None)
    with patch("persistence.st.context", _mock_context_cookies(None)), \
         patch("persistence._get_cookie_controller", return_value=mock_controller), \
         patch("persistence.get_supabase_client", return_value=None):
        persistence.get_or_create_device_id()
        st.session_state["_device_id"] = st.session_state.get("_device_id") or "resolved-id"

        # get_supabase_client() returns None here (no credentials),
        # so restore_profile_if_available() returns False fast - but
        # it must still resolve device_id from the cache, not the
        # controller, once _device_id is already set.
        persistence.restore_profile_if_available()

    assert mock_controller.set.call_count <= 1


def test_read_path_never_calls_st_rerun():
    """The whole point of switching the read to st.context.cookies
    (26 Aug 2026, live incident): it is populated synchronously from
    the real HTTP Cookie header on the initial request, so reading it
    should never need to defer via st.rerun() the way the old async
    CookieController-based read did. A stray st.rerun() call here
    would silently reintroduce the exact class of timing bug this
    rewrite exists to remove."""
    with patch("persistence.st.context", _mock_context_cookies("existing-id")), \
         patch("persistence.st.rerun") as mock_rerun:
        persistence.get_or_create_device_id()

    mock_rerun.assert_not_called()
