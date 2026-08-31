"""
Tests for healthcheck.py (added 31 Aug 2026) — the scheduled Railway
service that checks the Anthropic API directly, the same way a real
render would, and emails an alert if it's down. Added after a real
incident: the ANTHROPIC_API_KEY expired and every render failed
silently until a user reported it — nothing alerted anyone before
that.
"""
from unittest.mock import patch, MagicMock

import healthcheck as hc


def test_check_fails_cleanly_when_key_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ok, detail = hc._check_anthropic_api()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in detail


def test_check_succeeds_on_valid_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-real")
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = MagicMock()
        ok, detail = hc._check_anthropic_api()
        assert ok is True
        assert detail == "ok"


def test_check_reports_exception_detail_on_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-real")
    with patch("anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = Exception("boom")
        ok, detail = hc._check_anthropic_api()
        assert ok is False
        assert "boom" in detail


def test_alert_email_skipped_without_sendgrid_key(monkeypatch, capsys):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    sent = hc._send_alert_email("some failure detail")
    assert sent is False
    assert "SENDGRID_API_KEY" in capsys.readouterr().err


def test_alert_email_sent_on_failure(monkeypatch):
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")
    monkeypatch.setenv("VOICOVA_EMAIL_FROM", "hello@voicova.com")
    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        mock_cls.return_value.send = MagicMock()
        sent = hc._send_alert_email("AuthenticationError: invalid key")
        assert sent is True
        mock_cls.return_value.send.assert_called_once()


def test_main_returns_zero_and_sends_no_email_when_healthy(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-real")
    with patch("anthropic.Anthropic") as mock_cls, \
         patch("sendgrid.SendGridAPIClient") as mock_sg_cls:
        mock_cls.return_value.messages.create.return_value = MagicMock()
        exit_code = hc.main()
        assert exit_code == 0
        mock_sg_cls.return_value.send.assert_not_called()


def test_main_retries_once_before_alerting(monkeypatch):
    """A single transient failure should not immediately alert — only
    a failure that survives one retry does."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-real")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    call_count = {"n": 0}

    def flaky_then_ok(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("transient blip")
        return MagicMock()

    with patch("anthropic.Anthropic") as mock_cls, \
         patch("sendgrid.SendGridAPIClient") as mock_sg_cls, \
         patch("time.sleep"):
        mock_cls.return_value.messages.create.side_effect = flaky_then_ok
        exit_code = hc.main()
        assert exit_code == 0
        assert call_count["n"] == 2
        mock_sg_cls.return_value.send.assert_not_called()


def test_main_sends_alert_and_returns_nonzero_on_sustained_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-not-real")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    with patch("anthropic.Anthropic") as mock_cls, \
         patch("sendgrid.SendGridAPIClient") as mock_sg_cls, \
         patch("time.sleep"):
        mock_cls.return_value.messages.create.side_effect = Exception("still down")
        mock_sg_cls.return_value.send = MagicMock()
        exit_code = hc.main()
        assert exit_code == 1
        mock_sg_cls.return_value.send.assert_called_once()
