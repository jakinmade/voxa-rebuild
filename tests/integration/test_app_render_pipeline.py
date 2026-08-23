"""
End-to-end test of app.py's render pipeline via Streamlit's AppTest —
the real script, real widgets, real session-state wiring, with only the
Anthropic API call mocked (zero cost, per standing cost discipline).

Why this exists: app.py has no test harness anywhere else in this
codebase — it's a Streamlit script, not a library of testable
functions, and the existing full suite only covers voice_engine.py and
prompts.py directly. Everything in those modules can pass while the
wiring connecting them in app.py is still broken (wrong session-state
key, wrong argument order, a call site left unedited). This file closes
that specific gap for the changes made during the guardrail-
consolidation and Burrows'-Delta sessions (August 2026): the
plausibility-shield fix, and the new fingerprint_sample_texts /
compute_burrows_delta wiring.

Known limitation, stated plainly: Screen 3 (the required-starters flow)
uses paste_guard, a custom JS-based Streamlit component
(components.v1.declare_component). AppTest cannot drive custom
components — this is a hard framework limitation, not a workaround
gap, and the component's own docstring already flags itself as
"unverified outside the protocol spec, needs a live browser smoke
test." These tests work around that by seeding session state directly
to the post-Screen-3 shape (two baseline samples, as Screen 3 would
have produced) rather than driving the actual paste_guard widgets. The
Screen 3 write path itself (fingerprint_sample_texts.extend(...)) is
verified by direct code review only, pending a live Railway
click-through — see project history for that follow-up.
"""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness


_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


FAKE_LLM_OUTPUT = (
    "I see it as the clearest way forward—we should leverage this "
    "approach across the team."
)

BASELINE_SAMPLE_1 = (
    "I think we should move fast on this. I want the team to focus on "
    "the core problem first, and then we can look at the edges."
)
BASELINE_SAMPLE_2 = (
    "I believe the data backs this up, and I think it is the right call "
    "for now. We need to move quickly and stay focused."
)


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _run_screen4_with_mocked_render(input_text: str = "Please write a short note about the launch plan."):
    """
    Shared setup: seeds session state as if Screen 1 + Screen 3 had
    already completed (two distinct baseline samples), lands on Screen
    4, and drives the real 'Write as me' button with the Anthropic
    client mocked. Returns the AppTest instance after the render.
    """
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
            at.run()

            combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
            metrics_1 = compute_baseline_metrics(BASELINE_SAMPLE_1)
            metrics_2 = compute_baseline_metrics(BASELINE_SAMPLE_2)

            at.session_state["screen"] = 4
            at.session_state["raw_text"] = BASELINE_SAMPLE_1
            at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
            at.session_state["observations"] = analyse_writing(combined)
            at.session_state["sample_fitness"] = _score_sample_fitness(combined)
            at.session_state["fingerprint_samples"] = [metrics_1, metrics_2]
            at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
            at.session_state["sample2_completions"] = ["", "", "", ""]

            at.run()
            assert not at.exception, f"App raised on Screen 4 load: {at.exception}"

            at.text_area[0].input(input_text)
            at.button[0].click()
            # Explicit generous timeout, matching the convention already
            # used elsewhere for render-triggering calls (e.g.
            # test_correction_fallback_safety_net.py) - this button click
            # runs the full multi-step render pipeline (initial render,
            # correction pass, verify pass), each a separate mocked API
            # call. Streamlit AppTest's short default timeout was
            # observed to occasionally trip under full-suite CPU load
            # even with every call mocked (confirmed via repeated runs,
            # 23 Aug 2026 final testing pass) - not a functional issue,
            # just insufficient headroom for a multi-call pipeline
            # running alongside hundreds of other tests.
            at.run(timeout=15)
            assert not at.exception, f"App raised during render: {at.exception}"

            return at


def test_screen4_loads_with_seeded_baseline_no_exceptions():
    at = _run_screen4_with_mocked_render()
    assert at.session_state["screen"] == 4


# ------------------------------------------------------------------
# 17 Aug 2026 layout/feedback fix: "Write as me" button now disables
# itself immediately on click, before the (multi-second, multi-API-
# call) render pipeline runs - fixes reports of it "taking more than
# one click to fire", since previously the button stayed fully
# clickable with no visible change for the whole duration.
# ------------------------------------------------------------------

def _land_on_screen4_no_render(input_text: str = "Please write a short note about the launch plan."):
    """Same seeded-baseline setup as _run_screen4_with_mocked_render,
    but stops short of clicking the render button - for tests that
    need to inspect button/session-state right before or during the
    two-phase click, not after a render has already completed."""
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()

    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    metrics_1 = compute_baseline_metrics(BASELINE_SAMPLE_1)
    metrics_2 = compute_baseline_metrics(BASELINE_SAMPLE_2)

    at.session_state["screen"] = 4
    at.session_state["raw_text"] = BASELINE_SAMPLE_1
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["fingerprint_samples"] = [metrics_1, metrics_2]
    at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
    at.session_state["sample2_completions"] = ["", "", "", ""]

    at.run()
    assert not at.exception, f"App raised on Screen 4 load: {at.exception}"
    at.text_area[0].input(input_text)
    return at


def test_write_as_me_button_enabled_before_any_click():
    at = _land_on_screen4_no_render()
    write_button = next(b for b in at.button if "Write as me" in b.label)
    assert not write_button.disabled


def test_write_as_me_button_wired_to_disable_on_render_in_progress():
    """The core of the fix: the button's disabled= parameter must be
    tied to render_in_progress, so the button is disabled the instant
    that line executes - before the slow, multi-API-call render
    pipeline runs later in the same script pass.

    This can't be observed end-to-end through AppTest: it resolves
    the full st.rerun() chain (click -> set flag -> rerun -> render ->
    clear flag -> rerun) synchronously within a single .run() call, so
    by the time control returns to the test, the pipeline has already
    completed and the flag is already cleared - there's no hook to
    freeze on the intermediate disabled state the way a real browser
    would see it. In a real browser, Streamlit streams each element to
    the frontend as it's created during script execution, so the
    disabled button appears on screen immediately, well before the
    slow API call further down in that same script pass finishes -
    that's the actual mechanism the fix relies on, and it's a
    real, standard Streamlit pattern for exactly this problem.

    What IS reliably testable is that the wiring is actually there -
    checking the real source, not a hand-copied string, so this fails
    if the wiring is ever accidentally removed or reworded."""
    import inspect
    import app as app_module
    source = inspect.getsource(app_module.screen_render)
    assert 'disabled=render_in_progress' in source.replace(' ', '').replace('\n', ''), (
        "Expected the 'Write as me' button's disabled= parameter to "
        "be wired to render_in_progress in screen_render's source."
    )


def test_render_in_progress_flag_clears_after_render_completes():
    """Confirms the flag is a transient in-flight marker, not a stuck
    state - once the render pipeline finishes, the button must become
    clickable again for the next paste."""
    at = _run_screen4_with_mocked_render()
    assert at.session_state["render_in_progress"] is False
    write_button = next(b for b in at.button if "Write as me" in b.label)
    assert not write_button.disabled


def test_click_sets_render_in_progress_and_completes_the_render():
    """End-to-end: a real click still results in a completed render
    (the two-phase mechanism doesn't break the actual render), and by
    the time control returns the flag is correctly cleared."""
    at = _run_screen4_with_mocked_render()
    assert at.session_state["render_output"], "Render did not complete"
    assert at.session_state["render_in_progress"] is False


def test_render_output_passed_through_real_guardrail_sweep():
    # Proves the plausibility-shield fix (and the rest of the sweep)
    # actually runs in the live app pipeline, not just in isolated
    # voice_engine/prompts unit tests.
    at = _run_screen4_with_mocked_render()
    output = at.session_state["render_output"]

    assert "\u2014" not in output, "Em dash survived the sweep"
    assert "leverage" not in output.lower(), "Claude construction survived the sweep"
    assert "I see it as" not in output, "Plausibility shield survived the sweep"


def test_function_word_delta_populated_with_two_baseline_samples():
    at = _run_screen4_with_mocked_render()
    fwd = at.session_state["function_word_delta"]

    assert fwd is not None
    assert fwd["delta"] is not None
    assert fwd["tier"] in ("Close", "Moderate", "Wide")


def test_function_word_delta_flows_into_voice_report():
    at = _run_screen4_with_mocked_render()
    fwd = at.session_state["function_word_delta"]
    report = at.session_state["voice_report"]

    assert report["function_word_delta"] == fwd["delta"]
    assert report["function_word_delta_tier"] == fwd["tier"]
    assert report["function_word_biggest_divergences"] == fwd["biggest_divergences"]


def test_function_word_delta_detects_sharp_register_divergence():
    # The fake output uses zero first-person language against a baseline
    # heavy on "I think" / "I want" / "I believe" — this should register
    # as a real, large divergence, not a near-zero score.
    at = _run_screen4_with_mocked_render()
    fwd = at.session_state["function_word_delta"]

    assert fwd["delta"] > 1.5  # outside the "Close" band
    divergent_words = {d["word"] for d in fwd["biggest_divergences"]}
    assert "i" in divergent_words


def test_insufficient_baseline_samples_handled_without_crashing():
    # Fewer than 2 baseline samples (the common case for anyone who
    # hasn't gone through Screen 3) must not crash the render — it
    # should report the honest "Insufficient baseline samples" tier.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(
                "A plain, clean sentence with nothing unusual in it."
            )

            at = AppTest.from_file(_APP_PATH)
            at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
            at.run()

            at.session_state["screen"] = 4
            at.session_state["raw_text"] = BASELINE_SAMPLE_1
            at.session_state["baseline_fingerprint"] = compute_baseline_metrics(BASELINE_SAMPLE_1)
            at.session_state["observations"] = analyse_writing(BASELINE_SAMPLE_1)
            at.session_state["sample_fitness"] = _score_sample_fitness(BASELINE_SAMPLE_1)
            at.session_state["fingerprint_samples"] = [compute_baseline_metrics(BASELINE_SAMPLE_1)]
            at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1]  # only 1
            at.session_state["sample2_completions"] = ["", "", "", ""]

            at.run()
            at.text_area[0].input("Some AI text to rewrite here.")
            at.button[0].click()
            at.run()

            assert not at.exception, f"App raised with only 1 baseline sample: {at.exception}"
            fwd = at.session_state["function_word_delta"]
            assert fwd["delta"] is None
            assert fwd["tier"] == "Insufficient baseline samples"
