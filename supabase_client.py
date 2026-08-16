"""
supabase_client.py — one shared Supabase client factory.

Extracted from persistence.py's previously-private _get_supabase_client
so a second module (render_events.py) doesn't have to either duplicate
the connection logic or reach into persistence.py's internals to get
it. Same fail-open contract as before: never raises, None means
"not configured or unreachable", every caller treats that as
"proceed without persistence" rather than blocking on it.

TIMEOUT — the actual bug this fixes, found while investigating a live
report of a review-gate confirmation appearing to silently do nothing.
supabase-py's create_client() defaults postgrest_client_timeout to 120
seconds. Every write this client makes (persistence.py's profile save,
render_events.py, review_gate.py, firm_signal.py) happens synchronously
inside a Streamlit widget's click handler, sitting directly between a
person's click and the page actually updating. A 120-second default on
that path means any transient network hiccup between Railway and
Supabase can freeze the whole script mid-run for up to two minutes -
and if Railway's own proxy or the browser's connection gives up before
that (very plausible; most reverse proxies default well under 120s),
the person sees exactly the last fully-rendered page, frozen, with the
click having registered but nothing ever arriving. Not a hypothetical:
this is a real, structural gap regardless of whether it was the exact
cause of any specific report - a blocking call this deep in a click
path should never be allowed to run for two minutes. Set short and
uniform across every write this client makes; a write that hasn't
succeeded in a few seconds should fail fast and let the caller's
existing fail-open handling take over, not hold the person's browser
hostage waiting for it.
"""

import os

from logging_config import get_logger

log = get_logger(__name__)

# Deliberately short and the same for every table operation this
# client performs — see the module docstring's TIMEOUT section for
# why. 5 seconds is generous for a simple insert/select against a
# healthy Supabase instance and short enough that even a total outage
# never holds a person's click hostage for more than a few seconds.
_POSTGREST_TIMEOUT_SECONDS = 5


def get_supabase_client():
    """Returns a Supabase client, or None if not configured. Never
    raises — callers treat None as 'persistence unavailable, proceed
    without it', matching the fail-open design used throughout the
    persistence layer."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client
        from supabase.lib.client_options import ClientOptions
        return create_client(
            url, key,
            options=ClientOptions(postgrest_client_timeout=_POSTGREST_TIMEOUT_SECONDS),
        )
    except Exception:
        log.error("supabase_client_init_failed", exc_info=True)
        return None
