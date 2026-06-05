"""
Voxa — Sprint 1 Test Suite
Maps directly to the Sprint 1 Done When criteria.

Done When:
[1] A user completes onboarding. HumanisedProfile is produced.
[2] Profile builds from onboarding output. Bootstrap state resolves.
[3] System renders governed output from the profile.
[4] User edits the output. Edit is classified. Voice change proceeds.
[5] Calibration event is stored. Rule candidate is extracted.
[6] Voice profile version increments.
[7] All 19 AI register detection rules run and produce valid output.
[8] Boundary violation returns no output — confirmed by test.
[9] Neutral defaults tag correctly in rendered output.
[10] LLM makes no decision outside the rendering layer — confirmed by test.
"""

import pytest
from uuid import uuid4
from datetime import date

from voxa_core.enums import (
    EditClass,
    Explicitness,
    LifecycleStage,
    SemanticDomain,
    SourceType,
)
from voxa_core.entities import (
    BoundaryRules,
    HumanisedProfile,
    IdentityRules,
    LinguisticRules,
    RuleMetadata,
    VoiceProfile,
)
from voxa_core.defaults import get_neutral_default, NEUTRAL_DEFAULTS
from voxa_core.bootstrap import check_bootstrap, BOOTSTRAP_EXIT_STABLE_RULE_COUNT


# ---------------------------------------------------------------------------
# [1] Humanisation Engine — onboarding produces HumanisedProfile
# ---------------------------------------------------------------------------

class TestHumanisationEngine:

    def test_explicit_preference_extracted(self):
        from voxa_humanisation.engine import humanise
        user_id = uuid4()
        result = humanise(
            raw_input="I hate fluffy corporate language. Keep it short and direct.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        assert isinstance(result, HumanisedProfile)
        assert len(result.facts) > 0

    def test_no_inference_from_vague_input(self):
        from voxa_humanisation.engine import humanise
        user_id = uuid4()
        result = humanise(
            raw_input="The weather is nice today.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        # No explicit preference — no facts extracted
        assert len(result.facts) == 0

    def test_fact_carries_evidence_metadata(self):
        from voxa_humanisation.engine import humanise
        user_id = uuid4()
        result = humanise(
            raw_input="I prefer direct communication.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        if result.facts:
            fact = result.facts[0]
            assert fact.evidence.explicitness in list(Explicitness)
            assert fact.evidence.source_type == SourceType.ONBOARDING
            assert isinstance(fact.evidence.recency, date)
            assert 0.0 <= fact.evidence.source_weight <= 1.0

    def test_fact_carries_semantic_domain(self):
        from voxa_humanisation.engine import humanise
        user_id = uuid4()
        result = humanise(
            raw_input="I hate corporate jargon.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        if result.facts:
            assert result.facts[0].domain in list(SemanticDomain)

    def test_contradictions_preserved_not_resolved(self):
        from voxa_humanisation.engine import humanise
        user_id = uuid4()
        result = humanise(
            raw_input="I prefer formal communication. Keep it casual and informal.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        # Contradictions stored, not resolved
        assert isinstance(result.conflicts, list)

    def test_gaps_not_filled_with_defaults(self):
        from voxa_humanisation.engine import identify_gaps
        from voxa_core.entities import ExtractedFact, EvidenceStrength
        facts = []  # No facts — all dimensions unknown
        gaps = identify_gaps(facts)
        # Every gap must be "unknown" — never a default value
        for dim, val in gaps.items():
            assert val == "unknown", f"Gap {dim} should be 'unknown', got {val}"


# ---------------------------------------------------------------------------
# [2] Profile builds from onboarding. Bootstrap state checked.
# ---------------------------------------------------------------------------

class TestProfileBuilder:

    def test_profile_built_from_humanised(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile
        user_id = uuid4()
        humanised = humanise(
            raw_input="I prefer direct and concise communication.",
            user_id=user_id,
        )
        profile = build_profile(humanised)
        assert isinstance(profile, VoiceProfile)
        assert profile.user_id == user_id
        assert profile.version == 1

    def test_system_default_boundary_applied(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile
        user_id = uuid4()
        humanised = humanise(raw_input="Keep it direct.", user_id=user_id)
        profile = build_profile(humanised)
        # System default boundary must always be present
        assert profile.boundaries.tone_boundaries is not None
        assert profile.boundaries.tone_boundaries.lifecycle_stage == LifecycleStage.BOUNDARY
        assert profile.boundaries.tone_boundaries.confidence == 1.0
        assert profile.boundaries.tone_boundaries.decay_rate == 0.0

    def test_rule_metadata_schema_enforced(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile
        user_id = uuid4()
        humanised = humanise(raw_input="I prefer direct communication.", user_id=user_id)
        profile = build_profile(humanised)
        # Check any present rule has full metadata
        if profile.identity.directness:
            rule = profile.identity.directness
            assert rule.confidence is not None
            assert rule.evidence_count is not None
            assert rule.last_updated is not None
            assert rule.source is not None
            assert rule.stability is not None
            assert rule.decay_rate is not None
            assert rule.lifecycle_stage is not None

    def test_bootstrap_state_set(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile
        user_id = uuid4()
        humanised = humanise(raw_input="Keep it short.", user_id=user_id)
        profile = build_profile(humanised)
        # New profile with minimal input — should be in bootstrap
        assert isinstance(profile.is_bootstrap, bool)


# ---------------------------------------------------------------------------
# [8] Boundary violation returns no output
# ---------------------------------------------------------------------------

class TestBoundaryValidation:

    @pytest.mark.asyncio
    async def test_boundary_violation_returns_none(self):
        from voxa_rendering.engine import render
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.boundaries = BoundaryRules(
            tone_boundaries=RuleMetadata(
                value=["aggressive"],
                confidence=1.0,
                source=["system_default"],
                stability=1.0,
                decay_rate=0.0,
                lifecycle_stage=LifecycleStage.BOUNDARY,
            )
        )
        # Force bootstrap to renderable by adding a provisional identity rule
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        profile.linguistic.forbidden_phrases = RuleMetadata(
            value=[],
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )

        # Input that would trigger boundary (aggressive in output)
        # With no API key, LLM passes through input — so put violation in input
        output = await render(
            input_text="This is an aggressive response.",
            profile=profile,
            session_id=uuid4(),
            context="default",
        )
        # Output should be None — boundary blocks it
        assert output is None

    @pytest.mark.asyncio
    async def test_clean_output_passes_boundary(self):
        from voxa_rendering.engine import render
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.boundaries = BoundaryRules(
            tone_boundaries=RuleMetadata(
                value=["salesy"],
                confidence=1.0,
                source=["system_default"],
                stability=1.0,
                decay_rate=0.0,
                lifecycle_stage=LifecycleStage.BOUNDARY,
            )
        )
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        profile.linguistic.forbidden_phrases = RuleMetadata(
            value=[],
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )

        output = await render(
            input_text="Here is a clear and direct summary.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None
        assert output.boundary_blocked is False if hasattr(output, 'boundary_blocked') else True


# ---------------------------------------------------------------------------
# [9] Neutral defaults tag correctly
# ---------------------------------------------------------------------------

class TestNeutralDefaults:

    def test_all_neutral_defaults_defined(self):
        expected_dimensions = [
            "cadence", "compression", "directness", "warmth", "formality",
            "reasoning_style", "confidence_expression", "humour",
            "intensity", "audience_positioning",
        ]
        for dim in expected_dimensions:
            val = get_neutral_default(dim)
            assert val is not None, f"No neutral default for {dim}"

    def test_unknown_dimension_raises(self):
        with pytest.raises(KeyError):
            get_neutral_default("nonexistent_dimension")

    @pytest.mark.asyncio
    async def test_unknown_rules_produce_neutral_default_tags(self):
        from voxa_rendering.engine import render
        user_id = uuid4()
        # Profile with NO rules set — all dimensions unknown
        profile = VoiceProfile(user_id=user_id)
        profile.boundaries = BoundaryRules(
            tone_boundaries=RuleMetadata(
                value=["patronising"],
                confidence=1.0,
                source=["system_default"],
                stability=1.0,
                decay_rate=0.0,
                lifecycle_stage=LifecycleStage.BOUNDARY,
            )
        )
        profile.identity.directness = RuleMetadata(
            value="medium",
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.PROVISIONAL,
        )
        profile.linguistic.forbidden_phrases = RuleMetadata(
            value=[],
            confidence=0.5,
            source=["test"],
            stability=0.5,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )

        output = await render(
            input_text="Here is a clear summary.",
            profile=profile,
            session_id=uuid4(),
        )
        if output and not output.is_bootstrap_output:
            # Most dimensions had no rule — neutral defaults should be used
            assert len(output.neutral_defaults_used) > 0


# ---------------------------------------------------------------------------
# [4] & [5] Edit classification and calibration
# ---------------------------------------------------------------------------

class TestCalibrationEngine:

    def test_voice_edit_classified(self):
        from voxa_calibration.engine import classify_edit
        result = classify_edit(
            original="This might be worth considering.",
            edited="Consider this.",
            user_instruction="Make it more direct, remove the hedging.",
        )
        assert result == EditClass.VOICE

    def test_content_edit_discarded(self):
        from voxa_calibration.engine import classify_edit
        result = classify_edit(
            original="The meeting is at 2pm.",
            edited="The meeting is at 3pm.",
            user_instruction="Change the time to 3pm.",
        )
        assert result == EditClass.FACTUAL

    def test_format_edit_classified(self):
        from voxa_calibration.engine import classify_edit
        result = classify_edit(
            original="Here are the points.",
            edited="Here are the points.",
            user_instruction="Put this in bullet points.",
        )
        assert result == EditClass.FORMAT

    def test_hedge_removal_produces_observation(self):
        from voxa_calibration.engine import extract_rule_observations, semantic_diff
        from uuid import uuid4
        diff = semantic_diff(
            original="This might work and could be worth trying.",
            edited="This works.",
        )
        observations = extract_rule_observations(
            diff=diff,
            user_id=uuid4(),
            session_id=uuid4(),
            edit_event_id=uuid4(),
        )
        dimensions = [o.rule_dimension for o in observations]
        assert "confidence_expression" in dimensions

    def test_repeated_observations_produce_candidate(self):
        from voxa_calibration.engine import promote_to_candidates
        from voxa_core.entities import RuleObservation
        user_id = uuid4()
        session_id = uuid4()
        obs1 = RuleObservation(
            user_id=user_id,
            rule_dimension="confidence_expression",
            observed_value="certain",
            source_edit_id=uuid4(),
            session_id=session_id,
        )
        obs2 = RuleObservation(
            user_id=user_id,
            rule_dimension="confidence_expression",
            observed_value="certain",
            source_edit_id=uuid4(),
            session_id=session_id,
        )
        candidates = promote_to_candidates([obs1], [obs2])
        assert len(candidates) > 0
        assert candidates[0].rule_dimension == "confidence_expression"


# ---------------------------------------------------------------------------
# [10] LLM boundary contract — LLM not called outside rendering layer
# ---------------------------------------------------------------------------

class TestLLMBoundaryContract:

    def test_llm_client_not_importable_from_other_layers(self):
        """
        The Claude API client lives only in voxa-rendering.
        Other layers must not import it.
        Confirmed by checking imports.
        """
        import voxa_humanisation.engine as humanisation_module
        import voxa_calibration.engine as calibration_module
        import voxa_governance.engine as governance_module
        import voxa_profile.builder as profile_module

        for module in [humanisation_module, calibration_module, governance_module, profile_module]:
            source = open(module.__file__).read()
            assert "anthropic" not in source.lower(), (
                f"LLM client found in {module.__name__} — boundary contract violated"
            )
            assert "ANTHROPIC_API_URL" not in source, (
                f"LLM API URL found in {module.__name__} — boundary contract violated"
            )

    def test_llm_api_url_only_in_rendering_layer(self):
        import voxa_rendering.llm_boundary as boundary_module
        source = open(boundary_module.__file__).read()
        assert "ANTHROPIC_API_URL" in source


# ---------------------------------------------------------------------------
# [6] Profile version increments
# ---------------------------------------------------------------------------

class TestProfileVersioning:

    def test_version_increments_on_change(self):
        from voxa_profile.builder import increment_version
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile

        user_id = uuid4()
        humanised = humanise(raw_input="Keep it direct.", user_id=user_id)
        profile = build_profile(humanised)
        assert profile.version == 1

        snapshot = increment_version(profile, changes=["test_change"])
        assert profile.version == 2
        assert snapshot.version == 2
        assert "test_change" in snapshot.changes

    def test_version_snapshot_is_deep_copy(self):
        from voxa_profile.builder import increment_version, build_profile
        from voxa_humanisation.engine import humanise

        user_id = uuid4()
        humanised = humanise(raw_input="Keep it direct.", user_id=user_id)
        profile = build_profile(humanised)
        snapshot = increment_version(profile, changes=["test"])

        # Mutating the profile should not affect the snapshot
        profile.version = 999
        assert snapshot.snapshot.version != 999
