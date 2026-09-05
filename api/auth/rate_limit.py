"""
api/auth/rate_limit.py — in-memory sliding-window rate limiting
(Section 4.7).

Resolved in the Engineering Architecture doc: in-memory, not Redis —
neither Railway service in this project has a Redis instance, and
adding one is a new paid dependency this pilot doesn't need yet.
Correct ONLY as long as the FastAPI service runs as a single Railway
replica (Section 4.1 deploy config must pin replicas: 1) — an
in-memory counter silently becomes N times too permissive the moment a
second replica exists, since each replica keeps its own counts with no
shared state between them.

Independent of the 15-render lifetime cap (render_cap.py /
lifetime_cap.py): this defends against a runaway content-script bug or
abuse pattern hitting the API repeatedly, which a lifetime cap alone
wouldn't catch quickly.

Written behind check()/increment() rather than exposing the dict
directly, so swapping to Redis later — if/when the service scales
beyond one replica — is a contained change to this one module.
"""
from __future__ import annotations

import time
import threading

from logging_config import get_logger

log = get_logger(__name__)

_WINDOW_SECONDS = 60
_MAX_REQUESTS_PER_WINDOW = 30

_lock = threading.Lock()
# installation_id -> list of request timestamps within the current window.
# Pruned lazily on each check() call, not by a background sweep — fine
# at pilot volume, and keeps this module free of any startup/shutdown
# lifecycle to wire into main.py.
_requests: dict[str, list[float]] = {}


def check(key: str) -> bool:
    """Returns True if this key is under the limit (and records this
    request), False if it should be rejected with 429 rate_limited
    (Section 4.6). Call this once per request, not check() then a
    separate increment() — combining them avoids a TOCTOU gap between
    the two under this module's own lock."""
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    with _lock:
        timestamps = [t for t in _requests.get(key, []) if t > cutoff]
        if len(timestamps) >= _MAX_REQUESTS_PER_WINDOW:
            _requests[key] = timestamps
            return False
        timestamps.append(now)
        _requests[key] = timestamps
        return True


def enforce(key: str) -> None:
    """Route-layer convenience: raises the Section 4.6 429 shape
    directly rather than making every route re-check the bool."""
    from fastapi import HTTPException
    if not check(key):
        log.info("rate_limited", key=key)
        raise HTTPException(status_code=429, detail={"error_code": "rate_limited"})
