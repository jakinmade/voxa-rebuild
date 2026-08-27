"""
stripe_subscription.py — the missing half of Step 4
(VOICOVA_Product_2.0_Consolidated.docx Section 5.2 / Section 13):
lets a device that's used its 15 free renders (lifetime_cap.py) become
a paying subscriber, without VOICOVA ever running its own signup or
email-collection flow. See lifetime_cap.py's docstring for why this
had to land before that module could be wired into _run_render.

Same account, same pattern as AQE and CLEARANCE (jakinmade/aqe-app,
jakinmade/clearance-app) - reused deliberately, not reinvented:

  - _get_secret / _stripe_metadata_get are copied near-verbatim from
    aqe-app's app.py. In particular: real stripe.checkout.Session.
    metadata objects (stripe-python's StripeObject) do NOT support
    .get() - it raises AttributeError('get'), not None. Every place
    that reads metadata here goes through _stripe_metadata_get,
    subscript access only, per the standing cross-product rule (this
    exact failure hit CLEARANCE 20 June 2026 and AQE 19 July 2026).
  - Verification uses only stripe.checkout.Session.retrieve(session_id)
    - the same proven call AQE/CLEARANCE use for every real payment.
    No Session.list(), no webhooks: Session.list() hit an unexplained
    permissions error on AQE's founding-cohort feature and was
    abandoned there; no reason to introduce that surface here when a
    redirect + single retrieve call is already proven end to end.

WHAT'S GENUINELY DIFFERENT HERE: AQE/CLEARANCE sell one-off reports
(mode="payment", a single Session.retrieve tells the whole story).
VOICOVA sells an ongoing subscription (mode="subscription") - the
Checkout Session's own payment_status only covers the *first*
payment, not whether the subscription is still active later. This
module expands the subscription on the same retrieve call
(expand=["subscription"]) and checks the Subscription object's own
.status, rather than adding any new Stripe call type.

IDENTITY BRIDGE: VOICOVA has no accounts, no email, no signup
(Section 5.2) - device-cookie identity only. This module doesn't
change that for the free tier. It only bridges identity at the exact
moment someone chooses to pay: the Checkout Session carries device_id
in metadata (VOICOVA's own identifier), and Stripe's own checkout page
collects the email as a normal, unavoidable part of paying - not a
signup flow VOICOVA built or asked for separately. Session-binding
check (expected_device_id) mirrors AQE's storage_key binding: without
it, a valid paid session_id could be replayed against a different
device_id in the URL and grant that device someone else's paid status.

HONEST LIMITATION, stated plainly per the same standard as AQE's
founding-cohort docstring: subscription status is looked up by
device_id only. Clear cookies or switch browsers and VOICOVA can't
find the subscription again without a manual support fix (Stripe
Customer Portal lookup by email). Acceptable for Tier 1, same as the
device-only limitation the free tier already accepts; a self-serve
"restore my subscription" flow is a reasonable fast-follow once it's
an actual support ticket, not a Tier 1 blocker.

SCHEMA (two nullable columns added to the EXISTING lifetime_render_cap
table, not a new table - one row per device already exists there):

    alter table lifetime_render_cap
        add column stripe_customer_id text,
        add column subscription_status text;
"""

import os

import streamlit as st

from logging_config import get_logger
from supabase_client import get_supabase_client

log = get_logger(__name__)

_TABLE = "lifetime_render_cap"

# Section 5.1: £6.99/month or £49/year. Wedge price, not the ceiling -
# see that section for why these numbers stay fixed for Tier 1.
_MONTHLY_AMOUNT_PENCE = 50  # TEMP 27 Aug 2026: cheap real-money test of the
                            # webhook reconciliation redesign - normally 699.
                            # MUST be reverted right after the test - see the
                            # matching commit message for the revert step.
_ANNUAL_AMOUNT_PENCE = 4900


def _get_secret(env_name: str, secrets_path: tuple, default=None):
    """Environment variable first, st.secrets fallback second - same
    precedence as AQE/CLEARANCE's own _get_secret, so Railway env vars
    take over cleanly from a committed secrets.toml without a breaking
    change either way."""
    env_val = os.environ.get(env_name)
    if env_val:
        return env_val
    node = st.secrets
    try:
        for key in secrets_path:
            node = node.get(key, {}) if hasattr(node, "get") else {}
        return node if node else default
    except Exception:
        return default


def _stripe_metadata_get(metadata, key: str, default=None):
    """The one safe way to read a Stripe metadata field. Real
    StripeObject metadata does not support .get() - AttributeError,
    not None - confirmed by direct reproduction against stripe==15.3.0,
    same version pinned on AQE. Subscript access works identically on
    the real object and on a plain dict (used in this module's tests),
    so this is the only pattern used anywhere metadata is read here."""
    if metadata is None:
        return default
    try:
        return metadata[key]
    except (KeyError, TypeError):
        return default


def create_subscription_checkout(device_id: str, plan: str = "monthly") -> str | None:
    """Creates a Stripe Checkout Session in subscription mode for this
    device and returns its URL, or None if Stripe isn't configured or
    the API call fails (caller shows a plain error - see screen_render's
    upgrade button).

    device_id is folded into metadata unconditionally, same convention
    as AQE's create_stripe_checkout always folding in storage_key - so
    verify_and_record_subscription can always confirm which device a
    session belongs to, not just that some session paid.
    """
    import stripe
    secret_key = _get_secret("STRIPE_API_KEY", ("stripe", "API_KEY"))
    if not secret_key:
        log.error("create_subscription_checkout_unavailable", reason="stripe_not_configured")
        return None
    stripe.api_key = secret_key

    app_url = _get_secret("VOICOVA_APP_URL", ("VOICOVA_APP_URL",), "http://localhost:8501")
    amount_pence = _ANNUAL_AMOUNT_PENCE if plan == "annual" else _MONTHLY_AMOUNT_PENCE
    interval = "year" if plan == "annual" else "month"

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            metadata={"device_id": device_id, "plan": plan},
            line_items=[{
                "price_data": {
                    "currency": "gbp",
                    "recurring": {"interval": interval},
                    "product_data": {
                        "name": "VOICOVA",
                        "description": "Your voice. Your meaning. AI's speed.",
                    },
                    "unit_amount": amount_pence,
                },
                "quantity": 1,
            }],
            success_url=f"{app_url}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{app_url}/?payment=cancelled",
        )
        return session.url
    except Exception:
        log.error("create_subscription_checkout_failed", reason="stripe_error", exc_info=True)
        return None


def verify_and_record_subscription(session_id: str) -> str | None:
    """Confirms a Checkout Session actually resulted in an active
    subscription, and if so, upserts stripe_customer_id +
    subscription_status onto the RIGHT device's lifetime_render_cap
    row. Returns the verified device_id on success (not a bare bool)
    so the caller can re-establish that device_id as this browser's
    identity — see below for why that's now the caller's job — or
    None on any failure.

    UPDATE, 27 Aug 2026 hardening pass, live incident confirmed in
    production logs: this used to take expected_device_id as a
    parameter and reject any mismatch against session metadata's own
    device_id, mirroring AQE's expected_storage_key pattern — the
    reasoning being that trusting Stripe's metadata alone, with no
    check against the caller's current device, would let a copied
    session_id URL grant a different device someone else's paid
    status. Real reasoning, wrong fix for a race this session
    surfaced directly: the device cookie is written by a JS component
    that needs a moment to actually run in the browser, not a
    server-set HTTP header. If checkout redirects to Stripe before
    that write lands (routine for a first-time subscriber — nothing
    else has needed the cookie yet), the browser genuinely has no
    cookie when it comes back, a fresh unrelated device_id gets
    minted, and it can never match what Stripe has on file — a real
    payment was then permanently rejected by this exact check.

    The metadata's device_id doesn't need a caller-supplied value to
    trust it: create_subscription_checkout (this same module) is the
    ONLY code path that ever writes it, at session-creation time,
    before Stripe is involved — the payer cannot edit their own
    session's metadata through anything in the checkout flow. Once
    session.status == "complete" AND the resulting subscription is
    genuinely active (both still checked below, unchanged), that
    metadata IS the correct source of truth for which device this
    payment belongs to — trusting it is not a new exposure, it's
    removing a redundant, now-provably-harmful check on top of an
    already-sufficient one. The caller (app.py's checkout-success
    handler) now calls persistence.py's set_device_id_cookie with the
    value this returns, re-establishing the correct identity in this
    browser regardless of whether the original write ever completed.

    Fails CLOSED on any error (returns None) - deliberately the
    opposite direction from lifetime_cap.py's fail-open render checks.
    A missed verification here means a real paying customer sees an
    error and can retry or contact support; the alternative (failing
    open to "yes, subscribed") would mean anyone could type a random
    session_id into the URL and get free access whenever Stripe or
    Supabase hiccups. Same fail-closed reasoning as AQE's
    verify_stripe_session. This includes the DB write itself (27 Aug
    2026, independent codebase review finding 2a): a genuine payment
    whose recording write then fails must not be reported as a
    success — see _record_subscription's own docstring for the exact
    sequence this closes.
    """
    import stripe
    secret_key = _get_secret("STRIPE_API_KEY", ("stripe", "API_KEY"))
    if not secret_key:
        log.error("verify_subscription_unavailable", reason="stripe_not_configured")
        return None
    stripe.api_key = secret_key

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception:
        log.error("verify_subscription_failed", reason="stripe_retrieve_error", exc_info=True)
        return None

    session_device_id = _stripe_metadata_get(session.metadata, "device_id")
    if not session_device_id:
        # Our own create_subscription_checkout always sets this - its
        # absence means something is genuinely wrong (a malformed or
        # foreign session_id), not a device-continuity issue.
        log.error("verify_subscription_no_device_id_in_metadata")
        return None

    if session.status != "complete":
        return None

    subscription = getattr(session, "subscription", None)
    if subscription is None or getattr(subscription, "status", None) != "active":
        log.error(
            "verify_subscription_not_active",
            subscription_status=getattr(subscription, "status", None) if subscription else None,
        )
        return None

    write_succeeded = _record_subscription(
        device_id=session_device_id,
        stripe_customer_id=session.customer,
        subscription_status="active",
    )
    if not write_succeeded:
        # A real payment, correctly verified with Stripe, whose
        # recording write then failed - must not report success to
        # the caller. The write failure itself was already logged
        # inside _record_subscription.
        return None
    return session_device_id


def _record_subscription(device_id: str, stripe_customer_id: str, subscription_status: str) -> bool:
    """Upserts onto the SAME lifetime_render_cap row lifetime_cap.py
    already reads/writes for this device - not a new table. Returns
    True/False so callers can treat a failed write as part of their
    own success contract, rather than firing-and-forgetting it.

    UPDATE, 27 Aug 2026 hardening pass (independent codebase review,
    finding 2a): this used to return None unconditionally and log a
    failure silently, while verify_and_record_subscription returned
    True right after calling it regardless of whether the write
    actually landed. That sequence was possible: customer genuinely
    pays, Stripe verification succeeds, this write fails, the caller
    still returns True, the UI says "You're subscribed," and the
    database still says they're not - worse than an honest error,
    because it actively told a paying customer their entitlement was
    recorded when it wasn't. Now the write's success is part of the
    contract every caller checks - see verify_and_record_subscription
    and confirm_subscription_restore, both updated alongside this.
    """
    client = get_supabase_client()
    if client is None:
        log.error("record_subscription_unavailable", reason="supabase_not_configured")
        return False
    try:
        client.table(_TABLE).upsert({
            "device_id": device_id,
            "stripe_customer_id": stripe_customer_id,
            "subscription_status": subscription_status,
        }).execute()
        return True
    except Exception:
        log.error("record_subscription_write_failed", reason="supabase_error", exc_info=True)
        return False


def request_subscription_restore(email: str) -> None:
    """Step 1 of restore-by-magic-link: if `email` belongs to a Stripe
    customer with an active subscription, emails them a one-time link.
    Always returns None and never reveals whether the email matched -
    same "one plain message regardless of case" posture as the old
    restore_subscription_by_email had, now extended to the token step
    too. This is deliberate: telling an unauthenticated caller "no
    subscription found for that email" would let anyone probe which
    emails are paying customers.

    SECURITY NOTE - why this replaced the earlier draft: an email-only
    restore_subscription_by_email(email, device_id) that immediately
    granted access was a real vulnerability - anyone who knew a
    subscriber's email (not even the account itself, just knowledge of
    the address) could attach that person's active subscription to
    their own device_id. A magic link fixes this the standard way
    (Auth0/Supabase/FusionAuth all converge on this pattern for
    passwordless account recovery): only proof of inbox access, via
    clicking the emailed link, can bind the subscription to a device.
    Matches VOICOVA's own existing convention of carrying state through
    a URL query param (?payment=success&session_id=... already does
    this for checkout) - ?restore=<token> is the same shape, not a new
    one.

    Token: 32 bytes of secrets.token_urlsafe, stored server-side in the
    SAME lifetime_render_cap row keyed by the found stripe_customer_id
    (restore_token, restore_token_expires_at columns - see this file's
    module docstring for the schema this joins), single-use, 15-minute
    expiry - the industry-converged window (Supertokens, Slack) for
    magic-link tokens, short enough to limit exposure, long enough that
    an email arriving in 2-3 minutes doesn't feel like a race.

    Email delivery reuses CLEARANCE's proven SendGrid pattern
    (clearance-app/app.py send_report_email): same SENDGRID_API_KEY
    account, same EMAIL_ENABLED kill switch, same defensive key-
    cleaning (a stray pasted newline broke CLEARANCE's key in
    production on 30 Jun 2026 - stripped here too). Only the body
    changes: a plain restore link instead of a PDF attachment.
    """
    import secrets as secrets_module
    import stripe
    secret_key = _get_secret("STRIPE_API_KEY", ("stripe", "API_KEY"))
    if not secret_key:
        log.error("restore_request_unavailable", reason="stripe_not_configured")
        return

    stripe.api_key = secret_key
    try:
        customers = stripe.Customer.list(email=email, limit=1)
    except Exception:
        log.error("restore_request_failed", reason="stripe_customer_lookup_error", exc_info=True)
        return
    if not customers.data:
        log.info("restore_request_no_customer", email_present=True)
        return

    customer = customers.data[0]
    try:
        subscriptions = stripe.Subscription.list(customer=customer.id, status="active", limit=1)
    except Exception:
        log.error("restore_request_failed", reason="stripe_subscription_lookup_error", exc_info=True)
        return
    if not subscriptions.data:
        log.info("restore_request_no_active_subscription", email_present=True)
        return

    token = secrets_module.token_urlsafe(32)
    expires_at = _utcnow_iso_plus_minutes(15)

    client = get_supabase_client()
    if client is None:
        log.error("restore_request_unavailable", reason="supabase_not_configured")
        return
    try:
        client.table(_TABLE).update({
            "restore_token": token,
            "restore_token_expires_at": expires_at,
        }).eq("stripe_customer_id", customer.id).execute()
    except Exception:
        log.error("restore_request_write_failed", reason="supabase_error", exc_info=True)
        return

    _send_restore_email(to_email=email, token=token)
    log.info("restore_request_sent", email_present=True)


def confirm_subscription_restore(token: str, device_id: str) -> bool:
    """Step 2: called when the user lands on ?restore=<token>. Binds
    the subscription behind that token to THIS device_id - fails
    closed (False) on expired, already-used, or unknown tokens, same
    posture as verify_and_record_subscription's own fail-closed
    reasoning. Consuming the token (clearing it after use) makes it
    genuinely single-use, not just single-intended.

    UPDATE, 27 Aug 2026 hardening pass (independent codebase review,
    finding 2c): now re-checks the subscription's CURRENT status with
    Stripe before writing "active" — previously this trusted "token is
    unexpired" as equivalent to "currently entitled," which isn't the
    same fact. request_subscription_restore only issues a token when
    Stripe shows an active subscription AT THAT MOMENT, with a
    15-minute expiry; if the subscription is canceled in the window
    between the token being issued and the (still-valid) link being
    clicked, the old code would resurrect access Stripe itself now
    says shouldn't exist. Narrow window (15 minutes, and only if
    canceled after the token was already issued) but a real gap, not
    hypothetical. Also now fails closed if the recording write itself
    fails, same reasoning as verify_and_record_subscription (2a,
    same pass) — a restore that can't actually be recorded must not
    report success or consume the token.
    """
    client = get_supabase_client()
    if client is None:
        log.error("restore_confirm_unavailable", reason="supabase_not_configured")
        return False
    try:
        result = (
            client.table(_TABLE)
            .select("stripe_customer_id, restore_token_expires_at")
            .eq("restore_token", token)
            .limit(1)
            .execute()
        )
    except Exception:
        log.error("restore_confirm_failed", reason="supabase_error", exc_info=True)
        return False

    if not result.data:
        log.info("restore_confirm_invalid_token")
        return False

    row = result.data[0]
    if _is_expired(row.get("restore_token_expires_at")):
        log.info("restore_confirm_expired_token")
        return False

    import stripe
    secret_key = _get_secret("STRIPE_API_KEY", ("stripe", "API_KEY"))
    if not secret_key:
        log.error("restore_confirm_unavailable", reason="stripe_not_configured")
        return False
    stripe.api_key = secret_key
    try:
        subscriptions = stripe.Subscription.list(
            customer=row["stripe_customer_id"], status="active", limit=1
        )
    except Exception:
        log.error("restore_confirm_failed", reason="stripe_subscription_lookup_error", exc_info=True)
        return False
    if not subscriptions.data:
        # Token was valid and unexpired, but Stripe no longer shows an
        # active subscription for this customer — canceled sometime
        # after the token was issued. Do not resurrect it.
        log.info("restore_confirm_subscription_no_longer_active")
        return False

    write_succeeded = _record_subscription(
        device_id=device_id,
        stripe_customer_id=row["stripe_customer_id"],
        subscription_status="active",
    )
    if not write_succeeded:
        return False
    try:
        client.table(_TABLE).update({
            "restore_token": None,
            "restore_token_expires_at": None,
        }).eq("stripe_customer_id", row["stripe_customer_id"]).execute()
    except Exception:
        log.error("restore_confirm_token_clear_failed", reason="supabase_error", exc_info=True)

    log.info("restore_confirm_succeeded", device_id_present=True)
    return True


def _utcnow_iso_plus_minutes(minutes: int) -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


def _is_expired(expires_at_iso: str | None) -> bool:
    if not expires_at_iso:
        return True
    from datetime import datetime, timezone
    try:
        expires_at = datetime.fromisoformat(expires_at_iso)
    except ValueError:
        return True
    return datetime.now(timezone.utc) > expires_at


def _send_restore_email(to_email: str, token: str) -> None:
    """Sends the restore link via SendGrid, same account/pattern as
    CLEARANCE's send_report_email (clearance-app/app.py) - reused
    deliberately per the standing cross-product convention documented
    at the top of this file, not reinvented."""
    if os.environ.get("EMAIL_ENABLED", "true").strip().lower() == "false":
        log.info("restore_email_skipped", reason="EMAIL_ENABLED=false")
        return

    api_key = os.environ.get("SENDGRID_API_KEY")
    if api_key:
        api_key = "".join(c for c in api_key if 33 <= ord(c) <= 126).strip()
    if not api_key:
        log.error("restore_email_unavailable", reason="sendgrid_key_not_set")
        return

    app_url = _get_secret("VOICOVA_APP_URL", ("VOICOVA_APP_URL",), "http://localhost:8501")
    restore_url = f"{app_url}/?restore={token}"

    html_body = f"""
<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;color:#111827;">
  <div style="padding:2rem 1.5rem;">
    <p style="font-size:0.95rem;color:#374151;line-height:1.7;">
      Click below to restore your VOICOVA subscription on this device.
    </p>
    <p style="margin:1.5rem 0;">
      <a href="{restore_url}"
         style="background:#111827;color:#ffffff;padding:0.75rem 1.5rem;
                border-radius:6px;text-decoration:none;font-size:0.95rem;">
        Restore access
      </a>
    </p>
    <p style="font-size:0.8rem;color:#6b7280;">
      This link expires in 15 minutes and works once. If you didn't
      request this, you can ignore this email.
    </p>
  </div>
</div>
"""
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail
        from_email = os.environ.get("VOICOVA_EMAIL_FROM", "hello@voicova.com")
        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject="Restore your VOICOVA access",
            html_content=html_body,
        )
        sg = SendGridAPIClient(api_key)
        sg.send(message)
    except Exception:
        log.error("restore_email_send_failed", exc_info=True)
