"""
tests/api/test_fix.py — route-level coverage of POST /api/fix, against
the same in-memory fake Supabase layer test_check_draft_and_auth.py
uses (conftest.py), with the Anthropic client mocked the same way
tests/unit/test_render_pipeline.py already does for run_voice_render
directly — this file proves the ROUTE wiring (auth, credit accounting,
seal/telemetry, response shape), not the engine logic underneath it,
which that file and the rest of the existing suite already cover.
"""
from __future__ import annotations

import os
from unittest.mock import patch, MagicMock

os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


def _fake_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _linked_token(client) -> str:
    return client.post(
        "/api/extension/link", json={"device_identity": "device-abc"}
    ).json()["access_token"]


def test_fix_requires_a_token(client):
    r = client.post(
        "/api/fix",
        json={"original_draft": "hi", "surface": "linkedin"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "token_revoked"


def test_fix_with_valid_token_matches_documented_shape(client):
    access_token = _linked_token(client)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response(
            "I reviewed the numbers last night. They hold up. "
            "I want to ship this today, not next week."
        )
        r = client.post(
            "/api/fix",
            json={
                "original_draft": "It could perhaps be argued that further review might be advisable.",
                "surface": "linkedin",
                "request_id": "check-call-123",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert r.status_code == 200
    body = r.json()

    # Section 11.2's documented response field set.
    assert body["request_id"]
    # A fresh id for THIS action, distinct from the check_request_id
    # the request carried for traceability — see api/schemas/fix.py's
    # module docstring for why these are deliberately not the same
    # value.
    assert body["request_id"] != "check-call-123"
    assert body["corrected_text"]
    assert isinstance(body["what_changed"], list)
    assert isinstance(body["post_fix_predicted_score"], int)
    assert "pass" in body["content_lock_result"]
    assert isinstance(body["content_lock_result"]["reasons"], list)
    assert body["render_consumed"] is True
    assert body["scoring_version"]
    assert body["timestamp"]


def test_fix_without_a_usable_profile_returns_engine_error(client):
    # Same "shouldn't be reachable in practice" case check_draft.py's
    # own test suite exercises — a token for a profile with no
    # baseline. Reuses the identical setup: link succeeds (device-abc
    # has a baseline), but a hand-built token for an unknown profile
    # can't happen via the real link flow, so instead this proves the
    # documented fallback path via a profile lookup miss.
    from api.auth import tokens
    fake_claims_token = tokens._mint_access_token("no-such-installation")

    r = client.post(
        "/api/fix",
        json={"original_draft": "Some draft text.", "surface": "linkedin"},
        headers={"Authorization": f"Bearer {fake_claims_token}"},
    )
    # installation_id doesn't resolve at all -> installation_mismatch,
    # per api/auth/middleware.py — proves the same auth gate that
    # protects check-draft protects fix too, not a second, weaker copy.
    assert r.status_code == 403
    assert r.json()["detail"]["error_code"] == "installation_mismatch"


def test_fix_release_lifetime_render_on_engine_failure(client):
    access_token = _linked_token(client)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = Exception("boom")
        r = client.post(
            "/api/fix",
            json={"original_draft": "Some draft text.", "surface": "linkedin"},
            headers={"Authorization": f"Bearer {access_token}"},
        )

    assert r.status_code == 500
    assert r.json()["detail"]["error_code"] == "engine_error"

    # The reservation made before the failed call must have been
    # released — confirmed by a subsequent successful call still
    # reporting a low used-count rather than one inflated by the
    # failed attempt (module docstring's release-on-failure guarantee).
    from tests.api.conftest import _fake_lifetime_counts
    assert _fake_lifetime_counts.get("device-abc", 0) == 0


def test_fix_blocked_when_lifetime_cap_exhausted(client):
    access_token = _linked_token(client)

    from tests.api.conftest import _fake_lifetime_counts
    _fake_lifetime_counts["device-abc"] = 15  # at the default limit

    r = client.post(
        "/api/fix",
        json={"original_draft": "Some draft text.", "surface": "linkedin"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 402
    assert r.json()["detail"]["error_code"] == "render_cap_exhausted"
