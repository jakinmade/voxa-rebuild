#!/usr/bin/env python3
"""
VOICOVA post-deploy smoke test — hardening item 4.

Checks, in order:
  1. Homepage loads (HTTP 200, correct SEO title from the voicova-seo Worker)
  2. Streamlit WebSocket handshake succeeds
  3. Stripe API connectivity (live key is valid and reachable — read-only)
  4. [--full only] A real render completes, via the actual onboarding →
     fingerprint → Check-a-draft flow in a real headless browser

Tiers, and why they're split:

  Default (no flags) — safe to run on every single deploy, zero side
  effects on production data. This is the "automatic" half of item 4's
  definition of done. Run this from CI right after Railway reports the
  deploy as live (see .github/workflows/smoke-test.yml).

  --full — additionally drives the real onboarding flow with a synthetic
  writing sample and confirms an actual render completes. This creates a
  real fingerprint record against a real (synthetic) device cookie in
  Supabase — it is NOT free of side effects, so it is not run
  automatically on every deploy. This is the "required manual step" half
  of item 4's definition of done: run it deliberately after a deploy you
  want to verify end-to-end, not as a background cron. The synthetic
  identity is clearly named (see SYNTHETIC_MARKER below) so it's easy to
  spot and clean up in Supabase if you want to purge smoke-test noise
  later.

Usage:
    python scripts/smoke_test.py                  # tier 1 only (safe, fast)
    python scripts/smoke_test.py --full            # tier 1 + real render
    python scripts/smoke_test.py --url https://staging.example.com

Exit code is non-zero if any check fails — designed to fail a CI step.
"""
import argparse
import os
import sys
import time
import urllib.request
import urllib.error

DEFAULT_URL = "https://voicova.com"
SYNTHETIC_MARKER = "voicova-smoke-test"

# A real, ordinary-looking piece of writing — long enough to pass the
# app's own fitness gate (roughly 100-250 words, per screen_paste's own
# guidance), clearly synthetic if anyone ever reads it in Supabase.
SYNTHETIC_SAMPLE = (
    "Hey, just wanted to follow up on the timeline we talked about last week. "
    "I think we're in good shape for the deadline but I want to flag one thing "
    "before it becomes a problem. The vendor still hasn't confirmed their part "
    "of the integration, and if that slips another few days it pushes our own "
    "testing window right up against the launch date. I'd rather raise this now "
    "than assume it'll sort itself out. Can we get on a quick call tomorrow "
    "morning to figure out a backup plan, just in case? I don't think it's "
    "urgent yet but I'd rather be ahead of it than behind it. Let me know what "
    "time works — this note is a synthetic sample used only by the "
    f"{SYNTHETIC_MARKER} automated check, not a real user."
)


class SmokeTestFailure(Exception):
    pass


def check_homepage(base_url: str) -> None:
    print(f"[1/4] Homepage: GET {base_url}/ ...", end=" ", flush=True)
    req = urllib.request.Request(base_url + "/", headers={"User-Agent": "voicova-smoke-test"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            body = resp.read(8192).decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise SmokeTestFailure(f"homepage request failed: {e}")

    if status != 200:
        raise SmokeTestFailure(f"homepage returned HTTP {status}, expected 200")

    if "<title>Streamlit</title>" in body or "<title>" not in body:
        raise SmokeTestFailure(
            "homepage is serving the raw Streamlit shell title, not the "
            "voicova-seo Worker's rewritten <head> — the Worker may be down "
            "or the DNS proxy (orange-cloud) may have been toggled off"
        )

    print(f"OK (HTTP {status}, SEO title present)")


def check_websocket(base_url: str) -> None:
    print(f"[2/4] WebSocket handshake: {base_url}/_stcore/stream ...", end=" ", flush=True)
    try:
        import websocket  # websocket-client
    except ImportError:
        raise SmokeTestFailure(
            "websocket-client not installed — run: pip install websocket-client"
        )

    ws_url = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/_stcore/stream"
    try:
        ws = websocket.create_connection(ws_url, timeout=10)
        ws.close()
    except Exception as e:
        raise SmokeTestFailure(f"WebSocket handshake failed: {e}")

    print("OK")


def check_stripe(secret_key: str | None) -> None:
    print("[3/4] Stripe API connectivity ...", end=" ", flush=True)
    if not secret_key:
        print("SKIPPED (no STRIPE_API_KEY / STRIPE_SECRET_KEY in environment)")
        return

    try:
        import stripe
    except ImportError:
        raise SmokeTestFailure("stripe package not installed — run: pip install stripe")

    stripe.api_key = secret_key
    try:
        # Read-only, non-destructive: confirms the key is live and Stripe
        # is reachable, without creating a real checkout session.
        stripe.Price.list(limit=1)
    except Exception as e:
        raise SmokeTestFailure(f"Stripe API call failed — key may be invalid/rotated: {e}")

    print("OK (live key valid, Stripe reachable)")


def check_full_render(base_url: str) -> None:
    print("[4/4] Full render flow (real browser, --full) ...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SmokeTestFailure(
            "playwright not installed — run: pip install playwright && "
            "playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(base_url, timeout=30000, wait_until="networkidle")

            textarea = page.get_by_label("Your writing")
            textarea.fill(SYNTHETIC_SAMPLE)
            textarea.blur()  # commits the value — Streamlit text_area only updates on blur

            page.get_by_role("button", name="Show me my fingerprint →").click()
            page.wait_for_load_state("networkidle", timeout=20000)

            # Fingerprint reveal screen should now be showing — look for
            # its known heading rather than assuming a specific selector
            # depth, so this doesn't break on unrelated CSS refactors.
            page.wait_for_selector("text=Your Voice", timeout=15000)

            print("      Fingerprint reveal reached — real render pipeline confirmed live.")
        except Exception as e:
            screenshot_path = "/tmp/smoke_test_failure.png"
            try:
                page.screenshot(path=screenshot_path)
                print(f"      Screenshot saved to {screenshot_path} for debugging.")
            except Exception:
                pass
            raise SmokeTestFailure(f"full render flow failed: {e}")
        finally:
            browser.close()

    print("OK — real render completed end-to-end")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Base URL to test (default: {DEFAULT_URL})")
    parser.add_argument("--full", action="store_true", help="Also run the real render-flow check (has side effects)")
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    checks = [
        ("Homepage", lambda: check_homepage(base_url)),
        ("WebSocket", lambda: check_websocket(base_url)),
        ("Stripe", lambda: check_stripe(os.environ.get("STRIPE_API_KEY") or os.environ.get("STRIPE_SECRET_KEY"))),
    ]
    if args.full:
        checks.append(("Full render", lambda: check_full_render(base_url)))

    print(f"VOICOVA smoke test — target: {base_url}\n")
    failures = []
    for name, fn in checks:
        try:
            fn()
        except SmokeTestFailure as e:
            print(f"FAILED — {e}")
            failures.append((name, str(e)))
        except Exception as e:
            print(f"FAILED (unexpected error) — {e}")
            failures.append((name, f"unexpected: {e}"))

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for name, reason in failures:
            print(f"  - {name}: {reason}")
        sys.exit(1)

    print("All checks passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
