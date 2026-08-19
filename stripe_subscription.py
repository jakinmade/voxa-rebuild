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
_MONTHLY_AMOUNT_PENCE = 699
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


def verify_and_record_subscription(session_id: str, expected_device_id: str) -> bool:
    """Confirms a Checkout Session actually resulted in an active
    subscription for expected_device_id specifically, and if so,
    upserts stripe_customer_id + subscription_status onto that
    device's existing lifetime_render_cap row. Returns True only on a
    genuinely confirmed, correctly-bound active subscription.

    Binding check mirrors AQE's expected_storage_key pattern: without
    checking metadata's device_id against the caller's own device_id,
    a valid paid session_id copied into a different device's URL would
    grant that device paid status too - a real cross-tenant exposure,
    not a payment bypass in the narrow sense (a real payment did
    happen), but access to a subscription nobody on that device paid
    for.

    Fails CLOSED on any error (returns False) - deliberately the
    opposite direction from lifetime_cap.py's fail-open render checks.
    A missed verification here means a real paying customer sees an
    error and can retry or contact support; the alternative (failing
    open to "yes, subscribed") would mean anyone could type a random
    session_id into the URL and get free access whenever Stripe or
    Supabase hiccups. Same fail-closed reasoning as AQE's
    verify_stripe_session.
    """
    import stripe
    secret_key = _get_secret("STRIPE_API_KEY", ("stripe", "API_KEY"))
    if not secret_key:
        log.error("verify_subscription_unavailable", reason="stripe_not_configured")
        return False
    stripe.api_key = secret_key

    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    except Exception:
        log.error("verify_subscription_failed", reason="stripe_retrieve_error", exc_info=True)
        return False

    session_device_id = _stripe_metadata_get(session.metadata, "device_id")
    if session_device_id != expected_device_id:
        log.error(
            "verify_subscription_device_mismatch",
            expected=expected_device_id, found=session_device_id,
        )
        return False

    if session.status != "complete":
        return False

    subscription = getattr(session, "subscription", None)
    if subscription is None or getattr(subscription, "status", None) != "active":
        log.error(
            "verify_subscription_not_active",
            subscription_status=getattr(subscription, "status", None) if subscription else None,
        )
        return False

    _record_subscription(
        device_id=expected_device_id,
        stripe_customer_id=session.customer,
        subscription_status="active",
    )
    return True


def _record_subscription(device_id: str, stripe_customer_id: str, subscription_status: str) -> None:
    """Upserts onto the SAME lifetime_render_cap row lifetime_cap.py
    already reads/writes for this device - not a new table. Fails
    open and silently: a write failure here must never crash the
    success page a real paying customer is looking at. Worst case on
    a write failure, device_has_active_subscription (lifetime_cap.py)
    still reads no active subscription on the next render and the
    person has to contact support - an honest degraded state, not a
    crash."""
    client = get_supabase_client()
    if client is None:
        log.error("record_subscription_unavailable", reason="supabase_not_configured")
        return
    try:
        client.table(_TABLE).upsert({
            "device_id": device_id,
            "stripe_customer_id": stripe_customer_id,
            "subscription_status": subscription_status,
        }).execute()
    except Exception:
        log.error("record_subscription_write_failed", reason="supabase_error", exc_info=True)
