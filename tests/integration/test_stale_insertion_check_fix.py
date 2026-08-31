"""
Regression test for a stale-insertion_check bug found live (31 Aug
2026, real render on voicova.com): the correction-pass diff
(_check_uncorrected_insertions(pre_llm_correction, clean), ~app.py
line 2603) is computed once, right after the correction LLM call.
Everything downstream of that point — the still_missed deterministic
fixers, _regex_sweep, UK English conversion — can further modify
`clean`, including stripping out the exact hedge phrase that diff
caught. The stale, pre-fix insertion_check was still being reused for
both the Content Lock report and content_integrity_hard_fail (which
gates content_lock_pass), so a hedge already removed from the text the
person actually saw was still reported as present, and the render was
persisted as content_lock_pass=false despite a clean final output.

Fix: insertion_check is now recomputed fresh against the true original
input_text and the FINAL clean text, right before it's used for
gating/reporting — same pattern already used on the no-correction-pass
path, just applied unconditionally at the end of the pipeline so it
can't go stale regardless of how many fixer stages ran.

Driven through the real render call chain (Streamlit AppTest), same
harness as test_correction_verify_pass.py.
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


def _complete_onboarding_and_render(monkeypatch, create_side_effect, render_input="Please write a short note about the launch plan."):
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


def test_hedge_removed_by_verify_pass_does_not_survive_in_content_lock_report(monkeypatch):
    """
    The correction call introduces a hedge as a side effect ("possibly"
    is new, not in pre_llm_correction). The verify pass then removes it
    from the actual output text. The Content Lock report and
    content_lock_pass must reflect the text the person actually
    received — clean — not the intermediate snapshot that caught the
    now-corrected hedge.
    """
    call_log = []

    def controlled_create(**kwargs):
        call_log.append(kwargs)
        if kwargs.get("max_tokens") == 200:
            return _fake_response("Writes short, direct sentences. Rarely hedges.")

        content_calls = [c for c in call_log if c.get("max_tokens") != 200]
        call_number = len(content_calls)
        if call_number == 1:
            # Initial render — clean, on-target, no hedges.
            return _fake_response(
                "This is the clearest way forward for the team."
            )
        elif call_number == 2:
            # Grammar-fix pass — pass through unchanged.
            return _fake_response(
                "This is the clearest way forward for the team."
            )
        else:
            # LLM correction call introduces a hedge as a side effect
            # of whatever dimension it was actually asked to fix — the
            # exact failure mode _check_uncorrected_insertions exists
            # to catch. The verify pass (_fix_hedge_density /
            # _fix_modal_hedge, run immediately after this call and
            # again in the still_missed block) should strip it back
            # out before the person ever sees it.
            return _fake_response(
                "This is possibly the clearest way forward for the team."
            )

    # Deliberately lowercase, entity-free render input. The default
    # ("Please write a short note...") trips an unrelated, pre-existing
    # quirk in the entity-preservation heuristic — a capitalized
    # sentence-starting word ("Please") gets misread as a dropped
    # proper-noun entity once the rewrite paraphrases it away, which
    # would trip content_integrity_hard_fail for a completely different
    # reason and defeat the point of this test. Not part of the bug
    # being fixed here.
    at = _complete_onboarding_and_render(
        monkeypatch, controlled_create,
        render_input="i want a short note about the launch plan",
    )

    output = at.session_state["render_output"]
    assert "possibly" not in output.lower(), (
        f"Expected the hedge introduced by the correction call to be "
        f"removed from the final output, but it survived: {output!r}"
    )

    assert "render_insertion_check" in at.session_state
    insertion_check = at.session_state["render_insertion_check"]
    assert insertion_check is not None
    assert insertion_check["new_hedges"] == [], (
        f"render_insertion_check still lists a hedge that was already "
        f"removed from the actual output text: {insertion_check['new_hedges']!r}. "
        f"This is the stale-snapshot bug: the report must reflect the "
        f"final text, not a pre-fix intermediate."
    )

    assert "voice_report" in at.session_state
    voice_report = at.session_state["voice_report"]
    assert voice_report is not None
    assert voice_report.get("content_integrity_hard_fail") is not True, (
        "content_integrity_hard_fail (and therefore content_lock_pass) "
        "must not be tripped by a hedge that no longer exists in the "
        "final output the person actually sees."
    )
