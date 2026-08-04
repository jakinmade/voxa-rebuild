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
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

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


def test_paste_to_fingerprint_to_export_no_api_call():
    at = AppTest.from_file("app.py")
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
    at.run()
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
    assert "The engine wrote as you. Not for you." in page_text


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
