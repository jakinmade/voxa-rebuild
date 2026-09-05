"""
tests/api/test_profile_recovery.py — route-level coverage of the
profile-recovery flow: registering a recovery email, initiating
recovery, and completing it via the emailed token.

SendGrid is never actually called in these tests — EMAIL_ENABLED
defaults to "true" but no SENDGRID_API_KEY is set in the test
environment, so _send_recovery_email logs and returns early (same
fail-open path production hits if the key is ever missing). Tests
that need the actual token reach into the fake store directly
(tests.api.conftest._fake_recovery_requests) rather than parsing an
email that was never sent.
"""
from __future__ import annotations


def _linked_token(client) -> str:
    return client.post(
        "/api/extension/link", json={"device_identity": "device-abc"}
    ).json()["access_token"]


def test_register_recovery_email_requires_a_token(client):
    r = client.post("/api/profile/recovery-email", json={"email": "person@example.com"})
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "token_revoked"


def test_register_recovery_email_rejects_malformed_email(client):
    access_token = _linked_token(client)
    r = client.post(
        "/api/profile/recovery-email",
        json={"email": "not-an-email"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 422


def test_register_and_recover_end_to_end(client):
    access_token = _linked_token(client)
    r = client.post(
        "/api/profile/recovery-email",
        json={"email": "person@example.com"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200
    assert r.json() == {"registered": True}

    r = client.post("/api/profile/recover", json={"email": "person@example.com"})
    assert r.status_code == 200
    assert r.json()["request_id"]

    from tests.api.conftest import _fake_recovery_requests
    assert len(_fake_recovery_requests) == 1
    old_hash, row = next(iter(_fake_recovery_requests.items()))
    assert row["profile_id"] == "device-abc"
    assert row["used_at"] is None

    # The raw token is never persisted (only its hash) and never
    # returned in any response (Section 11.6: "no token or profile
    # data is returned directly in this response"), so this test
    # swaps a known raw value's hash into the fake store in place of
    # the real (unrecoverable) one — re-keying the dict itself, not
    # just the row's own token_hash field, since the store is keyed
    # by hash.
    import api.routes.profile_recovery as recovery_route
    raw_token = "test-raw-token-value"
    new_hash = recovery_route._hash_token(raw_token)
    row["token_hash"] = new_hash
    del _fake_recovery_requests[old_hash]
    _fake_recovery_requests[new_hash] = row

    r = client.get(f"/api/profile/recover?token={raw_token}")
    assert r.status_code == 200
    body = r.json()
    for field in ("installation_id", "access_token", "refresh_handle"):
        assert field in body and body[field]

    # Single-use: the same token must not work twice.
    r = client.get(f"/api/profile/recover?token={raw_token}")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "recovery_token_invalid"


def test_recover_initiate_with_unknown_email_reports_the_same_shape(client):
    # Non-enumeration (Section 2.5): a bare, never-registered email
    # must return the same 200 + request_id shape as a real match,
    # not a 404 or an empty body that would leak the mismatch.
    r = client.post("/api/profile/recover", json={"email": "nobody@example.com"})
    assert r.status_code == 200
    assert r.json()["request_id"]

    from tests.api.conftest import _fake_recovery_requests
    assert len(_fake_recovery_requests) == 0


def test_recover_complete_rejects_unknown_token(client):
    r = client.get("/api/profile/recover?token=totally-made-up")
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "recovery_token_invalid"
