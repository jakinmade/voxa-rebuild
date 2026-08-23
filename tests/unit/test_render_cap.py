"""
Tests for render_cap.py — the global daily render ceiling that sits
in front of every paid API call.

This module fails OPEN, not closed: no Supabase client, or any error
talking to it, must never block a render. An earlier version failed
closed instead and was reverted — see render_cap.py's own module
docstring for why (it blocked every render in any environment without
Supabase configured, including local/dev/test runs). That fail-open
behaviour is the main thing worth testing carefully here — everything
else (limit reached, limit not reached, env var override) is standard
boundary-condition coverage.
"""
import os
from unittest.mock import MagicMock, patch

import pytest

import render_cap


@pytest.fixture(autouse=True)
def _clean_env():
    """MAX_RENDERS_PER_DAY must not leak between tests."""
    original = os.environ.pop("MAX_RENDERS_PER_DAY", None)
    yield
    if original is not None:
        os.environ["MAX_RENDERS_PER_DAY"] = original
    else:
        os.environ.pop("MAX_RENDERS_PER_DAY", None)


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
# Fail-open behaviour — matches every other module in the persistence
# layer. An earlier version of this failed closed instead; that broke
# 17 unrelated tests because no test configures Supabase, and more
# importantly would have blocked every render product-wide the moment
# Supabase hiccuped or the table wasn't provisioned yet. See the
# module docstring.
# ------------------------------------------------------------------

def test_allows_when_supabase_not_configured():
    with patch("render_cap.get_supabase_client", return_value=None):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True
    assert used == 0
    assert limit == render_cap._DEFAULT_MAX_RENDERS_PER_DAY


def test_allows_on_select_failure():
    client = _mock_supabase_client(raise_on_select=True)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True
    assert used == 0


def test_allows_on_upsert_failure():
    client = _mock_supabase_client(existing_count=3, raise_on_upsert=True)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True


# ------------------------------------------------------------------
# Normal operation
# ------------------------------------------------------------------

def test_allows_and_increments_when_under_limit():
    client = _mock_supabase_client(existing_count=5)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True
    assert used == 6
    client.table.return_value.upsert.assert_called_once()
    upsert_payload = client.table.return_value.upsert.call_args[0][0]
    assert upsert_payload["count"] == 6


def test_allows_first_render_of_the_day_with_no_existing_row():
    client = _mock_supabase_client(existing_count=None)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True
    assert used == 1


def test_blocks_exactly_at_the_limit():
    os.environ["MAX_RENDERS_PER_DAY"] = "10"
    client = _mock_supabase_client(existing_count=10)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is False
    assert used == 10
    assert limit == 10
    # No write should happen once the cap is already reached.
    client.table.return_value.upsert.assert_not_called()


def test_allows_one_under_the_limit():
    os.environ["MAX_RENDERS_PER_DAY"] = "10"
    client = _mock_supabase_client(existing_count=9)
    with patch("render_cap.get_supabase_client", return_value=client):
        allowed, used, limit = render_cap.check_and_reserve_render()
    assert allowed is True
    assert used == 10


# ------------------------------------------------------------------
# Env var handling
# ------------------------------------------------------------------

def test_env_var_overrides_default_limit():
    os.environ["MAX_RENDERS_PER_DAY"] = "5"
    assert render_cap._max_renders_per_day() == 5


def test_invalid_env_var_falls_back_to_default():
    os.environ["MAX_RENDERS_PER_DAY"] = "not-a-number"
    assert render_cap._max_renders_per_day() == render_cap._DEFAULT_MAX_RENDERS_PER_DAY


def test_zero_or_negative_env_var_floors_to_one():
    os.environ["MAX_RENDERS_PER_DAY"] = "0"
    assert render_cap._max_renders_per_day() == 1
    os.environ["MAX_RENDERS_PER_DAY"] = "-5"
    assert render_cap._max_renders_per_day() == 1


def test_missing_env_var_uses_default():
    assert render_cap._max_renders_per_day() == render_cap._DEFAULT_MAX_RENDERS_PER_DAY
