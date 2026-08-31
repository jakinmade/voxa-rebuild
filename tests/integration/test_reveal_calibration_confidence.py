"""
Calibration confidence on the reveal screen (screen 2) — 31 Aug 2026.

Before this, screen_reveal ("Your voice.") showed observations with no
indication of how solid each one was; the baseline got accepted on faith
before Voice Drift ever checked anything against it. This reuses the
exact per-dimension table already shipped on screen_my_voice
(_render_dimension_confidence_table) one step earlier, at the point the
baseline actually forms. Same AppTest-driving-the-real-screen pattern as
test_my_voice_screen.py, so this exercises the shared helper through
both call sites rather than trusting it in isolation.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

BASELINE_SAMPLE_1 = (
    "I think we should move fast on this. I want the team to focus on "
    "the core problem first, and then we can look at the edges."
)
BASELINE_SAMPLE_2 = (
    "I believe the data backs this up, and I think it is the right call "
    "for now. We need to move quickly and stay focused."
)


def _seed_reveal_screen(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = 2
    at.session_state["word_count"] = len(combined.split())
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)


def test_reveal_shows_calibration_confidence_when_stability_available():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.session_state["dimension_stability"] = {
        "dimensions": {
            "hedge_density": "stable",
            "sentence_length_sd": "volatile",
            "first_person_ratio": "stable",
            "directive_ratio": "insufficient_data",
        },
        "stable_count": 2, "volatile_count": 1, "sample_count": 2,
    }
    at.run()
    assert not at.exception, f"Reveal raised on load: {at.exception}"

    body = " ".join(m.value for m in at.markdown)
    assert "How solid this baseline is so far" in body
    assert "Confidence</th>" in body
    assert "Varies by register" in body


def test_reveal_hides_calibration_confidence_without_stability_data():
    """First-sample reveal with no fingerprint_samples history yet —
    the table must not appear (and must not crash), same gating as
    screen_my_voice."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.run()
    assert not at.exception, f"Reveal raised with no stability data: {at.exception}"

    body = " ".join(m.value for m in at.markdown)
    assert "How solid this baseline is so far" not in body


def test_reveal_calibration_confidence_does_not_break_continue_flow():
    """The new table must not disturb the existing Continue button or
    observations rendering on this screen."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.session_state["dimension_stability"] = {
        "dimensions": {
            "hedge_density": "stable",
            "sentence_length_sd": "stable",
            "first_person_ratio": "stable",
            "directive_ratio": "stable",
        },
        "stable_count": 4, "volatile_count": 0, "sample_count": 2,
    }
    at.run()
    assert not at.exception

    buttons = [b.label for b in at.button]
    assert any("Continue" in b for b in buttons)
    assert any("Start over" in b for b in buttons)
