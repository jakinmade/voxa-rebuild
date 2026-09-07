#!/usr/bin/env python3
"""
dev_tools/e2e_server_with_fix.py — same as e2e_server.py (real
api.main:app, fake Supabase persistence), but also patches
anthropic.Anthropic for the lifetime of the process, so /api/fix can
be exercised offline at zero cost.

Reuses the exact same fake-Supabase wiring api_smoke_test.py's --fake
mode and tests/api/conftest.py's autouse fixture both already trust
(including fix_idempotency, added after this session's earlier idem-
potency work) — one definition of "fake", not a second hand-rolled one.

Why this needs to exist separately from e2e_server.py: that script's
own docstring deliberately excludes Fix-it ("needs a real Anthropic
key this environment doesn't have"). This one supplies a fake key and
a mocked client instead, specifically to let extension_e2e_test.js
drive the real, unmodified extension's Fix-Accept flow (the actual
execCommand DOM-write this session set out to verify) without ever
calling a real LLM.

USAGE
    python3 dev_tools/e2e_server_with_fix.py [--port 8000]
"""
from __future__ import annotations

import argparse
import os
import sys
from unittest.mock import patch, MagicMock

os.environ.setdefault("TOKEN_SIGNING_SECRET", "e2e-test-signing-secret-32-bytes!")
os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key-for-e2e-fix-test")

_FIXED_OUTPUT = "I reviewed the numbers last night. They hold up. I want to ship this today, not next week."


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.api.conftest import _FakeClient

    fake_client_factory = lambda: _FakeClient()

    import lifetime_cap
    from api.db import extension_installations, evidence_seals, profile_lookup, fix_idempotency
    from api.telemetry import events as telemetry_events

    lifetime_cap.get_supabase_client = fake_client_factory
    extension_installations.get_supabase_client = fake_client_factory
    evidence_seals.get_supabase_client = fake_client_factory
    profile_lookup.get_supabase_client = fake_client_factory
    fix_idempotency.get_supabase_client = fake_client_factory
    telemetry_events.get_supabase_client = fake_client_factory
    # render_cap.py deliberately not patched — same as the pytest
    # suite's own autouse fixture (see conftest.py). It fails open
    # without real Supabase credentials, by design (its own docstring:
    # a product-wide cost control that degrades gracefully, unlike
    # lifetime_cap's fail-closed entitlement doctrine).

    import uvicorn
    from api.main import app

    print(f"e2e_server_with_fix: real api.main:app, fake persistence, mocked Anthropic, on http://127.0.0.1:{args.port}", flush=True)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _fake_anthropic_response(_FIXED_OUTPUT)
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
