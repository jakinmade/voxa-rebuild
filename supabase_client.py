"""
supabase_client.py — one shared Supabase client factory.

Extracted from persistence.py's previously-private _get_supabase_client
so a second module (render_events.py) doesn't have to either duplicate
the connection logic or reach into persistence.py's internals to get
it. Same fail-open contract as before: never raises, None means
"not configured or unreachable", every caller treats that as
"proceed without persistence" rather than blocking on it.
"""

import os

from logging_config import get_logger

log = get_logger(__name__)


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
        return create_client(url, key)
    except Exception:
        log.error("supabase_client_init_failed", exc_info=True)
        return None
