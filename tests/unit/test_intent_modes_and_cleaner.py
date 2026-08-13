"""
Direct tests for voxa_rendering.intent_modes and voxa_rendering.cleaner —
the two modules that were referenced by engine.py's render() but never
built, causing every render() call to fail on import. Built as ports of
the live app's real, shipped logic (prompts.py's _detect_mode,
mode_prompts, and the deterministic parts of _regex_sweep).
"""
from voxa_rendering.cleaner import clean_render_output
from voxa_rendering.intent_modes import (
    IntentMode,
    apply_intent_mode,
    build_intent_mode_trace,
    detect_intent_mode,
    mode_from_string,
)


class TestDetectIntentMode:
    def test_plain_text_defaults_to_get_it_done(self):
        mode, score = detect_intent_mode("Here is a quick update on the project.")
        assert mode == IntentMode.GET_IT_DONE

    def test_student_language_detected_as_help_me_understand(self):
        text = (
            "I don't understand this concept for my dissertation. "
            "Furthermore, the methodology cited in the literature is unclear. "
            "It can be argued that the framework needs further analysis. "
            "My professor wants a critical evaluation of the theory and evidence."
        )
        mode, score = detect_intent_mode(text)
        assert mode == IntentMode.HELP_ME_UNDERSTAND
        assert score >= 0.55

    def test_empty_text_returns_get_it_done_zero_confidence(self):
        mode, score = detect_intent_mode("")
        assert mode == IntentMode.GET_IT_DONE
        assert score == 0.0

    def test_score_bounded_between_zero_and_one(self):
        text = (
            "My essay, my assignment, my dissertation, my coursework, "
            "for class, my professor, my tutor, word limit, struggling with, "
            "furthermore moreover nevertheless in conclusion it can be argued "
            "according to as argued by cited in essay thesis hypothesis "
            "analysis evaluate critically literature methodology"
        )
        mode, score = detect_intent_mode(text)
        assert 0.0 <= score <= 1.0


class TestModeFromString:
    def test_valid_mode_parsed_case_insensitively(self):
        assert mode_from_string("get_it_done") == IntentMode.GET_IT_DONE
        assert mode_from_string("HELP_ME_UNDERSTAND") == IntentMode.HELP_ME_UNDERSTAND
        assert mode_from_string("Think_It_Through") == IntentMode.THINK_IT_THROUGH

    def test_invalid_mode_returns_none(self):
        assert mode_from_string("not_a_real_mode") is None

    def test_empty_or_none_returns_none(self):
        assert mode_from_string("") is None
        assert mode_from_string(None) is None


class TestApplyIntentMode:
    def test_adds_task_instruction_key(self):
        constraints = {"cadence": "fast", "directness": "high"}
        updated, applied_keys = apply_intent_mode(constraints, IntentMode.GET_IT_DONE)
        assert "task_instruction" in updated
        assert applied_keys == ["task_instruction"]

    def test_never_touches_identity_dimensions(self):
        """
        engine.py's docstring is explicit: intent mode adjusts execution
        constraints only, identity dimensions are never touched. This
        locks that contract in.
        """
        constraints = {
            "cadence": "fast", "compression": "high", "directness": "high",
            "warmth": "low", "formality": "casual",
        }
        updated, _ = apply_intent_mode(dict(constraints), IntentMode.HELP_ME_UNDERSTAND)
        for key, value in constraints.items():
            assert updated[key] == value

    def test_different_modes_produce_different_instructions(self):
        base = {}
        get_it_done, _ = apply_intent_mode(dict(base), IntentMode.GET_IT_DONE)
        understand, _ = apply_intent_mode(dict(base), IntentMode.HELP_ME_UNDERSTAND)
        assert get_it_done["task_instruction"] != understand["task_instruction"]

    def test_original_constraints_dict_not_mutated(self):
        constraints = {"cadence": "fast"}
        apply_intent_mode(constraints, IntentMode.GET_IT_DONE)
        assert "task_instruction" not in constraints


class TestBuildIntentModeTrace:
    def test_trace_contains_intent_mode_key(self):
        trace = build_intent_mode_trace(IntentMode.WRITE_SOMETHING, ["task_instruction"])
        assert trace["intent_mode"] == "WRITE_SOMETHING"

    def test_trace_contains_applied_keys(self):
        trace = build_intent_mode_trace(IntentMode.GET_IT_DONE, ["task_instruction"])
        assert trace["applied_keys"] == ["task_instruction"]


class TestCleanRenderOutput:
    def test_em_dashes_removed(self):
        result = clean_render_output("This is one thing—and this is another.")
        assert "—" not in result
        # Was previously "assert '-' in result" — that encoded the OLD
        # cleaner.py behaviour (crude spaced-hyphen substitution), which
        # is itself an AI tell that score_ai_tells' own spaced-hyphen
        # check exists to catch. The canonical sweep (voxa_core.
        # text_guardrail) never substitutes a hyphen — it splits into
        # two sentences or joins with a comma. A literal " - " surviving
        # here would mean the em-dash-laundering bug is back.
        assert " - " not in result

    def test_em_dash_never_becomes_spaced_hyphen(self):
        # Direct regression guard for the specific bug this replaced:
        # cleaner.py used to convert every em dash straight to " - ",
        # which is itself flagged as an AI tell elsewhere in the system.
        result = clean_render_output(
            "The team shipped fast—faster than anyone expected."
        )
        assert " - " not in result

    def test_claude_constructions_replaced(self):
        result = clean_render_output("We will leverage this to deliver a seamless result.")
        assert "leverage" not in result.lower()
        assert "seamless" not in result.lower()

    def test_empty_text_returns_empty(self):
        assert clean_render_output("") == ""

    def test_clean_text_passes_through_unchanged_in_substance(self):
        text = "This is a perfectly normal sentence."
        result = clean_render_output(text)
        assert result.strip() == text.strip()


class TestEndToEndRenderNoLongerBroken:
    """
    The actual regression test: render() previously failed on import for
    every single call, regardless of whether intent_mode was passed.
    This confirms the full pipeline now runs without ModuleNotFoundError.
    """
    async def test_render_completes_without_import_error(self):
        import sys
        from uuid import uuid4

        from voxa_core.entities import RuleMetadata, VoiceProfile
        from voxa_core.enums import LifecycleStage
        from voxa_rendering.engine import render

        profile = VoiceProfile(user_id=uuid4())
        profile.identity.directness = RuleMetadata(
            value="high", confidence=0.6, source=["onboarding"],
            stability=0.5, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        profile.linguistic.forbidden_phrases = RuleMetadata(
            value=[], confidence=0.5, source=["onboarding"],
            stability=0.4, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )
        profile.boundaries.tone_boundaries = RuleMetadata(
            value=["patronising"], confidence=1.0, source=["system"],
            stability=1.0, decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )

        output = await render(
            input_text="Here is a plain, direct update.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None
        assert output.is_bootstrap_output is False
        assert "_intent_mode" in output.reproducibility.rule_snapshot
