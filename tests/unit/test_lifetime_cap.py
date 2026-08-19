"""
Tests for lifetime_cap.py — the per-device lifetime free-render
counter (15 renders, Section 5.2). Same fail-open contract as
render_cap.py and persistence.py, but keyed to device_id rather than
a single global daily row.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import lifetime_cap


@pytest.fixture(autouse=True)
def _clean_env():
    original = os.environ.pop("MAX_LIFETIME_RENDERS", None)
    yield
    if original is not None:
        os.environ["MAX_LIFETIME_RENDERS"] = original
    else:
        os.environ.pop("MAX_LIFETIME_RENDERS", None)


def _mock_supabase_client(existing_count=None, raise_on_select=False, raise_on_upsert=False):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    if raise_on_select:
        table.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("boom")
    else:
        result = MagicMock()
        result.data = [{"count": existing_count}] if existing_count is not None else []
        table.select.return_value.eq.return_value.limit.return_value.execute.return_value = result

    if raise_on_upsert:
        table.upsert.return_value.execute.side_effect = Exception("boom")

    return client


# ------------------------------------------------------------------
# Fail-open behaviour
# ------------------------------------------------------------------

def test_allows_when_supabase_not_configured():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 0
    assert limit == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


def test_allows_on_select_failure():
    client = _mock_supabase_client(raise_on_select=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 0


def test_allows_on_upsert_failure():
    client = _mock_supabase_client(existing_count=3, raise_on_upsert=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True


# ------------------------------------------------------------------
# Normal operation
# ------------------------------------------------------------------

def test_allows_and_increments_when_under_limit():
    client = _mock_supabase_client(existing_count=5)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 6
    client.table.return_value.upsert.assert_called_once()
    upsert_payload = client.table.return_value.upsert.call_args[0][0]
    assert upsert_payload["count"] == 6
    assert upsert_payload["device_id"] == "device-1"


def test_allows_first_render_with_no_existing_row():
    client = _mock_supabase_client(existing_count=None)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 1


def test_blocks_exactly_at_the_limit():
    os.environ["MAX_LIFETIME_RENDERS"] = "15"
    client = _mock_supabase_client(existing_count=15)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False
    assert used == 15
    assert limit == 15
    client.table.return_value.upsert.assert_not_called()


def test_allows_one_under_the_limit():
    os.environ["MAX_LIFETIME_RENDERS"] = "15"
    client = _mock_supabase_client(existing_count=14)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 15


def test_default_limit_is_fifteen():
    assert lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS == 15


# ------------------------------------------------------------------
# Per-device isolation — the one behaviour render_cap.py doesn't need
# to have at all, since that module has a single global row.
# ------------------------------------------------------------------

def test_different_devices_checked_independently():
    client = _mock_supabase_client(existing_count=15)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.check_and_reserve_lifetime_render("device-a")
    # Confirms the query filters by this specific device_id, not a
    # global row — the .eq() call must have been made with it.
    client.table.return_value.select.return_value.eq.assert_called_with("device_id", "device-a")


# ------------------------------------------------------------------
# Read-only getter
# ------------------------------------------------------------------

def test_get_lifetime_render_count_does_not_write():
    client = _mock_supabase_client(existing_count=7)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        used, limit = lifetime_cap.get_lifetime_render_count("device-1")
    assert used == 7
    client.table.return_value.upsert.assert_not_called()


def test_get_lifetime_render_count_fails_open_to_zero():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        used, limit = lifetime_cap.get_lifetime_render_count("device-1")
    assert used == 0
    assert limit == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


# ------------------------------------------------------------------
# Env var handling
# ------------------------------------------------------------------

def test_env_var_overrides_default_limit():
    os.environ["MAX_LIFETIME_RENDERS"] = "5"
    assert lifetime_cap._max_lifetime_renders() == 5


def test_invalid_env_var_falls_back_to_default():
    os.environ["MAX_LIFETIME_RENDERS"] = "not-a-number"
    assert lifetime_cap._max_lifetime_renders() == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


def test_zero_or_negative_env_var_floors_to_one():
    os.environ["MAX_LIFETIME_RENDERS"] = "0"
    assert lifetime_cap._max_lifetime_renders() == 1
    os.environ["MAX_LIFETIME_RENDERS"] = "-5"
    assert lifetime_cap._max_lifetime_renders() == 1


def test_missing_env_var_uses_default():
    assert lifetime_cap._max_lifetime_renders() == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


# ------------------------------------------------------------------
# Subscription bypass (19 Aug 2026, stripe_subscription.py) — an
# active subscription must short-circuit the cap entirely: never
# blocked even over the limit, and never counted (no upsert at all).
# ------------------------------------------------------------------

def _mock_supabase_client_with_subscription(existing_count, subscription_status):
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    result = MagicMock()
    result.data = [{"count": existing_count, "subscription_status": subscription_status}]
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value = result
    return client


def test_active_subscription_bypasses_cap_even_over_limit():
    client = _mock_supabase_client_with_subscription(existing_count=999, subscription_status="active")
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 999


def test_active_subscription_does_not_increment_count():
    client = _mock_supabase_client_with_subscription(existing_count=3, subscription_status="active")
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.check_and_reserve_lifetime_render("device-1")
    client.table.return_value.upsert.assert_not_called()


def test_inactive_subscription_status_still_gets_capped():
    # Any status other than "active" (None, "cancelled", "past_due",
    # etc.) must fall through to the ordinary count/limit check - not
    # treated as paid.
    client = _mock_supabase_client_with_subscription(existing_count=15, subscription_status="cancelled")
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False


def test_no_subscription_column_value_still_gets_capped():
    # A device that's never touched the subscription flow at all -
    # subscription_status key absent entirely, not just None - must
    # not accidentally bypass the cap.
    client = _mock_supabase_client(existing_count=15)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False


# ------------------------------------------------------------------
# device_has_active_subscription — read-only UI helper
# ------------------------------------------------------------------

def test_device_has_active_subscription_true():
    client = _mock_supabase_client_with_subscription(existing_count=0, subscription_status="active")
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is True


def test_device_has_active_subscription_false_when_not_subscribed():
    client = _mock_supabase_client(existing_count=2)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is False


def test_device_has_active_subscription_fails_open_to_false():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        assert lifetime_cap.device_has_active_subscription("device-1") is False


def test_device_has_active_subscription_fails_open_on_error():
    client = _mock_supabase_client(raise_on_select=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is False
