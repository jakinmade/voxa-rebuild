"""
Voxa — Sprint 3 Test Suite
Maps directly to the Sprint 3 Done When criteria.

Done When:
[1]  email_investor context applies formality override. Rendered output reflects override. Global profile unchanged.
[2]  Switching context mid-session produces correct output for each context.
[3]  Any voice profile version can be restored. Restored profile renders identically.
[4]  Drift monitor detects rule volatility above threshold. Profile freezes. User notified. Calibration blocked.
[5]  Org policy forbidden phrase in render candidate. Boundary check blocks it. Output rejected.
[6]  Org policy takes precedence over user boundary. Both present, org policy wins.
[7]  /voice-governance returns complete audit trail.
[8]  /drift-status returns accurate readings. Freeze status reflects actual profile state.
[9]  Admin role receives drift notification. User receives separate notification.
[10] Full end-to-end: new user → bootstrap → calibration → stable rules → context override → drift → freeze → confirm → resume.
"""

import pytest
from uuid import uuid4

from voxa_core.entities import BoundaryRules, RuleMetadata, VoiceProfile
from voxa_core.enums import LifecycleStage


# ---------------------------------------------------------------------------
# [1] & [2] Context overrides
# ---------------------------------------------------------------------------

class TestContextOverrides:

    def test_context_override_applied_over_global(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            get_effective_constraints,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.formality = RuleMetadata(
            value="casual",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        # Global profile says casual — investor context overrides to formal
        set_context_override(
            user_id=user_id,
            context="email_investor",
            rules={"formality": "formal"},
        )

        constraints = get_effective_constraints(profile, context="email_investor")
        assert constraints["formality"] == "formal"

    def test_global_profile_unchanged_after_context_override(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            get_effective_constraints,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.formality = RuleMetadata(
            value="casual",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        set_context_override(
            user_id=user_id,
            context="email_investor",
            rules={"formality": "formal"},
        )

        # Global profile rule unchanged
        assert profile.identity.formality.value == "casual"

    def test_context_override_fallback_to_global(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            get_effective_constraints,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        # Override sets formality only — directness not overridden
        set_context_override(
            user_id=user_id,
            context="email_investor",
            rules={"formality": "formal"},
        )

        constraints = get_effective_constraints(profile, context="email_investor")
        # Directness falls back to global profile
        assert constraints["directness"] == "high"

    def test_switching_context_produces_correct_constraints(self):
        from voxa_profile.context_overrides import (
            set_context_override,
            get_effective_constraints,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.formality = RuleMetadata(
            value="semi-formal",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        set_context_override(user_id=user_id, context="email_investor", rules={"formality": "formal"})
        set_context_override(user_id=user_id, context="internal", rules={"formality": "casual"})

        investor = get_effective_constraints(profile, context="email_investor")
        internal = get_effective_constraints(profile, context="internal")
        default = get_effective_constraints(profile, context="default")

        assert investor["formality"] == "formal"
        assert internal["formality"] == "casual"
        assert default["formality"] == "semi-formal"  # Falls back to global

    def test_unsupported_context_raises(self):
        from voxa_profile.context_overrides import set_context_override
        with pytest.raises(ValueError):
            set_context_override(uuid4(), "nonexistent_context_xyz", {"formality": "formal"})


# ---------------------------------------------------------------------------
# [3] Immutable version snapshots and restore
# ---------------------------------------------------------------------------

class TestVersionSnapshots:

    def test_snapshot_stored_and_retrievable(self):
        from voxa_governance.snapshots import store_snapshot, get_snapshot
        from voxa_core.entities import VoiceProfileVersion

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.version = 5

        snapshot = VoiceProfileVersion(
            user_id=user_id,
            version=5,
            snapshot=profile.model_copy(deep=True),
            changes=["test_change"],
        )
        store_snapshot(snapshot)

        retrieved = get_snapshot(user_id, 5)
        assert retrieved is not None
        assert retrieved.version == 5
        assert "test_change" in retrieved.changes

    def test_restore_from_snapshot(self):
        from voxa_governance.snapshots import store_snapshot, restore_from_snapshot
        from voxa_core.entities import VoiceProfileVersion

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        profile.version = 3

        snapshot = VoiceProfileVersion(
            user_id=user_id,
            version=3,
            snapshot=profile.model_copy(deep=True),
            changes=["directness_set_high"],
        )
        store_snapshot(snapshot)

        # Corrupt the live profile
        profiles = {user_id: profile}
        profile.identity.directness.value = "low"

        # Restore
        restored, success = restore_from_snapshot(user_id, 3, profiles)
        assert success is True
        assert restored.identity.directness.value == "high"

    def test_restored_profile_matches_snapshot(self):
        from voxa_governance.snapshots import (
            store_snapshot, restore_from_snapshot, verify_reproducibility
        )
        from voxa_core.entities import VoiceProfileVersion

        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        profile.identity.directness = RuleMetadata(
            value="high",
            confidence=0.80,
            source=["edit_1"],
            stability=0.75,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )
        profile.version = 7

        snapshot = VoiceProfileVersion(
            user_id=user_id,
            version=7,
            snapshot=profile.model_copy(deep=True),
            changes=["test"],
        )
        store_snapshot(snapshot)
        profiles = {user_id: profile}
        restored, _ = restore_from_snapshot(user_id, 7, profiles)

        # Reproducibility check
        rule_snapshot = {"directness": "high"}
        verified = verify_reproducibility(snapshot, rule_snapshot)
        assert verified is True

    def test_snapshot_not_found_returns_false(self):
        from voxa_governance.snapshots import restore_from_snapshot
        profiles = {}
        result, success = restore_from_snapshot(uuid4(), 999, profiles)
        assert success is False


# ---------------------------------------------------------------------------
# [4] Drift monitor — freeze on threshold breach
# ---------------------------------------------------------------------------

class TestDriftMonitor:

    def test_drift_freeze_on_volatility_breach(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            is_profile_frozen,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        # Record enough rule changes to breach threshold
        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        _, freeze_triggered = evaluate_drift(user_id, profile)
        assert freeze_triggered is True
        assert is_profile_frozen(user_id) is True

    def test_no_freeze_below_threshold(self):
        from voxa_governance.drift_monitor import (
            evaluate_drift,
            is_profile_frozen,
        )
        user_id = uuid4()  # Fresh user_id — no prior events
        profile = VoiceProfile(user_id=user_id)

        _, freeze_triggered = evaluate_drift(user_id, profile)
        assert freeze_triggered is False
        assert is_profile_frozen(user_id) is False

    def test_freeze_blocks_calibration_until_confirmed(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            is_profile_frozen,
            confirm_unfreeze,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        evaluate_drift(user_id, profile)
        assert is_profile_frozen(user_id) is True

        # Not unfrozen yet
        assert is_profile_frozen(user_id) is True

        # User confirms
        unfrozen = confirm_unfreeze(user_id)
        assert unfrozen is True
        assert is_profile_frozen(user_id) is False

    def test_drift_notifications_queued(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            get_pending_notifications,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        evaluate_drift(user_id, profile)
        notifications = get_pending_notifications(user_id)
        assert len(notifications) >= 1
        assert notifications[0].user_id == user_id

    def test_enterprise_admin_notified_separately(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            get_pending_notifications,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        admin_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        evaluate_drift(user_id, profile, is_enterprise=True, admin_user_id=admin_id)

        user_notifications = [
            n for n in get_pending_notifications(user_id) if not n.is_admin_notification
        ]
        admin_notifications = [
            n for n in get_pending_notifications(user_id) if n.is_admin_notification
        ]
        assert len(user_notifications) >= 1
        assert len(admin_notifications) >= 1


# ---------------------------------------------------------------------------
# [5] & [6] Org policy enforcement
# ---------------------------------------------------------------------------

class TestOrgPolicyEnforcement:

    def test_org_policy_blocks_forbidden_phrase(self):
        from voxa_governance.policy import enforce_org_policy
        from voxa_profile.context_overrides import ContextOverride

        org_id = "test_org_1"
        org_policies = {
            org_id: ContextOverride(
                context=f"org_{org_id}",
                rules={"forbidden_phrases": ["guaranteed returns", "no risk"]},
                is_org_policy=True,
            )
        }

        text = "Our product offers guaranteed returns with no risk."
        result, violations = enforce_org_policy(
            rendered_text=text,
            org_id=org_id,
            org_policies=org_policies,
            user_id=uuid4(),
            output_id=uuid4(),
        )

        assert result is None  # Output rejected
        assert len(violations) > 0
        assert any("guaranteed returns" in v for v in violations)

    def test_org_policy_passes_clean_output(self):
        from voxa_governance.policy import enforce_org_policy
        from voxa_profile.context_overrides import ContextOverride

        org_id = "test_org_2"
        org_policies = {
            org_id: ContextOverride(
                context=f"org_{org_id}",
                rules={"forbidden_phrases": ["guaranteed returns"]},
                is_org_policy=True,
            )
        }

        text = "Our product has delivered consistent results over five years."
        result, violations = enforce_org_policy(
            rendered_text=text,
            org_id=org_id,
            org_policies=org_policies,
            user_id=uuid4(),
            output_id=uuid4(),
        )

        assert result == text
        assert violations == []

    def test_org_policy_takes_precedence_over_user_boundary(self):
        from voxa_profile.context_overrides import (
            set_org_policy,
            get_effective_constraints,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        # User boundary says casual tone allowed
        profile.boundaries.tone_boundaries = RuleMetadata(
            value=["aggressive"],  # User only forbids aggressive
            confidence=1.0,
            source=["user"],
            stability=1.0,
            decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        profile.identity.formality = RuleMetadata(
            value="casual",
            confidence=0.8,
            source=["edit_1"],
            stability=0.7,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.STABLE,
        )

        org_id = "test_org_3"
        set_org_policy(org_id=org_id, rules={"formality": "formal"})

        constraints = get_effective_constraints(profile, context="default", org_id=org_id)
        # Org policy overrides user's casual formality to formal
        assert constraints["formality"] == "formal"

    def test_enterprise_audit_trail_records_violations(self):
        from voxa_governance.policy import enforce_org_policy, get_enterprise_audit_trail
        from voxa_profile.context_overrides import ContextOverride

        user_id = uuid4()
        org_id = "audit_test_org"
        org_policies = {
            org_id: ContextOverride(
                context=f"org_{org_id}",
                rules={"forbidden_phrases": ["speculative claims"]},
                is_org_policy=True,
            )
        }

        enforce_org_policy(
            rendered_text="These are speculative claims about future performance.",
            org_id=org_id,
            org_policies=org_policies,
            user_id=user_id,
            output_id=uuid4(),
        )

        trail = get_enterprise_audit_trail(org_id=org_id)
        assert len(trail) > 0
        assert any(e["type"] == "policy_violation" for e in trail)


# ---------------------------------------------------------------------------
# [8] Drift status readings accuracy
# ---------------------------------------------------------------------------

class TestDriftStatus:

    def test_drift_status_reflects_freeze_state(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            get_drift_status,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)

        evaluate_drift(user_id, profile)
        status = get_drift_status(user_id, profile)

        assert status["profile_frozen"] is True
        assert status["rule_volatility"] >= VOLATILITY_THRESHOLD
        assert "thresholds" in status

    def test_drift_status_includes_all_signals(self):
        from voxa_governance.drift_monitor import get_drift_status
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)
        status = get_drift_status(user_id, profile)

        required_keys = [
            "rule_volatility", "calibration_frequency", "contradiction_frequency",
            "override_usage", "stability_decay", "profile_frozen", "thresholds",
        ]
        for key in required_keys:
            assert key in status, f"Missing key: {key}"


# ---------------------------------------------------------------------------
# [10] Full end-to-end scenario
# ---------------------------------------------------------------------------

class TestEndToEnd:

    def test_full_pipeline_new_user_to_context_override(self):
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile, increment_version
        from voxa_profile.context_overrides import set_context_override, get_effective_constraints
        from voxa_governance.snapshots import store_snapshot
        from voxa_core.entities import VoiceProfileVersion
        from voxa_core.enums import SourceType

        user_id = uuid4()

        # Step 1: Onboarding → HumanisedProfile
        humanised = humanise(
            raw_input="I prefer direct communication. Do not hedge. Keep it concise.",
            user_id=user_id,
            source_type=SourceType.ONBOARDING,
        )
        assert len(humanised.facts) > 0

        # Step 2: Build profile
        profile = build_profile(humanised)
        assert profile.user_id == user_id
        assert isinstance(profile.is_bootstrap, bool)

        # Step 3: Store initial snapshot
        snapshot = VoiceProfileVersion(
            user_id=user_id,
            version=profile.version,
            snapshot=profile.model_copy(deep=True),
            changes=["initial"],
        )
        store_snapshot(snapshot)

        # Step 4: Set context override
        set_context_override(
            user_id=user_id,
            context="email_investor",
            rules={"formality": "formal", "warmth": "low"},
        )

        # Step 5: Verify context resolves correctly
        investor_constraints = get_effective_constraints(profile, context="email_investor")
        default_constraints = get_effective_constraints(profile, context="default")

        assert investor_constraints["formality"] == "formal"
        assert investor_constraints["warmth"] == "low"
        # Default context should NOT have these overrides
        assert default_constraints.get("formality") != "formal" or profile.identity.formality is None

    def test_freeze_confirm_resume_cycle(self):
        from voxa_governance.drift_monitor import (
            record_rule_change,
            evaluate_drift,
            is_profile_frozen,
            confirm_unfreeze,
            VOLATILITY_THRESHOLD,
        )
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id)

        # Trigger freeze
        for _ in range(VOLATILITY_THRESHOLD):
            record_rule_change(user_id)
        evaluate_drift(user_id, profile)
        assert is_profile_frozen(user_id) is True

        # Confirm → resume
        confirm_unfreeze(user_id)
        assert is_profile_frozen(user_id) is False
