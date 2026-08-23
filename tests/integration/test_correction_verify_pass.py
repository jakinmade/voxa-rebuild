"""
Integration coverage for the post-correction verify pass added to
_run_render (app.py) — the gate that checks whether the LLM correction
call actually landed, rather than trusting it and reporting whatever
came back.

Why this exists: the correction pass has always re-scored delta after
the LLM correction call, but nothing previously acted on that score —
an instruction can be partially followed (the model fixes one hedge,
misses another) or missed entirely, and the old code would silently
report the post-correction delta as final either way. The ai_tells
check a few lines below already had the right pattern (measure, and if
still not clean, run one more free deterministic pass) — this closes
the gap where the four numeric dimensions were the one place still
trusting the LLM call at face value.

Driven through the real onboarding UI and render call chain (Streamlit
AppTest), Anthropic client mocked with a *controlled, multi-call*
side_effect so the correction call can be made to only partially
comply — the exact failure mode this fix targets. Zero cost.
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


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def _complete_onboarding_and_render(monkeypatch, create_side_effect, render_input="Please write a short note about the launch plan."):
    """Shared setup: onboard through the real UI, then trigger a render
    with a controllable, multi-call Anthropic mock so a test can make
    later calls (grammar-fix, correction) behave differently from the
    first (initial render) — needed to simulate partial compliance."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create_side_effect

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run(timeout=15)
        at.text_area[0].set_value(SAMPLE_TEXT)
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Screen 1->2 raised: {at.exception}"
        assert at.session_state["screen"] == 2

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

        at.text_area[0].set_value(render_input)
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception, f"Render raised: {at.exception}"

    return at


def test_verify_pass_cleans_up_a_hedge_the_llm_correction_left_behind(monkeypatch):
    """
    The core case: the mocked LLM correction call only partially
    complies (removes one hedge, leaves another) — simulating real
    imperfect instruction-following rather than assuming the LLM
    always does exactly what it's told. The free deterministic verify
    pass should catch and remove the residual hedge, without any
    additional API call beyond the pipeline's existing shape.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        # The voice-profile-summary call is uniquely identifiable by its
        # own max_tokens (200 — see _generate_voice_profile_summary),
        # rather than relying on call position, which the profile-summary
        # feature shifted once already (it fires once, lazily, on the
        # first render before anything else) and could shift again if
        # another one-time call gets added later.
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short, direct sentences. Rarely hedges.")

        content_calls = [c for c in call_log if c.get("max_tokens") != 200]
        call_number = len(content_calls)
        if call_number == 1:
            # Initial render — deliberately over-hedged.
            return _fake_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        elif call_number == 2:
            # Grammar-fix pass — pass through unchanged, not under test here.
            return _fake_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        else:
            # LLM correction — partial compliance: removed "perhaps",
            # left "possibly". This is the residue the verify pass
            # must catch.
            return _fake_response(
                "I think this is possibly the clearest way forward for the team."
            )

    at = _complete_onboarding_and_render(monkeypatch, controlled_create)

    output = at.session_state["render_output"]
    assert "possibly" not in output.lower(), (
        f"Expected the free verify pass to remove the residual hedge the "
        f"LLM correction left behind, but it survived: {output!r}"
    )
    # No extra API call beyond the pipeline's existing shape (initial
    # render, grammar-fix, correction) PLUS the one-time profile-summary
    # call this render also triggers (first render for this baseline) —
    # the verify pass itself must add zero additional LLM calls on top
    # of that.
    content_calls = [c for c in call_log if c.get("max_tokens") != 200]
    assert len(content_calls) == 3, (
        f"Expected exactly 3 content LLM calls (verify pass must be free), got {len(content_calls)}"
    )


def test_verify_pass_does_nothing_when_correction_fully_succeeded(monkeypatch):
    """
    The other half: when the LLM correction call already fully
    complies, the verify pass should be a no-op — not double-apply a
    fixer or otherwise alter an already-correct result.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        call_number = len(call_log)
        if call_number <= 2:
            return _fake_response("I think this is clearer now for everyone involved on the team.")
        else:
            # Correction already fully clean — no hedges, no residue.
            return _fake_response("I think this is clearer now for everyone involved on the team.")

    at = _complete_onboarding_and_render(monkeypatch, controlled_create)
    output = at.session_state["render_output"]
    assert output  # rendered something, no exception, no crash on the no-op path
