"""
Integration coverage for the live app.py (Streamlit), not the packages/
monorepo -- everything else in tests/integration and tests/unit targets
the dormant Sprint 1-3 packages, nothing previously covered the app
that's actually deployed.

Uses Streamlit's AppTest framework. Deliberately stops before the
render step (screen 4's live Anthropic call) -- that's a real API
call with real cost, and isn't worth automating into a test suite
that may run repeatedly. Run that one manually, once, per change.
"""
import json
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import voice_engine as ve

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

SAMPLE_TEXT = (
    "Sarah, I've been going back and forth on the Meridian contract all week and "
    "honestly I still don't have a clean answer for the board. The renewal numbers "
    "Tom sent look fine on paper but something about the March timeline bothers me. "
    "We told the Meridian team Q4 in the June call, we meant Q4, and now Tom's "
    "acting like March was always on the table. It wasn't, and I have the email "
    "thread from June 14th to prove it. I'd rather raise that plainly with Tom now "
    "than have it come up sideways in Thursday's board meeting, especially with "
    "Priya from finance already asking pointed questions about the renewal margin. "
    "Pls can you pull the June thread before the call. Also flagging that the "
    "Hartwell deal has the same shape - we quoted 90 days, they're now saying 120, "
    "and nobody on their side seems to remember agreeing to the shorter window. "
    "I've asked David twice for the signed order form and haven't heard back. "
    "Not trying to be difficult about this but I've been burned by loose verbal "
    "timelines before and I'm not doing it again on a deal this size. Will chase "
    "David again this afternoon. Cheers, John. Btw the numbers Priya wants for "
    "Thursday are in the shared folder, the 2024 renewal file not the 2023 one, "
    "Tom mixed them up last time and it cost us a full day re-checking three "
    "different spreadsheets before anyone noticed the mismatch."
)


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def test_screen3_requires_both_contrasting_starters_via_ui():
    """
    Drives the actual live Screen 3 UI - fills starters, clicks Continue -
    rather than force-setting session_state past it. Confirms the gate
    genuinely blocks on one required starter missing and genuinely passes
    with both filled, and that dimension_stability actually gets computed
    from the live click path, not just the harness mirror.
    """
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run(timeout=15)

    at.text_area[0].set_value(SAMPLE_TEXT)
    at.button[0].click()
    at.run(timeout=15)
    assert at.session_state["screen"] == 2

    assert _click(at, "Continue")
    at.run(timeout=15)
    assert at.session_state["screen"] == 3

    # Only the first required starter (index 0) filled - second required
    # starter (index 3) still empty. Continue must not advance the screen.
    # paste_guard is a custom JS component AppTest can't drive through its
    # widget API directly - set the value through session_state instead,
    # which is what the component's return value flows into on each rerun.
    completions = at.session_state["sample2_completions"]
    completions[0] = "This completely misses what I actually asked for, and I need to say so plainly."
    at.session_state["sample2_completions"] = completions
    at.run(timeout=15)
    assert _click(at, "Continue")
    at.run(timeout=15)
    assert at.session_state["screen"] == 3, "gate let the user through with only one required starter filled"

    # Fill the second required starter (index 3) too - now Continue
    # should genuinely advance.
    completions = at.session_state["sample2_completions"]
    completions[3] = "Honestly this has been bothering me all afternoon and I can't quite let it go."
    at.session_state["sample2_completions"] = completions
    at.run(timeout=15)
    assert _click(at, "Continue")
    at.run(timeout=15)
    assert not at.exception
    assert at.session_state["screen"] == 4

    stability = at.session_state["dimension_stability"]
    assert stability is not None
    assert stability["sample_count"] == 3
    assert at.session_state["starter_baseline"] is not None


def test_screen4_caveat_and_deepen_panel_resolve_low_confidence_via_ui():
    """
    Full loop, driven live: starters deliberately register-mismatched
    enough to trip the caveat -> caveat text and 'Firm up your
    fingerprint' panel appear on Screen 4 -> submitting one more sample
    through that panel actually grows dimension_stability and can move
    Confidence, closing the gap where the caveat pointed at an action
    with no way to take it.
    """
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run(timeout=15)

    at.text_area[0].set_value(SAMPLE_TEXT)
    at.button[0].click()
    at.run(timeout=15)
    assert at.session_state["screen"] == 2

    assert _click(at, "Continue")
    at.run(timeout=15)
    assert at.session_state["screen"] == 3

    completions = at.session_state["sample2_completions"]
    completions[0] = "Fix this. Redo it properly. Send it back today. Do not sit on it."
    completions[3] = (
        "I might be wrong about this but I think perhaps I could possibly be "
        "overreacting a little. I suppose I might just let it go for now maybe, "
        "though I am somewhat unsure whether that is right."
    )
    at.session_state["sample2_completions"] = completions
    at.run(timeout=15)
    assert _click(at, "Continue")
    at.run(timeout=15)
    assert at.session_state["screen"] == 4

    stability_before = at.session_state["dimension_stability"]
    assert stability_before["stable_count"] == 0
    caveat_before = ve.confidence_caveat(stability_before)
    assert caveat_before is not None

    # Seed a completed render (no live API call), same pattern as the
    # other no-API test, so the Voice Report card - and the caveat under
    # it - actually render.
    at.session_state["render_output"] = "Stand-in rendered text."
    at.session_state["render_input_text"] = "Stand-in AI draft."
    at.session_state["voice_report"] = {
        "voice_match": 80, "semantic_match": 90, "confidence": "Low", "risk": "Low",
        "biggest_changes": [], "ai_tell_clean": True, "ai_tell_flags": [],
    }
    at.session_state["intent_mode"] = "GET_IT_DONE"
    at.session_state["sample_fitness"] = {"tier": "strong"}
    at.run(timeout=15)
    assert not at.exception

    page_text = " ".join(m.value for m in at.markdown)
    assert "read pretty differently" in page_text
    assert any("Try one more sample" in e.label for e in at.expander)

    # Submit one more sample through the panel that appeared.
    at.text_area(key="deepen_text").set_value(
        "This is a perfectly ordinary extra sample of my own writing, long enough "
        "to clear the ten word floor the deepen panel requires before it accepts anything."
    )
    at.run(timeout=15)
    assert _click(at, "Add to my fingerprint")
    at.run(timeout=15)
    assert not at.exception

    stability_after = at.session_state["dimension_stability"]
    assert stability_after["sample_count"] == 4
    assert at.session_state["voice_report"]["confidence"] == at.session_state["confidence"]


def test_paste_to_fingerprint_to_export_no_api_call():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
    at.run()
    assert not at.exception

    # Screen 1 -> 2: paste a fitness-qualifying sample, fingerprint fires
    at.text_area[0].set_value(SAMPLE_TEXT)
    at.button[0].click()
    at.run()
    assert not at.exception
    assert at.session_state["screen"] == 2
    assert at.session_state["observations"]

    # Screen 2 -> 3
    assert _click(at, "Continue")
    at.run(timeout=15)
    assert not at.exception
    assert at.session_state["screen"] == 3

    # Seed a completed render (no live API call) to reach screen 4's UI
    at.session_state["screen"] = 4
    at.session_state["render_output"] = "Stand-in rendered text."
    at.session_state["render_input_text"] = "Stand-in AI draft."
    at.session_state["voice_report"] = None
    at.session_state["intent_mode"] = "GET_IT_DONE"
    at.run()
    assert not at.exception

    # Export button present and correctly labelled
    assert len(at.download_button) == 1
    assert at.download_button[0].label == "Export your profile"

    # Positioning copy visible on the page, not just in code
    page_text = " ".join(m.value for m in at.markdown)
    assert "Written as you. Not for you." in page_text


def test_export_profile_produces_correct_json():
    fake_state = {
        "session_start": "4 August 2026, 09:00",
        "locale": "uk",
        "cumulative_words": 243,
        "cumulative_docs": 1,
        "observations": [{"id": "hedging_signature", "headline": "You own your statements"}],
        "baseline_fingerprint": {"avg_sentence_length": 14.2},
    }
    with patch("storage.st") as mock_st:
        mock_st.session_state = fake_state
        import storage
        result = storage.export_profile()

    parsed = json.loads(result)
    assert parsed["words_analysed"] == 243
    assert parsed["fingerprint"] == fake_state["observations"]
    assert parsed["baseline"] == fake_state["baseline_fingerprint"]
