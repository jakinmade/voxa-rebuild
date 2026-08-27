"""
Regression test for Section 15.2 item 2 (engineering review response,
resolved 21 Aug 2026): "one user render = original generation + its
included refinement, one lifetime-counter decrement, not two."

Confirmed as a real bug before this fix, not hypothetical: _run_render
called check_and_reserve_lifetime_render unconditionally, with no gate
on is_refinement, so a person's one free refinement of a render silently
cost a second slot out of their 15 lifetime renders. This test locks in
the fix: the reserve call fires once, for the original render only; the
refinement is included in that same reservation. Same mocking approach
as test_step3_step4_wiring.py - only the Anthropic API call and Supabase
client are mocked.
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


def _clear_review_gate_if_present(at: AppTest):
    """If this render's flagged risk gated the output behind the
    'I've reviewed the report above' checkbox, clear it so show_output
    becomes True and the Refine button can render - mirrors what a
    real user does, not a workaround around real app behaviour."""
    checkbox = next((c for c in at.checkbox if c.key and c.key.startswith("confirm_checkbox_")), None)
    if checkbox is not None:
        checkbox.check().run()
        confirm_button = next((b for b in at.button if b.key and b.key.startswith("confirm_button_")), None)
        if confirm_button is not None:
            confirm_button.click().run()


def _seed_screen4(at: AppTest):
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
    at.session_state["_device_id"] = "test-device-1"


def test_refinement_does_not_reserve_a_second_lifetime_render():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("anthropic.Anthropic") as mock_anthropic_cls, \
         patch("render_history.get_supabase_client", return_value=MagicMock()), \
         patch(
             "lifetime_cap.check_and_reserve_lifetime_render",
             return_value=(True, 1, 15),
         ) as mock_reserve:
        mock_client = mock_anthropic_cls.return_value
        mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        # Original render.
        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()
        assert not at.exception, f"App raised during original render: {at.exception}"
        assert mock_reserve.call_count == 1
        _clear_review_gate_if_present(at)

        # Refinement of that same render - must not call the reserve
        # function again.
        refine_button = next(
            (b for b in at.button if b.label == "Refine \u2192"), None
        )
        assert refine_button is not None, "Refine button not present after a successful render"
        refine_button.click()
        at.run()
        assert not at.exception, f"App raised during refinement: {at.exception}"

        # Still exactly one reservation - the refinement must be
        # covered by the original render's reserved slot, not a second
        # draw against the person's 15.
        assert mock_reserve.call_count == 1


def test_refinement_failure_does_not_release_a_reservation_it_never_made():
    # A refinement that fails must not call release_reserved_lifetime_
    # render - it never reserved anything in the first place, so
    # releasing one would wrongly hand back a slot from the earlier,
    # successful original render instead.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("anthropic.Anthropic") as mock_anthropic_cls, \
         patch("render_history.get_supabase_client", return_value=MagicMock()), \
         patch(
             "lifetime_cap.check_and_reserve_lifetime_render",
             return_value=(True, 1, 15),
         ), \
         patch("lifetime_cap.release_reserved_lifetime_render") as mock_release:
        mock_client = mock_anthropic_cls.return_value
        # _run_render makes more than one messages.create call per
        # render (voice_profile_summary generation, the render itself,
        # and potentially a correction pass). Rather than counting exact
        # call positions - fragile, changes if the correction path's
        # call count ever changes - the test flips a shared flag right
        # before clicking Refine, so every call before that point
        # succeeds (covers the whole original render) and every call
        # from that point on fails (covers the whole refinement),
        # regardless of how many calls each phase internally makes.
        should_fail = {"flag": False}

        def _create_side_effect(*args, **kwargs):
            if should_fail["flag"]:
                raise Exception("simulated API failure")
            return _fake_anthropic_response(FAKE_LLM_OUTPUT)

        mock_client.messages.create.side_effect = _create_side_effect

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()
        assert not at.exception
        _clear_review_gate_if_present(at)

        refine_button = next(
            (b for b in at.button if b.label == "Refine \u2192"), None
        )
        assert refine_button is not None, "Refine button not present after a successful render"
        should_fail["flag"] = True
        refine_button.click()
        at.run()
        assert not at.exception, f"App raised during failed refinement: {at.exception}"

        mock_release.assert_not_called()


# ---------------------------------------------------------------------
# Regression test for the render-cap ordering fix (27 Aug 2026 hardening
# pass, independent codebase review finding #4): check_and_reserve_
# lifetime_render is now called BEFORE check_and_reserve_render (the
# daily spend cap), not after. render_cap.py's own docstring says the
# daily counter is reserved optimistically, before any API call, "so a
# render that fails partway through still counts" - with the old
# ordering, a free user who'd already used all 15 lifetime renders
# would still reserve a daily-cap slot on every retry attempt, even
# though the lifetime check right after would immediately block the
# render with zero API calls made. This locks in the fix: when the
# lifetime cap denies, the daily cap must never be touched at all.
# ---------------------------------------------------------------------

def test_lifetime_cap_denial_never_reserves_the_daily_cap():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("anthropic.Anthropic") as mock_anthropic_cls, \
         patch(
             "lifetime_cap.check_and_reserve_lifetime_render",
             return_value=(False, 15, 15),
         ), \
         patch("render_cap.check_and_reserve_render") as mock_daily_reserve:
        mock_client = mock_anthropic_cls.return_value
        mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        assert not at.exception, f"App raised on a lifetime-cap-denied render: {at.exception}"
        assert at.session_state["render_paywall_hit"] is True
        mock_daily_reserve.assert_not_called()


def test_lifetime_cap_allowed_still_reserves_the_daily_cap():
    """The fix must not accidentally skip the daily reservation for a
    render that's actually allowed to proceed - only a denial should
    short-circuit before it."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}), \
         patch("anthropic.Anthropic") as mock_anthropic_cls, \
         patch("render_history.get_supabase_client", return_value=MagicMock()), \
         patch(
             "lifetime_cap.check_and_reserve_lifetime_render",
             return_value=(True, 1, 15),
         ), \
         patch(
             "render_cap.check_and_reserve_render",
             return_value=(True, 1, 500),
         ) as mock_daily_reserve:
        mock_client = mock_anthropic_cls.return_value
        mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        assert not at.exception
        mock_daily_reserve.assert_called_once()
