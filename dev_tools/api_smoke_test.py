#!/usr/bin/env python3
"""
dev_tools/api_smoke_test.py — independent, headless smoke test for the
Chrome-First API service (api/main.py), run outside pytest against
either the real stack or the same in-memory fakes tests/api/conftest.py
already uses for the unit suite.

Exists for the same reason harness.py exists for the core engine (see
that file's own docstring): pytest's tests/api/ suite proves the route
wiring is correct against faked Supabase/Anthropic — it can never prove
the actual deployed service works end to end against real credentials
and real infra, because it was never meant to. This is that second,
independent check: small enough to run in seconds, real enough to
catch a misconfigured env var or a live Supabase schema drift pytest
literally cannot see.

Three modes, cheapest first:

  1. --fake — zero external dependencies (in-memory Supabase, mocked
     Anthropic, same fixtures tests/api/conftest.py uses). Proves the
     route code paths run at all; catches nothing about real infra.
     No env vars needed.

       python3 dev_tools/api_smoke_test.py --fake
       python3 dev_tools/api_smoke_test.py --fake --with-fix

  2. In-process against the real stack — no --base-url given, no
     --fake. Runs api.main:app in this same process via TestClient,
     but every Supabase/Anthropic call is real: TOKEN_SIGNING_SECRET
     (and ANTHROPIC_API_KEY, if --with-fix) must already be set in
     this shell's environment, and --device-id must name a real row
     in the live voice_profiles table. No server process needed —
     useful for quick local iteration.

       export TOKEN_SIGNING_SECRET=... SUPABASE_URL=... SUPABASE_KEY=...
       python3 dev_tools/api_smoke_test.py --device-id <real-device-id>

  3. Against a running service (local uvicorn, or the live Railway
     deployment) — real HTTP calls via httpx, zero shared process
     state with the target. That target's own environment must
     already be configured; this script only needs the URL:

       python3 dev_tools/api_smoke_test.py \\
           --base-url https://<service>.up.railway.app \\
           --device-id <real-device-id>

Check-draft is free (Full Spec Section 2.6) and always runs. Fix-it
consumes a real render credit and (outside --fake) real Anthropic
tokens — same "cheap by default, expensive by choice" philosophy as
harness.py's --dry-run/--no-refinement flags — so it's opt-in only:

  python3 dev_tools/api_smoke_test.py --fake --with-fix
  python3 dev_tools/api_smoke_test.py --base-url ... --device-id ... --with-fix

WHAT IT CHECKS — link, check-draft, optionally fix, then disconnect,
in sequence, printing each response and a final PASS/FAIL line based
on status codes and the presence of the fields Section 3.5.1/11.2
document (and the extension's state machine actually depends on) —
not a re-implementation of the spec, just a fast confirmation that
what's live matches what's documented. Exits non-zero on any failure,
so this is usable as a CI/deploy-gate step later, not only by hand.
"""
from __future__ import annotations

import argparse
import json
import sys
from contextlib import contextmanager
from unittest.mock import patch, MagicMock

DEFAULT_DRAFT = "It could perhaps be argued that further review might be advisable."
FAKE_DEVICE_ID = "device-abc"  # tests/api/conftest.py's own fixture profile


class SmokeTestFailure(Exception):
    """Raised on any check failure — caught once at the bottom, so
    every step reports what it found before the script exits, rather
    than a bare traceback from the first bad assertion."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestFailure(message)


def _print_step(title: str) -> None:
    print(f"\n== {title} ==")


def _print_response(resp) -> dict:
    body = resp.json()
    print(f"status: {resp.status_code}")
    print(json.dumps(body, indent=2))
    return body


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


@contextmanager
def _fake_backend():
    """Installs the exact same in-memory Supabase fakes
    tests/api/conftest.py uses for the pytest suite, applied the same
    way that file's own docstring insists on: patched directly on
    each module's own bound `get_supabase_client` name, not via
    sys.modules — see that file for why the latter isn't reliable
    here. Reused rather than re-implemented so this script and the
    pytest suite can never quietly diverge on what "fake" means."""
    import os
    os.environ.setdefault("TOKEN_SIGNING_SECRET", "smoke-test-signing-secret-32-bytes!")
    # fix.py checks for a non-empty key before ever reaching the
    # (mocked, in --fake mode) Anthropic call — the value itself is
    # never used against a real API, but it must be present or the
    # route fails closed at that check, before the mock ever matters.
    os.environ.setdefault("ANTHROPIC_API_KEY", "fake-key-for-smoke-test")

    # Repo root, not this script's own directory — tests.api.conftest
    # is a proper package (tests/__init__.py, tests/api/__init__.py
    # both exist) and importable exactly as pytest already imports it,
    # once the root is on sys.path. pytest adds this automatically;
    # a standalone script has to do it itself.
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from tests.api.conftest import _FakeClient, _fake_installations, _fake_lifetime_counts

    _fake_installations.clear()
    _fake_lifetime_counts.clear()
    fake_client_factory = lambda: _FakeClient()

    import lifetime_cap
    from api.db import extension_installations, evidence_seals, profile_lookup
    from api.telemetry import events as telemetry_events

    with patch.object(lifetime_cap, "get_supabase_client", fake_client_factory), \
         patch.object(extension_installations, "get_supabase_client", fake_client_factory), \
         patch.object(evidence_seals, "get_supabase_client", fake_client_factory), \
         patch.object(profile_lookup, "get_supabase_client", fake_client_factory), \
         patch.object(telemetry_events, "get_supabase_client", fake_client_factory):
        yield


def _in_process_client():
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def _live_client(base_url: str, timeout: float):
    import httpx
    return httpx.Client(base_url=base_url, timeout=timeout)


def run(client, *, device_id: str, draft: str, with_fix: bool) -> None:
    _print_step("POST /api/extension/link")
    r = client.post("/api/extension/link", json={"device_identity": device_id})
    body = _print_response(r)
    _require(r.status_code == 200, f"link failed: expected 200, got {r.status_code}")
    for field in ("installation_id", "access_token", "refresh_handle"):
        _require(field in body and body[field], f"link response missing {field!r}")
    access_token = body["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}

    _print_step("POST /api/check-draft")
    r = client.post(
        "/api/check-draft",
        json={"draft_text": draft, "surface": "linkedin"},
        headers=headers,
    )
    body = _print_response(r)
    _require(r.status_code == 200, f"check-draft failed: expected 200, got {r.status_code}")
    _require("content_lock" not in body, "check-draft response must never include content_lock")
    _require(
        body.get("verdict") in ("good", "borderline", "failed"),
        f"check-draft verdict {body.get('verdict')!r} not one of good/borderline/failed",
    )
    for field in ("request_id", "overall_match", "dimension_scores", "scoring_version", "timestamp"):
        _require(field in body, f"check-draft response missing {field!r}")

    if with_fix:
        _print_step("POST /api/fix")
        r = client.post(
            "/api/fix",
            json={"original_draft": draft, "surface": "linkedin"},
            headers=headers,
        )
        body = _print_response(r)
        _require(r.status_code == 200, f"fix failed: expected 200, got {r.status_code}")
        for field in (
            "request_id", "corrected_text", "what_changed",
            "post_fix_predicted_score", "content_lock_result",
            "render_consumed", "scoring_version", "timestamp",
        ):
            _require(field in body, f"fix response missing {field!r}")
        _require("pass" in body["content_lock_result"], "content_lock_result missing 'pass'")
        _require(body["render_consumed"] is True, "render_consumed should be true on a successful fix")

    _print_step("POST /api/extension/disconnect")
    r = client.post("/api/extension/disconnect", headers=headers)
    body = _print_response(r)
    _require(r.status_code == 200, f"disconnect failed: expected 200, got {r.status_code}")
    _require(body.get("disconnected") is True, "disconnect response should report disconnected=true")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", help="Hit a running service over real HTTP instead of in-process.")
    parser.add_argument("--device-id", help="Existing voice_profiles device_id to link. Required unless --fake.")
    parser.add_argument("--draft", default=DEFAULT_DRAFT, help="Draft text to check/fix.")
    parser.add_argument("--with-fix", action="store_true", help="Also call /api/fix (costs a real render credit outside --fake).")
    parser.add_argument("--fake", action="store_true", help="Use in-memory fakes (no real Supabase/Anthropic needed).")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds for --base-url mode.")
    args = parser.parse_args()

    if args.fake and args.base_url:
        parser.error("--fake only applies in-process; it has no effect against --base-url and won't be honoured there.")
    if not args.fake and not args.device_id:
        parser.error("--device-id is required unless --fake is set.")

    device_id = args.device_id or FAKE_DEVICE_ID

    try:
        if args.base_url:
            client = _live_client(args.base_url, args.timeout)
            run(client, device_id=device_id, draft=args.draft, with_fix=args.with_fix)
        elif args.fake:
            with _fake_backend():
                client = _in_process_client()
                if args.with_fix:
                    with patch("anthropic.Anthropic") as mock_cls:
                        mock_cls.return_value.messages.create.return_value = _fake_anthropic_response(
                            "I reviewed the numbers last night. They hold up. "
                            "I want to ship this today, not next week."
                        )
                        run(client, device_id=device_id, draft=args.draft, with_fix=True)
                else:
                    run(client, device_id=device_id, draft=args.draft, with_fix=False)
        else:
            client = _in_process_client()
            run(client, device_id=device_id, draft=args.draft, with_fix=args.with_fix)
    except SmokeTestFailure as exc:
        print(f"\nFAIL: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — deliberately broad: any exception here is a smoke-test failure
        print(f"\nFAIL: unexpected error — {type(exc).__name__}: {exc}")
        return 1

    print("\nPASS — all steps returned the documented shape.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
