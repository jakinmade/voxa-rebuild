"""
Integration coverage for render_cap.py's wiring into the real render
pipeline (_run_render in app.py).

test_render_cap.py already covers check_and_reserve_render's own
logic in isolation. What that can't catch: whether the real call site
actually calls it, and calls it BEFORE the paid API call rather than
after. Driven through the real onboarding + render UI (Streamlit
AppTest), both Anthropic and Supabase mocked throughout, zero cost.
"""
from unittest.mock import patch

from tests.integration.test_third_input_features import (
    SAMPLE_TEXT,
    _fake_response,
    _onboard_to_screen4,
)


def test_render_blocked_when_cap_reached_never_calls_anthropic(monkeypatch):
    with patch("render_cap.check_and_reserve_render", return_value=(False, 40, 40)):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = lambda **kwargs: _fake_response("should never be reached")
            at = _onboard_to_screen4(monkeypatch, mock_client.messages.create.side_effect)

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = lambda **kwargs: _fake_response("should never be reached")

            at.text_area[0].set_value(SAMPLE_TEXT)
            at.button[0].click()
            at.run(timeout=15)

            assert not at.exception, f"Render raised: {at.exception}"
            mock_client.messages.create.assert_not_called()
            assert at.session_state["render_error"]
            assert "limit" in at.session_state["render_error"].lower()


def test_render_proceeds_normally_when_cap_not_reached(monkeypatch):
    """Sanity check the wiring isn't accidentally blocking everything
    - only fires when check_and_reserve_render actually says no."""
    with patch("render_cap.check_and_reserve_render", return_value=(True, 1, 40)):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value

            def create(**kwargs):
                if kwargs.get("max_tokens") == 200:
                    return _fake_response("Writes short sentences.")
                return _fake_response("I see it as the clearest way forward for the team.")

            mock_client.messages.create.side_effect = create
            at = _onboard_to_screen4(monkeypatch, create)

        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = create

            at.text_area[0].set_value(SAMPLE_TEXT)
            at.button[0].click()
            at.run(timeout=15)

            assert not at.exception, f"Render raised: {at.exception}"
            mock_client.messages.create.assert_called()
            assert not at.session_state["render_error"]
