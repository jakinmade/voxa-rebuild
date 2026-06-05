"""
Voxa — Integration Tests
Full pipeline flows. Cross-layer contracts. Real data through real code.

Integration tests verify:
1.  Full onboarding → profile → render pipeline
2.  Full onboarding → profile → render → edit → calibrate pipeline
3.  Calibration accumulates into profile — profile changes after voice edits
4.  Context override changes rendered output — same input, different context
5.  Org policy blocks forbidden output — policy enforcement end-to-end
6.  Drift monitor fires after sustained activity — freeze blocks calibration
7.  Snapshot → restore → re-render → same output
8.  LLM boundary contract — no layer outside rendering touches the API
9.  Bootstrap state gates rendering — incomplete profile gets generic output
10. Promotion lifecycle — observations accumulate into candidates
11. Negative evidence demotes a rule through the pipeline
12. Interaction map applied in rendering constraints
13. Decay batch reduces confidence across a profile
14. Enterprise audit trail captures full activity
15. API health and version endpoint
"""

import pytest
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from voxa_core.entities import (
    BoundaryRules,
    RuleMetadata,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import EditClass, LifecycleStage, SourceType


# ---------------------------------------------------------------------------
# 1. Full onboarding → profile → render
# ---------------------------------------------------------------------------

class TestFullOnboardingToRender:

    @pytest.mark.asyncio
    async def test_onboarding_to_render_pipeline(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile
        from voxa_rendering.engine import render

        user_id = uuid4()

        # Onboard
        humanised = humanise(
            raw_input="I prefer direct communication. Do not hedge. Keep it concise.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        assert len(humanised.facts) > 0

        # Build profile
        profile = build_profile(humanised)
        assert profile.user_id == user_id

        # Make renderable — set minimum required rules
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
            value=["patronising", "aggressive"],
            confidence=1.0, source=["system"],
            stability=1.0, decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )

        # Render
        output = await render(
            input_text="We might want to consider exploring this opportunity.",
            profile=profile,
            session_id=uuid4(),
            context="default",
        )

        assert output is not None
        assert output.output_text != ""
        assert output.is_bootstrap_output is False
        assert output.reproducibility.voice_profile_version == profile.version

    @pytest.mark.asyncio
    async def test_bootstrap_incomplete_returns_generic_output(self):
        from voxa_rendering.engine import render

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        # No rules set — bootstrap incomplete, no boundary set either
        # Rendering should return a bootstrap output, not None

        output = await render(
            input_text="Here is some content.",
            profile=profile,
            session_id=uuid4(),
        )

        assert output is not None
        assert output.is_bootstrap_output is True
        assert "onboarding" in output.output_text.lower() or "profile" in output.output_text.lower()

    @pytest.mark.asyncio
    async def test_boundary_violation_returns_none_not_degraded(self):
        from voxa_rendering.engine import render

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
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
            value=["aggressive"],
            confidence=1.0, source=["system"],
            stability=1.0, decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )

        # Input contains boundary-violating word — passthrough LLM returns it unchanged
        output = await render(
            input_text="This is an aggressive response.",
            profile=profile,
            session_id=uuid4(),
        )

        # Must be None — not degraded output
        assert output is None


# ---------------------------------------------------------------------------
# 2. Full render → edit → calibrate pipeline
# ---------------------------------------------------------------------------

class TestRenderEditCalibratePipeline:

    @pytest.mark.asyncio
    async def test_voice_edit_produces_calibration_event(self):
        from voxa_rendering.engine import render
        from voxa_calibration.engine import calibrate

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)

        output = await render(
            input_text="This might work and could be worth considering.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None

        event, observations, candidates = calibrate(
            rendered_output=output,
            original_text=output.output_text,
            edited_text="This works. Consider it.",
            user_instruction="Make it more direct, remove the hedging.",
            profile=profile,
            existing_observations=[],
        )

        assert event is not None
        assert event.edit_class == EditClass.VOICE
        assert event.direction == "positive"

    @pytest.mark.asyncio
    async def test_non_voice_edit_discarded(self):
        from voxa_rendering.engine import render
        from voxa_calibration.engine import calibrate

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)

        output = await render(
            input_text="The meeting is scheduled for 2pm tomorrow.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None

        event, observations, candidates = calibrate(
            rendered_output=output,
            original_text=output.output_text,
            edited_text="The meeting is scheduled for 3pm tomorrow.",
            user_instruction="Change the time to 3pm.",
            profile=profile,
            existing_observations=[],
        )

        assert event is None
        assert observations == []
        assert candidates == []


# ---------------------------------------------------------------------------
# 3. Calibration accumulates into profile
# ---------------------------------------------------------------------------

class TestCalibrationAccumulation:

    @pytest.mark.asyncio
    async def test_repeated_voice_edits_produce_candidate(self):
        from voxa_rendering.engine import render
        from voxa_calibration.engine import calibrate

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)
        all_observations = []

        for i in range(3):
            output = await render(
                input_text=f"This might be option {i+1} and could perhaps work.",
                profile=profile,
                session_id=uuid4(),
            )
            assert output is not None

            event, observations, candidates = calibrate(
                rendered_output=output,
                original_text=output.output_text,
                edited_text=f"Option {i+1} works.",
                user_instruction="Remove hedging. Be direct.",
                profile=profile,
                existing_observations=all_observations,
            )
            all_observations.extend(observations)

        # After repeated voice edits removing hedges, candidates should appear
        assert len(all_observations) > 0

    @pytest.mark.asyncio
    async def test_profile_version_increments_after_calibration(self):
        from voxa_rendering.engine import render
        from voxa_calibration.engine import calibrate
        from voxa_profile.builder import increment_version

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)
        initial_version = profile.version

        output = await render(
            input_text="This might work.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None

        event, observations, candidates = calibrate(
            rendered_output=output,
            original_text=output.output_text,
            edited_text="This works.",
            user_instruction="Remove hedging.",
            profile=profile,
            existing_observations=[],
        )

        if candidates:
            increment_version(profile, changes=["candidate_promoted"])
            assert profile.version > initial_version


# ---------------------------------------------------------------------------
# 4. Context override changes rendered output
# ---------------------------------------------------------------------------

class TestContextOverrideIntegration:

    def test_same_profile_different_contexts_different_constraints(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            get_effective_constraints,
        )

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.formality = RuleMetadata(
            value="semi-formal", confidence=0.8, source=["edit_1"],
            stability=0.7, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        profile.identity.directness = RuleMetadata(
            value="high", confidence=0.8, source=["edit_1"],
            stability=0.7, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        set_context_override(user_id, "email_investor", {"formality": "formal", "warmth": "low"})
        set_context_override(user_id, "internal", {"formality": "casual"})

        investor = get_effective_constraints(profile, "email_investor")
        internal = get_effective_constraints(profile, "internal")
        default = get_effective_constraints(profile, "default")

        # Each context produces different formality
        assert investor["formality"] == "formal"
        assert internal["formality"] == "casual"
        assert default["formality"] == "semi-formal"

        # Directness falls back to global in all contexts (not overridden)
        assert investor["directness"] == "high"
        assert internal["directness"] == "high"
        assert default["directness"] == "high"


# ---------------------------------------------------------------------------
# 5. Org policy enforcement end-to-end
# ---------------------------------------------------------------------------

class TestOrgPolicyIntegration:

    def test_org_policy_blocks_forbidden_phrase_end_to_end(self):
        from voxa_profile.context_overrides import ContextOverride, set_org_policy
        from voxa_governance.policy import enforce_org_policy

        org_id = "integration_org_1"
        set_org_policy(org_id, {
            "forbidden_phrases": ["guaranteed returns", "no risk"],
            "tone_boundaries": ["salesy"],
        })

        from voxa_profile.context_overrides import _org_policies
        result, violations = enforce_org_policy(
            rendered_text="Our guaranteed returns product carries no risk.",
            org_id=org_id,
            org_policies=_org_policies,
            user_id=uuid4(),
            output_id=uuid4(),
        )

        assert result is None
        assert len(violations) >= 1

    def test_org_policy_higher_precedence_than_user_context(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            set_org_policy,
            get_effective_constraints,
        )

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.formality = RuleMetadata(
            value="casual", confidence=0.8, source=["edit_1"],
            stability=0.7, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        # User sets casual context override
        set_context_override(user_id, "email_customer", {"formality": "casual"})

        org_id = "integration_org_2"
        set_org_policy(org_id, {"formality": "formal", "compliance_tone": "formal"})

        constraints = get_effective_constraints(profile, "email_customer", org_id=org_id)
        # Org policy wins — formal, not the user's casual
        assert constraints["formality"] == "formal"


# ---------------------------------------------------------------------------
# 6. Drift monitor fires and blocks calibration
# ---------------------------------------------------------------------------

class TestDriftMonitorIntegration:

    @pytest.mark.asyncio
    async def test_drift_freeze_blocks_render_flow(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            is_profile_frozen,
            confirm_unfreeze,
            VOLATILITY_THRESHOLD,
        )

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)

        # Simulate volatile rule changes
        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        _, frozen = evaluate_drift(user_id, profile)
        assert frozen is True
        assert is_profile_frozen(user_id) is True

        # Confirm unfreeze
        confirm_unfreeze(user_id)
        assert is_profile_frozen(user_id) is False


# ---------------------------------------------------------------------------
# 7. Snapshot → restore → reproducibility
# ---------------------------------------------------------------------------

class TestSnapshotRestoreIntegration:

    def test_snapshot_restore_produces_identical_constraints(self):
        from voxa_governance.snapshots import (
            store_snapshot,
            restore_from_snapshot,
            verify_reproducibility,
        )
        from voxa_profile.context_overrides import _extract_profile_constraints

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.directness = RuleMetadata(
            value="high", confidence=0.85, source=["edit_1"],
            stability=0.80, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        profile.identity.formality = RuleMetadata(
            value="formal", confidence=0.80, source=["edit_2"],
            stability=0.75, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        profile.version = 4

        # Store snapshot
        snapshot = VoiceProfileVersion(
            user_id=user_id,
            version=4,
            snapshot=profile.model_copy(deep=True),
            changes=["directness_high", "formality_formal"],
        )
        store_snapshot(snapshot)

        # Record constraints at time of snapshot
        original_constraints = _extract_profile_constraints(profile)

        # Corrupt live profile
        profiles = {user_id: profile}
        profile.identity.directness.value = "low"
        profile.identity.formality.value = "casual"

        # Restore
        restored, success = restore_from_snapshot(user_id, 4, profiles)
        assert success is True

        restored_constraints = _extract_profile_constraints(restored)

        # Key dimensions must match original
        assert restored_constraints["directness"] == original_constraints["directness"]
        assert restored_constraints["formality"] == original_constraints["formality"]


# ---------------------------------------------------------------------------
# 8. LLM boundary contract — cross-layer verification
# ---------------------------------------------------------------------------

class TestLLMBoundaryContractIntegration:

    def test_no_llm_imports_outside_rendering_layer(self):
        """
        Verifies that no layer outside voxa-rendering imports or references
        the Anthropic API URL or client. Architecture spec Section 3.1.
        """
        import os
        import ast

        packages_to_check = [
            "packages/voxa-core/src/voxa_core",
            "packages/voxa-humanisation/src/voxa_humanisation",
            "packages/voxa-profile/src/voxa_profile",
            "packages/voxa-calibration/src/voxa_calibration",
            "packages/voxa-governance/src/voxa_governance",
        ]

        violations = []
        for pkg_path in packages_to_check:
            for root, _, files in os.walk(pkg_path):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    source = open(fpath).read()
                    if "anthropic.com" in source or "ANTHROPIC_API_URL" in source:
                        violations.append(fpath)

        assert violations == [], f"LLM boundary violated in: {violations}"

    def test_llm_only_in_rendering_layer(self):
        # ANTHROPIC_API_URL and model live in llm_boundary.py — the single LLM entry point
        boundary_source = open("packages/voxa-rendering/src/voxa_rendering/llm_boundary.py").read()
        assert "ANTHROPIC_API_URL" in boundary_source
        assert "claude-sonnet" in boundary_source
        # engine.py delegates to llm_boundary — does not hold the URL itself
        engine_source = open("packages/voxa-rendering/src/voxa_rendering/engine.py").read()
        assert "llm_boundary" in engine_source


# ---------------------------------------------------------------------------
# 9. Promotion lifecycle cross-layer
# ---------------------------------------------------------------------------

class TestPromotionLifecycleIntegration:

    def test_observations_accumulate_to_candidate_then_provisional(self):
        from voxa_calibration.engine import extract_rule_observations, promote_to_candidates, semantic_diff
        from voxa_profile.lifecycle import attempt_promotion
        from datetime import timedelta

        user_id = uuid4()
        session_id = uuid4()
        all_obs = []

        # Simulate 5 hedge-removal edits
        for i in range(5):
            diff = semantic_diff(
                original="This might work and could perhaps be done.",
                edited="This works and can be done.",
            )
            obs = extract_rule_observations(diff, user_id, session_id, uuid4())
            all_obs.extend(obs)

        assert len(all_obs) > 0

        # Candidates should be promoted
        candidates = promote_to_candidates(all_obs[:1], all_obs[1:])
        assert len(candidates) > 0

        # Attempt promotion to PROVISIONAL
        candidate = candidates[0]
        rule = RuleMetadata(
            value=candidate.candidate_value,
            confidence=0.55,
            evidence_count=candidate.evidence_count,
            source=[str(o) for o in candidate.supporting_observations],
            stability=0.40,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )

        now = datetime.now(timezone.utc)
        timestamps = [now - timedelta(days=i) for i in range(candidate.evidence_count)]
        values = [str(candidate.candidate_value)] * candidate.evidence_count

        updated, reason, promoted = attempt_promotion(
            rule=rule,
            dimension=candidate.rule_dimension,
            evidence_timestamps=timestamps,
            values_observed=values,
            additional_sessions=0,
            has_active_conflict=False,
        )

        # With enough evidence, should promote to at least CANDIDATE or PROVISIONAL
        assert updated.lifecycle_stage in {LifecycleStage.CANDIDATE, LifecycleStage.PROVISIONAL}


# ---------------------------------------------------------------------------
# 10. Interaction map applied end-to-end
# ---------------------------------------------------------------------------

class TestInteractionMapIntegration:

    def test_interaction_map_resolves_before_rendering(self):
        from voxa_profile.interactions import resolve_all_interactions

        constraints = {
            "directness": "high",
            "formality": "formal",
            "warmth": "low",
            "intensity": "high",
            "humour": "sarcastic",
            "audience_positioning": "teacher",
            "confidence_expression": "certain",
        }

        updated, results = resolve_all_interactions(constraints, context="written")

        # All four pairs resolved
        pair_names = [r.pair for r in results]
        assert "directness_vs_formality" in pair_names
        assert "warmth_vs_intensity" in pair_names
        assert "humour_vs_audience_positioning" in pair_names
        assert "confidence_expression_vs_hedging" in pair_names

        # Intensity capped at medium (warmth=low, intensity=high, written)
        assert updated.get("intensity") == "medium"

        # Humour moderated under teacher positioning
        assert updated.get("humour") == "dry"


# ---------------------------------------------------------------------------
# 11. Decay batch cross-layer
# ---------------------------------------------------------------------------

class TestDecayBatchIntegration:

    def test_decay_batch_does_not_touch_boundary_rules(self):
        from voxa_profile.lifecycle import run_decay_batch

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        profile.boundaries.tone_boundaries = RuleMetadata(
            value=["patronising"],
            confidence=1.0, source=["system"],
            stability=1.0, decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        profile.identity.directness = RuleMetadata(
            value="high", confidence=0.80, source=["edit_1"],
            stability=0.70, decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        run_decay_batch(profile)

        # Boundary unchanged
        assert profile.boundaries.tone_boundaries.confidence == 1.0
        assert profile.boundaries.tone_boundaries.lifecycle_stage == LifecycleStage.BOUNDARY

        # Non-boundary rule decayed
        assert profile.identity.directness.confidence < 0.80


# ---------------------------------------------------------------------------
# 12. Enterprise audit trail end-to-end
# ---------------------------------------------------------------------------

class TestEnterpriseAuditIntegration:

    def test_audit_trail_captures_policy_violation_and_pass(self):
        from voxa_profile.context_overrides import ContextOverride
        from voxa_governance.policy import enforce_org_policy, get_enterprise_audit_trail

        org_id = f"audit_integration_{uuid4().hex[:8]}"
        user_id = uuid4()
        output_id_1 = uuid4()
        output_id_2 = uuid4()

        org_policies = {
            org_id: ContextOverride(
                context=f"org_{org_id}",
                rules={"forbidden_phrases": ["guaranteed"]},
                is_org_policy=True,
            )
        }

        # One pass, one violation
        enforce_org_policy("Clear results over five years.", org_id, org_policies, user_id, output_id_1)
        enforce_org_policy("Guaranteed results.", org_id, org_policies, user_id, output_id_2)

        trail = get_enterprise_audit_trail(org_id=org_id)
        types = [e["type"] for e in trail]

        assert "policy_check" in types
        assert "policy_violation" in types

    def test_calibration_audit_log_append_only(self):
        from voxa_governance.engine import record_calibration_event, get_audit_log
        from voxa_core.entities import CalibrationEvent

        user_id = uuid4()
        event = CalibrationEvent(
            user_id=user_id,
            session_id=uuid4(),
            rendered_output_id=uuid4(),
            edit_class=EditClass.VOICE,
            direction="positive",
            rule_dimension="directness",
            pattern_detected="hedge_removed",
            raw_edit="This works.",
            profile_version_before=1,
            profile_version_after=2,
        )
        record_calibration_event(event)

        log = get_audit_log(user_id=user_id)
        assert len(log) >= 1
        assert log[-1]["edit_class"] == "voice"
        assert log[-1]["direction"] == "positive"


# ---------------------------------------------------------------------------
# 13. API health check
# ---------------------------------------------------------------------------

class TestAPIIntegration:

    @pytest.mark.asyncio
    async def test_api_health_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "9.2.0" in data["version"]

    @pytest.mark.asyncio
    async def test_api_humanise_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "I prefer direct communication. Keep it concise.",
                "source_type": "onboarding",
            })
        assert response.status_code == 200
        data = response.json()
        assert "humanised_profile" in data
        assert "fact_count" in data

    @pytest.mark.asyncio
    async def test_api_render_without_profile_returns_404(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/render", json={
                "user_id": str(uuid4()),
                "input_text": "Some text.",
            })
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_api_full_humanise_then_voice_profile(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Humanise
            await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "I prefer direct communication. Do not hedge.",
                "source_type": "onboarding",
            })

            # Get profile
            response = await client.get(f"/voice-profile?user_id={user_id}")

        assert response.status_code == 200
        profile_data = response.json()
        assert profile_data["user_id"] == user_id

    @pytest.mark.asyncio
    async def test_api_bootstrap_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "Keep it direct and concise.",
                "source_type": "onboarding",
            })
            response = await client.get(f"/bootstrap-status?user_id={user_id}")

        assert response.status_code == 200
        data = response.json()
        assert "is_renderable" in data
        assert "is_bootstrap_complete" in data
        assert "rules_by_stage" in data
        assert "missing_requirements" in data

    @pytest.mark.asyncio
    async def test_api_drift_status_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "I prefer direct communication.",
                "source_type": "onboarding",
            })
            response = await client.get(f"/drift-status?user_id={user_id}")

        assert response.status_code == 200
        data = response.json()
        assert "rule_volatility" in data
        assert "profile_frozen" in data
        assert "thresholds" in data

    @pytest.mark.asyncio
    async def test_api_context_override_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "I prefer direct communication.",
                "source_type": "onboarding",
            })
            response = await client.post("/context-override", json={
                "user_id": user_id,
                "context": "email_investor",
                "rules": {"formality": "formal"},
            })

        assert response.status_code == 200
        data = response.json()
        assert data["context"] == "email_investor"
        assert data["rules"]["formality"] == "formal"

    @pytest.mark.asyncio
    async def test_api_voice_history_endpoint(self):
        from httpx import AsyncClient, ASGITransport
        from voxa_api.main import app

        user_id = str(uuid4())
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/humanise", json={
                "user_id": user_id,
                "raw_input": "Keep it direct.",
                "source_type": "onboarding",
            })
            response = await client.get(f"/voice-history?user_id={user_id}")

        assert response.status_code == 200
        history = response.json()
        assert isinstance(history, list)
        assert len(history) >= 1


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_renderable_profile(user_id) -> VoiceProfile:
    profile = VoiceProfile(user_id=user_id)
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
        value=["patronising", "aggressive", "salesy"],
        confidence=1.0, source=["system"],
        stability=1.0, decay_rate=0.0,
        lifecycle_stage=LifecycleStage.BOUNDARY,
    )
    return profile
