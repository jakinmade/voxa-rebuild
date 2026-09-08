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
