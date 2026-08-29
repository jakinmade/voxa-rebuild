"""
Tests for stripe_subscription.py — Step 4's subscription checkout and
verification (Section 5.2 / Section 13). Verification tests build real
stripe.checkout.Session / Subscription objects via Session.construct_from
rather than plain dicts or MagicMocks - genuine StripeObject metadata
does NOT support .get() (AttributeError, not None), which is exactly
the bug class the standing cross-product Stripe rule exists to catch
(hit CLEARANCE 20 June 2026, AQE 19 July 2026). A plain-dict mock would
let a .get() call pass tests while crashing in production, the same
gap AQE's own test_stripe_session_binding.py documents.
"""
from unittest.mock import MagicMock, patch

import pytest
import stripe

import stripe_subscription as sub


# ---------------------------------------------------------------------------
# _stripe_metadata_get — the safe-access helper itself
# ---------------------------------------------------------------------------

def test_metadata_get_real_stripe_object_key_present():
    session = stripe.checkout.Session.construct_from(
        {"id": "sess_1", "object": "checkout.session", "metadata": {"device_id": "device-1"}},
        "sk_test_fake",
    )
    assert sub._stripe_metadata_get(session.metadata, "device_id") == "device-1"


def test_metadata_get_real_stripe_object_key_absent_returns_default():
    session = stripe.checkout.Session.construct_from(
        {"id": "sess_1", "object": "checkout.session", "metadata": {}},
        "sk_test_fake",
    )
    assert sub._stripe_metadata_get(session.metadata, "device_id") is None
    assert sub._stripe_metadata_get(session.metadata, "device_id", "fallback") == "fallback"


def test_metadata_get_never_calls_get_on_stripe_object():
    # The actual regression this exists to prevent: .get() on a real
    # StripeObject raises AttributeError, not None. If this test ever
    # fails, _stripe_metadata_get has been changed to call .get()
    # somewhere and the whole point of this helper is gone.
    session = stripe.checkout.Session.construct_from(
        {"id": "sess_1", "object": "checkout.session", "metadata": {"device_id": "device-1"}},
        "sk_test_fake",
    )
    with pytest.raises(AttributeError):
        session.metadata.get("device_id")
    # ...but the helper itself must not raise.
    assert sub._stripe_metadata_get(session.metadata, "device_id") == "device-1"


def test_metadata_get_none_metadata_returns_default():
    assert sub._stripe_metadata_get(None, "device_id", "fallback") == "fallback"


def test_metadata_get_plain_dict_also_works():
    assert sub._stripe_metadata_get({"device_id": "device-1"}, "device_id") == "device-1"


# ---------------------------------------------------------------------------
# create_subscription_checkout
# ---------------------------------------------------------------------------

def test_create_checkout_returns_none_when_stripe_not_configured():
    with patch("stripe_subscription._get_secret", return_value=None):
        assert sub.create_subscription_checkout("device-1") is None


def test_create_checkout_monthly_uses_correct_amount_and_interval():
    with patch("stripe_subscription._get_secret", side_effect=lambda name, *a, **k: "sk_test_fake" if name == "STRIPE_API_KEY" else "https://app.example.com"), \
         patch.object(stripe.checkout.Session, "create") as mock_create:
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/pay/sess_1")
        url = sub.create_subscription_checkout("device-1", plan="monthly")
        assert url == "https://checkout.stripe.com/pay/sess_1"
        kwargs = mock_create.call_args.kwargs
        assert kwargs["mode"] == "subscription"
        assert kwargs["metadata"]["device_id"] == "device-1"
        assert kwargs["metadata"]["plan"] == "monthly"
        line_item = kwargs["line_items"][0]
        assert line_item["price_data"]["unit_amount"] == sub._MONTHLY_AMOUNT_PENCE  # TEMP: was hardcoded 699, see revert note in stripe_subscription.py
        assert line_item["price_data"]["recurring"]["interval"] == "month"


def test_create_checkout_annual_uses_correct_amount_and_interval():
    with patch("stripe_subscription._get_secret", side_effect=lambda name, *a, **k: "sk_test_fake" if name == "STRIPE_API_KEY" else "https://app.example.com"), \
         patch.object(stripe.checkout.Session, "create") as mock_create:
        mock_create.return_value = MagicMock(url="https://checkout.stripe.com/pay/sess_2")
        sub.create_subscription_checkout("device-1", plan="annual")
        kwargs = mock_create.call_args.kwargs
        line_item = kwargs["line_items"][0]
        assert line_item["price_data"]["unit_amount"] == 4900
        assert line_item["price_data"]["recurring"]["interval"] == "year"


def test_create_checkout_returns_none_on_stripe_error():
    with patch("stripe_subscription._get_secret", side_effect=lambda name, *a, **k: "sk_test_fake" if name == "STRIPE_API_KEY" else "https://app.example.com"), \
         patch.object(stripe.checkout.Session, "create", side_effect=Exception("boom")):
        assert sub.create_subscription_checkout("device-1") is None


# ---------------------------------------------------------------------------
# verify_and_record_subscription — real StripeObject fixtures throughout
# ---------------------------------------------------------------------------

def _build_session(device_id: str | None, status: str, sub_status: str | None, customer="cus_1"):
    subscription = None
    if sub_status is not None:
        subscription = stripe.Subscription.construct_from(
            {"id": "sub_1", "object": "subscription", "status": sub_status}, "sk_test_fake",
        )
    raw = {
        "id": "sess_1", "object": "checkout.session", "status": status,
        "customer": customer,
        "metadata": {"device_id": device_id} if device_id is not None else {},
    }
    session = stripe.checkout.Session.construct_from(raw, "sk_test_fake")
    if subscription is not None:
        session["subscription"] = subscription
    return session


def _patch_retrieve(session):
    return patch.object(stripe.checkout.Session, "retrieve", return_value=session)


def test_verify_succeeds_and_returns_the_verified_device_id():
    session = _build_session("device-1", status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session), \
         patch("stripe_subscription._record_subscription") as mock_record:
        result = sub.verify_and_record_subscription("sess_1")
    assert result == "device-1"
    mock_record.assert_called_once_with(
        device_id="device-1", stripe_customer_id="cus_1", subscription_status="active",
    )


def test_verify_trusts_stripe_metadata_device_id_regardless_of_local_state():
    """27 Aug 2026 hardening pass, live incident confirmed in
    production logs: this used to take a caller-supplied
    expected_device_id and reject anything not matching it. That
    check was rejecting genuine payments whenever the device cookie
    hadn't finished writing before the Stripe redirect (routine for a
    first-time subscriber) - the caller no longer supplies a device
    to check against at all; whatever Stripe's own session metadata
    says IS the answer, always, once session.status == 'complete' and
    the subscription is genuinely active. This test is really just
    confirming the signature no longer accepts (or needs) a second
    argument - the trust itself is exercised by the test above."""
    session = _build_session("device-42", status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session), \
         patch("stripe_subscription._record_subscription") as mock_record:
        result = sub.verify_and_record_subscription("sess_1")
    assert result == "device-42"
    mock_record.assert_called_once_with(
        device_id="device-42", stripe_customer_id="cus_1", subscription_status="active",
    )


def test_verify_fails_when_session_not_complete():
    session = _build_session("device-1", status="open", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_when_subscription_not_active():
    session = _build_session("device-1", status="complete", sub_status="past_due")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_when_no_subscription_present():
    session = _build_session("device-1", status="complete", sub_status=None)
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_closed_when_stripe_not_configured():
    with patch("stripe_subscription._get_secret", return_value=None):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_closed_on_retrieve_error():
    # Fails CLOSED here, deliberately the opposite direction from
    # lifetime_cap.py's render checks - see verify_and_record_
    # subscription's own docstring for why.
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.checkout.Session, "retrieve", side_effect=Exception("boom")):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_when_metadata_has_no_device_id_at_all():
    # A malformed/foreign session_id, not a device-continuity issue -
    # our own create_subscription_checkout always sets this field, so
    # its absence means something else is wrong.
    session = _build_session(None, status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1") is None


def test_verify_fails_closed_when_the_recording_write_fails():
    """27 Aug 2026 hardening pass, finding 2a: a genuine, correctly-
    verified payment whose DB write then fails must not be reported
    as a success — the exact sequence that used to let a real paying
    customer be falsely told 'You're subscribed' while the database
    still said otherwise."""
    session = _build_session("device-1", status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session), \
         patch("stripe_subscription._record_subscription", return_value=False):
        assert sub.verify_and_record_subscription("sess_1") is None


# ---------------------------------------------------------------------------
# _record_subscription — fails open and silently, never crashes the
# success page
# ---------------------------------------------------------------------------

def test_record_subscription_upserts_onto_lifetime_render_cap_table():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        result = sub._record_subscription("device-1", "cus_1", "active")
    client.table.assert_called_once_with("lifetime_render_cap")
    table.upsert.assert_called_once_with({
        "device_id": "device-1",
        "stripe_customer_id": "cus_1",
        "subscription_status": "active",
    })
    assert result is True


def test_record_subscription_returns_false_when_supabase_not_configured():
    with patch("stripe_subscription.get_supabase_client", return_value=None):
        assert sub._record_subscription("device-1", "cus_1", "active") is False


def test_record_subscription_returns_false_on_upsert_failure():
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.side_effect = Exception("boom")
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        assert sub._record_subscription("device-1", "cus_1", "active") is False


# ---------------------------------------------------------------------------
# confirm_subscription_restore — 27 Aug 2026 hardening pass, finding 2c.
# No prior test coverage existed for this function at all before this
# pass; all of the below is new, not just the re-verification cases.
# ---------------------------------------------------------------------------

def _subscription_list_result(has_active: bool):
    data = []
    if has_active:
        data = [stripe.Subscription.construct_from(
            {"id": "sub_1", "object": "subscription", "status": "active"}, "sk_test_fake",
        )]
    result = MagicMock()
    result.data = data
    return result


def test_restore_confirm_succeeds_when_stripe_still_shows_active():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", return_value=_subscription_list_result(True)), \
         patch("stripe_subscription._record_subscription", return_value=True) as mock_record:
        result = sub.confirm_subscription_restore("tok_1", "device-2")
    assert result is True
    mock_record.assert_called_once_with(
        device_id="device-2", stripe_customer_id="cus_1", subscription_status="active",
    )


def test_restore_confirm_fails_when_stripe_no_longer_shows_active():
    """The exact regression this pass fixes: a valid, unexpired token
    must not resurrect access if the subscription was canceled in the
    window since the token was issued."""
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", return_value=_subscription_list_result(False)), \
         patch("stripe_subscription._record_subscription") as mock_record:
        result = sub.confirm_subscription_restore("tok_1", "device-2")
    assert result is False
    mock_record.assert_not_called()


def test_restore_confirm_fails_on_invalid_token():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        assert sub.confirm_subscription_restore("bad_tok", "device-2") is False


def test_restore_confirm_fails_on_expired_token():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2000-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        assert sub.confirm_subscription_restore("tok_1", "device-2") is False


def test_restore_confirm_fails_closed_when_supabase_not_configured():
    with patch("stripe_subscription.get_supabase_client", return_value=None):
        assert sub.confirm_subscription_restore("tok_1", "device-2") is False


def test_restore_confirm_fails_closed_when_stripe_not_configured():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value=None):
        assert sub.confirm_subscription_restore("tok_1", "device-2") is False


def test_restore_confirm_fails_closed_on_stripe_lookup_error():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", side_effect=Exception("boom")):
        assert sub.confirm_subscription_restore("tok_1", "device-2") is False


def test_restore_confirm_fails_closed_when_recording_write_fails():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", return_value=_subscription_list_result(True)), \
         patch("stripe_subscription._record_subscription", return_value=False):
        assert sub.confirm_subscription_restore("tok_1", "device-2") is False


def test_restore_confirm_clears_the_token_only_on_success():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1", "restore_token_expires_at": "2099-01-01T00:00:00+00:00"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.Subscription, "list", return_value=_subscription_list_result(True)), \
         patch("stripe_subscription._record_subscription", return_value=True):
        sub.confirm_subscription_restore("tok_1", "device-2")
    update_call = client.table.return_value.update
    update_call.assert_called_once_with({
        "restore_token": None, "restore_token_expires_at": None,
    })


# ---------------------------------------------------------------------------
# create_billing_portal_session — frictionless cancel, same device-only
# lookup pattern as the rest of this module, fails closed on any missing
# piece rather than guessing.
# ---------------------------------------------------------------------------

def test_billing_portal_session_returns_url_for_known_customer():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1"}
    ]
    portal_session = MagicMock()
    portal_session.url = "https://billing.stripe.com/session/test_1"
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.billing_portal.Session, "create", return_value=portal_session) as mock_create:
        result = sub.create_billing_portal_session("device-1")
    assert result == "https://billing.stripe.com/session/test_1"
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["customer"] == "cus_1"


def test_billing_portal_session_none_when_device_has_no_customer_id():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        assert sub.create_billing_portal_session("device-1") is None


def test_billing_portal_session_none_when_supabase_unavailable():
    with patch("stripe_subscription.get_supabase_client", return_value=None):
        assert sub.create_billing_portal_session("device-1") is None


def test_billing_portal_session_none_when_stripe_not_configured():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value=None):
        assert sub.create_billing_portal_session("device-1") is None


def test_billing_portal_session_none_on_stripe_error():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
        {"stripe_customer_id": "cus_1"}
    ]
    with patch("stripe_subscription.get_supabase_client", return_value=client), \
         patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.billing_portal.Session, "create", side_effect=Exception("boom")):
        assert sub.create_billing_portal_session("device-1") is None
