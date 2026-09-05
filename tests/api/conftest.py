"""
tests/api/conftest.py — shared fixtures for api/ tests.

Stubs supabase_client.get_supabase_client() with an in-memory fake
rather than hitting real Supabase — these tests exercise the API's own
logic (auth, routing, response mapping), not Supabase itself. The real
Supabase tables (extension_installations, evidence_seals,
telemetry_events, voice_profiles, profile_recovery_emails,
profile_recovery_requests) are exercised separately, live, against the
actual project — see the manual verification in this session's PR
description.
"""
from __future__ import annotations

import os
import sys
import types
import uuid
from datetime import datetime, timezone

import pytest

os.environ.setdefault("TOKEN_SIGNING_SECRET", "test-signing-secret-32-bytes-long!")

_fake_installations: dict = {}
# device_id/profile_id -> count, backing the fake reserve_lifetime_render/
# release_lifetime_render RPCs below (lifetime_cap.py's atomic Postgres
# functions) — needed once a test exercises /api/fix, which reserves a
# lifetime render before calling run_voice_render. check-draft-only
# tests never touch this (get_lifetime_render_count reads the
# lifetime_render_cap TABLE directly, which isn't faked either — falls
# through _FakeTable's catch-all empty result, i.e. "0 used", exactly
# as before this addition).
_fake_lifetime_counts: dict = {}
# profile_id -> email, backing the fake profile_recovery_emails table;
# token_hash -> row, backing the fake profile_recovery_requests table
# and its consume_recovery_request RPC below — needed once a test
# exercises the profile-recovery routes.
_fake_recovery_emails: dict = {}
_fake_recovery_requests: dict = {}
_fake_profiles = {
    "device-abc": {
        "device_id": "device-abc",
        "raw_text": (
            "I reviewed the deck last night. It holds up. I want to "
            "send this to the board today, not next week."
        ),
        "sample2_completions": [
            "I checked the numbers myself. They are solid.", "", "", "",
        ],
        "baseline_fingerprint": {
            "hedge_density": 1.0, "sentence_length_sd": 3.0,
            "first_person_ratio": 0.4, "directive_ratio": 0.3,
            "conclusion_opener_ratio": 0.5, "scaffolding_density": 0.1,
        },
        "starter_baseline": None,
        "baseline_fingerprints_by_format": None,
        "correction_evidence": None,
        "flagged_dimensions": None,
        "voice_profile_summary": None,
        "updated_at": "2026-09-05T00:00:00Z",
    }
}


class _FakeResult:
    def __init__(self, data):
        self.data = data


class _FakeRPC:
    # Dispatches by RPC name (matching the three Postgres functions
    # this codebase actually calls — see lifetime_cap.py and
    # api/db/extension_installations.py) rather than guessing from
    # which params keys happen to be present, which broke the moment
    # a second RPC (reserve_lifetime_render) needed faking here too.
    def __init__(self, name, params):
        self.name = name
        self.params = params

    def execute(self):
        if self.name == "rotate_refresh_handle":
            return self._rotate_refresh_handle()
        if self.name == "reserve_lifetime_render":
            return self._reserve_lifetime_render()
        if self.name == "release_lifetime_render":
            return self._release_lifetime_render()
        if self.name == "consume_recovery_request":
            return self._consume_recovery_request()
        return _FakeResult([])

    def _rotate_refresh_handle(self):
        params = self.params
        inst = _fake_installations.get(params["p_installation_id"])
        if (
            inst
            and inst.get("refresh_handle_hash") == params["p_presented_hash"]
            and not inst.get("revoked_at")
        ):
            inst["refresh_handle_hash"] = params["p_new_hash"]
            inst["refresh_handle_version"] = inst.get("refresh_handle_version", 1) + 1
            inst["last_refreshed_at"] = "2026-09-05T00:00:00Z"
            return _FakeResult([inst])
        return _FakeResult([])

    def _reserve_lifetime_render(self):
        # Mirrors reserve_lifetime_render's real contract (single
        # atomic UPDATE ... WHERE count < limit ... RETURNING) closely
        # enough for route-level tests: read-check-increment against
        # the in-memory store, returning (allowed, used_count).
        device_id = self.params["p_device_id"]
        limit = self.params["p_limit"]
        current = _fake_lifetime_counts.get(device_id, 0)
        if current >= limit:
            return _FakeResult([{"allowed": False, "used_count": current}])
        current += 1
        _fake_lifetime_counts[device_id] = current
        return _FakeResult([{"allowed": True, "used_count": current}])

    def _release_lifetime_render(self):
        device_id = self.params["p_device_id"]
        current = _fake_lifetime_counts.get(device_id, 0)
        _fake_lifetime_counts[device_id] = max(0, current - 1)
        return _FakeResult([{"used_count": _fake_lifetime_counts[device_id]}])

    def _consume_recovery_request(self):
        # Mirrors consume_recovery_request's real atomic contract
        # (single UPDATE ... WHERE used_at IS NULL AND expires_at >
        # now() ... RETURNING) closely enough for route-level tests:
        # check-and-mark against the in-memory store.
        token_hash = self.params["p_token_hash"]
        row = _fake_recovery_requests.get(token_hash)
        if row is None or row.get("used_at") is not None:
            return _FakeResult([])
        if row["expires_at"] < datetime.now(timezone.utc).isoformat():
            return _FakeResult([])
        row["used_at"] = datetime.now(timezone.utc).isoformat()
        return _FakeResult([{"profile_id": row["profile_id"], "email": row["email"]}])


class _FakeTable:
    def __init__(self, name):
        self.name = name
        self._filters = {}

    def select(self, *a, **k):
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    def upsert(self, payload, on_conflict=None):
        self._insert_payload = payload
        self._upsert = True
        self._on_conflict = on_conflict
        return self

    def update(self, payload):
        self._update_payload = payload
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def is_(self, col, val):
        return self

    def limit(self, n):
        return self

    def execute(self):
        if self.name == "voice_profiles":
            dev = self._filters.get("device_id")
            row = _fake_profiles.get(dev)
            return _FakeResult([row] if row else [])

        if self.name == "extension_installations":
            if hasattr(self, "_insert_payload"):
                inst_id = str(uuid.uuid4())
                row = {
                    **self._insert_payload, "installation_id": inst_id,
                    "created_at": "2026-09-05T00:00:00Z",
                    "last_refreshed_at": None, "revoked_at": None,
                }
                _fake_installations[inst_id] = row
                return _FakeResult([row])
            if hasattr(self, "_update_payload"):
                inst_id = self._filters.get("installation_id")
                row = _fake_installations.get(inst_id)
                if row:
                    row.update(self._update_payload)
                return _FakeResult([row] if row else [])
            inst_id = self._filters.get("installation_id")
            row = _fake_installations.get(inst_id)
            return _FakeResult([row] if row else [])

        if self.name in ("evidence_seals", "telemetry_events"):
            payload = getattr(self, "_insert_payload", {})
            return _FakeResult([{**payload, "seal_id": "seal-1", "sealed_at": "2026-09-05T00:00:00Z"}])

        if self.name == "profile_recovery_emails":
            # Upsert-by-profile_id, matching the real table's own
            # on_conflict="profile_id" — one row per profile, latest
            # submission overwrites the last.
            if hasattr(self, "_insert_payload"):
                payload = self._insert_payload
                _fake_recovery_emails[payload["profile_id"]] = payload["email"]
                return _FakeResult([payload])
            # Lookup path: register_recovery_email's caller never reads
            # this table back, only get_profile_id_for_email does, by
            # email (not profile_id) — search values, not keys.
            wanted_email = self._filters.get("email")
            if wanted_email is not None:
                for pid, email in _fake_recovery_emails.items():
                    if email == wanted_email:
                        return _FakeResult([{"profile_id": pid}])
                return _FakeResult([])
            return _FakeResult([])

        if self.name == "profile_recovery_requests":
            if hasattr(self, "_insert_payload"):
                payload = self._insert_payload
                row = {**payload, "used_at": None, "created_at": "2026-09-05T00:00:00Z"}
                _fake_recovery_requests[payload["token_hash"]] = row
                return _FakeResult([row])
            return _FakeResult([])

        return _FakeResult([])


class _FakeClient:
    def table(self, name):
        return _FakeTable(name)

    def rpc(self, name, params):
        return _FakeRPC(name, params)


@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    """Applies to every test in tests/api/ automatically. Resets the
    in-memory fake stores between tests so one test's installations
    can't leak into another's assertions.

    Patches get_supabase_client directly on each module that already
    holds its own bound reference (via `from supabase_client import
    get_supabase_client`) — patching sys.modules['supabase_client']
    alone is not reliable here, since that statement copies a
    reference at import time rather than keeping a live link back to
    the supabase_client module. Any test file that triggers an early
    import of one of these modules (e.g. via a top-level `from
    api.auth import tokens`) would otherwise bind the REAL function
    before this fixture ever runs, and no later sys.modules patch
    could reach it.
    """
    _fake_installations.clear()
    _fake_lifetime_counts.clear()
    _fake_recovery_emails.clear()
    _fake_recovery_requests.clear()

    fake_client_factory = lambda: _FakeClient()

    import lifetime_cap
    monkeypatch.setattr(lifetime_cap, "get_supabase_client", fake_client_factory)

    from api.db import extension_installations, evidence_seals, profile_lookup, profile_recovery
    monkeypatch.setattr(extension_installations, "get_supabase_client", fake_client_factory)
    monkeypatch.setattr(evidence_seals, "get_supabase_client", fake_client_factory)
    monkeypatch.setattr(profile_lookup, "get_supabase_client", fake_client_factory)
    monkeypatch.setattr(profile_recovery, "get_supabase_client", fake_client_factory)

    from api.telemetry import events as telemetry_events
    monkeypatch.setattr(telemetry_events, "get_supabase_client", fake_client_factory)

    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)
