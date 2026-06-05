"""
Voxa — API Middleware
Rate limiting, auth, and request guards.

Rate limiting backend selected by VOXA_RATE_LIMIT_BACKEND:
  memory  — process-local (default, development)
  redis   — Redis-backed (production, multi-instance safe)

Redis config:
  VOXA_REDIS_URL — default: redis://localhost:6379

Per-user limits enforced using sliding window algorithm.
Multiple instances share the same Redis counters — no bypass by hitting different instances.

Routes protected:
  /render    — LLM call, most expensive
  /humanise  — profile mutation
  /calibrate — profile mutation

Configuration:
  VOXA_RATE_LIMIT_RENDER    default: 60  per minute per user
  VOXA_RATE_LIMIT_HUMANISE  default: 20  per minute per user
  VOXA_RATE_LIMIT_CALIBRATE default: 120 per minute per user
  VOXA_API_KEY_REQUIRED     default: false
  VOXA_API_KEYS             comma-separated valid keys
"""

from __future__ import annotations

import os
import time
from collections import defaultdict

import structlog
from fastapi import HTTPException, status

logger = structlog.get_logger(__name__)

RATE_LIMITS: dict[str, int] = {
    "/render":    int(os.environ.get("VOXA_RATE_LIMIT_RENDER", 60)),
    "/humanise":  int(os.environ.get("VOXA_RATE_LIMIT_HUMANISE", 20)),
    "/calibrate": int(os.environ.get("VOXA_RATE_LIMIT_CALIBRATE", 120)),
}
WINDOW_SECONDS = 60
RATE_BACKEND = os.environ.get("VOXA_RATE_LIMIT_BACKEND", "memory").lower()
REDIS_URL = os.environ.get("VOXA_REDIS_URL", "redis://localhost:6379")
API_KEY_REQUIRED = os.environ.get("VOXA_API_KEY_REQUIRED", "false").lower() == "true"
VALID_API_KEYS = set(filter(None, os.environ.get("VOXA_API_KEYS", "").split(",")))

# Process-local store (memory backend only)
_rate_store: dict[tuple[str, str], list[float]] = defaultdict(list)

# Redis client (lazy init)
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def _check_redis_rate_limit(user_id: str, path: str, limit: int) -> None:
    """
    Sliding window rate limit using Redis sorted sets.
    Multi-instance safe — all instances share the same counters.
    Key: voxa:rl:{user_id}:{path}
    """
    r = _get_redis()
    now = time.time()
    window_start = now - WINDOW_SECONDS
    key = f"voxa:rl:{user_id}:{path}"

    pipe = r.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)     # Remove expired entries
    pipe.zcard(key)                                  # Count in window
    pipe.zadd(key, {str(now): now})                  # Add current request
    pipe.expire(key, WINDOW_SECONDS * 2)             # TTL cleanup
    results = pipe.execute()

    count_before = results[1]
    if count_before >= limit:
        logger.warning("rate_limit_exceeded_redis", user_id=user_id, path=path, limit=limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s on {path}",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )


def _check_memory_rate_limit(user_id: str, path: str, limit: int) -> None:
    """
    Process-local sliding window. Development only.
    Not multi-instance safe.
    """
    now = time.time()
    window_start = now - WINDOW_SECONDS
    key = (user_id, path)
    _rate_store[key] = [t for t in _rate_store[key] if t > window_start]

    if len(_rate_store[key]) >= limit:
        logger.warning("rate_limit_exceeded_memory", user_id=user_id, path=path, limit=limit)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded: {limit} requests per {WINDOW_SECONDS}s on {path}",
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
    _rate_store[key].append(now)


def _get_user_id(request) -> str:
    return request.headers.get("X-Voxa-User-Id") or getattr(request.client, "host", None) or "anonymous"


def check_rate_limit(request) -> None:
    path = request.url.path
    limit = RATE_LIMITS.get(path)
    if limit is None:
        return

    user_id = _get_user_id(request)

    if RATE_BACKEND == "redis":
        try:
            _check_redis_rate_limit(user_id, path, limit)
        except ImportError:
            logger.warning("redis_not_installed_falling_back_to_memory")
            _check_memory_rate_limit(user_id, path, limit)
        except Exception as e:
            logger.warning("redis_rate_limit_error_falling_back", error=str(e))
            _check_memory_rate_limit(user_id, path, limit)
    else:
        _check_memory_rate_limit(user_id, path, limit)


def check_api_key(request) -> None:
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
