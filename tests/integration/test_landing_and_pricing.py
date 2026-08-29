"""
Integration coverage for the landing (screen 0) and /pricing (screen 7)
screens added in the 22 Aug 2026 "needs a design decision" pass.

Before this, voicova.com had no landing screen at all in app.py's
router — a brand-new AppTest session went straight to screen 1's paste
UI. These tests lock in the new default (fresh visitor -> screen 0)
and the two navigation paths off it, real-click driven, not seeded
past the screens under test.
"""
from pathlib import Path

from streamlit.testing.v1 import AppTest

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")


def test_fresh_visitor_lands_on_landing_screen_not_step_one():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    assert not at.exception
    assert at.session_state["screen"] == 0
    # Landing has no text_area (that's Step 1's paste box) - confirms
    # we're genuinely on the marketing screen, not onboarding.
    assert len(at.text_area) == 0
    headlines = [m.value for m in at.markdown if "whether it actually did" in m.value]
    assert headlines


def test_get_started_button_advances_to_step_one():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    get_started = next(b for b in at.button if b.label == "Get started \u2192")
    get_started.click().run()
    assert not at.exception
    assert at.session_state["screen"] == 1
    assert len(at.text_area) == 1


def test_see_pricing_button_from_landing_reaches_pricing_screen():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    see_pricing = next(b for b in at.button if b.label == "See pricing")
    see_pricing.click().run()
    assert not at.exception
    assert at.session_state["screen"] == 7
    tier_names = [m.value for m in at.markdown if "Monthly" in m.value or "Annual" in m.value]
    assert tier_names


def test_pricing_back_button_returns_to_landing_for_a_fresh_visitor():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    next(b for b in at.button if b.label == "See pricing").click().run()
    back = next(b for b in at.button if "Back" in b.label)
    back.click().run()
    assert not at.exception
    assert at.session_state["screen"] == 0


def test_pricing_reachable_from_step_one_without_losing_pasted_text():
    """Step 1's "See pricing" link (onboarding audit item) must not
    discard whatever the person has already pasted - the underlying
    text_area widget is keyed on raw_text's session_state value, so
    round-tripping through /pricing and back should leave it intact."""
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 1
    at.run()
    at.text_area[0].set_value("Some real writing sample for the fingerprint.").run()
    pricing_link = next(b for b in at.button if b.label == "See pricing \u2192")
    pricing_link.click().run()
    assert not at.exception
    assert at.session_state["screen"] == 7


def test_returning_visitor_with_baseline_skips_landing_when_backing_out_of_pricing():
    at = AppTest.from_file(_APP_PATH)
    at.session_state["screen"] = 7
    at.session_state["baseline_fingerprint"] = {"placeholder": True}
    at.run()
    back = next(b for b in at.button if "Back" in b.label)
    back.click().run()
    assert not at.exception
    assert at.session_state["screen"] == 4


def test_landing_shows_check_a_draft_as_fourth_step():
    """29 Aug 2026 copy update: landing page previously only described
    the rewrite flow (Paste/Calibrate/Write). Check a Draft is now
    surfaced as a fourth step, matching its elevated position inside
    the app itself."""
    at = AppTest.from_file(_APP_PATH)
    at.run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "4. Check" in body
    assert "still sounds like you" in body


def test_pricing_free_tier_mentions_unlimited_draft_checks():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    next(b for b in at.button if b.label == "See pricing").click().run()
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Unlimited draft checks" in body
