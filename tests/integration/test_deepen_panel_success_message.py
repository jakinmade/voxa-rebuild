"""
Integration coverage for the deepen-fingerprint panel's success
message actually reaching the screen — a real bug found this session,
reported by the user as "adding one more sample element appears not
to be working."

Root cause, confirmed by direct diagnosis before fixing: the
underlying logic worked correctly the whole time (baseline, word
count, and dimension_stability all updated genuinely), but
st.success("Added...") was immediately followed by st.rerun(), which
wipes any UI drawn in that run before the browser ever paints it — the
same class of bug this codebase had already fixed once for the
render-error path, just not applied here.

Fix: the success message is stored in session_state (survives the
rerun) and displayed + cleared at the top of any screen that can host
the deepen panel, rather than displayed inline before the rerun that
wipes it. Displayed at the CALLER level deliberately, not inside
_deepen_fingerprint_panel itself, because adding a sample can resolve
the confidence caveat that gates the panel on Screen 4 — the message
must still show even when the panel that produced it no longer
renders on the next run.
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

SAMPLE_TEXT = (
    "Sarah, I have been going back and forth on the Meridian contract all week and "
    "honestly I still do not have a clean answer for the board. The renewal numbers "
    "Tom sent look fine on paper but something about the March timeline bothers me. "
    "We told the Meridian team Q4 in the June call, we meant Q4, and now Tom is "
    "acting like March was always on the table. It was not, and I have the email "
    "thread from June 14th to prove it. I would rather raise that plainly with Tom now "
    "than have it come up sideways in Thursday board meeting, especially with "
    "Priya from finance already asking pointed questions about the renewal margin. "
    "Please can you pull the June thread before the call. Also flagging that the "
    "Hartwell deal has the same shape, we quoted 90 days, they are now saying 120, "
    "and nobody on their side seems to remember agreeing to the shorter window. "
    "I have asked David twice for the signed order form and have not heard back."
)

DEEPEN_SAMPLE = (
    "Honestly I keep going back to this one point. We should not have to keep "
    "re-explaining the same thing every single quarter to the same people who "
    "already agreed to it the first time around."
)


def _click(at, label_substr):
    for b in at.button:
        if label_substr in b.label:
            b.click()
            return True
    return False


def _fake_response(text: str):
    # Single block satisfies both response shapes the pipeline uses:
    # .text for the plain-text calls (initial render, grammar-fix,
    # voice-profile-summary — unchanged), and .type/.input for the
    # correction call, which now uses a forced tool_choice response
    # (see prompts.CORRECTION_TOOL / app.py's correction-pass fix,
    # 18 Aug 2026). Without both, the correction call's tool_use
    # extraction finds nothing, silently retries, and inflates the
    # call count — exactly the regression this fix closes.
    block = MagicMock()
    block.text = text
    block.type = "tool_use"
    block.input = {"corrected_text": text}
    resp = MagicMock()
    resp.content = [block]
    resp.stop_reason = "tool_use"
    return resp


def _reach_screen4_with_low_stability(monkeypatch):
    """Onboards through the real UI to Screen 4, then forces the
    low-stability condition that triggers confidence_caveat() (matching
    the real report that surfaced this bug: Low confidence, 'your two
    samples read pretty differently from each other') so the caveat-
    framed deepen panel actually renders."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")

    with patch("anthropic.Anthropic") as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.messages.create.return_value = _fake_response(
            "I see it as the clearest way forward for the team on this."
        )

        at = AppTest.from_file(_APP_PATH)
        at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
        at.run(timeout=15)
        at.text_area[0].set_value(SAMPLE_TEXT)
        at.button[0].click()
        at.run(timeout=15)
        assert _click(at, "Continue")
        at.run(timeout=15)
        completions = at.session_state["sample2_completions"]
        completions[0] = "This completely misses what I actually asked for, and I need to say so plainly."
        completions[3] = "Honestly this has been bothering me all afternoon and I can't quite let it go."
        at.session_state["sample2_completions"] = completions
        at.run(timeout=15)
        assert _click(at, "Continue")
        at.run(timeout=15)
        assert at.session_state["screen"] == 4

        at.text_area[0].set_value("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run(timeout=15)
        assert not at.exception

        # Force the caveat condition directly rather than hunting for
        # starter phrasing that happens to produce it organically —
        # this is what the real report showed (Low confidence, samples
        # reading differently), reproduced deterministically.
        at.session_state["dimension_stability"] = {
            "sample_count": 2, "stable_count": 1, "volatile_count": 3,
        }
        at.run(timeout=15)

    return at


def test_deepen_panel_data_genuinely_updates(monkeypatch):
    """The underlying logic was never broken — confirms that first,
    separately from the visibility question, so a future regression
    here is diagnosed as a UI or a data problem correctly."""
    at = _reach_screen4_with_low_stability(monkeypatch)

    words_before = at.session_state["cumulative_words"]
    stability_before = at.session_state["dimension_stability"]["sample_count"]

    deepen_area = next(ta for ta in at.text_area if ta.key == "deepen_text")
    deepen_area.set_value(DEEPEN_SAMPLE)
    at.run(timeout=15)
    next(b for b in at.button if b.key == "deepen_submit").click()
    at.run(timeout=15)

    assert not at.exception, f"Deepen-panel submit raised: {at.exception}"
    assert at.session_state["cumulative_words"] > words_before
    assert at.session_state["dimension_stability"]["sample_count"] > stability_before


def test_deepen_panel_success_message_survives_the_rerun(monkeypatch):
    """
    The actual bug: st.success() called immediately before st.rerun()
    was wiped before ever reaching the screen. This is the direct
    regression guard — confirms the message is genuinely present in
    the rendered output after the rerun, not just that the underlying
    data changed.
    """
    at = _reach_screen4_with_low_stability(monkeypatch)

    deepen_area = next(ta for ta in at.text_area if ta.key == "deepen_text")
    deepen_area.set_value(DEEPEN_SAMPLE)
    at.run(timeout=15)
    next(b for b in at.button if b.key == "deepen_submit").click()
    at.run(timeout=15)

    assert not at.exception
    success_texts = [s.value for s in at.success]
    assert any("Added" in s for s in success_texts), (
        f"Expected the success message to survive the rerun and appear "
        f"on screen, found success elements: {success_texts}"
    )


def test_deepen_panel_success_message_clears_after_one_display(monkeypatch):
    """The message must not persist forever — it should show once,
    then clear, so it doesn't linger on screen through unrelated later
    reruns (e.g. clicking something else afterward)."""
    at = _reach_screen4_with_low_stability(monkeypatch)

    deepen_area = next(ta for ta in at.text_area if ta.key == "deepen_text")
    deepen_area.set_value(DEEPEN_SAMPLE)
    at.run(timeout=15)
    next(b for b in at.button if b.key == "deepen_submit").click()
    at.run(timeout=15)
    assert any("Added" in s.value for s in at.success)

    # A further, unrelated rerun (session_state mutation + rerun,
    # simulating any subsequent interaction) should not show it again.
    at.run(timeout=15)
    assert not any("Added" in s.value for s in at.success), (
        "Expected the success message to have cleared after its first display"
    )
