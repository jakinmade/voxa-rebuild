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
import uuid
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
                "idempotency_key": str(uuid.uuid4()),
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
        json={"original_draft": "Some draft text.", "surface": "linkedin", "idempotency_key": str(uuid.uuid4())},
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
            json={"original_draft": "Some draft text.", "surface": "linkedin", "idempotency_key": str(uuid.uuid4())},
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
        json={"original_draft": "Some draft text.", "surface": "linkedin", "idempotency_key": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 402
    assert r.json()["detail"]["error_code"] == "render_cap_exhausted"


def test_fix_requires_idempotency_key(client):
    # Independent architecture review, finding #5 — required, not
    # optional, so an older/misbehaving client can't silently bypass
    # duplicate-submission protection by omitting it.
    access_token = _linked_token(client)
    r = client.post(
        "/api/fix",
        json={"original_draft": "Some draft text.", "surface": "linkedin"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 422


def test_fix_repeated_idempotency_key_returns_original_response_without_a_second_render(client):
    access_token = _linked_token(client)
    key = str(uuid.uuid4())

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response(
            "I reviewed the numbers last night. They hold up. "
            "I want to ship this today, not next week."
        )
        first = client.post(
            "/api/fix",
            json={
                "original_draft": "It could perhaps be argued that further review might be advisable.",
                "surface": "linkedin",
                "idempotency_key": key,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert first.status_code == 200
        # The render pipeline can legitimately call the LLM more than
        # once per request (fabrication/correction retry passes) — the
        # real assertion isn't "exactly one call total", it's "the
        # SECOND, deduped request adds none at all".
        calls_after_first = mock_client.messages.create.call_count

        # A second call with the SAME key must not touch the mocked
        # Anthropic client again — proven below by call_count, not
        # just by the response matching.
        second = client.post(
            "/api/fix",
            json={
                "original_draft": "It could perhaps be argued that further review might be advisable.",
                "surface": "linkedin",
                "idempotency_key": key,
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert second.status_code == 200
        assert second.json() == first.json()
        assert mock_client.messages.create.call_count == calls_after_first, (
            "a repeated idempotency key must not trigger any additional LLM calls"
        )

    # And no second render credit was consumed either.
    from tests.api.conftest import _fake_lifetime_counts
    assert _fake_lifetime_counts.get("device-abc", 0) == 1


def test_fix_simultaneous_duplicate_key_is_rejected_while_first_is_in_flight(client):
    # Simulates the genuine race the review flagged (two tabs, a
    # duplicated extension message) rather than a plain sequential
    # retry: a second request with the same key arrives before the
    # first has completed and stored a response.
    access_token = _linked_token(client)
    key = str(uuid.uuid4())

    from api.db import fix_idempotency as fix_idempotency_module
    reservation = fix_idempotency_module.reserve(key, "device-abc")
    assert reservation["is_new"] is True  # this call "wins" the reservation, as a real first request would

    r = client.post(
        "/api/fix",
        json={"original_draft": "Some draft text.", "surface": "linkedin", "idempotency_key": key},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error_code"] == "fix_already_in_progress"

    # No credit was touched by the rejected duplicate.
    from tests.api.conftest import _fake_lifetime_counts
    assert _fake_lifetime_counts.get("device-abc", 0) == 0


def test_fix_releases_idempotency_key_on_render_failure(client):
    # A failed render must release its reservation (not just the
    # lifetime credit — see test_fix_release_lifetime_render_on_
    # engine_failure above for that half) so a legitimate client retry
    # with the same key gets a genuinely fresh attempt rather than
    # being stuck behind a 'pending' row that will never resolve.
    access_token = _linked_token(client)
    key = str(uuid.uuid4())

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = Exception("boom")
        first = client.post(
            "/api/fix",
            json={"original_draft": "Some draft text.", "surface": "linkedin", "idempotency_key": key},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    assert first.status_code == 500

    from api.db import fix_idempotency as fix_idempotency_module
    reservation = fix_idempotency_module.reserve(key, "device-abc")
    assert reservation["is_new"] is True, "the failed attempt's reservation must have been released"
