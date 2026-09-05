"""
tests/api/test_check_draft_and_auth.py — end-to-end coverage of the
identity bridge (link/refresh/disconnect) and POST /api/check-draft,
against the in-memory fake Supabase layer in conftest.py.

This is the permanent, committed version of the manual smoke test run
during this session's build — same assertions, now a real regression
guard rather than a one-off script.
"""
from __future__ import annotations


def test_health_check(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_link_rejects_unknown_device_identity(client):
    r = client.post("/api/extension/link", json={"device_identity": "no-such-device"})
    assert r.status_code == 400
    assert r.json()["detail"]["error_code"] == "invalid_device_identity"


def test_link_succeeds_for_known_device(client):
    r = client.post("/api/extension/link", json={"device_identity": "device-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["installation_id"]
    assert body["access_token"]
    assert body["refresh_handle"]


def test_check_draft_requires_a_token(client):
    r = client.post("/api/check-draft", json={"draft_text": "hi", "surface": "linkedin"})
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "token_revoked"


def test_check_draft_with_valid_token_matches_documented_shape(client):
    access_token = client.post(
        "/api/extension/link", json={"device_identity": "device-abc"}
    ).json()["access_token"]

    r = client.post(
        "/api/check-draft",
        json={
            "draft_text": "It could perhaps be argued that further review might be advisable.",
            "surface": "linkedin",
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 200
    body = r.json()

    # Section 3.5.1's documented field set, as corrected against the
    # real engine this session (see routes/check_draft.py docstring):
    # content_lock must NOT appear, verdict is the three-value form.
    assert "content_lock" not in body
    assert body["verdict"] in ("good", "borderline", "failed")
    assert set(body["dimension_scores"].keys()) == {
        "hedge_density", "sentence_length_sd", "first_person_ratio",
        "directive_ratio", "conclusion_opener_ratio", "scaffolding_density",
    }
    assert body["recommended_action"] in ("fix_available", "none")
    assert body["scoring_version"]
    assert body["timestamp"]


def test_refresh_rotates_and_old_handle_stops_working(client):
    link = client.post("/api/extension/link", json={"device_identity": "device-abc"}).json()

    r1 = client.post("/api/extension/refresh", json={
        "installation_id": link["installation_id"],
        "refresh_handle": link["refresh_handle"],
    })
    assert r1.status_code == 200
    # Not asserting access_token != link["access_token"]: two JWTs for
    # the same installation minted within the same wall-clock second
    # have identical iat/exp and are legitimately byte-identical
    # (same payload, same key -> same signature) — that's correct JWT
    # behavior, not a rotation failure. The refresh handle rotating
    # (and the old one dying, asserted below) is what actually proves
    # rotation happened.
    assert r1.json()["refresh_handle"] != link["refresh_handle"]

    # The old, already-rotated-away handle must never work again.
    r2 = client.post("/api/extension/refresh", json={
        "installation_id": link["installation_id"],
        "refresh_handle": link["refresh_handle"],
    })
    assert r2.status_code == 401


def test_refresh_handle_reuse_revokes_the_whole_chain(client):
    """Section 4.3: presenting an already-rotated-away handle is
    treated as a compromise signal and revokes the entire chain — not
    just the one call — so even the newest, never-reused handle from
    that chain must stop working afterward."""
    link = client.post("/api/extension/link", json={"device_identity": "device-abc"}).json()

    refreshed = client.post("/api/extension/refresh", json={
        "installation_id": link["installation_id"],
        "refresh_handle": link["refresh_handle"],
    }).json()

    # Reuse the original (now stale) handle — triggers reuse detection.
    reuse_attempt = client.post("/api/extension/refresh", json={
        "installation_id": link["installation_id"],
        "refresh_handle": link["refresh_handle"],
    })
    assert reuse_attempt.status_code == 401

    # The newest handle, never itself reused, must be dead too.
    r = client.post("/api/extension/refresh", json={
        "installation_id": link["installation_id"],
        "refresh_handle": refreshed["refresh_handle"],
    })
    assert r.status_code == 401


def test_disconnect_revokes_and_blocks_further_calls(client):
    link = client.post("/api/extension/link", json={"device_identity": "device-abc"}).json()
    access_token = link["access_token"]

    r = client.post("/api/extension/disconnect", headers={"Authorization": f"Bearer {access_token}"})
    assert r.status_code == 200
    assert r.json()["disconnected"] is True

    r = client.post(
        "/api/check-draft",
        json={"draft_text": "hi there", "surface": "linkedin"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error_code"] == "token_revoked"
