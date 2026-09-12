"""
tests/unit/test_render_pipeline.py — direct coverage of
render_pipeline.run_voice_render, the pure core extracted from
app.py's _run_render (5 Sept 2026).

The property these tests exist to prove, that no other test in this
suite checks directly: this module is genuinely callable with zero
Streamlit dependency, anywhere in the process — the entire reason for
the extraction (api/routes/fix.py, not yet built, needs exactly this).
Everything else about the pipeline's actual behaviour (fixers,
scoring, correction logic) is already covered by the existing suite
via app.py's Streamlit-driven integration tests and
test_llm_boundary_contract.py's guard-presence checks; this file
doesn't re-prove that, only that the same logic is reachable without
Streamlit at all.
"""
import sys
from unittest.mock import patch, MagicMock

import pytest


def _fake_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


_BASELINE = {
    "hedge_density": 1.0, "sentence_length_sd": 3.0,
    "first_person_ratio": 0.4, "directive_ratio": 0.3,
    "conclusion_opener_ratio": 0.5, "scaffolding_density": 0.1,
    # word_count added 7 Sept 2026 — previously missing here because
    # this fixture's renders always took the "clean human input" path,
    # which never read this field. Fixed alongside the change that
    # made _build_restoration_targets run on the clean path too (see
    # that function's own docstring) and started reading it there.
    "word_count": 500,
}
_RAW_TEXT = "I checked the deck myself. It's solid. Let's send it today."


def test_importing_render_pipeline_never_pulls_in_streamlit():
    # A prior, unrelated test in this suite may have already imported
    # streamlit (Streamlit's own test harness does exactly that) — so
    # this only proves something if streamlit ISN'T already loaded.
    # Running this file in isolation (pytest tests/unit/test_render_pipeline.py)
    # is the real proof; run as part of the full suite it's a
    # best-effort check.
    was_present = "streamlit" in sys.modules
    import render_pipeline  # noqa: F401
    if not was_present:
        assert "streamlit" not in sys.modules, (
            "importing render_pipeline pulled in streamlit — the whole "
            "point of the extraction is that it doesn't need to."
        )


def test_run_voice_render_works_with_zero_streamlit_dependency():
    """The actual proof: call the real pipeline, mocked Anthropic
    client only, and confirm streamlit never gets imported during
    execution — not just at import time."""
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response(
            "I reviewed the numbers last night. They hold up. I want to ship this today, not next week."
        )
        from render_pipeline import run_voice_render
        was_present = "streamlit" in sys.modules

        stages_seen = []
        result = run_voice_render(
            input_text="Please review the attached figures at your earliest convenience.",
            api_key="test-key",
            raw_text=_RAW_TEXT,
            sample2_completions=["I looked at the numbers. They add up.", "", "", ""],
            baseline=_BASELINE,
            baseline_texts=[_RAW_TEXT],
            on_stage=stages_seen.append,
        )

    if not was_present:
        assert "streamlit" not in sys.modules, (
            "streamlit got imported during run_voice_render's execution"
        )

    assert result.success
    assert result.output_text
    assert result.voice_report is not None
    assert result.render_id
    assert "writing" in stages_seen


def test_missing_api_key_fails_cleanly_without_calling_anthropic():
    from render_pipeline import run_voice_render
    with patch("anthropic.Anthropic") as mock_cls:
        result = run_voice_render(
            input_text="Some input.",
            api_key="",
            raw_text=_RAW_TEXT,
            sample2_completions=[],
            baseline=_BASELINE,
            baseline_texts=[_RAW_TEXT],
        )
        mock_cls.assert_not_called()
    assert not result.success
    assert result.error == "API key missing."


def test_no_baseline_still_generates_but_skips_scoring():
    """Matches the original _run_render's else-branch: generation
    still happens with no baseline to score against, but every
    scoring/report field comes back empty rather than a
    real-looking-but-meaningless report."""
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response("Some output text.")
        from render_pipeline import run_voice_render
        result = run_voice_render(
            input_text="Some input.",
            api_key="test-key",
            raw_text="",
            sample2_completions=[],
            baseline=None,
            baseline_texts=[],
        )
    assert result.success
    assert result.output_text
    assert result.voice_report is None
    assert result.delta is None


def test_insertion_check_in_final_result_matches_fresh_check_against_actual_output():
    """Regression test for a real bug found via live adversarial testing
    (8 Sept 2026): insertion_check - the signal behind Content Lock's
    "no sentences invented"/"no new hedging" checks - could reflect an
    intermediate/stale text state rather than the true final
    output_text, depending on which internal branch a render happened
    to take (no correction needed at all / the general LLM
    correction-prompt branch / the still-missed re-fix loop that runs
    after either of those). Confirmed live: two renders of the same
    input under different render_mode settings converged on
    byte-identical final output text but reported different Content
    Lock verdicts on it.

    Deliberately not trying to reproduce the exact branch-divergence
    (fragile, depends on internal dimension-fixer timing that can
    change). Instead asserts the actual invariant that must hold
    regardless of path: whatever RenderResult.insertion_check says
    must be identical to a fresh, independent
    _check_uncorrected_insertions call against the input and the
    ACTUAL final output_text. Fails on the pre-fix code whenever any
    branch leaves a stale value in place; passes when insertion_check
    is always freshly computed against the true final text right
    before use."""
    from render_pipeline import run_voice_render
    from deterministic_fixers import _check_uncorrected_insertions

    input_text = "Please review the attached figures at your earliest convenience."
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response(
            "I reviewed the numbers last night. They hold up. I want to ship this today, not next week."
        )
        result = run_voice_render(
            input_text=input_text,
            api_key="test-key",
            raw_text=_RAW_TEXT,
            sample2_completions=["I looked at the numbers. They add up.", "", "", ""],
            baseline=_BASELINE,
            baseline_texts=[_RAW_TEXT],
        )

    assert result.success
    fresh_check = _check_uncorrected_insertions(input_text, result.output_text)
    assert result.insertion_check == fresh_check, (
        "insertion_check in the final RenderResult must always match a "
        "fresh check against the actual final output_text - if this "
        "fails, some branch in the pipeline is leaving a stale value "
        "in place again."
    )


def _fake_tool_response(corrected_text: str):
    block = MagicMock()
    block.type = "tool_use"
    block.input = {"corrected_text": corrected_text}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    return resp


def test_correction_pass_reintroducing_dropped_subject_is_caught_by_final_restore():
    """Real production bug, 12 Sept 2026: _restore_dropped_subject_openers
    ran once, early (right after the initial render), correctly fixing
    a dropped-subject sentence. The general LLM correction-prompt
    branch then fired afterward (build_correction_prompt has no
    SENTENCE COMPLETENESS guardrail of its own) and its fresh
    generation reintroduced the exact same drift, with nothing running
    afterward to catch it a second time — confirmed by pulling the
    actual byte-exact input/output from render_history and reproducing
    directly against _restore_dropped_subject_openers in isolation.

    This simulates that exact shape end-to-end: the initial render
    already has the dropped-subject sentence correctly restored, a
    baseline deliberately calibrated to guarantee a MISSED dimension
    (forcing the correction-prompt branch to fire), and that branch's
    mocked tool response reintroduces the drift. Asserts the final
    output_text does NOT contain the reintroduced version — proving
    the final-pass re-application (added alongside this test) is what
    catches it, not just the early one."""
    from render_pipeline import run_voice_render

    input_text = "Curious if you got a chance to run it. Built out for US Financial Services specifically."

    # Baseline with a first_person_ratio far above anything the output
    # will show — guarantees build_correction_prompt fires the general
    # LLM correction branch regardless of any other dimension.
    baseline = {
        "hedge_density": 1.0, "sentence_length_sd": 3.0,
        "first_person_ratio": 0.9, "directive_ratio": 0.3,
        "conclusion_opener_ratio": 0.5, "scaffolding_density": 0.1,
        "word_count": 500,
    }
    raw_text = "I checked the numbers myself. I think it holds up. I want to ship this."

    initial_output = "Curious if you got a chance to run it. Built out for US Financial Services specifically."
    # The correction pass's own fresh generation reintroduces exactly
    # the drift the early pass already fixed — the real-world shape of
    # the bug, not a hypothetical.
    corrected_with_drift = "I'm curious if you got a chance to run it. I built this out for US Financial Services specifically."

    def _side_effect(*args, **kwargs):
        if kwargs.get("tools"):
            return _fake_tool_response(corrected_with_drift)
        return _fake_response(initial_output)

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.side_effect = _side_effect
        result = run_voice_render(
            input_text=input_text,
            api_key="test-key",
            raw_text=raw_text,
            sample2_completions=["", "", "", ""],
            baseline=baseline,
            baseline_texts=[raw_text],
        )

    assert result.success
    assert "I'm curious if" not in result.output_text, (
        "the correction pass's reintroduced dropped-subject drift "
        "survived to the final output — the final-pass restoration "
        "isn't catching it."
    )
    assert "I built this out" not in result.output_text
    assert "Curious if you got a chance to run it." in result.output_text
    assert "Built out for US Financial Services specifically." in result.output_text


def test_on_stage_callback_is_optional():
    """The API path never supplies one — must not raise or behave
    differently when omitted."""
    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response("Some output text.")
        from render_pipeline import run_voice_render
        result = run_voice_render(
            input_text="Some input.",
            api_key="test-key",
            raw_text="",
            sample2_completions=[],
            baseline=None,
            baseline_texts=[],
            on_stage=None,
        )
    assert result.success
