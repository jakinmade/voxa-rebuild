"""
healthcheck.py — proactive Anthropic API check, added 31 Aug 2026.

Runs as a separate, scheduled Railway service (Cron Schedule, not an
always-on process) in the same project as the main "web" service, so
it reuses that service's ANTHROPIC_API_KEY / SENDGRID_API_KEY /
VOICOVA_EMAIL_FROM via Railway variable references
(${{web.ANTHROPIC_API_KEY}} etc.) rather than needing its own copies
or any manual secret entry.

Why this exists: on 31 Aug 2026 the ANTHROPIC_API_KEY expired (a
30-day preset chosen at creation) and every render on voicova.com
failed silently behind a generic "That didn't go through" message
until a user reported it directly. Nothing alerted anyone before
that. The key has since been replaced with a non-expiring one, but
the actual failure mode this guards against is broader than key
expiry alone — a revoked key, a billing/credit lockout, or an
Anthropic-side outage would all fail exactly the same way, silently,
with no one finding out until a render fails in front of a real
person.

Deliberately minimal: one cheap API call, one alert path, no new
infrastructure beyond a Railway cron service. Reuses the exact same
SendGrid send pattern already established in stripe_subscription.py
(_send_restore_email, send_issue_report_email) rather than inventing
a new one — same EMAIL_ENABLED kill switch, same defensive
key-cleaning.

Deliberately does NOT alert on every failure — a single transient
blip (a momentary network hiccup, a brief rate limit) isn't worth
waking anyone up for. Retries once after a short pause before
concluding the API is actually down and sending an email.
"""
import os
import sys
import time


def _clean_key(raw: str | None) -> str | None:
    if not raw:
        return None
    return "".join(c for c in raw if 33 <= ord(c) <= 126).strip()


def _check_anthropic_api() -> tuple[bool, str]:
    """Cheapest possible real call: max_tokens=1 confirms the key is
    valid, the account has credit, and the API is reachable — the
    exact three failure modes this script exists to catch. Returns
    (ok, detail)."""
    api_key = _clean_key(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key:
        return False, "ANTHROPIC_API_KEY not set"

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _send_alert_email(detail: str) -> bool:
    api_key = _clean_key(os.environ.get("SENDGRID_API_KEY"))
    if not api_key:
        print("healthcheck: cannot send alert, SENDGRID_API_KEY not set", file=sys.stderr)
        return False

    to_email = os.environ.get(
        "VOICOVA_SUPPORT_EMAIL",
        os.environ.get("VOICOVA_EMAIL_FROM", "hello@voicova.com"),
    )
    from_email = os.environ.get("VOICOVA_EMAIL_FROM", "hello@voicova.com")

    from datetime import datetime, timezone
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#111827;">
  <div style="padding:1.5rem;">
    <p style="font-size:0.95rem;color:#111827;">
      VOICOVA's Anthropic API healthcheck failed at {checked_at}.
    </p>
    <p style="font-size:0.85rem;color:#6b7280;">
      Renders on voicova.com are likely failing right now. Detail:
    </p>
    <p style="font-size:0.85rem;color:#111827;background:#f3f4f6;
              padding:0.75rem;border-radius:6px;white-space:pre-wrap;">{detail}</p>
  </div>
</div>
"""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=f"[VOICOVA] API healthcheck failed ({checked_at})",
            html_content=html_body,
        )
        SendGridAPIClient(api_key).send(message)
        return True
    except Exception as exc:
        print(f"healthcheck: alert email itself failed to send: {exc}", file=sys.stderr)
        return False


def main() -> int:
    ok, detail = _check_anthropic_api()
    if ok:
        print("healthcheck: ok")
        return 0

    # One retry after a short pause — a single transient blip isn't
    # worth an alert. Confirmed still failing before emailing.
    print(f"healthcheck: first attempt failed ({detail}), retrying once...")
    time.sleep(15)
    ok, detail = _check_anthropic_api()
    if ok:
        print("healthcheck: ok on retry")
        return 0

    print(f"healthcheck: FAILED — {detail}", file=sys.stderr)
    sent = _send_alert_email(detail)
    print(f"healthcheck: alert email sent = {sent}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
