"""
review_gate.py — turns "the report shows Medium/High risk" from
something a person can scroll past into something they have to
actively confirm before they get the rewritten text.

WHY THIS EXISTS
The voice report (risk/confidence/semantic_match badges) has always
been informational: it sits above the output text_area, but nothing
stops a person from ignoring it and copying the text anyway. FINRA's
own guidance on AI-assisted communications (Rule 3110/2210/4511)
identifies exactly this gap as the most common small-firm compliance
failure - not that firms use AI to draft, but that they can't show a
documented human actually reviewed the output before it went out.
This module is the structural fix: for the risk levels in
scoring_rules.REVIEW_REQUIRED_RISK_LEVELS, the output stays hidden
behind an explicit confirmation step, not just a badge someone can
scroll past.

WHAT THIS IS NOT
Not a per-person compliance record. log_review_confirmation() below
follows the exact same anonymous, fail-open contract as
render_events.log_render_event() - no device_id, no identity, nothing
that ties a confirmation to a person or a firm. It records that a
gated render was reviewed and confirmed, with the same risk/
semantic_match/scoring_rules_version shape already logged elsewhere,
so the aggregate question ("what fraction of Medium/High renders get
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
from scoring_rules import REVIEW_REQUIRED_RISK_LEVELS
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "review_confirmations"


def requires_review(risk: str | None) -> bool:
    """True if this risk verdict must be confirmed before the
    rewritten text is shown. risk=None (no baseline yet, no report
    computed) never requires review - there's nothing to have missed
    against, so gating would just be friction with no signal behind
    it."""
    if risk is None:
        return False
    return risk in REVIEW_REQUIRED_RISK_LEVELS


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
