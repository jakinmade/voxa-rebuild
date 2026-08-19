"""
render_history.py — persists each render for the History screen
(VOICOVA_Product_2.0_Consolidated.docx Section 9.4). Backend gap
identified in Section 8: render_cap.py's Supabase table only stores a
daily count, no render text, timestamps, or per-render results are
persisted anywhere today. This is that missing write path.

Distinct from render_events.py by design: that table is deliberately
anonymous (no device_id, no text) because it exists to calibrate the
SCORING SYSTEM in aggregate. This table is deliberately tied to a
device_id and DOES carry render text, because its entire purpose is
letting one person reopen their own past renders — same identity
model persistence.py already uses for voice profiles, no new consent
surface needed since it's the same device cookie already covering
onboarding persistence.

Retention: capped at the last 50 renders per device (Section 9.4
decision, 19 Aug 2026) rather than kept indefinitely or time-expired.
Enforced here, on write, not by a separate cleanup job — each write
checks the count and trims the oldest rows past 50 for that device_id,
so the table never needs an external cron/scheduled task to stay
bounded.

FAILS OPEN, same as every other module in the persistence layer: a
render must never be blocked or fail because History couldn't be
written. Called AFTER a render successfully completes, never before -
see the wiring note in write_render_history's docstring.

SCHEMA (create once in Supabase's SQL editor):

    create table render_history (
        id uuid primary key default gen_random_uuid(),
        device_id uuid not null,
        created_at timestamptz not null default now(),
        context text,
        mode text not null,
        input_text text not null,
        output_text text not null,
        voice_match text,
        content_lock_pass boolean
    );

    create index render_history_device_id_created_at_idx
        on render_history (device_id, created_at desc);

DEPLOYED 19 Aug 2026 to the live Supabase project (txpsphethknujgqvqdzl)
via Supabase MCP, matching the schema above exactly. device_id is
uuid, not text - matches voice_profiles.device_id's actual column
type, checked against the live schema before creating this table.
"""

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "render_history"
_RETENTION_LIMIT = 50


def write_render_history(
    device_id: str,
    input_text: str,
    output_text: str,
    context: str = "",
    mode: str = "preserve",
    voice_match: str | None = None,
    content_lock_pass: bool | None = None,
) -> None:
    """Call once, after a render has fully succeeded — see the
    end of _run_render in app.py, after report/delta are computed
    and before returning True. Never call this before a render is
    known to have succeeded; a failed or in-progress render has
    nothing worth reopening later.

    Fails open and silently: any error here must never surface to the
    person or interrupt the render flow that already completed. This
    function has no return value and raises nothing — callers should
    not wrap this in error handling of their own, it already handles
    its own.
    """
    client = get_supabase_client()
    if client is None:
        log.error("render_history_write_unavailable", reason="supabase_not_configured")
        return

    try:
        client.table(_TABLE).insert({
            "device_id": device_id,
            "context": context,
            "mode": mode,
            "input_text": input_text,
            "output_text": output_text,
            "voice_match": voice_match,
            "content_lock_pass": content_lock_pass,
        }).execute()
        _trim_to_retention_limit(client, device_id)
    except Exception:
        log.error("render_history_write_failed", reason="supabase_error", exc_info=True)


def _trim_to_retention_limit(client, device_id: str) -> None:
    """Keeps only the most recent _RETENTION_LIMIT rows for this
    device_id. Runs on every write rather than a scheduled job, so
    the table is self-bounding with no external cron dependency.
    Failure here is swallowed by the caller's own try/except — this
    is a housekeeping step, not part of the write's success criteria."""
    existing = (
        client.table(_TABLE)
        .select("id")
        .eq("device_id", device_id)
        .order("created_at", desc=True)
        .execute()
    )
    rows = existing.data or []
    if len(rows) <= _RETENTION_LIMIT:
        return

    stale_ids = [row["id"] for row in rows[_RETENTION_LIMIT:]]
    for stale_id in stale_ids:
        client.table(_TABLE).delete().eq("id", stale_id).execute()


def get_render_history(device_id: str, limit: int = _RETENTION_LIMIT) -> list[dict]:
    """Reads back this device's render history, most recent first, for
    the History screen (Section 9.4). Fails open to an empty list on
    any error — an empty History screen is a fine degraded state, a
    crashed one is not."""
    client = get_supabase_client()
    if client is None:
        return []

    try:
        result = (
            client.table(_TABLE)
            .select("*")
            .eq("device_id", device_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []
    except Exception:
        log.error("render_history_read_failed", reason="supabase_error", exc_info=True)
        return []
