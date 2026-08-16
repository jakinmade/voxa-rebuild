"""
supabase_client.get_supabase_client() — the shared client factory, and
specifically the timeout fix made while investigating a live report of
a review-gate confirmation appearing to do nothing. See the module's
own docstring for the full mechanism: a 120-second default timeout on
a synchronous call sitting inside a Streamlit click handler can freeze
the whole script mid-run, which looks from the outside like nothing
happened at all.
"""
import os
from unittest.mock import patch, MagicMock

import supabase_client


def test_returns_none_when_url_missing():
    with patch.dict(os.environ, {"SUPABASE_SERVICE_KEY": "fake-key"}, clear=True):
        assert supabase_client.get_supabase_client() is None


def test_returns_none_when_key_missing():
    with patch.dict(os.environ, {"SUPABASE_URL": "https://fake.supabase.co"}, clear=True):
        assert supabase_client.get_supabase_client() is None


def test_returns_none_on_both_missing():
    with patch.dict(os.environ, {}, clear=True):
        assert supabase_client.get_supabase_client() is None


def test_create_client_exception_is_caught_not_raised():
    env = {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_SERVICE_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        with patch("supabase.create_client", side_effect=Exception("boom")):
            # Must not propagate — same fail-open contract as every
            # other caller in this codebase.
            result = supabase_client.get_supabase_client()
            assert result is None


def test_create_client_called_with_short_postgrest_timeout():
    """The actual fix: confirms create_client() is called with a
    ClientOptions carrying a short postgrest_client_timeout, not the
    library's 120-second default. This is the specific thing that
    changed to address the hang investigation - a test asserting the
    default (unconfigured) behaviour would not have caught this gap."""
    env = {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_SERVICE_KEY": "fake-key"}
    with patch.dict(os.environ, env, clear=True):
        with patch("supabase.create_client") as mock_create:
            mock_create.return_value = MagicMock()
            supabase_client.get_supabase_client()

    mock_create.assert_called_once()
    _, kwargs = mock_create.call_args
    assert "options" in kwargs, "create_client() must be called with explicit options"
    options = kwargs["options"]
    assert options.postgrest_client_timeout == supabase_client._POSTGREST_TIMEOUT_SECONDS
    assert options.postgrest_client_timeout < 120, (
        "Timeout must be shorter than supabase-py's 120s default - "
        "that default is the actual bug this fix addresses."
    )
    assert options.postgrest_client_timeout <= 10, (
        "Timeout should be short enough that a hung Supabase call "
        "never holds a person's click for more than a few seconds."
    )


def test_returns_the_real_client_on_success():
    env = {"SUPABASE_URL": "https://fake.supabase.co", "SUPABASE_SERVICE_KEY": "fake-key"}
    fake_client = MagicMock()
    with patch.dict(os.environ, env, clear=True):
        with patch("supabase.create_client", return_value=fake_client):
            result = supabase_client.get_supabase_client()
    assert result is fake_client
