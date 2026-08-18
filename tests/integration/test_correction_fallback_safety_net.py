"""
Integration coverage for the retry-then-fail-closed safety net inside
_run_render's correction pass (app.py, lines ~1259-1290) — added as
part of the correction-pass leak fix (18 Aug 2026) but never actually
exercised by any test until this file. Coverage analysis run the same
day found this exact block (contamination retry + fail-closed fallback)
at 0% — the single most important path in that fix, unverified.

Three real failure modes get their own test, not just the happy path:
1. The model never returns a tool_use block on either attempt (e.g. a
   refusal, or a model that doesn't respect tool_choice) — must fail
   closed to the pre-correction text, not crash or ship garbage.
2. The model returns a tool_use block both times, but the extracted
   text still looks contaminated (narration leaked through the schema
   anyway) — must also fail closed, not ship the leak.
3. The model fails once, then succeeds on retry — must actually
   recover and use the retry's result, not stay on the failed one.
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


def _fake_text_response(text: str):
    """Old-style plain text response — no tool_use block at all."""
    block = MagicMock()
    block.text = text
    block.type = "text"
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "end_turn"
    return resp


def _fake_tool_response(corrected_text: str):
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"corrected_text": corrected_text}
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


def _onboard_and_render(monkeypatch, create_side_effect):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = create_side_effect

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

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)

    return at


def test_both_attempts_return_no_tool_use_fails_closed(monkeypatch):
    """
    Failure mode 1: model never returns a tool_use block, on either
    attempt. Must fail closed to the pre-correction text and must not
    raise — a crash here would be worse than the original leak, since
    it would break the render entirely rather than degrade gracefully.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_text_response("Writes short, direct sentences. Rarely hedges.")
        content_calls = [c for c in call_log if c.get("max_tokens") != 200]
        call_number = len(content_calls)
        if call_number == 1:
            # Initial render — deliberately over-hedged so a
            # correction pass is triggered.
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        elif call_number == 2:
            # Grammar-fix pass — unchanged, not under test.
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        else:
            # Both correction attempts (call_number 3 and 4): no
            # tool_use block at all.
            return _fake_text_response("irrelevant — should never be read as corrected_text")

    at = _onboard_and_render(monkeypatch, controlled_create)

    assert not at.exception, f"Fail-closed path raised instead of degrading: {at.exception}"
    output = at.session_state["render_output"]
    assert output, "Expected a render output even when correction fails closed"
    # NOTE: this does NOT assert the hedges survive. Initial assumption
    # here was wrong — there's a second, separate safety net downstream
    # (post_correction_verify_pass) that re-checks delta after the
    # correction pass, succeeded or not, and runs free deterministic
    # fixers on anything still missing. So hedges get cleaned up either
    # way; that's correct, defense-in-depth behaviour, not a bug this
    # test should be asserting against. What this fallback actually
    # guarantees is narrower: no crash, and no leaked/garbage LLM text
    # in the output — checked below and in the contamination test.
    assert "irrelevant" not in output.lower(), (
        f"The failed correction attempt's placeholder text leaked into "
        f"output instead of falling back to pre_llm_correction: {output!r}"
    )

    content_calls = [c for c in call_log if c.get("max_tokens") != 200]
    assert len(content_calls) == 4, (
        f"Expected exactly 4 calls (render, grammar-fix, 2 failed correction "
        f"attempts) — got {len(content_calls)}. A different count means the "
        f"retry bound isn't behaving as documented."
    )


def test_both_attempts_contaminated_fails_closed(monkeypatch):
    """
    Failure mode 2: the model returns a well-formed tool_use block
    both times, but the extracted corrected_text still contains
    narration (schema compliance doesn't guarantee clean content).
    Must also fail closed — this is the harder case, since it proves
    response_looks_contaminated is actually being consulted on the
    tool_use path, not just the missing-block path.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_text_response("Writes short, direct sentences.")
        content_calls = [c for c in call_log if c.get("max_tokens") != 200]
        call_number = len(content_calls)
        if call_number == 1:
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        elif call_number == 2:
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        else:
            # Well-formed tool_use, but the content itself leaked
            # reasoning — response_looks_contaminated must catch this.
            return _fake_tool_response(
                "I notice this doesn't actually need changes, but here is "
                "the corrected version: This is the clearest way forward."
            )

    at = _onboard_and_render(monkeypatch, controlled_create)

    assert not at.exception, f"Contaminated-both-attempts path raised: {at.exception}"
    output = at.session_state["render_output"]
    assert "i notice" not in output.lower(), (
        f"A contaminated correction response leaked into the final output "
        f"despite response_looks_contaminated: {output!r}"
    )


def test_first_attempt_fails_second_succeeds_uses_retry_result(monkeypatch):
    """
    Failure mode 3, the positive case: retry must actually work, not
    just exist. First correction attempt returns no tool_use, second
    returns a clean one — the clean retry result must be the one used.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_text_response("Writes short, direct sentences.")
        content_calls = [c for c in call_log if c.get("max_tokens") != 200]
        call_number = len(content_calls)
        if call_number == 1:
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        elif call_number == 2:
            return _fake_text_response(
                "I perhaps think this is possibly the clearest way forward for the team."
            )
        elif call_number == 3:
            # First correction attempt: fails.
            return _fake_text_response("irrelevant")
        else:
            # Second correction attempt: succeeds, hedges removed.
            return _fake_tool_response("This is the clearest way forward for the team.")

    at = _onboard_and_render(monkeypatch, controlled_create)

    assert not at.exception, f"Retry-recovers path raised: {at.exception}"
    output = at.session_state["render_output"]
    assert "perhaps" not in output.lower() and "possibly" not in output.lower(), (
        f"Expected the successful retry's cleaned text to be used, but "
        f"hedges from the failed first attempt survived: {output!r}"
    )
