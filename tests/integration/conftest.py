import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _default_lifetime_cap_allows():
    """Most integration tests in this directory exercise render/UI flows
    (render history, the review gate, checkout, the History screen) and
    aren't themselves testing lifetime_cap.py's entitlement logic - that
    is tests/unit/test_lifetime_cap.py's job. They need a render to be
    allowed to proceed so the rest of the flow under test can run.

    check_and_reserve_lifetime_render now fails CLOSED (Phase 2, 23 Aug
    2026 hardening pass) rather than open, since it's the real free-tier
    entitlement boundary, not a soft spend guard - see lifetime_cap.py's
    own module docstring for why. That means any integration test that
    doesn't itself configure a working Supabase client for lifetime_cap
    would otherwise have every render silently blocked before the code
    path under test ever runs, which is exactly what broke here when the
    fail-open default was removed.

    This autouse fixture defaults check_and_reserve_lifetime_render to
    "allowed" for every test in this directory. A test that specifically
    wants to exercise the paywall/blocked path still can, by nesting its
    own `patch("lifetime_cap.check_and_reserve_lifetime_render", ...)`
    inside its test body - the innermost active patch wins for the
    duration of that `with` block, per unittest.mock's normal context-
    manager stacking, and reverts to this fixture's "allowed" default
    once that block exits.
    """
    with patch("lifetime_cap.check_and_reserve_lifetime_render", return_value=(True, 1, 15)):
        yield
