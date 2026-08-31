"""
Smoke test for the "Report an issue" sidebar feature added 31 Aug
2026 -- the first real path for a person to report a problem in
VOICOVA. Driven through the real UI (Streamlit AppTest) so a broken
expander, a missing key collision between screens, or a crash in
send_issue_report_email's call site would actually surface.

SendGrid itself is mocked -- this test is about the UI wiring, not
email delivery (see test_issue_report_email.py for that).
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

BASELINE_SAMPLE_1 = (
    "I reviewed the deck last night. It holds up. I want to send this "
    "to the board today, not next week."
)
BASELINE_SAMPLE_2 = (
    "I checked the numbers myself. They are solid. Let's ship this "
    "now rather than waiting on another round of review."
)


def _seed_shell_screen(at: AppTest, screen: int = 5):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = screen
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["_device_id"] = "test-device-report-issue"
    at.session_state["_returning_user_sidebar"] = True


def test_report_an_issue_expander_present_in_sidebar(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_shell_screen(at)
    at.run()
    assert not at.exception

    expanders = [e for e in at.expander if "Report an issue" in (e.label or "")]
    assert expanders, "Expected a 'Report an issue' expander in the sidebar"


def test_submitting_a_report_sends_email_and_shows_confirmation(monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        mock_cls.return_value.send = MagicMock()

        at = AppTest.from_file(_APP_PATH, default_timeout=30)
        _seed_shell_screen(at)
        at.run()
        assert not at.exception

        text_areas = [t for t in at.text_area if "issue_report_input" in (t.key or "")]
        assert text_areas, "Expected the issue-report text area to be present"
        text_areas[0].set_value("The render came back empty for me.")
        at.run()

        submit_buttons = [b for b in at.button if "issue_report_submit" in (b.key or "")]
        assert submit_buttons, "Expected the issue-report submit button to be present"
        submit_buttons[0].click()
        at.run()

        assert not at.exception
        mock_cls.return_value.send.assert_called_once()


def test_empty_submission_does_not_send_email(monkeypatch):
    """Whitespace-only input must not fire an email or crash -- same
    empty-state discipline as everywhere else in this app."""
    monkeypatch.setenv("EMAIL_ENABLED", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "fake-key-not-real")

    with patch("sendgrid.SendGridAPIClient") as mock_cls:
        at = AppTest.from_file(_APP_PATH, default_timeout=30)
        _seed_shell_screen(at)
        at.run()

        submit_buttons = [b for b in at.button if "issue_report_submit" in (b.key or "")]
        assert submit_buttons
        submit_buttons[0].click()
        at.run()

        assert not at.exception
        mock_cls.return_value.send.assert_not_called()
