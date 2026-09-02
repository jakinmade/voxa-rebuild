#!/usr/bin/env python3
"""
smoke_test.py — post-deploy verification for voicova.com.

Foundation Hardening, Session 3, Item 4. Exists because the SEO
Worker's WebSocket-breaking change (1 Sept 2026) shipped and broke the
entire live app, and was only caught by someone happening to test it
manually that day. This closes that gap: run this after every deploy,
automatically or as a required manual step, instead of trusting a
green build.

FOUR CHECKS, TWO TIERS
-----------------------
Two checks are pure external HTTP/WS — no secrets needed, runs
anywhere:
  1. Homepage loads (200, correct title in the raw HTML — proves the
     voicova-seo Worker's server-side rewrite is still intact)
  2. A WebSocket connects to Streamlit's own stream endpoint (proves
     the Origin/Host header fix from 1 Sept hasn't regressed — this
     is the exact failure mode that shipped undetected)

Two checks need real production secrets that only exist on Railway,
not in a local/CI checkout — so they run via internal function calls,
not HTTP, and only when ANTHROPIC_API_KEY / STRIPE_API_KEY are present
in the environment:
  3. A real render completes (reuses dev_tools/harness.py's
     run_persona against a small fixture persona — same pipeline
     app.py's screen 4 calls, costs a few hundred tokens)
  4. Stripe checkout produces a real, valid session (calls
     stripe_subscription.create_subscription_checkout with a
     throwaway device_id and confirms the response is a real
     checkout.stripe.com URL — does not complete a payment)

USAGE
-----
External checks only (no secrets required):
    python3 dev_tools/smoke_test.py

Full suite, with Railway's live environment injected (recommended
post-deploy check):
    railway run --service web python3 dev_tools/smoke_test.py --full

Exit code is 0 only if every check that ran, passed. Checks 3/4 are
skipped (not failed) when their required secret isn't present, so
running without --full or without Railway's env doesn't produce a
false failure — it produces an honest "skipped, no secret."
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_URL = "https://voicova.com"
EXPECTED_TITLE = "Voicova - Communication Identity"
WS_TIMEOUT_SECONDS = 10
FIXTURE_PERSONA = "personas/terse_engineer.json"  # small, cheap, deterministic input


def _result(name, ok, detail):
    return {"check": name, "ok": ok, "detail": detail}


def check_homepage():
    """Check 1: homepage loads and carries the server-side-rewritten
    title in the raw HTML — proves voicova-seo's HTMLRewriter is
    still wired up, not just that Railway itself is up."""
    try:
        req = urllib.request.Request(BASE_URL + "/", headers={"User-Agent": "voicova-smoke-test"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read(20000).decode("utf-8", errors="replace")
    except Exception as e:
        return _result("homepage_loads", False, f"request failed: {e}")

    if status != 200:
        return _result("homepage_loads", False, f"HTTP {status}")
    if EXPECTED_TITLE not in body:
        return _result(
            "homepage_loads", False,
            f"200 OK but expected title not found in raw HTML — voicova-seo's "
            f"server-side rewrite may have regressed (falls back to Streamlit's "
            f"blank client-side-only shell, same failure mode as before 31 Aug)"
        )
    return _result("homepage_loads", True, "200 OK, title present in raw HTML")


def check_robots_txt():
    """Bonus, cheap: confirms the Worker's routing is intercepting at
    all, independent of the HTML rewrite — a faster signal if this
    fails while the homepage check passes for some other reason."""
    try:
        req = urllib.request.Request(BASE_URL + "/robots.txt", headers={"User-Agent": "voicova-smoke-test"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read(2000).decode("utf-8", errors="replace")
    except Exception as e:
        return _result("robots_txt", False, f"request failed: {e}")
    ok = status == 200 and "Sitemap:" in body
    return _result("robots_txt", ok, f"HTTP {status}" if not ok else "200 OK, sitemap referenced")


def check_websocket():
    """Check 2: a Streamlit WebSocket handshake succeeds. This is the
    exact thing that broke app-wide on 1 Sept when the SEO Worker's
    proxy set the wrong Host header — Streamlit rejects the handshake
    when Origin and Host don't match, and that failure is invisible
    to a plain HTTP check like check_homepage above, which is why this
    needs its own check rather than being folded into it."""
    try:
        import websocket  # websocket-client — add to requirements.txt if not already present
    except ImportError:
        return _result(
            "websocket_connects", False,
            "websocket-client not installed — run `pip install websocket-client` "
            "(consider adding to requirements.txt's dev/test block if this check "
            "is wired into CI)"
        )

    ws_url = BASE_URL.replace("https://", "wss://") + "/_stcore/stream"
    try:
        ws = websocket.create_connection(
            ws_url,
            timeout=WS_TIMEOUT_SECONDS,
            origin=BASE_URL,
        )
        ws.close()
    except Exception as e:
        return _result(
            "websocket_connects", False,
            f"handshake failed: {e} — if this is an Origin/Host rejection, "
            f"check the voicova-seo Worker's proxy Host header first (see its "
            f"1 Sept 2026 fix comment) before assuming a Streamlit-side issue"
        )
    return _result("websocket_connects", True, "WebSocket handshake succeeded")


def check_real_render():
    """Check 3: a real render completes end to end, same pipeline
    app.py's screen 4 uses. Needs ANTHROPIC_API_KEY — skipped, not
    failed, if it's not in the environment."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return _result("real_render_completes", None, "skipped — ANTHROPIC_API_KEY not set")

    try:
        import harness
        persona_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), FIXTURE_PERSONA)
        with open(persona_path) as f:
            persona = json.load(f)
        result = harness.run_persona(persona, dry_run=False, api_key=api_key, skip_refinement=True)
    except Exception as e:
        return _result("real_render_completes", False, f"harness call raised: {e}")

    if result.get("status") != "complete":
        return _result(
            "real_render_completes", False,
            f"status={result.get('status')} error={result.get('error', '')}"
        )
    return _result("real_render_completes", True, "fingerprint + render pipeline completed")


def check_stripe_checkout():
    """Check 4: Stripe checkout produces a real session URL. Needs
    STRIPE_API_KEY — skipped, not failed, if it's not in the
    environment. Does NOT complete a payment; a created-but-unpaid
    Checkout Session expires on its own and costs nothing."""
    api_key = os.environ.get("STRIPE_API_KEY")
    if not api_key:
        return _result("stripe_checkout_reachable", None, "skipped — STRIPE_API_KEY not set")

    try:
        import stripe_subscription
        url = stripe_subscription.create_subscription_checkout(
            device_id="smoke-test-" + str(int(time.time())), plan="monthly"
        )
    except Exception as e:
        return _result("stripe_checkout_reachable", False, f"raised: {e}")

    if not url or "checkout.stripe.com" not in url:
        return _result("stripe_checkout_reachable", False, f"no valid checkout URL returned: {url!r}")
    return _result("stripe_checkout_reachable", True, "valid checkout.stripe.com session created (unpaid, will expire)")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--full", action="store_true", help="also run the secret-requiring checks (render, Stripe)")
    args = parser.parse_args()

    checks = [check_homepage, check_robots_txt, check_websocket]
    if args.full:
        checks += [check_real_render, check_stripe_checkout]

    results = [c() for c in checks]

    failed = False
    for r in results:
        if r["ok"] is None:
            symbol = "SKIP"
        elif r["ok"]:
            symbol = "PASS"
        else:
            symbol = "FAIL"
            failed = True
        print(f"[{symbol}] {r['check']}: {r['detail']}")

    if not args.full:
        print("\n(--full not passed — render/Stripe checks skipped by default; "
              "run via `railway run` for the complete post-deploy suite)")

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
