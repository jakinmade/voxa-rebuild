#!/usr/bin/env python3
"""
dev_tools/e2e_server.py — runs the REAL api.main:app (real routing,
real auth, real score_draft_check scoring) over a real local port, so
the real, unmodified Chrome extension can be tested against it.

Uses the exact same in-memory fake Supabase layer
tests/api/conftest.py already uses for the pytest suite — reused
directly (imported, not copied), not a second, hand-rolled mock, for
the same reason api_smoke_test.py's --fake mode reuses it: one
definition of "fake" the whole codebase trusts.

Why fakes and not the real Supabase project: the backend uses
SUPABASE_SERVICE_KEY (service_role, bypasses RLS), which is correctly
never exposed by the Supabase MCP tools this session has access to —
only publishable/anon keys are, and with RLS now enabled on every
Chrome-First table (migrations/2026_09_05_profile_recovery.sql's own
RLS fix), an anon key can't read or write them at all. This is a
genuine, correct boundary, not a workaround target — weakening RLS
just to make a local test possible would undo real security work for
test convenience. So: real extension, real FastAPI, real voice_engine
scoring — fake persistence only, same fake the pytest suite already
trusts.

Voice Check only (no Fix-it): score_draft_check makes zero Anthropic
calls, so this needs no API key at all. Fix-it E2E coverage is
deferred to Week 4 alongside the actual Fix-it wiring, matching the
build plan's own phasing (Full Spec Section 4.1).

USAGE
    python3 dev_tools/e2e_server.py [--port 8000]
"""
from __future__ import annotations

import argparse
import os
import sys

os.environ.setdefault("TOKEN_SIGNING_SECRET", "e2e-test-signing-secret-32-bytes!")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.api.conftest import _FakeClient

    fake_client_factory = lambda: _FakeClient()

    import lifetime_cap
    from api.db import extension_installations, evidence_seals, profile_lookup
    from api.telemetry import events as telemetry_events

    lifetime_cap.get_supabase_client = fake_client_factory
    extension_installations.get_supabase_client = fake_client_factory
    evidence_seals.get_supabase_client = fake_client_factory
    profile_lookup.get_supabase_client = fake_client_factory
    telemetry_events.get_supabase_client = fake_client_factory

    import uvicorn
    from api.main import app

    print(f"e2e_server: real api.main:app, fake persistence, on http://127.0.0.1:{args.port}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
