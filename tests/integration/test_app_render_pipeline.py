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
            at.run()
            assert not at.exception, f"App raised during render: {at.exception}"

            return at


def test_screen4_loads_with_seeded_baseline_no_exceptions():
    at = _run_screen4_with_mocked_render()
    assert at.session_state["screen"] == 4


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
