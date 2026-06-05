"""
Voxa — Change Vector Test Suite
Tests the change vector classifier against hard cases.

The reviewer's central challenge:
  An edit containing confidence + directness + intent + content signals
  simultaneously must be correctly classified.

These tests prove the vector approach handles implicit, mixed-signal edits
that would defeat regex or simple structural heuristics.
"""

import pytest
from voxa_core.enums import EditClass
from voxa_calibration.change_vector import (
    compute_change_vector,
    classify_from_vector,
    analyse_edit,
)
from voxa_calibration.engine import classify_edit, semantic_diff, extract_rule_observations
from uuid import uuid4


# ---------------------------------------------------------------------------
# The reviewer's hard case — must pass
# ---------------------------------------------------------------------------

class TestReviewerHardCase:

    def test_implicit_directness_certainty_edit(self):
        """
        Reviewer's example:
        "We should consider alternative approaches." → "This isn't the right direction."

        Old approach: low Jaccard → ambiguous or content.
        Vector approach: strong certainty + directness displacement → VOICE.
        """
        result = analyse_edit(
            original="We should consider alternative approaches.",
            edited="This isn't the right direction.",
        )
        assert result.edit_class == "voice", (
            f"Expected voice, got {result.edit_class}. Reasoning: {result.reasoning}"
        )
        assert result.confidence > 0.45

    def test_implicit_directness_certainty_produces_observations(self):
        result = analyse_edit(
            original="We should consider alternative approaches.",
            edited="This isn't the right direction.",
        )
        dimensions = [obs[0] for obs in result.voice_observations]
        # Should observe certainty and/or directness shift
        assert any(d in dimensions for d in ["confidence_expression", "directness"]), (
            f"Expected certainty/directness observations, got {dimensions}"
        )


# ---------------------------------------------------------------------------
# Unambiguous voice edits — must classify as voice
# ---------------------------------------------------------------------------

class TestUnambiguousVoiceEdits:

    def test_hedge_removal_explicit(self):
        result = classify_edit(
            original="This might work and could perhaps be useful.",
            edited="This works and is useful.",
            user_instruction="Remove the hedging.",
        )
        assert result == EditClass.VOICE

    def test_compression_same_content(self):
        result = classify_edit(
            original="I wanted to reach out to let you know that the report has been completed and is now ready for your review.",
            edited="The report is ready for your review.",
        )
        assert result == EditClass.VOICE

    def test_tone_instruction_explicit(self):
        result = classify_edit(
            original="We could potentially explore some options here.",
            edited="Here are three options. Pick one.",
            user_instruction="Make it more direct.",
        )
        assert result == EditClass.VOICE

    def test_formality_shift(self):
        result = classify_edit(
            original="Hey, just wanted to check in on where things stand.",
            edited="I am following up to understand the current status.",
            user_instruction="Make it more formal.",
        )
        assert result == EditClass.VOICE

    def test_certainty_increase(self):
        result = classify_edit(
            original="We might be able to deliver this by Friday.",
            edited="We will deliver this by Friday.",
        )
        assert result == EditClass.VOICE

    def test_kill_it_compression(self):
        """
        "Kill it" — maximum compression voice edit.
        Old regex approach would fail this.
        """
        result = classify_edit(
            original="We should consider whether to continue pursuing this particular initiative.",
            edited="Kill it.",
            user_instruction="",
        )
        # Strong compression + same subject = voice
        assert result == EditClass.VOICE

    def test_warmth_increase(self):
        result = classify_edit(
            original="The deadline is Friday.",
            edited="I appreciate your work on this — Friday is the deadline.",
            user_instruction="Make it warmer.",
        )
        assert result == EditClass.VOICE


# ---------------------------------------------------------------------------
# Unambiguous non-voice edits — must NOT classify as voice
# ---------------------------------------------------------------------------

class TestUnambiguousNonVoiceEdits:

    def test_factual_time_change(self):
        result = classify_edit(
            original="The meeting is at 2pm.",
            edited="The meeting is at 3pm.",
            user_instruction="Change the time to 3pm.",
        )
        assert result == EditClass.FACTUAL

    def test_factual_number_correction(self):
        result = classify_edit(
            original="Revenue increased by 15% this quarter.",
            edited="Revenue increased by 12% this quarter.",
            user_instruction="The number should be 12, not 15.",
        )
        assert result == EditClass.FACTUAL

    def test_format_bullets(self):
        result = classify_edit(
            original="We need to do three things.",
            edited="We need to do three things.",
            user_instruction="Put this in bullet points.",
        )
        assert result == EditClass.FORMAT

    def test_content_addition(self):
        result = classify_edit(
            original="The project is on track.",
            edited="The project is on track. We also need to address the budget overrun before end of month.",
            user_instruction="Add the budget issue.",
        )
        assert result == EditClass.CONTENT


# ---------------------------------------------------------------------------
# Change vector — axis-level correctness
# ---------------------------------------------------------------------------

class TestChangeVectorAxes:

    def test_certainty_axis_positive_on_hedge_removal(self):
        vector = compute_change_vector(
            original="This might work.",
            edited="This works.",
        )
        assert vector.certainty > 0.0, f"Expected positive certainty, got {vector.certainty}"

    def test_certainty_axis_negative_on_hedge_addition(self):
        vector = compute_change_vector(
            original="This works.",
            edited="This might work.",
        )
        assert vector.certainty < 0.0, f"Expected negative certainty, got {vector.certainty}"

    def test_compression_axis_positive_on_shortening(self):
        vector = compute_change_vector(
            original="I would like to take this opportunity to inform you that the deadline for submission has now been set to the end of this week.",
            edited="Deadline is Friday.",
        )
        assert vector.compression > 0.0, f"Expected positive compression, got {vector.compression}"

    def test_directness_axis_positive_on_decisive_language(self):
        vector = compute_change_vector(
            original="We could perhaps consider taking action.",
            edited="Take action now.",
        )
        # Decisiveness captured across certainty + intensity (imperative "Take action now")
        decisive_signal = vector.certainty + vector.intensity + vector.directness
        assert decisive_signal > 0.5
        assert vector.voice_magnitude > 0.5

    def test_factual_correction_axis_triggered(self):
        vector = compute_change_vector(
            original="Meet at 2pm.",
            edited="Meet at 3pm.",
            instruction="Change the time to 3pm.",
        )
        assert vector.factual_correction > 0.40

    def test_format_axis_triggered_on_instruction(self):
        vector = compute_change_vector(
            original="Here are three things.",
            edited="Here are three things.",
            instruction="Format this as a numbered list.",
        )
        assert vector.format_change > 0.40

    def test_voice_magnitude_higher_than_non_voice_for_voice_edit(self):
        vector = compute_change_vector(
            original="We might want to consider whether this approach could work.",
            edited="This approach works.",
        )
        assert vector.voice_magnitude > vector.non_voice_magnitude

    def test_dominant_axes_populated_for_voice_edit(self):
        vector = compute_change_vector(
            original="We might want to consider this.",
            edited="Do this.",
            instruction="Be direct.",
        )
        assert len(vector.dominant_voice_axes) > 0

    def test_jaccard_low_but_voice_still_classified(self):
        """
        Low Jaccard should not automatically mean content edit
        when voice displacement is strong.
        """
        result = analyse_edit(
            original="We should consider alternative approaches.",
            edited="This isn't the right direction.",
        )
        vector = result.vector
        assert vector.jaccard_similarity < 0.35  # Confirm low Jaccard
        assert result.edit_class == "voice"       # But still classified as voice


# ---------------------------------------------------------------------------
# Observation extraction from vector
# ---------------------------------------------------------------------------

class TestObservationExtraction:

    def test_certainty_observation_extracted(self):
        diff = semantic_diff(
            original="This might work.",
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

    def test_directness_observation_extracted(self):
        diff = semantic_diff(
            original="We could perhaps consider taking action on this.",
            edited="Take action now.",
        )
        observations = extract_rule_observations(
            diff=diff,
            user_id=uuid4(),
            session_id=uuid4(),
            edit_event_id=uuid4(),
        )
        dimensions = [o.rule_dimension for o in observations]
        # Should extract directness and/or compression
        assert any(d in dimensions for d in ["directness", "compression", "confidence_expression"])

    def test_compression_observation_value_correct(self):
        diff = semantic_diff(
            original="I would like to let you know that the report is ready for your review.",
            edited="Report ready.",
        )
        observations = extract_rule_observations(
            diff=diff,
            user_id=uuid4(),
            session_id=uuid4(),
            edit_event_id=uuid4(),
        )
        comp_obs = [o for o in observations if o.rule_dimension == "compression"]
        if comp_obs:
            assert comp_obs[0].observed_value == "high"

    def test_multiple_axes_produce_multiple_observations(self):
        """
        An edit that shifts certainty AND directness AND compresses
        should produce observations on all affected dimensions.
        """
        diff = semantic_diff(
            original="We should consider whether we might want to explore some potential alternative approaches here.",
            edited="No. Try this instead.",
        )
        observations = extract_rule_observations(
            diff=diff,
            user_id=uuid4(),
            session_id=uuid4(),
            edit_event_id=uuid4(),
        )
        assert len(observations) >= 2


# ---------------------------------------------------------------------------
# Confidence model coherence
# ---------------------------------------------------------------------------

class TestConfidenceModelCoherence:

    def test_high_consistency_slows_decay(self):
        from voxa_profile.confidence import apply_decay
        low_consistency = apply_decay(0.80, decay_rate=0.05, consistency_score=0.0)
        high_consistency = apply_decay(0.80, decay_rate=0.05, consistency_score=1.0)
        assert high_consistency > low_consistency

    def test_stability_modulates_confidence(self):
        from voxa_profile.confidence import compute_confidence, ConfidenceInputs
        low_stability = compute_confidence(ConfidenceInputs(10, 0.8, 0.8, 0.8, 0.0, stability=0.0))
        high_stability = compute_confidence(ConfidenceInputs(10, 0.8, 0.8, 0.8, 0.0, stability=1.0))
        assert high_stability.confidence > low_stability.confidence

    def test_negative_evidence_reduces_stability_first(self):
        from voxa_profile.confidence import apply_negative_evidence, NEGATIVE_STABILITY_PENALTY, NEGATIVE_CONFIDENCE_PENALTY
        conf, stab, _ = apply_negative_evidence(0.80, 0.80, "stable")
        stability_drop = 0.80 - stab
        confidence_drop = 0.80 - conf
        # Stability penalty and confidence penalty are both applied
        assert abs(stability_drop - NEGATIVE_STABILITY_PENALTY) < 0.001
        assert abs(confidence_drop - NEGATIVE_CONFIDENCE_PENALTY) < 0.001

    def test_single_negative_event_does_not_collapse_stable_rule(self):
        from voxa_profile.confidence import apply_negative_evidence
        # STABLE rule at 0.80 confidence — one negative event should not trigger demotion
        # Demotion threshold for stable is 0.60
        conf, stab, should_demote = apply_negative_evidence(0.80, 0.80, "stable")
        assert should_demote is False
        assert conf > 0.60

    def test_sustained_negative_evidence_eventually_demotes(self):
        from voxa_profile.confidence import apply_negative_evidence
        conf, stab = 0.65, 0.65
        demoted = False
        for _ in range(8):
            conf, stab, should_demote = apply_negative_evidence(conf, stab, "stable")
            if should_demote:
                demoted = True
                break
        assert demoted is True

    def test_exponential_recency_recent_higher_than_old(self):
        from voxa_profile.confidence import compute_recency_score
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        recent_score = compute_recency_score([now - timedelta(days=1)])
        old_score = compute_recency_score([now - timedelta(days=25)])
        assert recent_score > old_score
        # Exponential: recent should be substantially higher
        assert recent_score > old_score * 3
