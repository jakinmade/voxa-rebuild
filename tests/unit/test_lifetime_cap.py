"""
Tests for lifetime_cap.py — the per-device lifetime free-render
counter (15 renders, Section 5.2).

Rewritten 23 Aug 2026 (Phase 2 of the hardening build order) alongside
two behaviour changes to the module itself:

1. ATOMIC: check_and_reserve_lifetime_render now calls a single
   Postgres RPC (reserve_lifetime_render) instead of a separate
   select-then-upsert, closing the read-then-write race a device could
   previously exploit under concurrency to exceed its 15-render cap.
2. FAILS CLOSED: unlike render_cap.py (a deliberate, tested fail-open
   spend guard), this module is the actual free-tier entitlement
   boundary. check_and_reserve_lifetime_render now DENIES the render
   when Supabase is unreachable/unconfigured, rather than allowing it
   through — see the module's own docstring for why. The two read-only
   helpers (get_lifetime_render_count, device_has_active_subscription)
   keep the old fail-open contract, since they only affect UI display,
   never the entitlement decision itself.
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


def _mock_rpc_client(rpc_rows=None, raise_on_rpc=False):
    """Mocks the client.rpc(name, params).execute() call path used by
    check_and_reserve_lifetime_render and release_reserved_lifetime_render.
    rpc_rows, if given, is the list that .execute().data returns —
    matching what Supabase returns for an RPC backed by a
    RETURNS TABLE(...) Postgres function."""
    client = MagicMock()
    if raise_on_rpc:
        client.rpc.return_value.execute.side_effect = Exception("boom")
    else:
        result = MagicMock()
        result.data = rpc_rows if rpc_rows is not None else []
        client.rpc.return_value.execute.return_value = result
    return client


def _mock_table_client(existing_count=None, subscription_status=None, raise_on_select=False):
    """Mocks the client.table(...).select(...).eq(...).limit(...).execute()
    path used by the read-only helpers (get_lifetime_render_count,
    device_has_active_subscription), which still query the table
    directly rather than going through the RPC."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    if raise_on_select:
        table.select.return_value.eq.return_value.limit.return_value.execute.side_effect = Exception("boom")
    else:
        result = MagicMock()
        if existing_count is not None:
            row = {"count": existing_count}
            if subscription_status is not None:
                row["subscription_status"] = subscription_status
            result.data = [row]
        else:
            result.data = []
        table.select.return_value.eq.return_value.limit.return_value.execute.return_value = result
    return client


# ------------------------------------------------------------------
# Fail-CLOSED behaviour (the enforcement function) — the main thing
# that changed in this pass. A device must be denied, not waved
# through, whenever the reservation can't be verified.
# ------------------------------------------------------------------

def test_denies_when_supabase_not_configured():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False
    assert used == 0
    assert limit == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


def test_denies_on_rpc_failure():
    client = _mock_rpc_client(raise_on_rpc=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False
    assert used == 0


def test_denies_when_rpc_returns_no_rows():
    client = _mock_rpc_client(rpc_rows=[])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False
    assert used == 0


# ------------------------------------------------------------------
# Normal operation — via the atomic RPC
# ------------------------------------------------------------------

def test_allows_and_increments_when_under_limit():
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 6, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 6
    client.rpc.assert_called_once_with(
        "reserve_lifetime_render",
        {"p_device_id": "device-1", "p_limit": lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS},
    )


def test_allows_first_render_with_no_existing_row():
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 1, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 1


def test_blocks_exactly_at_the_limit():
    os.environ["MAX_LIFETIME_RENDERS"] = "15"
    client = _mock_rpc_client(rpc_rows=[{"allowed": False, "used_count": 15, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False
    assert used == 15
    assert limit == 15


def test_allows_one_under_the_limit():
    os.environ["MAX_LIFETIME_RENDERS"] = "15"
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 15, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 15


def test_default_limit_is_fifteen():
    assert lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS == 15


def test_limit_is_passed_through_to_the_rpc():
    os.environ["MAX_LIFETIME_RENDERS"] = "5"
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 1, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.check_and_reserve_lifetime_render("device-1")
    client.rpc.assert_called_once_with(
        "reserve_lifetime_render", {"p_device_id": "device-1", "p_limit": 5}
    )


# ------------------------------------------------------------------
# Per-device isolation — the RPC is called with this specific
# device_id, not a shared/global identity.
# ------------------------------------------------------------------

def test_different_devices_checked_independently():
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 1, "subscription_status": None}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.check_and_reserve_lifetime_render("device-a")
    call_args = client.rpc.call_args[0]
    assert call_args[1]["p_device_id"] == "device-a"


# ------------------------------------------------------------------
# Read-only getter — unchanged: still queries the table directly,
# still fails open (a display helper, not the entitlement check).
# ------------------------------------------------------------------

def test_get_lifetime_render_count_does_not_write():
    client = _mock_table_client(existing_count=7)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        used, limit = lifetime_cap.get_lifetime_render_count("device-1")
    assert used == 7
    client.table.return_value.upsert.assert_not_called()


def test_get_lifetime_render_count_fails_open_to_zero():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        used, limit = lifetime_cap.get_lifetime_render_count("device-1")
    assert used == 0
    assert limit == lifetime_cap._DEFAULT_MAX_LIFETIME_RENDERS


def test_get_lifetime_render_count_fails_open_on_error():
    client = _mock_table_client(raise_on_select=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        used, limit = lifetime_cap.get_lifetime_render_count("device-1")
    assert used == 0


# ------------------------------------------------------------------
# Env var handling — unchanged
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
# active subscription must short-circuit the cap entirely: the RPC
# itself handles this server-side now (see the migration SQL), so
# these tests confirm the Python layer correctly passes through
# whatever the RPC decided rather than re-deciding it.
# ------------------------------------------------------------------

def test_active_subscription_bypasses_cap_even_over_limit():
    client = _mock_rpc_client(rpc_rows=[{"allowed": True, "used_count": 999, "subscription_status": "active"}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is True
    assert used == 999


def test_inactive_subscription_status_still_gets_capped():
    # Any status other than "active" (None, "cancelled", "past_due",
    # etc.) must fall through to the ordinary count/limit decision -
    # not treated as paid. The RPC makes this decision; here we just
    # confirm the Python layer honours an "allowed: False" from it.
    client = _mock_rpc_client(rpc_rows=[{"allowed": False, "used_count": 15, "subscription_status": "cancelled"}])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        allowed, used, limit = lifetime_cap.check_and_reserve_lifetime_render("device-1")
    assert allowed is False


# ------------------------------------------------------------------
# device_has_active_subscription — read-only UI helper, unchanged
# ------------------------------------------------------------------

def test_device_has_active_subscription_true():
    client = _mock_table_client(existing_count=0, subscription_status="active")
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is True


def test_device_has_active_subscription_false_when_not_subscribed():
    client = _mock_table_client(existing_count=2)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is False


def test_device_has_active_subscription_fails_open_to_false():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        assert lifetime_cap.device_has_active_subscription("device-1") is False


def test_device_has_active_subscription_fails_open_on_error():
    client = _mock_table_client(raise_on_select=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        assert lifetime_cap.device_has_active_subscription("device-1") is False


# ------------------------------------------------------------------
# release_reserved_lifetime_render — now calls the release_lifetime_
# render RPC. Fails open/silently on any error, same contract as
# before: a failed release just costs the user a slot they shouldn't
# have lost, not something worth surfacing as an error to them.
# ------------------------------------------------------------------

def test_release_calls_the_release_rpc():
    client = _mock_rpc_client(rpc_rows=[14])
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.release_reserved_lifetime_render("device-1")
    client.rpc.assert_called_once_with("release_lifetime_render", {"p_device_id": "device-1"})


def test_release_no_ops_when_supabase_not_configured():
    with patch("lifetime_cap.get_supabase_client", return_value=None):
        lifetime_cap.release_reserved_lifetime_render("device-1")  # must not raise


def test_release_fails_silently_on_rpc_error():
    client = _mock_rpc_client(raise_on_rpc=True)
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        lifetime_cap.release_reserved_lifetime_render("device-1")  # must not raise


# ------------------------------------------------------------------
# Concurrency — the specific thing Phase 2 of the hardening build
# order asked to prove: "fire N simultaneous render requests near the
# cap boundary, assert exactly the correct number succeed."
#
# IMPORTANT SCOPE NOTE: the actual atomicity guarantee lives in
# Postgres, not in this Python module — a single
# UPDATE ... WHERE count < limit ... RETURNING statement is what makes
# concurrent callers for the same device_id serialize correctly (see
# migrations/2026_08_23_atomic_lifetime_render_cap.sql). That
# guarantee can only be verified by exercising the real, deployed RPC
# against a live or local Postgres instance under genuine concurrent
# connections — this sandbox has neither, so this test cannot and
# does not prove the SQL is atomic.
#
# What this test DOES prove: that the Python wrapper around the RPC
# introduces no race of its OWN — no shared mutable state read or
# written outside the (simulated) atomic call — using a fake RPC
# client whose .execute() reproduces the exact semantics the real
# Postgres function guarantees (a single lock-protected
# check-then-increment). If that premise holds for the real RPC
# (which the migration's own single-UPDATE-statement design gives it),
# this test's result — exactly `limit - already_used` callers
# succeed, no more, no fewer — is what the live system will do too.
# Before shipping, this should additionally be exercised once against
# a real Supabase/Postgres branch, per Phase 3-style "prove it
# end-to-end" verification, not just this mocked contract test.
# ------------------------------------------------------------------

def test_concurrent_reservations_never_exceed_the_limit():
    import threading

    limit = 15
    already_used = 13
    n_concurrent_callers = 10  # more than the 2 slots actually remaining

    lock = threading.Lock()
    state = {"count": already_used, "subscription_status": None}

    def fake_rpc(name, params):
        assert name == "reserve_lifetime_render"
        response = MagicMock()
        # Reproduces the real Postgres function's atomicity: the
        # check-and-increment happens under a single lock, exactly
        # like the real UPDATE ... WHERE ... RETURNING statement
        # serializes concurrent callers on the row lock.
        with lock:
            if state["count"] < params["p_limit"]:
                state["count"] += 1
                allowed = True
            else:
                allowed = False
            result = MagicMock()
            result.data = [{
                "allowed": allowed,
                "used_count": state["count"],
                "subscription_status": state["subscription_status"],
            }]
            return result

    client = MagicMock()
    client.rpc.side_effect = lambda name, params: MagicMock(execute=lambda: fake_rpc(name, params))

    results = []
    results_lock = threading.Lock()

    def call_once():
        allowed, used, lim = lifetime_cap.check_and_reserve_lifetime_render("device-1")
        with results_lock:
            results.append(allowed)

    # Patched ONCE, outside the threads - not per-thread. unittest.mock.patch()
    # is not itself thread-safe: each thread's own `with patch(...)` saves
    # and restores the target attribute on enter/exit, and concurrent
    # enter/exit ordering across threads can restore the WRONG saved value,
    # permanently leaving a MagicMock in place on lifetime_cap.get_supabase_
    # client after the test ends - which then breaks every subsequent test
    # that imports lifetime_cap and hits a real code path expecting a real
    # client or None. Confirmed live: this exact bug polluted global state
    # and broke ~60 unrelated integration tests when this test patched
    # per-thread instead of once, before this fix.
    with patch("lifetime_cap.get_supabase_client", return_value=client):
        threads = [threading.Thread(target=call_once) for _ in range(n_concurrent_callers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    allowed_count = sum(1 for r in results if r is True)
    denied_count = sum(1 for r in results if r is False)

    assert allowed_count == limit - already_used, (
        f"Expected exactly {limit - already_used} of {n_concurrent_callers} "
        f"concurrent callers to be allowed (the remaining slots under the "
        f"cap), got {allowed_count}. This would mean the reservation isn't "
        f"correctly serialized even under the simulated-atomic RPC."
    )
    assert denied_count == n_concurrent_callers - (limit - already_used)
    assert state["count"] == limit, (
        f"Final count should land exactly on the limit ({limit}), "
        f"got {state['count']} — any deviation means callers raced past "
        f"or under the boundary."
    )
