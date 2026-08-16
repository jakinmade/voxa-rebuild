"""
render_events.py — the evidence trail scoring_rules.py's changelog
promised.

v1.1.0's compute_risk_reason() (voice_engine.py) identifies WHICH
check drove a render's risk verdict, but a stdout log line alone
(Railway's deploy-log viewer, see logging_config.py) can't answer an
aggregate question like "of the last 200 renders, how many hit High
risk via aggregate_band alone, and at what semantic_match did that
band actually fire" - that needs rows in a table you can run SQL
against, not JSON lines you can only grep.

This is that table. Deliberately minimal and deliberately NOT linked
to a device or a person: no device_id, no text content, nothing that
could re-identify a render or the person behind it - this exists
purely to answer a calibration question about the SCORING SYSTEM,
not to profile usage. Reuses supabase_client.py's fail-open contract
exactly like persistence.py: if Supabase isn't configured or is
unreachable, log_render_event() logs the failure and returns, it
never raises and never blocks a render on this.

SCHEMA (create once in Supabase's SQL editor before this starts
recording anything — this module fails open and silently records
nothing if the table doesn't exist yet):

    create table render_events (
        id uuid primary key default gen_random_uuid(),
        created_at timestamptz not null default now(),
        risk text not null,
        risk_reason text not null,
        semantic_match integer,
        missed_dimensions integer not null default 0,
        ai_tells_clean boolean,
        is_refinement boolean not null default false,
        scoring_rules_version text not null
    );

USING THE DATA ONCE IT ACCUMULATES
The question this exists to answer, as SQL, once there's real volume:

    select semantic_match, missed_dimensions
    from render_events
    where risk_reason = 'aggregate_band'
    order by created_at desc;

If that query comes back empty after real usage, the aggregate bands
(RISK_HIGH/MEDIUM_SEMANTIC_MATCH_BELOW in scoring_rules.py) are never
actually the deciding factor in practice — the hard-fails are doing
all the work, same as every render checked in the 16 Aug 2026
session — and that itself is a finding worth a changelog entry. If it
comes back with real rows, the semantic_match values there are the
actual evidence to recalibrate against, not a guess.
"""

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "render_events"


def log_render_event(
    risk: str,
    risk_reason: str,
    semantic_match: int | None,
    missed_dimensions: int,
    ai_tells_clean: bool | None,
    is_refinement: bool,
    scoring_rules_version: str,
) -> None:
    """Fire-and-forget. Call once per completed render, right after
    compute_risk/compute_risk_reason - see render_complete in
    app.py's _run_render. Never raises, never returns a value the
    caller needs to check; a failed write here must never be visible
    to the person waiting on their render, same fail-open contract as
    persistence.py's save_profile_if_available()."""
    client = get_supabase_client()
    if client is None:
        return

    payload = {
        "risk": risk,
        "risk_reason": risk_reason,
        "semantic_match": semantic_match,
        "missed_dimensions": missed_dimensions,
        "ai_tells_clean": ai_tells_clean,
        "is_refinement": is_refinement,
        "scoring_rules_version": scoring_rules_version,
    }

    try:
        client.table(_TABLE).insert(payload).execute()
    except Exception:
        # Fails open and silent by design - table not created yet is
        # the expected state until the SQL above is run once. Logged
        # at error level so it's visible in Railway's deploy logs if
        # someone goes looking, but never surfaced to the person.
        log.error("render_event_log_failed", exc_info=True)
