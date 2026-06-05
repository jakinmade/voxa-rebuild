"""
Voxa — API Middleware
Rate limiting, auth, and request guards.

Per-user limits enforced via in-memory counters (development).
Production: swap for Redis-backed rate limiter.

Routes protected:
  /render    — LLM call, most expensive
  /humanise  — profile mutation
  /calibrate — profile mutation

Configuration via environment variables:
  VOXA_RATE_LIMIT_RENDER    default: 60  per minute per user
  VOXA_RATE_LIMIT_HUMANISE  default: 20  per minute per user
  VOXA_RATE_LIMIT_CALIBRATE default: 120 per minute per user
  VOXA_API_KEY_REQUIRED     default: false (set to "true" for production)
"""

from __future__ import annotations

import os
import time
from collections import defaultdict
from uuid import UUID

import structlog
from fastapi import HTTPException, Request, status

logger = structlog.get_logger(__name__)

RATE_LIMITS = {
    "/render":    int(os.environ.get("VOXA_RATE_LIMIT_RENDER", 60)),
    "/humanise":  int(os.environ.get("VOXA_RATE_LIMIT_HUMANISE", 20)),
    "/calibrate": int(os.environ.get("VOXA_RATE_LIMIT_CALIBRATE", 120)),
}
API_KEY_REQUIRED = os.environ.get("VOXA_API_KEY_REQUIRED", "false").lower() == "true"
VALID_API_KEYS = set(filter(None, os.environ.get("VOXA_API_KEYS", "").split(",")))

# In-memory rate limit store — (user_id, path) -> list of timestamps
_rate_store: dict[tuple[str, str], list[float]] = defaultdict(list)
WINDOW_SECONDS = 60


def _get_user_id(request: Request) -> str:
    """Extracts user identifier from request for rate limiting."""
    # Try header first, fall back to IP
    return request.headers.get("X-Voxa-User-Id") or request.client.host or "anonymous"


def check_rate_limit(request: Request) -> None:
    """
    Enforces per-user rate limits on expensive endpoints.
    Raises HTTP 429 if limit exceeded.
    """
    path = request.url.path
    limit = RATE_LIMITS.get(path)
    if limit is None:
        return  # Endpoint not rate-limited

    user_id = _get_user_id(request)
    key = (user_id, path)
    now = time.time()
    window_start = now - WINDOW_SECONDS

    # Prune old entries
    _rate_store[key] = [t for t in _rate_store[key] if t > window_start]

    if len(_rate_store[key]) >= limit:
        logger.warning("rate_limit_exceeded", user_id=user_id, path=path, limit=limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s on {path}",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )

    _rate_store[key].append(now)


def check_api_key(request: Request) -> None:
    """
    Validates API key if VOXA_API_KEY_REQUIRED=true.
    Key passed as Authorization: Bearer <key> header.
    """
    if not API_KEY_REQUIRED:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header required: Bearer <api_key>",
        )
    key = auth[7:]
    if key not in VALID_API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )
