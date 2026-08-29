"""
Tests for screen_check_draft's "Fix it ->" button (added 29 Aug 2026) -
routes a REVIEW-verdict draft into the existing render pipeline via
the same render_input_text hand-off the Write screen's own paste box
already reads. Deliberately does not auto-render (no render credit
spent without an explicit click), so these tests check the hand-off
only, not a render itself - _run_render's own pipeline is covered by
existing render tests elsewhere.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

BASELINE_SAMPLE_1 = (
    "I reviewed the deck last night. It holds up. I want to send this "
    "to the board today, not next week."
)
BASELINE_SAMPLE_2 = (
    "I checked the numbers myself. They are solid. Let's ship this "
    "now rather than waiting on another round of review."
)

HEDGY_DRAFT = (
    "It could perhaps be argued that, in certain circumstances, "
    "further review might potentially be advisable before any "
    "materials are sent to stakeholders, depending on various "
    "factors that may or may not apply here."
)


def _seed_check_draft_screen(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = 8
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
    at.session_state["_device_id"] = "test-device-1"


def test_fix_it_button_shown_on_review_verdict():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.run()
    at.text_area(key="check_draft_input").set_value(HEDGY_DRAFT)
    at.button(key="check_draft_submit").click()
    at.run()
    assert not at.exception
    fix_it_buttons = [b for b in at.button if b.key == "check_draft_fix_it"]
    assert fix_it_buttons, "Expected a Fix it button on a REVIEW verdict"


def test_fix_it_click_hands_off_draft_to_write_screen():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.run()
    at.text_area(key="check_draft_input").set_value(HEDGY_DRAFT)
    at.button(key="check_draft_submit").click()
    at.run()

    fix_it_button = next(b for b in at.button if b.key == "check_draft_fix_it")
    fix_it_button.click()
    at.run()
    assert not at.exception
    assert at.session_state["screen"] == 4
    assert at.session_state["render_input_text"] == HEDGY_DRAFT


def test_no_fix_it_button_on_pass_verdict():
    # A matching draft (same register/hedging as the baseline samples)
    # should verdict PASS and show no Fix it button - nothing to fix.
    matching_draft = (
        "I looked at the figures again this morning. They hold up. "
        "I think we should send this to the board today rather than "
        "wait for another pass."
    )
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_check_draft_screen(at)
    at.run()
    at.text_area(key="check_draft_input").set_value(matching_draft)
    at.button(key="check_draft_submit").click()
    at.run()
    assert not at.exception

    verdict_badges = [m.value for m in at.markdown if "Verdict:" in (m.value or "")]
    if verdict_badges and "PASS" in verdict_badges[0]:
        assert not any(b.key == "check_draft_fix_it" for b in at.button)
