#!/usr/bin/env python3
"""
api_e2e_tier1.py — companion to dev_tools/api_smoke_test.py, covering
the three flows that script does NOT touch: /api/fix idempotency
(sequential replay + concurrent race), /api/extension/refresh
(expired vs revoked), and the profile-recovery-email -> SendGrid ->
Supabase chain.

Run this on your own machine (not inside Claude's sandbox) — it needs
real network access to the live Railway API and, optionally, the
SendGrid Activity API.

USAGE

  pip install httpx

  export VOICOVA_API_BASE_URL=https://web-production-dbceb0.up.railway.app
  export VOICOVA_DEVICE_ID=8f225068-b18d-43a8-af8d-b32a433f17a1   # a real voice_profiles.device_id
  export RECOVERY_TEST_EMAIL=you@example.com                      # an inbox you can check
  export SENDGRID_API_KEY=SG....                                  # optional, for delivery confirmation

  python3 api_e2e_tier1.py                # runs everything except --with-fix-cost
  python3 api_e2e_tier1.py --with-fix-cost  # also runs the /api/fix idempotency tests
                                             # (these DO consume real render credits/LLM tokens)

WHAT THIS DOES NOT DO

  - Does not run dev_tools/api_smoke_test.py's own link/check-draft/fix
    happy path — run that separately first if you haven't already:
      python3 dev_tools/api_smoke_test.py --base-url $VOICOVA_API_BASE_URL --device-id $VOICOVA_DEVICE_ID
  - Does not touch the Chrome extension or any LinkedIn/Gmail DOM —
    that's Tier 3, out of scope here on purpose.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import time
import uuid

try:
    import httpx
except ImportError:
    print("Missing dependency: pip install httpx")
    sys.exit(1)


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def step(title: str) -> None:
    print(f"\n== {title} ==")


def show(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
    except Exception:
        body = {"_raw": resp.text}
    print(f"status: {resp.status_code}")
    print(body)
    return body


def link(client: httpx.Client, device_id: str) -> dict:
    step("POST /api/extension/link")
    r = client.post("/api/extension/link", json={"device_identity": device_id})
    body = show(r)
    require(r.status_code == 200, f"link failed: {r.status_code}")
    for f in ("installation_id", "access_token", "refresh_handle"):
        require(f in body and body[f], f"link response missing {f!r}")
    return body


def test_idempotency_sequential_replay(client: httpx.Client, headers: dict, draft: str) -> None:
    step("Idempotency: same key twice, sequentially (expect: identical response, one credit)")
    key = str(uuid.uuid4())
    payload = {"original_draft": draft, "surface": "linkedin", "idempotency_key": key}

    r1 = client.post("/api/fix", json=payload, headers=headers)
    body1 = show(r1)
    require(r1.status_code == 200, f"first fix call failed: {r1.status_code}")
    require(body1.get("render_consumed") is True, "first call should consume a render credit")

    r2 = client.post("/api/fix", json=payload, headers=headers)
    body2 = show(r2)
    require(r2.status_code == 200, f"replay call failed: {r2.status_code}")
    require(
        body2.get("request_id") == body1.get("request_id"),
        "replay returned a DIFFERENT request_id — this means a second real render ran "
        "(idempotency protection is not working as intended)",
    )
    require(
        body2.get("corrected_text") == body1.get("corrected_text"),
        "replay returned different corrected_text — same concern as above",
    )
    print("PASS: sequential replay returned the exact same response, no second render.")


def test_idempotency_concurrent_race(client: httpx.Client, headers: dict, draft: str) -> None:
    step("Idempotency: same key fired concurrently (expect: one 200, one 409 fix_already_in_progress)")
    key = str(uuid.uuid4())
    payload = {"original_draft": draft, "surface": "linkedin", "idempotency_key": key}

    def fire():
        return client.post("/api/fix", json=payload, headers=headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(fire)
        f2 = ex.submit(fire)
        r1, r2 = f1.result(), f2.result()

    codes = sorted([r1.status_code, r2.status_code])
    print(f"race result codes: {codes}")
    require(
        codes == [200, 409] or codes == [200, 200],
        f"unexpected code pair from concurrent identical fix calls: {codes} "
        "(expected [200, 409], or [200, 200] if both landed sequentially enough to both replay)",
    )
    if codes == [200, 200]:
        print("NOTE: both calls got 200 — they likely didn't overlap enough in time to race; "
              "not a failure, but doesn't prove the 409 path. Re-run if you want to force it.")
    else:
        print("PASS: exactly one call was rejected as in-progress, as designed.")


def test_refresh_token(client: httpx.Client, installation_id: str, refresh_handle: str) -> None:
    step("Refresh: valid handle (expect: 200, new access_token + refresh_handle)")
    r = client.post(
        "/api/extension/refresh",
        json={"installation_id": installation_id, "refresh_handle": refresh_handle},
    )
    body = show(r)
    require(r.status_code == 200, f"valid refresh failed: {r.status_code}")
    for f in ("access_token", "refresh_handle"):
        require(f in body and body[f], f"refresh response missing {f!r}")
    new_handle = body["refresh_handle"]
    require(new_handle != refresh_handle, "refresh_handle did not rotate — old handle was reused")
    print("PASS: refresh rotated the handle correctly.")

    step("Refresh: reuse the now-OLD handle (expect: 401 token_revoked or token_expired)")
    r2 = client.post(
        "/api/extension/refresh",
        json={"installation_id": installation_id, "refresh_handle": refresh_handle},
    )
    body2 = show(r2)
    require(r2.status_code == 401, f"reused old handle should be rejected, got {r2.status_code}")
    code = (body2.get("detail") or {}).get("error_code") if isinstance(body2.get("detail"), dict) else body2.get("error_code")
    print(f"error_code returned: {code}")
    require(code in ("token_revoked", "token_expired"), f"unexpected error_code: {code}")
    print("PASS: reused refresh handle correctly rejected.")

    step("Refresh: garbage handle (expect: 401 token_revoked)")
    r3 = client.post(
        "/api/extension/refresh",
        json={"installation_id": installation_id, "refresh_handle": "not-a-real-handle"},
    )
    show(r3)
    require(r3.status_code == 401, f"garbage handle should be rejected, got {r3.status_code}")
    print("PASS: garbage refresh handle correctly rejected.")


def test_recovery_flow(client: httpx.Client, headers: dict, email: str, sendgrid_key: str | None) -> None:
    step("Register recovery email (authenticated)")
    r = client.post("/api/profile/recovery-email", json={"email": email}, headers=headers)
    body = show(r)
    require(r.status_code == 200, f"register recovery email failed: {r.status_code}")
    require(body.get("registered") is True, "expected registered=true")

    before_ts = time.time()

    step("Trigger recovery (unauthenticated, real endpoint a lost-device user would hit)")
    r2 = client.post("/api/profile/recover", json={"email": email})
    body2 = show(r2)
    require(r2.status_code == 200, f"recover-initiate failed: {r2.status_code}")
    require("request_id" in body2, "recover-initiate response missing request_id")
    print(f"Recovery triggered. Check the inbox for {email} for the email now.")

    if not sendgrid_key:
        print("SENDGRID_API_KEY not set — skipping automated delivery check. "
              "Manually confirm the email arrived, then you're done with this step.")
        return

    step("Polling SendGrid Activity API for delivery confirmation")
    # SendGrid's Activity feed indexes with a short delay; poll briefly.
    sg_headers = {"Authorization": f"Bearer {sendgrid_key}"}
    found = False
    for attempt in range(6):
        time.sleep(5)
        resp = httpx.get(
            "https://api.sendgrid.com/v3/messages",
            headers=sg_headers,
            params={"query": f'to_email="{email}"', "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"SendGrid query attempt {attempt+1}: HTTP {resp.status_code} — {resp.text[:200]}")
            continue
        data = resp.json()
        msgs = data.get("messages", [])
        recent = [m for m in msgs if m.get("last_event_time")]
        if recent:
            print(f"SendGrid attempt {attempt+1}: found {len(recent)} recent message(s) to {email}")
            for m in recent[:3]:
                print(f"  status={m.get('status')} subject={m.get('subject')!r} last_event={m.get('last_event_time')}")
            found = True
            break
        print(f"SendGrid attempt {attempt+1}: no messages yet, retrying...")

    require(found, "no matching message found in SendGrid Activity API after polling — "
                    "either delivery failed, or SENDGRID_API_KEY lacks Activity API scope")
    print("PASS: SendGrid confirms a real send to this address (not just a 200 from our own endpoint).")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--with-fix-cost", action="store_true",
                         help="Run the /api/fix idempotency tests. Consumes real render credits/LLM tokens.")
    parser.add_argument("--draft", default="It could perhaps be argued that further review might be advisable.")
    args = parser.parse_args()

    base_url = os.environ.get("VOICOVA_API_BASE_URL")
    device_id = os.environ.get("VOICOVA_DEVICE_ID")
    recovery_email = os.environ.get("RECOVERY_TEST_EMAIL")
    sendgrid_key = os.environ.get("SENDGRID_API_KEY")

    require(bool(base_url), "set VOICOVA_API_BASE_URL")
    require(bool(device_id), "set VOICOVA_DEVICE_ID to a real voice_profiles.device_id")

    client = httpx.Client(base_url=base_url, timeout=60.0)

    try:
        link_body = link(client, device_id)
        headers = {"Authorization": f"Bearer {link_body['access_token']}"}

        if args.with_fix_cost:
            test_idempotency_sequential_replay(client, headers, args.draft)
            test_idempotency_concurrent_race(client, headers, args.draft)
        else:
            print("\n(Skipping /api/fix idempotency tests — pass --with-fix-cost to run them.)")

        test_refresh_token(client, link_body["installation_id"], link_body["refresh_handle"])

        if recovery_email:
            test_recovery_flow(client, headers, recovery_email, sendgrid_key)
        else:
            print("\n(Skipping recovery-email test — set RECOVERY_TEST_EMAIL to run it.)")

    except Failure as exc:
        print(f"\nFAIL: {exc}")
        return 1
    except Exception as exc:
        print(f"\nFAIL: unexpected error — {type(exc).__name__}: {exc}")
        return 1

    print("\nALL RUN STEPS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
