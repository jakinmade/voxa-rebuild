"""
tests/api/test_tokens.py — focused unit coverage of api/auth/tokens.py.

Patches installations_db.create_installation directly on the already-
imported tokens module (the standard, reliable way to stub a
dependency in pytest) rather than juggling sys.modules and reloads,
which don't reliably override an attribute another test may have
already caused Python to cache on the api.db package.

Covers the specific JWT security properties this session's "established
industry patterns" correction was about: explicit algorithm pinning
defeats algorithm-confusion attacks, expiry is enforced, tampered
signatures are rejected.
"""
from __future__ import annotations

import os
import time

import jwt
import pytest

from api.auth import tokens as tokens_module


@pytest.fixture(autouse=True)
def stub_installations_db(monkeypatch):
    monkeypatch.setattr(
        tokens_module.installations_db,
        "create_installation",
        lambda profile_id, refresh_handle_hash: {
            "installation_id": "inst-123", "profile_id": profile_id,
        },
    )


def test_issue_and_verify_round_trip():
    result = tokens_module.issue_installation("device-abc")
    claims = tokens_module.verify_access_token(result["access_token"])
    assert claims["installation_id"] == "inst-123"


def test_expired_token_raises_token_expired():
    expired = jwt.encode(
        {"sub": "x", "iat": int(time.time()) - 100, "exp": int(time.time()) - 10},
        os.environ["TOKEN_SIGNING_SECRET"],
        algorithm="HS256",
    )
    with pytest.raises(tokens_module.TokenExpired):
        tokens_module.verify_access_token(expired)


def test_malformed_token_raises_token_invalid():
    with pytest.raises(tokens_module.TokenInvalid):
        tokens_module.verify_access_token("garbage.not.a.token")


def test_wrong_signing_key_raises_token_invalid():
    wrong = jwt.encode(
        {"sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 100},
        "a-completely-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(tokens_module.TokenInvalid):
        tokens_module.verify_access_token(wrong)


def test_algorithm_confusion_alg_none_is_rejected():
    """The specific vulnerability class explicit algorithm-pinning
    defends against: a token that declares its own algorithm as
    'none' must never be accepted, regardless of what it claims."""
    none_token = jwt.encode(
        {"sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 100},
        key=None, algorithm="none",
    )
    with pytest.raises(tokens_module.TokenInvalid):
        tokens_module.verify_access_token(none_token)
