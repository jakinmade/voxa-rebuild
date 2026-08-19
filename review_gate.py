"""
review_gate.py — turns a genuine content-integrity failure (not style
drift) into something a person has to actively confirm before they
get the rewritten text.

WHY THIS EXISTS
The voice report (risk/confidence/semantic_match badges) has always
been informational: it sits above the output text_area, but nothing
stops a person from ignoring it and copying the text anyway. FINRA's
own guidance on AI-assisted communications (Rule 3110/2210/4511)
identifies exactly this gap as the most common small-firm compliance
failure - not that firms use AI to draft, but that they can't show a
documented human actually reviewed the output before it went out.
This module is the structural fix: when voice_engine.
has_content_integrity_hard_fail() is True — a surviving AI tell, an
attribution swap, a dropped fact, or an invented sentence — the output
stays hidden behind an explicit confirmation step, not just a badge
someone can scroll past.

WHAT CHANGED (19 Aug 2026) — narrowed from risk-level gating to
hard-fail-only gating. Previously this gated on scoring_rules.
REVIEW_REQUIRED_RISK_LEVELS = {"Medium", "High"}, and Risk went
Medium the moment a render missed even ONE of four tracked style
dimensions (RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST = 1). Real renders
miss at least one of four style targets on nearly every render, so
the gate was firing almost constantly on renders with zero actual
content problems — a genuine, high-consequence attribution swap and a
merely-imperfect-style render got the identical checkbox wall. JA (19
Aug 2026): "user friction is front and centre of VOICOVA, nothing
should contribute to friction." Style drift alone — however severe —
no longer gates anything; only the four documented content-integrity
hard fails do. The FINRA rationale above still applies to exactly
those cases, arguably more precisely than before: reviewing for a
genuine factual/attribution error is what the guidance is actually
about, not reviewing for a slightly-under-owned first-person clause.

WHAT THIS IS NOT
Not a per-person compliance record. log_review_confirmation() below
follows the exact same anonymous, fail-open contract as
render_events.log_render_event() - no device_id, no identity, nothing
that ties a confirmation to a person or a firm. It records that a
gated render was reviewed and confirmed, with the same risk/
semantic_match/scoring_rules_version shape already logged elsewhere,
so the aggregate question ("what fraction of gated renders get
confirmed vs abandoned") is answerable without collecting anything
this product has explicitly chosen not to collect (see persistence.py's
own docstring: "no accounts, no email"). A real supervisor-facing
audit trail - one a compliance officer could review by advisor name -
needs an identity model this product doesn't have. That's a
deliberate, separate decision for later, not something this module
backs into by accident.

SCHEMA (create once in Supabase's SQL editor, same as render_events.py
- this module fails open and silently records nothing if the table
doesn't exist yet):

    create table review_confirmations (
        id uuid primary key default gen_random_uuid(),
        created_at timestamptz not null default now(),
        risk text not null,
        risk_reason text not null,
        semantic_match integer,
        scoring_rules_version text not null
    );
"""

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "review_confirmations"


def requires_review(hard_fail: bool | None) -> bool:
    """True if this render must be confirmed before the rewritten
    text is shown. hard_fail=None (no report computed yet) never
    requires review - there's nothing to have failed against, so
    gating would just be friction with no signal behind it. Style
    drift, however severe, is NOT a reason to gate - see this
    module's docstring and voice_engine.has_content_integrity_hard_fail
    for what does."""
    return bool(hard_fail)


def log_review_confirmation(
    risk: str,
    risk_reason: str,
    semantic_match: int | None,
    scoring_rules_version: str,
) -> None:
    """Fire-and-forget, same fail-open contract as
    render_events.log_render_event() - never raises, never returns
    anything the caller needs to check. Call this once, at the moment
    the person confirms they've reviewed a gated render, not before."""
    client = get_supabase_client()
    if client is None:
        return

    payload = {
        "risk": risk,
        "risk_reason": risk_reason,
        "semantic_match": semantic_match,
        "scoring_rules_version": scoring_rules_version,
    }

    try:
        client.table(_TABLE).insert(payload).execute()
    except Exception:
        # Fails open and silent by design - table not created yet is
        # the expected state until the SQL above is run once. Logged
        # at error level so it's visible in Railway's deploy logs, but
        # never surfaced to the person, and never blocks the render
        # they already confirmed.
        log.error("review_confirmation_log_failed", exc_info=True)
