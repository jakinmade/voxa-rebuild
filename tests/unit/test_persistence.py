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
    mock = MagicMock()
    mock.get.return_value = existing_value
    return mock


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
    with patch("persistence.CookieController", return_value=_mock_cookie_controller("existing-id")):
        assert persistence.get_or_create_device_id() == "existing-id"


def test_generates_and_sets_a_new_id_if_no_cookie():
    mock_controller = _mock_cookie_controller(existing_value=None)
    with patch("persistence.CookieController", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id  # non-empty
        mock_controller.set.assert_called_once()
        assert mock_controller.set.call_args[0][0] == persistence._COOKIE_NAME
        assert mock_controller.set.call_args[0][1] == new_id


def test_cookie_read_failure_still_returns_a_usable_id():
    mock_controller = _mock_cookie_controller()
    mock_controller.get.side_effect = Exception("cookie blocked")
    with patch("persistence.CookieController", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id


def test_cookie_write_failure_does_not_raise():
    mock_controller = _mock_cookie_controller(existing_value=None)
    mock_controller.set.side_effect = Exception("cookie blocked")
    with patch("persistence.CookieController", return_value=mock_controller):
        new_id = persistence.get_or_create_device_id()
        assert new_id  # still returns something usable this visit


# ------------------------------------------------------------------
# restore_profile_if_available
# ------------------------------------------------------------------

def test_restore_returns_false_when_credentials_not_configured():
    with patch.dict(os.environ, {}, clear=True):
        assert persistence.restore_profile_if_available() is False
    assert "baseline_fingerprint" not in st.session_state


def test_restore_returns_false_and_does_not_raise_when_supabase_unreachable():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(raise_on_select=True)):
                assert persistence.restore_profile_if_available() is False


def test_restore_returns_false_when_no_matching_row():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[])):
                assert persistence.restore_profile_if_available() is False


def test_restore_returns_false_when_row_exists_but_has_no_baseline():
    row = {"device_id": "device-1", "raw_text": "hello", "baseline_fingerprint": None}
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
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
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
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
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
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
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
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
        with patch("persistence.CookieController", return_value=_mock_cookie_controller("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(raise_on_upsert=True)):
                persistence.save_profile_if_available()  # must not raise
