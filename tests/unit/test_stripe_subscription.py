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
        assert line_item["price_data"]["unit_amount"] == 699
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


def test_verify_succeeds_for_matching_device_and_active_subscription():
    session = _build_session("device-1", status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session), \
         patch("stripe_subscription._record_subscription") as mock_record:
        result = sub.verify_and_record_subscription("sess_1", "device-1")
    assert result is True
    mock_record.assert_called_once_with(
        device_id="device-1", stripe_customer_id="cus_1", subscription_status="active",
    )


def test_verify_fails_when_device_id_does_not_match():
    # Session-binding check, same shape as AQE's expected_storage_key -
    # a valid paid session for a DIFFERENT device must not grant this
    # device paid status.
    session = _build_session("device-999", status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session), \
         patch("stripe_subscription._record_subscription") as mock_record:
        result = sub.verify_and_record_subscription("sess_1", "device-1")
    assert result is False
    mock_record.assert_not_called()


def test_verify_fails_when_session_not_complete():
    session = _build_session("device-1", status="open", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


def test_verify_fails_when_subscription_not_active():
    session = _build_session("device-1", status="complete", sub_status="past_due")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


def test_verify_fails_when_no_subscription_present():
    session = _build_session("device-1", status="complete", sub_status=None)
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


def test_verify_fails_closed_when_stripe_not_configured():
    with patch("stripe_subscription._get_secret", return_value=None):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


def test_verify_fails_closed_on_retrieve_error():
    # Fails CLOSED here, deliberately the opposite direction from
    # lifetime_cap.py's render checks - see verify_and_record_
    # subscription's own docstring for why.
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         patch.object(stripe.checkout.Session, "retrieve", side_effect=Exception("boom")):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


def test_verify_fails_when_metadata_has_no_device_id_at_all():
    # Absent device_id in metadata, not a mismatch - must not be
    # treated as a wildcard match.
    session = _build_session(None, status="complete", sub_status="active")
    with patch("stripe_subscription._get_secret", return_value="sk_test_fake"), \
         _patch_retrieve(session):
        assert sub.verify_and_record_subscription("sess_1", "device-1") is False


# ---------------------------------------------------------------------------
# _record_subscription — fails open and silently, never crashes the
# success page
# ---------------------------------------------------------------------------

def test_record_subscription_upserts_onto_lifetime_render_cap_table():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        sub._record_subscription("device-1", "cus_1", "active")
    client.table.assert_called_once_with("lifetime_render_cap")
    table.upsert.assert_called_once_with({
        "device_id": "device-1",
        "stripe_customer_id": "cus_1",
        "subscription_status": "active",
    })


def test_record_subscription_does_nothing_when_supabase_not_configured():
    with patch("stripe_subscription.get_supabase_client", return_value=None):
        sub._record_subscription("device-1", "cus_1", "active")  # must not raise


def test_record_subscription_swallows_upsert_failure():
    client = MagicMock()
    client.table.return_value.upsert.return_value.execute.side_effect = Exception("boom")
    with patch("stripe_subscription.get_supabase_client", return_value=client):
        sub._record_subscription("device-1", "cus_1", "active")  # must not raise
