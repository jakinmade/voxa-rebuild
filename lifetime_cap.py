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
        device_id text primary key,
        count integer not null default 0
    );

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
    before the API call, matching render_cap.py's convention).

    If allowed is False, the device has used all free renders and has
    not yet paid — the caller shows the paywall instead of rendering.

    A device_id belonging to a PAID subscriber must be checked against
    subscription status BEFORE this function is called at all — this
    module has no concept of payment, it only counts. Wiring that
    check is a Stripe-integration task, out of scope here; until it
    exists, this function alone would incorrectly cap paying users
    too, so do not wire this into _run_render without the subscription
    check landing in the same change.

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
            .select("count")
            .eq("device_id", device_id)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        current = rows[0]["count"] if rows else 0

        if current >= limit:
            log.info("lifetime_cap_reached", used=current, limit=limit)
            return False, current, limit

        new_count = current + 1
        client.table(_TABLE).upsert({"device_id": device_id, "count": new_count}).execute()
        return True, new_count, limit
    except Exception:
        log.error("lifetime_cap_check_unavailable", reason="supabase_error", exc_info=True)
        return True, 0, limit


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
