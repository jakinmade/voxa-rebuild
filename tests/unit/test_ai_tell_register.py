"""
Tests for the analytical-register AI-tell detection added on top of the
existing corporate-register check in voice_engine.py / prompts.py.

Background: score_ai_tells and the claude_constructions rewrite sweep
were both built against a single tell vocabulary tuned for corporate
marketing slop (leverage, synergy, robust, seamless). Neither caught a
different tell vocabulary that shows up in analytical/argumentative
writing — abstract nouns used as verbs (drift, surface, land on),
essay connectives (worth noting, to be fair), and the fragment-as-
emphasis tic ("Distinct stage. Not a subdivision."). A real example
sailed through score_ai_tells as "Clean" with "drift" still present.
This file locks in the fix and guards against regression on the
existing corporate-register behaviour.

Before this file, voice_engine.py and prompts.py had zero test
coverage of any kind.
"""
import re

import prompts as pr
import voice_engine as ve


ANALYTICAL_EXAMPLE = (
    "Distinct stage, and I think you have found the gap rather than a "
    "subdivision of one.\n"
    "My test for whether two controls are really one is whether they "
    "fail the same way. These do not.\n"
    "A governance failure is loud. An agent does something it should "
    "not have, and there is an incident, a trace and someone to ask. "
    "A qualification failure is silent. The system runs correctly for "
    "eighteen months, every action inside policy, every log clean, "
    "and it should never have been deployed for that purpose in the "
    "first place. Nobody finds it through monitoring, because "
    "monitoring confirms it is behaving exactly as built. It surfaces "
    "when someone finally asks who decided this was suitable, and the "
    "answer turns out to be nobody in particular.\n"
    "Where I would push back slightly, or at least add friction. "
    "An agent's context drifts underneath it. So I suspect "
    "qualification is not a gate but a gate plus an expiry, which is "
    "closer to how banks handle model revalidation than to how anyone "
    "currently handles software."
)

CORPORATE_EXAMPLE = (
    "We need to leverage our synergies to deliver a seamless, robust "
    "solution that will elevate the customer experience going forward. "
    "This holistic, paradigm-shifting approach is truly game-changing."
)

GENUINELY_CLEAN_EXAMPLE = (
    "I met Dave for coffee yesterday. He is still annoyed about the "
    "parking fine. We talked football for a bit, then I headed home."
)


class TestRegisterClassification:
    def test_analytical_text_classified_as_analytical(self):
        assert ve._classify_register(ANALYTICAL_EXAMPLE) == "analytical"

    def test_corporate_text_classified_as_corporate(self):
        assert ve._classify_register(CORPORATE_EXAMPLE) == "corporate"

    def test_empty_text_falls_back_to_mixed(self):
        assert ve._classify_register("") == "mixed"
        assert ve._classify_register("   ") == "mixed"


class TestAnalyticalTellDetection:
    """
    The specific regression this whole change exists to fix: an
    analytical-register AI tell ("drift") that previously passed
    score_ai_tells as clean=True.
    """

    def test_drift_no_longer_passes_as_clean(self):
        result = ve.score_ai_tells(ANALYTICAL_EXAMPLE)
        assert result["clean"] is False

    def test_drift_specifically_flagged(self):
        result = ve.score_ai_tells(ANALYTICAL_EXAMPLE)
        flagged_text = " ".join(result["flagged"]).lower()
        assert "drift" in flagged_text

    def test_register_reported_as_analytical(self):
        result = ve.score_ai_tells(ANALYTICAL_EXAMPLE)
        assert result["register"] == "analytical"

    def test_return_shape_unchanged_for_existing_callers(self):
        """
        app.py and dev_tools/harness.py call score_ai_tells(text) and
        read .get('clean') / .get('flagged') off the result. This locks
        in that those keys still exist with the right types, so this
        change can't silently break either caller.
        """
        result = ve.score_ai_tells(ANALYTICAL_EXAMPLE)
        assert isinstance(result["clean"], bool)
        assert isinstance(result["em_dash_count"], int)
        assert isinstance(result["phrase_hit_count"], int)
        assert isinstance(result["flagged"], list)


class TestCorporateTellDetectionUnaffected:
    """
    Guards against regression: adding the analytical list must not
    change behaviour on corporate-register text in any way.
    """

    def test_corporate_tells_still_caught(self):
        result = ve.score_ai_tells(CORPORATE_EXAMPLE)
        assert result["clean"] is False
        flagged_text = " ".join(result["flagged"]).lower()
        assert "leverag" in flagged_text or "synerg" in flagged_text

    def test_analytical_list_does_not_fire_on_corporate_text(self):
        result = ve.score_ai_tells(CORPORATE_EXAMPLE)
        flagged_text = " ".join(result["flagged"]).lower()
        assert "drift" not in flagged_text
        assert "land on" not in flagged_text


class TestGenuinelyCleanTextUnaffected:
    def test_casual_human_text_still_scores_clean(self):
        result = ve.score_ai_tells(GENUINELY_CLEAN_EXAMPLE)
        assert result["clean"] is True
        assert result["flagged"] == []


class TestRegexSweepFixesAnalyticalTells:
    """
    score_ai_tells only flags. _regex_sweep (prompts.py) is meant to
    fix. Confirms the analytical replacements actually fire in the
    sweep, not just in the verification check.
    """

    def test_drift_replaced_by_sweep(self):
        text = "The context drifts underneath it before anyone notices."
        result = pr._regex_sweep(text)
        assert "drift" not in result.lower()

    def test_surfaces_replaced_by_sweep(self):
        text = "It surfaces when someone finally asks the question."
        result = pr._regex_sweep(text)
        assert not re.search(r"\bsurfaces\b", result, re.IGNORECASE)

    def test_sweep_output_then_passes_verification(self):
        """
        End-to-end: sweep a tell-laden sentence, then confirm the
        verification check now considers it clean. This is the actual
        product flow in app.py — sweep, then verify.
        """
        text = "The situation drifts and surfaces without warning."
        swept = pr._regex_sweep(text)
        result = ve.score_ai_tells(swept)
        assert result["clean"] is True

    def test_corporate_sweep_behaviour_unchanged(self):
        """
        The existing claude_constructions replacements must still fire
        exactly as before on corporate-register text.
        """
        text = "We will leverage this to deliver a seamless solution."
        result = pr._regex_sweep(text)
        assert "leverage" not in result.lower()
        assert "seamless" not in result.lower()
