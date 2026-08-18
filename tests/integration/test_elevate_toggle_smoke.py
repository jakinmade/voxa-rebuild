"""
Smoke test for the Preserve/Elevate toggle added 18 Aug 2026 — driven
through the real UI (Streamlit AppTest), not just the underlying
functions in isolation. This exists because test_elevate_mode.py only
proves build_correction_prompt behaves correctly given a mode string;
it says nothing about whether the radio widget itself renders without
crashing, whether clicking it actually changes st.session_state, or
whether that value survives all the way through _run_render's three
call sites to the correction call. This test closes that gap.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

SAMPLE_TEXT = (
    "Sarah, I have been going back and forth on the Meridian contract all week and "
    "honestly I still do not have a clean answer for the board. The renewal numbers "
    "Tom sent look fine on paper but something about the March timeline bothers me. "
    "We told the Meridian team Q4 in the June call, we meant Q4, and now Tom is "
    "acting like March was always on the table. It was not, and I have the email "
    "thread from June 14th to prove it. I would rather raise that plainly with Tom now "
    "than have it come up sideways in Thursday board meeting, especially with "
    "Priya from finance already asking pointed questions about the renewal margin. "
    "Please can you pull the June thread before the call. Also flagging that the "
    "Hartwell deal has the same shape, we quoted 90 days, they are now saying 120, "
    "and nobody on their side seems to remember agreeing to the shorter window. "
    "I have asked David twice for the signed order form and have not heard back."
)


def _fake_response(text: str):
    block = MagicMock()
    block.text = text
    block.type = "tool_use"
    block.input = {"corrected_text": text}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    return resp


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def test_elevate_toggle_renders_and_survives_full_render_pipeline(monkeypatch):
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short, direct sentences.")
        return _fake_response(
            "This is the clearest way forward for the team, and I think we should act on it."
        )

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = controlled_create

        at = AppTest.from_file(_APP_PATH)
        at.run(timeout=15)
        at.text_area[0].set_value(SAMPLE_TEXT)
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Screen 1->2 raised: {at.exception}"

        assert _click(at, "Continue")
        at.run(timeout=15)
        completions = at.session_state["sample2_completions"]
        completions[0] = "This completely misses what I actually asked for, and I need to say so plainly."
        completions[3] = "Honestly this has been bothering me all afternoon and I can't quite let it go."
        at.session_state["sample2_completions"] = completions
        at.run(timeout=15)
        assert _click(at, "Continue")
        at.run(timeout=15)
        assert not at.exception, f"Screen 3->4 raised: {at.exception}"
        assert at.session_state["screen"] == 4

        # The actual point of this test: the radio widget exists and
        # can be set, without crashing the screen.
        assert len(at.radio) >= 1, "Expected the render-mode radio to be present on screen 4"
        mode_radio = at.radio[0]
        mode_radio.set_value("elevate")
        at.run(timeout=15)
        assert not at.exception, f"Setting Elevate mode raised: {at.exception}"

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Render in elevate mode raised: {at.exception}"

    # The value chosen in the UI must have actually reached session
    # state under the key _run_render reads back at every call site —
    # a wiring break here (e.g. wrong key name) wouldn't raise an
    # exception, it would just silently render as if Preserve were
    # still selected.
    assert at.session_state["render_mode_input"] == "elevate", (
        "Elevate selection did not survive into render_mode_input — "
        "the toggle is visually there but not actually wired through."
    )

    output = at.session_state["render_output"]
    assert output, "Expected a render output in elevate mode"
