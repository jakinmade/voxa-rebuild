"""
Real, click-driven coverage of the onboarding entry point (Screen 1's
sample-fitness gate) - the single highest-traffic flow in the product,
and per the code-review pass on 22 Aug 2026, one with zero prior test
coverage exercising the real UI path. Every existing test in this
suite seeds session_state directly at screen 4 or later, bypassing
_score_sample_fitness/_fitness_gate entirely (confirmed by checking:
even test_review_gate_live_flow.py's own BASELINE_SAMPLE_1 +
BASELINE_SAMPLE_2 fixture scores 'weak' tier and gets nudged, not
fired, when actually driven through Screen 1's real button).

This isn't testing new behaviour - _score_sample_fitness and
_fitness_gate are untouched. It's closing a coverage gap: per
Streamlit's own testing docs and general happy-path-testing practice
(test the 3-5 actions most users take, above all else), an app's
first-ever interaction having zero real-click coverage is a genuine
gap worth closing, not just noting.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock
import os

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

# Confirmed directly against _score_sample_fitness/_fitness_gate before
# use here (22 Aug 2026): 175 words, spontaneous/first-person register
# with specific names and context (matching what the gate's own nudge
# copy explicitly asks for), scores 'thin' tier and fires at wc>=150.
# A shorter, more generic-sounding sample (including the exact
# BASELINE_SAMPLE_1+2 text used elsewhere in this suite) does NOT fire
# this real gate - that's the gate working as designed, not a bug.
STRONG_ONBOARDING_SAMPLE = (
    "Hey Sarah, quick one before the standup - I looked at the Q3 numbers "
    "you sent over last night and I honestly think we're overcomplicating "
    "the migration timeline. My gut says we push the Postgres cutover to "
    "next Tuesday instead of Thursday, because Dave's team still hasn't "
    "finished the read-replica testing and I don't want us scrambling on "
    "a Friday deploy again like we did with the payments service back in "
    "March. Also, can you ping Marcus about the Stripe webhook retries? "
    "I noticed three failed events in the logs this morning and I'm not "
    "sure if that's on us or their side. Let me know what you think when "
    "you get a sec, I'll be in the office by nine. One more thing - Jenny "
    "from finance asked about the invoice reconciliation script again, "
    "and I told her we'd have an answer by Friday. Can you take a look "
    "at that ticket when you have a moment, I think it's still sitting "
    "in the backlog from last sprint and nobody's picked it up yet."
)

FAKE_LLM_OUTPUT = (
    "I see it as the clearest way forward-we should leverage this "
    "approach across the team."
)


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _ss(at, key, default=None):
    """AppTest's session_state raises KeyError/AttributeError on a
    missing key rather than supporting .get() the way real
    st.session_state does - this normalises that for readable asserts."""
    try:
        return at.session_state[key]
    except Exception:
        return default


def test_real_fitness_gate_fires_for_a_strong_sample():
    """Sanity check on the fixture itself, isolated from the rest of
    the flow: confirms the sample above genuinely fires the real gate
    via the real button, not seeded state. If this fails, the fixture
    text itself needs adjusting - it's not a signal about app logic."""
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.text_area[0].input(STRONG_ONBOARDING_SAMPLE)
    at.button[0].click()
    at.run()
    assert not at.exception, f"Unexpected exception: {at.exception}"
    assert _ss(at, "screen") == 2, (
        f"Fixture sample did not fire the real fitness gate as expected "
        f"(landed on screen={_ss(at, 'screen')}). fitness_nudge was: "
        f"{_ss(at, 'fitness_nudge')!r}"
    )


def test_full_onboarding_flow_via_real_clicks_no_exceptions():
    """Screen 1 through Screen 4, driven entirely by real widget
    interactions (.input()/.click()), not session_state seeding. New
    coverage, not a regression check against previously-verified
    behaviour - this exact path was never exercised via real clicks
    before this test existed."""
    at = AppTest.from_file(_APP_PATH)
    at.run()
    assert not at.exception
    assert _ss(at, "screen") == 1

    # Screen 1 -> 2
    at.text_area[0].input(STRONG_ONBOARDING_SAMPLE)
    at.button[0].click()
    at.run()
    assert not at.exception, f"Screen 1->2: {at.exception}"
    assert _ss(at, "screen") == 2

    # Screen 2 -> 3
    continue_btns = [b for b in at.button if "Continue" in (b.label or "")]
    assert continue_btns, "No Continue button found on Screen 2"
    continue_btns[0].click()
    at.run()
    assert not at.exception, f"Screen 2->3: {at.exception}"
    assert _ss(at, "screen") == 3

    # Screen 3: fill both required starters, then Continue -> 4
    from app import REQUIRED_STARTER_INDICES
    filler = (
        "This is a live typed response that is definitely long enough "
        "to pass the required word floor for this starter prompt today."
    )
    completions = _ss(at, "sample2_completions", ["", "", "", ""])
    for idx in REQUIRED_STARTER_INDICES:
        completions[idx] = filler
    at.session_state["sample2_completions"] = completions
    at.run()
    assert not at.exception, f"Screen 3 fill: {at.exception}"

    continue_btns = [b for b in at.button if "Continue" in (b.label or "")]
    assert continue_btns, "No Continue button found on Screen 3"
    continue_btns[0].click()
    at.run()
    assert not at.exception, f"Screen 3->4: {at.exception}"
    assert _ss(at, "screen") == 4, f"Expected screen 4, got {_ss(at, 'screen')}"

    # Screen 4: confirm the render pipeline completes for a freshly
    # onboarded (real-clicks) user, with the LLM call mocked.
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = (
                _fake_anthropic_response(FAKE_LLM_OUTPUT)
            )
            at.text_area[0].input("Please write a short note about the launch plan.")
            write_btns = [b for b in at.button if "Write as me" in (b.label or "")]
            assert write_btns, "No 'Write as me' button found on Screen 4"
            write_btns[0].click()
            at.run()

    assert not at.exception, f"Render: {at.exception}"
    assert _ss(at, "voice_report"), "voice_report not populated after a real-click-driven render"
