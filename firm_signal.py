"""
firm_signal.py — opt-in, domain-only signal for spotting when several
people at the same firm are already using VOICOVA independently.

WHY THIS EXISTS AND WHY IT'S SEPARATE FROM review_gate.py
review_gate.py and render_events.py are both deliberately anonymous -
no identity, by design, and this file doesn't touch that. This is new,
additive surface area: a person who has just confirmed a Medium/High
risk review gate (see review_gate.py) can optionally offer their work
email so VOICOVA can tell, in aggregate, when a firm has enough
independent users that a team/compliance conversation would actually
be useful to them. The offer is opt-in and appears after the gate
confirmation, not at signup - asking before someone has demonstrated
they're a professional who cares about this is just friction with a
worse conversion rate.

WHAT ACTUALLY GETS STORED: the domain, and only the domain. The email
address itself is parsed in extract_domain() and never passed to
log_firm_signal() or persisted anywhere - the local part (everything
before the @) is discarded the moment the domain is extracted, in the
same function call, before this module's public logging function ever
sees it. If a future version of this file's tests can find a code path
where a full email reaches log_firm_signal(), that's a bug in this
module, not a variant of intended behaviour.

PERSONAL EMAIL DOMAINS ARE EXCLUDED. Multiple people signing up with
@gmail.com isn't a firm signal - it's noise, and storing it would
imply a company relationship that doesn't exist. See
scoring_rules.PERSONAL_EMAIL_DOMAINS for the exclusion list and how to
extend it.

SCHEMA (create once in Supabase's SQL editor, same pattern as
render_events.py and review_gate.py - fails open and silently records
nothing if the table doesn't exist yet):

    create table firm_signals (
        id uuid primary key default gen_random_uuid(),
        created_at timestamptz not null default now(),
        domain text not null,
        risk text not null,
        risk_reason text not null,
        scoring_rules_version text not null
    );

USING THE DATA ONCE IT ACCUMULATES
    select domain, count(*) as confirmed_users
    from firm_signals
    group by domain
    having count(*) >= 3
    order by confirmed_users desc;

That query is the entire "enterprise lead" detector - domains showing
up repeatedly are firms where VOICOVA is already in independent use.
"""

import re

from logging_config import get_logger
from scoring_rules import PERSONAL_EMAIL_DOMAINS
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "firm_signals"

# Deliberately conservative shape check, not full RFC 5322 validation -
# this only needs to reject obvious junk (no @, no dot after the @)
# well enough that extract_domain() doesn't hand back garbage. A
# stricter validator would reject some real addresses; a looser one
# would let more junk through. This errs toward rejecting anything
# ambiguous, since a missed opt-in costs nothing (the person can just
# try again) but a bad domain stored is a small, permanent data-
# quality problem in a table meant to answer a specific question.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def extract_domain(email: str) -> str | None:
    """
    Takes a full email address, returns the lowercased domain, and
    nothing else - the local part is never returned, logged, or passed
    anywhere by this function. Returns None (not the domain) for:
    empty/whitespace-only input, input that doesn't look like an email,
    and personal-email-provider domains (scoring_rules.
    PERSONAL_EMAIL_DOMAINS) - all three cases mean "no usable firm
    signal here," and the caller should treat None as "don't offer to
    log anything," not as an error to surface to the person.
    """
    if not email or not email.strip():
        return None
    candidate = email.strip()
    if not _EMAIL_SHAPE.match(candidate):
        return None
    domain = candidate.rsplit("@", 1)[1].lower()
    if domain in PERSONAL_EMAIL_DOMAINS:
        return None
    return domain


def log_firm_signal(
    domain: str,
    risk: str,
    risk_reason: str,
    scoring_rules_version: str,
) -> None:
    """Fire-and-forget, same fail-open contract as render_events.py and
    review_gate.py - never raises, never returns anything the caller
    needs to check. Call this only with a domain already returned by
    extract_domain() - this function does not itself validate or
    re-derive a domain from an email, by design, so a full email
    accidentally passed in here would be stored as-is. Keeping domain
    extraction and logging as two separate functions, rather than one
    function that takes an email, makes that failure mode visible at
    every call site instead of hidden inside a single "just pass the
    email" helper."""
    client = get_supabase_client()
    if client is None:
        return

    payload = {
        "domain": domain,
        "risk": risk,
        "risk_reason": risk_reason,
        "scoring_rules_version": scoring_rules_version,
    }

    try:
        client.table(_TABLE).insert(payload).execute()
    except Exception:
        # Fails open and silent by design - table not created yet is
        # the expected state until the SQL above is run once. Never
        # surfaced to the person, never blocks anything they were
        # already doing.
        log.error("firm_signal_log_failed", exc_info=True)
