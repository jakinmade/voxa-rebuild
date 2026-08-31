"""
Tests for send_issue_report_email (stripe_subscription.py, added
31 Aug 2026) — the first real path for a person to report a problem
in VOICOVA. Until this, the only mention of support anywhere in the
app was a bare "contact support" phrase with no address, link, or
form behind it.

Same SendGrid account/pattern as _send_restore_email (EMAIL_ENABLED
kill switch, defensive key-cleaning, fail-silent contract) — these
tests mirror that function's own conventions rather than inventing a
new mocking shape.
"""
from unittest.mock import patch, MagicMock

import stripe_subscription as ss


def test_returns_false_and_skips_send_when_email_disabled(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        result = ss.send_issue_report_email("Something broke", "device-1", 4)
        assert result is False
        mock_cls.assert_not_called()


def test_returns_false_when_sendgrid_key_missing(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    result = ss.send_issue_report_email("Something broke", "device-1", 4)
    assert result is False


def test_sends_email_and_returns_true_on_success(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")
    monkeypatch.setenv("VOICOVA_EMAIL_FROM", "hello@voicova.com")

    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        mock_sg = mock_cls.return_value
        result = ss.send_issue_report_email(
            "The render came back empty", "device-abc", 4,
            context={"last_render_id": "render-123"},
        )
        assert result is True
        mock_sg.send.assert_called_once()


def test_defaults_to_from_address_when_support_email_unset(monkeypatch):
    """VOICOVA_SUPPORT_EMAIL is optional — must work with zero
    additional Railway configuration beyond what already exists."""
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")
    monkeypatch.delenv("VOICOVA_SUPPORT_EMAIL", raising=False)
    monkeypatch.setenv("VOICOVA_EMAIL_FROM", "hello@voicova.com")

    captured = {}

    def _fake_mail(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch("sendgrid.SendGridAPIClient") as mock_cls, \
         patch("sendgrid.helpers.mail.Mail", side_effect=_fake_mail):
        mock_cls.return_value.send = MagicMock()
        ss.send_issue_report_email("Test", "device-1", 4)

    assert captured.get("to_emails") == "hello@voicova.com"


def test_returns_false_on_send_exception(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        mock_cls.return_value.send.side_effect = Exception("network error")
        result = ss.send_issue_report_email("Test", "device-1", 4)
        assert result is False


def test_message_is_html_escaped(monkeypatch):
    """A person's issue report is untrusted input rendered into an
    HTML email — must be escaped, same discipline as _safe_html in
    app.py for anything dynamic reaching unsafe_allow_html."""
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    captured = {}

    def _fake_mail(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    with patch("sendgrid.SendGridAPIClient") as mock_cls, \
         patch("sendgrid.helpers.mail.Mail", side_effect=_fake_mail):
        mock_cls.return_value.send = MagicMock()
        ss.send_issue_report_email("<script>alert(1)</script>", "device-1", 4)

    assert "<script>" not in captured.get("html_content", "")
