"""
render_cap.py — a soft daily ceiling on paid renders, product-wide.

Why this exists: pre-revenue, a live public link with no usage limit
means a stress test, a bot, or just an unexpectedly popular share can
turn into an open-ended Anthropic bill with no one deciding that was
okay. This is the backstop for that specific failure mode. It is not
a quality feature and not a per-user fairness mechanism - it is a
single global counter with one job: once MAX_RENDERS_PER_DAY paid
renders have happened today, stop calling the API and tell the next
person to come back tomorrow.

FAILS OPEN, same as everything else in the persistence layer
(persistence.py, render_events.py, supabase_client.py). An earlier
version of this module deliberately failed closed instead - blocking
a render whenever the cap couldn't be checked - on the reasoning that
a spend guard which fails open defeats its own purpose. That reasoning
sounded right in isolation but was wrong in practice: it means any
environment without Supabase configured (a fresh deploy before the
render_cap table exists, a local/dev/test run, a genuine Supabase
outage) blocks EVERY render, not just renders over the cap. Confirmed
by the test suite - 17 unrelated tests broke the moment this shipped,
because none of them configure Supabase and none of them expect a
render to be blocked. Taking the whole product offline over a spend
guard that hasn't been provisioned yet is a worse failure mode than
the one this exists to prevent. If Supabase is unreachable, allow the
render and log loudly - the cap protects the steady-state case (real
usage, real traffic) which is exactly when Supabase is expected to be
up; it does not need to also hold during the rarer case of Supabase
itself being down.

SCHEMA (create once in Supabase's SQL editor):

    create table render_cap (
        day date primary key,
        count integer not null default 0
    );

No device_id, no person, no text - same minimal-data philosophy as
render_events.py, for the same reason: this exists to answer "how
many renders happened today", not to profile anyone.

CONCURRENCY NOTE: this reads the current count, then writes count+1.
Two renders landing in the same instant could both read the same
count and both proceed, undercounting by one in the rare case of a
true simultaneous race. Acceptable for a soft daily ceiling meant to
catch sustained overuse, not to enforce an exact billing boundary -
if it ever needs to be exact, replace with a Postgres RPC that does
the increment atomically server-side.
"""

import os
from datetime import datetime, timezone

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "render_cap"

# Generous default chosen to comfortably cover real testing/demo use
# without being effectively unlimited. Override via env var per
# environment (e.g. lower for a public demo link, higher once real
# paying usage needs headroom).
_DEFAULT_MAX_RENDERS_PER_DAY = 40


def _max_renders_per_day() -> int:
    raw = os.environ.get("MAX_RENDERS_PER_DAY")
    if not raw:
        return _DEFAULT_MAX_RENDERS_PER_DAY
    try:
        return max(1, int(raw))
    except ValueError:
        log.error("render_cap_invalid_env_value", value=raw)
        return _DEFAULT_MAX_RENDERS_PER_DAY


def _today() -> str:
    # UTC, not local - Railway's runtime clock is UTC and the cap
    # should reset at a fixed, unambiguous point regardless of where
    # a given render happens to originate from.
    return datetime.now(timezone.utc).date().isoformat()


def check_and_reserve_render() -> tuple[bool, int, int]:
    """Call this once, right before the first paid API call in a
    render - see _run_render in app.py. Returns (allowed, used, limit).

    If allowed is True, the caller may proceed; the render already
    counts toward today's total when it was possible to record it
    (reserved optimistically before the API call, not after, so a
    render that fails partway through still counts - matching the
    intent of a spend cap, which cares about calls made, not calls
    that succeeded).

    If allowed is False, the caller must not make any paid API call
    for this render - this only happens when the count was
    successfully checked AND is at or above the limit.

    Fails OPEN: no Supabase client configured, or any error talking
    to it, returns (True, 0, limit) - the render proceeds, same as
    every other module in the persistence layer. See module docstring
    for why this module doesn't fail closed despite being a spend
    guard - blocking every render over an unconfigured or unreachable
    guard is a worse outcome than the occasional uncapped render.
    """
    limit = _max_renders_per_day()
    client = get_supabase_client()
    if client is None:
        log.error("render_cap_check_unavailable", reason="supabase_not_configured")
        return True, 0, limit

    today = _today()
    try:
        existing = (
            client.table(_TABLE)
            .select("count")
            .eq("day", today)
            .limit(1)
            .execute()
        )
        rows = existing.data or []
        current = rows[0]["count"] if rows else 0

        if current >= limit:
            log.info("render_cap_reached", used=current, limit=limit, day=today)
            return False, current, limit

        new_count = current + 1
        client.table(_TABLE).upsert({"day": today, "count": new_count}).execute()
        return True, new_count, limit
    except Exception:
        # Table not created yet, network issue, timeout - fails open,
        # same as the "not configured" case above and for the same
        # reason. Logged at error level so a sustained outage is
        # visible in Railway's deploy logs, but never blocks a render.
        log.error("render_cap_check_unavailable", reason="supabase_error", exc_info=True)
        return True, 0, limit
