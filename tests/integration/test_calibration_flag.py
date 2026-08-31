"""
Calibration flag/confirm (31 Aug 2026) — the confirm/flag interaction
that follows the calibration confidence table (test_reveal_calibration_
confidence.py). Lets a person mark a dimension "doesn't sound like me"
on the reveal screen; this demotes that dimension's Confidence badge
one tier (compute_dimension_confidence, voice_engine.py) and surfaces
a soft caveat on Check a Draft — never a hard block, and never a
hand-edit of the underlying baseline value itself.
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

_STABLE_DIMENSIONS = {
    "hedge_density": "stable",
    "sentence_length_sd": "stable",
    "first_person_ratio": "stable",
    "directive_ratio": "stable",
}


def _seed_reveal_screen(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = 2
    at.session_state["word_count"] = len(combined.split())
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["dimension_stability"] = {
        "dimensions": dict(_STABLE_DIMENSIONS),
        "stable_count": 4, "volatile_count": 0, "sample_count": 2,
    }


def test_flag_control_renders_on_reveal_screen():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.run()
    assert not at.exception, f"Reveal raised on load: {at.exception}"

    body = " ".join(m.value for m in at.markdown)
    assert "Anything above not sound like you?" in body
    multiselects = [w for w in at.multiselect if w.key == "calibration_flag_select"]
    assert multiselects, "Expected the flag multiselect on the reveal screen"


def test_flagging_a_dimension_demotes_its_confidence_badge():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    # Enough overall evidence to be Medium-eligible so the demotion is
    # visible (Medium -> Low) rather than already floored at Low.
    at.session_state["baseline_fingerprint"]["word_count"] = 900
    at.session_state["sample_fitness"] = {"tier": "strong"}
    at.run()
    assert not at.exception

    ms = next(w for w in at.multiselect if w.key == "calibration_flag_select")
    ms.set_value(["Hedging"]).run()
    assert not at.exception
    assert st_session_flagged(at) == ["hedge_density"]

    body = " ".join(m.value for m in at.markdown)
    assert "Hedging \u2691" in body


def st_session_flagged(at: AppTest):
    return list(at.session_state["flagged_dimensions"] or [])


def test_unflagging_clears_the_flag():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.session_state["flagged_dimensions"] = ["hedge_density"]
    at.run()
    assert not at.exception

    ms = next(w for w in at.multiselect if w.key == "calibration_flag_select")
    assert ms.value == ["Hedging"]
    ms.set_value([]).run()
    assert not at.exception
    assert st_session_flagged(at) == []


def test_flag_control_does_not_break_continue_flow():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_reveal_screen(at)
    at.run()
    assert not at.exception

    buttons = [b.label for b in at.button]
    assert any("Continue" in b for b in buttons)
    assert any("Start over" in b for b in buttons)


def _seed_check_draft_screen(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = 8
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
    at.session_state["_device_id"] = "test-device-1"


def test_check_draft_shows_caveat_when_a_dimension_is_flagged():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.session_state["flagged_dimensions"] = ["hedge_density"]
    at.run()
    at.text_area(key="check_draft_input").set_value(BASELINE_SAMPLE_1)
    at.button(key="check_draft_submit").click()
    at.run()
    assert not at.exception

    body = " ".join(m.value for m in at.markdown)
    assert "flagged some of your voice readings" in body


def test_check_draft_has_no_caveat_when_nothing_flagged():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.run()
    at.text_area(key="check_draft_input").set_value(BASELINE_SAMPLE_1)
    at.button(key="check_draft_submit").click()
    at.run()
    assert not at.exception

    body = " ".join(m.value for m in at.markdown)
    assert "flagged some of your voice readings" not in body


def test_check_draft_is_not_blocked_by_a_flag():
    """The caveat is soft — a flagged dimension must not prevent the
    verdict banner or result from rendering at all."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.session_state["flagged_dimensions"] = ["hedge_density", "directive_ratio"]
    at.run()
    at.text_area(key="check_draft_input").set_value(BASELINE_SAMPLE_1)
    at.button(key="check_draft_submit").click()
    at.run()
    assert not at.exception
    assert st_session_result_present(at)


def st_session_result_present(at: AppTest) -> bool:
    return bool(at.session_state["check_draft_result"])
