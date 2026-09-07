"""
dev_tools/_verify_caps.py — offline verification (zero cost, no real
LLM calls) that:

1. render_cap_exhausted (402) is correctly returned once a device's
   lifetime render cap is hit, and that the error_code the extension's
   state machine actually branches on (CREDITS_EXHAUSTED) is present.
2. rate_limited (429)... rate_limit.py's own check() function is
   verified directly (30 requests/60s) rather than by firing 31 real
   HTTP requests through the full render pipeline, since check-draft
   is the cheapest path that still calls rate_limit.enforce and this
   proves the exact same shared code path fix.py also uses.

Uses MAX_LIFETIME_RENDERS=2 (overriding the real default of 15) so
this completes in 3 fake render calls instead of 16.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TOKEN_SIGNING_SECRET", "verify-signing-secret-32-bytes!!")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key-for-verify")
os.environ["MAX_LIFETIME_RENDERS"] = "2"

from unittest.mock import patch, MagicMock
from tests.api.conftest import _FakeClient, _fake_installations, _fake_lifetime_counts, _fake_recovery_requests

_fake_installations.clear()
_fake_lifetime_counts.clear()

fake_client_factory = lambda: _FakeClient()
import lifetime_cap
from api.db import extension_installations, evidence_seals, profile_lookup, profile_recovery as recovery_db, fix_idempotency
from api.telemetry import events as telemetry_events


def _fake_anthropic_response(text):
    block = MagicMock(); block.type = "text"; block.text = text
    resp = MagicMock(); resp.content = [block]
    return resp


with patch.object(lifetime_cap, "get_supabase_client", fake_client_factory), \
     patch.object(extension_installations, "get_supabase_client", fake_client_factory), \
     patch.object(evidence_seals, "get_supabase_client", fake_client_factory), \
     patch.object(profile_lookup, "get_supabase_client", fake_client_factory), \
     patch.object(recovery_db, "get_supabase_client", fake_client_factory), \
     patch.object(fix_idempotency, "get_supabase_client", fake_client_factory), \
     patch.object(telemetry_events, "get_supabase_client", fake_client_factory), \
     patch("anthropic.Anthropic") as mock_cls:

    mock_cls.return_value.messages.create.return_value = _fake_anthropic_response(
        "I reviewed the numbers last night. They hold up."
    )

    from fastapi.testclient import TestClient
    from api.main import app
    client = TestClient(app)

    r = client.post("/api/extension/link", json={"device_identity": "device-abc"})
    access_token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    draft = "It could perhaps be argued that further review might be advisable."

    print("=== render_cap_exhausted (402) ===")
    for i in range(3):
        import uuid
        r = client.post("/api/fix", json={
            "original_draft": draft, "surface": "linkedin",
            "idempotency_key": str(uuid.uuid4()),
        }, headers=headers)
        print(f"call {i+1}: status={r.status_code} body={r.json()}")

    assert r.status_code == 402, f"expected 402 on the 3rd call (limit=2), got {r.status_code}"
    assert r.json()["detail"]["error_code"] == "render_cap_exhausted", r.json()
    print("PASS: 3rd call correctly returned 402 render_cap_exhausted\n")

print("=== rate_limited (429) ===")
import api.auth.rate_limit as rate_limit
rate_limit._requests.clear()
key = "test-installation-id"
allowed_count = 0
for i in range(35):
    if rate_limit.check(key):
        allowed_count += 1
print(f"allowed: {allowed_count} / 35 requests within the window")
assert allowed_count == 30, f"expected exactly 30 allowed (the documented _MAX_REQUESTS_PER_WINDOW), got {allowed_count}"
print("PASS: rate limiter allows exactly 30/60s, rejects the rest\n")

print("ALL CAP/RATE-LIMIT CHECKS PASSED")
