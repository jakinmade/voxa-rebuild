"""
lifetime_cap.py — per-device lifetime free-render counter, the
mechanism the VOICOVA Product 2.0 thesis's freemium model actually
needs (15 lifetime renders, then paywall) and which render_cap.py
does NOT provide — that module is a global DAILY spend guard, unrelated
to monetisation. See VOICOVA_Product_2.0_Consolidated.docx Section 5.2
for why these are two separate mechanisms, not one.

This module answers "has this device used its 15 free renders", not
"is VOICOVA over budget today" — the two checks run independently and
both must pass before a render proceeds (see check order in _run_render,
app.py). render_cap.py stays exactly as-is, untouched by this change.

Reuses the existing device-cookie identity from persistence.py
(get_or_create_device_id) rather than inventing a second identity
model — same device_id already used for voice_profiles.

FAILS OPEN, same reasoning as render_cap.py and persistence.py: an
unconfigured or unreachable Supabase must never block every render.
A missed lifetime-cap check on a rare outage means a small number of
extra free renders slip through, which is a far smaller problem than
blocking every render, paid or free, product-wide.

SCHEMA (create once in Supabase's SQL editor):

    create table lifetime_render_cap (
        device_id uuid primary key,
        count integer not null default 0
    );

    -- Added 19 Aug 2026 (stripe_subscription.py) — same row per
    -- device, not a new table. See that module's docstring for why
    -- subscription identity is bridged onto this existing table
    -- rather than a separate one.
    alter table lifetime_render_cap
        add column stripe_customer_id text,
        add column subscription_status text;

DEPLOYED 19 Aug 2026 to the live Supabase project (txpsphethknujgqvqdzl)
via Supabase MCP, both columns included from the start rather than as
a later alter - matches the schema above exactly. device_id is uuid,
not text: matches voice_profiles.device_id's actual column type (the
existing table this identity model was built to reuse), not a
same-format assumption - checked against the live schema before
creating this table, not copied from an earlier draft of this
docstring.

CONCURRENCY NOTE: same read-then-write pattern and same accepted
undercounting-on-true-race caveat as render_cap.py — acceptable for a
soft lifetime ceiling, not a billing-exact counter.
"""

import os

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "lifetime_render_cap"

# 15 per the thesis and Section 5.2 of the consolidated spec.
# Override via env var only for testing/demo purposes.
_DEFAULT_MAX_LIFETIME_RENDERS = 15


def _max_lifetime_renders() -> int:
    raw = os.environ.get("MAX_LIFETIME_RENDERS")
    if not raw:
        return _DEFAULT_MAX_LIFETIME_RENDERS
    try:
        return max(1, int(raw))
    except ValueError:
        log.error("lifetime_cap_invalid_env_value", value=raw)
        return _DEFAULT_MAX_LIFETIME_RENDERS


def check_and_reserve_lifetime_render(device_id: str) -> tuple[bool, int, int]:
    """Call once, right before the first paid API call in a render,
    alongside (not instead of) render_cap.py's check_and_reserve_render.
    Returns (allowed, used, limit).

    If allowed is True, the caller may proceed; the render already
    counts toward this device's lifetime total (reserved optimistically
    before the API call, matching render_cap.py's convention) - UNLESS
    the device has an active subscription, in which case nothing is
    counted or checked at all (see subscription check below).

    If allowed is False, the device has used all free renders and has
    not yet paid — the caller shows the paywall instead of rendering.

    Subscription check (19 Aug 2026, stripe_subscription.py): reads the
    SAME row this function already queries for `count` - one extra
    column (subscription_status), no extra Supabase call. An active
    subscription short-circuits before the limit check and before any
    count increment, so paying subscribers are never capped and their
    lifetime count stops moving once they've paid (nothing left for it
    to gate). See stripe_subscription.py's docstring for why this
    couldn't be wired in without that module landing first.

    Fails OPEN: no Supabase configured, or any error, returns
    (True, 0, limit) — same contract as render_cap.py, same reasoning
    in this module's docstring.
    """
    limit = _max_lifetime_renders()
    client = get_supabase_client()
    if client is None:
        log.error("lifetime_cap_check_unavailable", reason="supabase_not_configured")
        return True, 0, limit

    try:
        existing = (
            client.table(_TABLE)
            .select("count, subscription_status")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        current = rows[0]["count"] if rows else 0

        if rows and rows[0].get("subscription_status") == "active":
            return True, current, limit

        if current >= limit:
            log.info("lifetime_cap_reached", used=current, limit=limit)
            return False, current, limit

        new_count = current + 1
        client.table(_TABLE).upsert({"device_id": device_id, "count": new_count}).execute()
        return True, new_count, limit
    except Exception:
        log.error("lifetime_cap_check_unavailable", reason="supabase_error", exc_info=True)
        return True, 0, limit


def device_has_active_subscription(device_id: str) -> bool:
    """Read-only check for UI purposes (e.g. hiding the upgrade prompt
    for someone who's already subscribed). Fails open to False on any
    error - same direction as every other read in this module; a
    missed read here just means the UI offers to upgrade someone who
    already has, which they can dismiss, not a lockout."""
    client = get_supabase_client()
    if client is None:
        return False
    try:
        existing = (
            client.table(_TABLE)
            .select("subscription_status")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        return bool(rows) and rows[0].get("subscription_status") == "active"
    except Exception:
        log.error("subscription_status_read_failed", reason="supabase_error", exc_info=True)
        return False


def get_lifetime_render_count(device_id: str) -> tuple[int, int]:
    """Read-only: returns (used, limit) without reserving a render.
    For UI display (e.g. 'You have used 8 of 15 free renders') where
    a check shouldn't itself consume a render. Fails open to (0, limit)
    on any error, same as the reserving function above."""
    limit = _max_lifetime_renders()
    client = get_supabase_client()
    if client is None:
        return 0, limit

    try:
        existing = (
            client.table(_TABLE)
            .select("count")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        current = rows[0]["count"] if rows else 0
        return current, limit
    except Exception:
        log.error("lifetime_cap_read_unavailable", reason="supabase_error", exc_info=True)
        return 0, limit


def release_reserved_lifetime_render(device_id: str) -> None:
    """Undoes one reservation made by check_and_reserve_lifetime_render
    for this device, for the case where the render that reservation
    was for subsequently fails (Anthropic API error, timeout,
    malformed response) after the cap already counted it against the
    person's 15. Without this, a failed render still costs a free
    slot (Section 15.2 item 5, engineering review response, 19 Aug
    2026: "Separate the reservation from confirmed consumption, or add
    a release-on-failure path"). This is the release-on-failure path.

    Deliberately NOT a change to check_and_reserve_lifetime_render's
    own return signature - that function is unpacked as a fixed
    3-tuple in 10+ existing call sites and tests; widening it to
    signal "was this reservation real" would be a much larger,
    riskier change for the same result. Instead this function is
    self-contained: it reads subscription_status itself before
    deciding whether to decrement, rather than trusting the caller to
    know whether check_and_reserve_lifetime_render actually
    incremented anything for this device. An active-subscription
    device is never incremented in the first place (that function's
    own early-return path), so this correctly no-ops for a subscriber
    rather than incorrectly lowering a count that was never raised.

    Fails open and silently on any error, same contract as every
    other write in this module - a failed release just means one
    render costs the user a slot it shouldn't have, an honest minor
    degradation, not a reason to raise into the render's own error
    handling and mask the real failure that triggered the release."""
    client = get_supabase_client()
    if client is None:
        return
    try:
        existing = (
            client.table(_TABLE)
            .select("count, subscription_status")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        if not rows:
            return
        if rows[0].get("subscription_status") == "active":
            return
        current = rows[0]["count"]
        new_count = max(0, current - 1)
        client.table(_TABLE).update({"count": new_count}).eq("device_id", device_id).execute()
    except Exception:
        log.error("lifetime_cap_release_failed", reason="supabase_error", exc_info=True)
