"""
Integration coverage for the two "third input" additions this
session: an optional per-render audience/purpose field, and a
lazily-generated, cached distilled voice-profile summary.

Both are grounded in research checked before building (see
build_voice_profile_summary_prompt's docstring in prompts.py and the
render_context comment in app.py's screen_render): register/audience
is a genuinely separate style axis from personal voice per the
field's own theory of style transfer, and generating from a distilled
profile measurably outperforms generating from raw context directly.

Driven through the real onboarding + render UI (Streamlit AppTest),
Anthropic client mocked throughout, zero cost. A real bug was caught
by this exact test before it existed as a permanent file: the
voice_profile_summary parameter was added to _build_system_prompt and
its injection logic written, but never actually passed at the real
call site in _run_render — unit tests calling _build_system_prompt
directly couldn't catch that, only a test exercising the real wiring
could.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest


def _system_text(system) -> str:
    """The call site currently sends `system` as a plain string
    (the caching restructure that briefly sent it as a list of
    content blocks was reverted - see the commit reverting
    _build_system_prompt_blocks). Kept defensive/shape-agnostic in
    case that's revisited later, rather than assuming a plain string
    and breaking again if it changes."""
    if isinstance(system, str):
        return system
    return "".join(block.get("text", "") for block in system)

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


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def _fake_response(text: str):
    # Single block satisfies both response shapes the pipeline uses:
    # .text for the plain-text calls (initial render, grammar-fix,
    # voice-profile-summary — unchanged), and .type/.input for the
    # correction call, which now uses a forced tool_choice response
    # (see prompts.CORRECTION_TOOL / app.py's correction-pass fix,
    # 18 Aug 2026). Without both, the correction call's tool_use
    # extraction finds nothing, silently retries, and inflates the
    # call count — exactly the regression this fix closes.
    block = MagicMock()
    block.text = text
    block.type = "tool_use"
    block.input = {"corrected_text": text}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    return resp


def _onboard_to_screen4(monkeypatch, create_side_effect):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create_side_effect

        at = AppTest.from_file(_APP_PATH)
        at.run(timeout=15)
        at.text_area[0].set_value(SAMPLE_TEXT)
        at.button[0].click()
        at.run(timeout=15)
        assert _click(at, "Continue")
        at.run(timeout=15)
        completions = at.session_state["sample2_completions"]
        completions[0] = "This completely misses what I actually asked for, and I need to say so plainly."
        completions[3] = "Honestly this has been bothering me all afternoon and I can't quite let it go."
        at.session_state["sample2_completions"] = completions
        at.run(timeout=15)
        assert _click(at, "Continue")
        at.run(timeout=15)
        assert at.session_state["screen"] == 4
    return at


def test_render_context_reaches_the_real_system_prompt(monkeypatch):
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short sentences.")
        return _fake_response("I see it as the clearest way forward for the team.")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create
        at = _onboard_to_screen4(monkeypatch, create)

        context_input = next(iter(at.text_input), None)
        assert context_input is not None, "Expected a text_input widget for render_context"
        context_input.set_value("A cold outreach follow-up to Scott, keep it low-pressure")
        at.run(timeout=15)

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Render raised: {at.exception}"

    content_calls = [c for c in captured if c.get("max_tokens") != 200]
    assert content_calls, "Expected at least one content (non-profile-summary) call"
    system = _system_text(content_calls[0]["system"])
    assert "CONTEXT FOR THIS PIECE" in system
    assert "cold outreach follow-up to Scott" in system


def test_render_context_omitted_when_left_blank(monkeypatch):
    """The field must be genuinely optional — leaving it blank should
    not inject an empty CONTEXT block into the prompt."""
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short sentences.")
        return _fake_response("I see it as the clearest way forward for the team.")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create
        at = _onboard_to_screen4(monkeypatch, create)

        # Deliberately do NOT fill in the context field.
        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception

    content_calls = [c for c in captured if c.get("max_tokens") != 200]
    assert "CONTEXT FOR THIS PIECE" not in _system_text(content_calls[0]["system"])


def test_voice_profile_summary_generated_once_and_cached_across_renders(monkeypatch):
    """The core cost-guardrail behaviour: the distillation call fires
    lazily on the first render, then must NOT fire again on a second
    render in the same session."""
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short, direct sentences. Rarely hedges.")
        return _fake_response("I see it as the clearest way forward for the team.")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create
        at = _onboard_to_screen4(monkeypatch, create)

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"First render raised: {at.exception}"

        summary_calls_after_first = [c for c in captured if c.get("max_tokens") == 200]
        assert len(summary_calls_after_first) == 1

        first_content_call = [c for c in captured if c.get("max_tokens") != 200][0]
        assert "WRITER'S DISTINCTIVE HABITS" in _system_text(first_content_call["system"])
        assert "Writes short, direct sentences" in _system_text(first_content_call["system"])

        # Second render, same session.
        at.text_area[0].set_value("Please write a second short note about pricing.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Second render raised: {at.exception}"

    summary_calls_after_second = [c for c in captured if c.get("max_tokens") == 200]
    assert len(summary_calls_after_second) == 1, (
        f"Expected the profile summary to stay cached across renders "
        f"(1 call total), got {len(summary_calls_after_second)}"
    )


def test_render_proceeds_normally_when_profile_summary_generation_fails(monkeypatch):
    """Fail-open: if the distillation call itself fails, the render
    must still complete successfully with the rest of the pipeline
    working exactly as it did before this feature existed."""
    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            raise Exception("simulated distillation failure")
        return _fake_response("I see it as the clearest way forward for the team.")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create
        at = _onboard_to_screen4(monkeypatch, create)

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Render raised despite profile-summary failure: {at.exception}"
        assert at.session_state["render_output"], "Expected a render to still complete"

    content_calls = [c for c in captured if c.get("max_tokens") != 200]
    assert "WRITER'S DISTINCTIVE HABITS" not in _system_text(content_calls[0]["system"])
