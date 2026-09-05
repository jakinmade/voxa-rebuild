"""
tests/api/conftest.py — shared fixtures for api/ tests.

Stubs supabase_client.get_supabase_client() with an in-memory fake
rather than hitting real Supabase — these tests exercise the API's own
logic (auth, routing, response mapping), not Supabase itself. The real
Supabase tables (extension_installations, evidence_seals,
telemetry_events, voice_profiles) are exercised separately, live,
against the actual project — see the manual verification in this
session's PR description.
"""
from __future__ import annotations

import os
import sys
import types
import uuid

import pytest

os.environ.setdefault("TOKEN_SIGNING_SECRET", "test-signing-secret-32-bytes-long!")

_fake_installations: dict = {}
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
    def __init__(self, params):
        self.params = params

    def execute(self):
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


class _FakeTable:
    def __init__(self, name):
        self.name = name
        self._filters = {}

    def select(self, *a, **k):
        return self

    def insert(self, payload):
        self._insert_payload = payload
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

        return _FakeResult([])


class _FakeClient:
    def table(self, name):
        return _FakeTable(name)

    def rpc(self, name, params):
        return _FakeRPC(params)


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

    fake_client_factory = lambda: _FakeClient()

    import lifetime_cap
    monkeypatch.setattr(lifetime_cap, "get_supabase_client", fake_client_factory)

    from api.db import extension_installations, evidence_seals, profile_lookup
    monkeypatch.setattr(extension_installations, "get_supabase_client", fake_client_factory)
    monkeypatch.setattr(evidence_seals, "get_supabase_client", fake_client_factory)
    monkeypatch.setattr(profile_lookup, "get_supabase_client", fake_client_factory)

    from api.telemetry import events as telemetry_events
    monkeypatch.setattr(telemetry_events, "get_supabase_client", fake_client_factory)

    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)
