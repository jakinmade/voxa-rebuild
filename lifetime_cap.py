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

ATOMICITY (23 Aug 2026, Phase 2 of the hardening build order): the
check-and-reserve path now calls a Postgres RPC
(reserve_lifetime_render, see migrations/2026_08_23_atomic_lifetime_
render_cap.sql) that does the read-check-increment as a single
UPDATE ... WHERE count < limit ... RETURNING statement inside the
database, rather than reading `count` in Python and writing `count+1`
back in a second call. That two-step version had a real race: two
concurrent renders both reading count=14 could both pass the check and
both write 15, letting a device exceed its cap under concurrency. A
single UPDATE statement is atomic in Postgres — concurrent callers for
the same device_id serialize on that row's lock — which closes the
race entirely rather than just narrowing it.

FAILS CLOSED (changed 23 Aug 2026, Phase 2): unlike render_cap.py
(a soft, product-wide spend guard where fail-open is the deliberate,
tested choice — see that module's own docstring), this module is the
actual free-tier ENTITLEMENT boundary: what stands between "free" and
"paid". If Supabase is unreachable, check_and_reserve_lifetime_render
now DENIES the render rather than letting it through — a spend guard
failing open just means a few extra renders happen during a rare
outage, but an entitlement gate failing open means the free cap is
silently unenforceable for as long as the outage lasts, which is a
materially different and worse failure for a paid product. This
applies ONLY to the enforcement function below. The read-only helpers
(get_lifetime_render_count, device_has_active_subscription) remain
fail-open, same as before — they only affect what the UI displays
(e.g. "8 of 15 used"), never whether a render is allowed, so there is
no entitlement risk in letting them degrade gracefully during an
outage.
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
    counts toward this device's lifetime total (reserved atomically,
    server-side, before the API call — see the module docstring's
    ATOMICITY section) - UNLESS the device has an active subscription,
    in which case nothing is counted or checked at all.

    If allowed is False, either the device has used all free renders
    and has not yet paid, OR the reservation could not be verified at
    all (Supabase unreachable/unconfigured) — see FAILS CLOSED in the
    module docstring for why this function, unlike render_cap.py,
    denies rather than allows when it can't check.
    """
    limit = _max_lifetime_renders()
    print(f"DIAG check_and_reserve_lifetime_render: device_id={device_id} limit={limit}", flush=True)
    client = get_supabase_client()
    if client is None:
        print("DIAG check_and_reserve_lifetime_render: client is None, failing closed", flush=True)
        log.error("lifetime_cap_check_unavailable", reason="supabase_not_configured")
        return False, 0, limit

    try:
        result = client.rpc(
            "reserve_lifetime_render",
            {"p_device_id": device_id, "p_limit": limit},
        ).execute()
        rows = result.data or []
        print(f"DIAG check_and_reserve_lifetime_render: rpc returned rows={rows}", flush=True)
        if not rows:
            log.error("lifetime_cap_check_unavailable", reason="rpc_returned_no_rows")
            return False, 0, limit

        row = rows[0]
        allowed = bool(row["allowed"])
        used = row["used_count"]
        if not allowed:
            log.info("lifetime_cap_reached", used=used, limit=limit)
        return allowed, used, limit
    except Exception as e:
        print(f"DIAG check_and_reserve_lifetime_render: EXCEPTION {type(e).__name__}: {e}", flush=True)
        log.error("lifetime_cap_check_unavailable", reason="supabase_error", exc_info=True)
        return False, 0, limit


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
    on any error, same as before — this is a display helper, not the
    entitlement check itself, so it keeps the old fail-open contract
    (see module docstring's FAILS CLOSED section for why the actual
    enforcement function above no longer does)."""
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

    Calls the atomic release_lifetime_render RPC (see this module's
    ATOMICITY docstring section and the migration file) rather than a
    separate read-then-write, for the same reason the reservation path
    is atomic — though the consequence of losing this particular race
    is minor (an extra free render slips through), not a correctness
    issue worth leaving unfixed given the RPC already exists.

    Fails open and silently on any error, same contract as every
    other write in this module - a failed release just means one
    render costs the user a slot it shouldn't have, an honest minor
    degradation, not a reason to raise into the render's own error
    handling and mask the real failure that triggered the release."""
    client = get_supabase_client()
    if client is None:
        return
    try:
        client.rpc("release_lifetime_render", {"p_device_id": device_id}).execute()
    except Exception:
        log.error("lifetime_cap_release_failed", reason="supabase_error", exc_info=True)
