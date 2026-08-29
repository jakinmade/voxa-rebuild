"""
My Voice (screen 5) — Tier 1 v1, overall confidence only. Drives the
real screen_my_voice() via AppTest rather than trusting the unit-level
HTML builders in isolation, same pattern as
test_review_gate_live_flow.py. Confirms the screen renders without
exception on both a populated profile and an empty one, and that the
sidebar nav round-trips between Write and My Voice.
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


def _seed_established_profile(at: AppTest):
    combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
    at.session_state["screen"] = 5
    at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
    at.session_state["observations"] = analyse_writing(combined)
    at.session_state["sample_fitness"] = _score_sample_fitness(combined)
    at.session_state["confidence"] = "Medium"


def test_my_voice_renders_confidence_and_observations():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
    at.run()
    assert not at.exception, f"My Voice raised on load: {at.exception}"

    body = " ".join(m.value for m in at.markdown)
    assert "Medium" in body


def test_my_voice_handles_no_profile_without_crashing():
    """A person shouldn't be able to land on My Voice with nothing
    built yet (the sidebar nav is gated on baseline_fingerprint), but
    the screen itself must not crash if session state ever gets here
    some other way — same defensive standard as the rest of the app."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    at.session_state["screen"] = 5
    at.run()
    assert not at.exception, f"My Voice raised with no profile: {at.exception}"


def test_back_to_write_nav_returns_to_screen_4():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
    at.run()
    assert not at.exception

    at.sidebar.button[0].click()
    at.run()
    assert not at.exception, f"Nav back to Write raised: {at.exception}"
    assert at.session_state["screen"] == 4


def test_my_voice_shows_voice_profile_download_button():
    """Voice Profile Markdown export (29 Aug 2026) - same screen, same
    download_button pattern as the existing 'Share your Voice DNA'
    button just above it."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
    at.run()
    assert not at.exception

    download_labels = [b.label for b in at.download_button]
    assert "Download Voice Profile" in download_labels
    assert "Share your Voice DNA" in download_labels


def test_check_a_draft_is_second_in_shell_nav_not_last():
    """Positioning fix, 29 Aug 2026: 'Check a draft' used to be last
    (least visible) in the persistent shell sidebar; moved to second,
    right after Write. Locks in the order so it can't silently drift
    back."""
    from app import _SHELL_SCREENS
    screen_ids_in_order = [screen_id for screen_id, _ in _SHELL_SCREENS]
    assert screen_ids_in_order[1] == 8, (
        f"Expected 'Check a draft' (screen 8) second in nav order, got: {_SHELL_SCREENS}"
    )
    assert screen_ids_in_order[-1] != 8


def test_check_a_draft_headline_leads_with_check_anything():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
    at.session_state["screen"] = 8
    at.run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Check anything." in body


def test_my_voice_shows_stability_table_when_available():
    """Voice History (29 Aug 2026) - surfaces dimension_stability
    directly on screen, not just in the downloadable Voice Profile
    doc."""
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
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
    assert not at.exception

    body = " ".join(m.value for m in at.markdown)
    assert "Stability across your last 2 samples" in body
    assert "Hedging" in body
    assert "Sentence rhythm" in body
    assert "Varies by register" in body


def test_my_voice_hides_stability_table_when_no_stability_data():
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    _seed_established_profile(at)
    at.run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Stability across your last" not in body
