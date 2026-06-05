"""
Voxa — Calibration Engine Sprint 2 Extensions
Adds LLM escalation, full semantic diff, negative evidence tracking,
full validation gate, and CANDIDATE → PROVISIONAL promotion.

Architecture Spec v9.2.0, Section 8.

LLM escalation: LLM returns a confidence score only.
Rules-based layer makes the final decision.
The LLM never makes the final decision.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog

from voxa_core.entities import (
    CalibrationEvent,
    RenderedOutput,
    RuleCandidate,
    RuleMetadata,
    RuleObservation,
    VoiceProfile,
)
from voxa_core.enums import EditClass, LifecycleStage, SourceType
from voxa_profile.confidence import (
    ConfidenceInputs,
    compute_confidence,
    compute_consistency_score,
    compute_recency_score,
)
from voxa_profile.lifecycle import (
    attempt_promotion,
    can_promote_to_provisional,
    demote_rule,
)

logger = structlog.get_logger(__name__)

# LLM escalation threshold — if rules-based confidence below this, escalate
LLM_ESCALATION_CONFIDENCE_THRESHOLD = 0.55

# LLM calls are delegated to the rendering layer — boundary contract enforced
# The calibration layer never calls the Anthropic API directly

# Self-report conflict: N contrary edits before onboarding preference is challenged
SELF_REPORT_CONFLICT_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Extended semantic diff — Sprint 2 adds tonal and confidence change detection
# ---------------------------------------------------------------------------

TONAL_MARKERS = {
    "warmer": ("warmth", "high"),
    "colder": ("warmth", "low"),
    "more formal": ("formality", "formal"),
    "less formal": ("formality", "casual"),
    "more casual": ("formality", "casual"),
    "more confident": ("confidence_expression", "certain"),
    "less confident": ("confidence_expression", "hedged"),
    "more direct": ("directness", "high"),
    "less direct": ("directness", "low"),
    "more intense": ("intensity", "high"),
    "toned down": ("intensity", "low"),
}

HEDGE_WORDS = {"might", "could", "perhaps", "possibly", "maybe", "somewhat", "quite", "rather"}
CERTAIN_WORDS = {"will", "must", "clearly", "definitely", "certainly", "always"}


def full_semantic_diff(original: str, edited: str, user_instruction: str = "") -> dict:
    """
    Sprint 2 full semantic diff.
    Detects lexical, tonal, structural, and confidence changes.
    """
    orig_words = original.lower().split()
    edit_words = edited.lower().split()
    orig_set = set(orig_words)
    edit_set = set(edit_words)

    added = edit_set - orig_set
    removed = orig_set - edit_set

    # Hedge/certainty tracking
    hedges_removed = HEDGE_WORDS & removed
    hedges_added = HEDGE_WORDS & added
    certain_added = CERTAIN_WORDS & added
    certain_removed = CERTAIN_WORDS & removed

    # Structural
    orig_sentences = [s.strip() for s in original.split(".") if s.strip()]
    edit_sentences = [s.strip() for s in edited.split(".") if s.strip()]
    avg_orig_len = sum(len(s.split()) for s in orig_sentences) / max(len(orig_sentences), 1)
    avg_edit_len = sum(len(s.split()) for s in edit_sentences) / max(len(edit_sentences), 1)

    compression_ratio = len(edited) / max(len(original), 1)

    # Tonal signals from instruction
    tonal_signals: list[tuple[str, str]] = []
    instruction_lower = user_instruction.lower()
    for marker, (dimension, value) in TONAL_MARKERS.items():
        if marker in instruction_lower:
            tonal_signals.append((dimension, value))

    # Confidence direction
    confidence_shift = None
    if hedges_removed and not hedges_added:
        confidence_shift = "more_certain"
    elif hedges_added and not hedges_removed:
        confidence_shift = "more_hedged"
    elif certain_added and not certain_removed:
        confidence_shift = "more_certain"

    return {
        "words_added": list(added),
        "words_removed": list(removed),
        "hedges_removed": list(hedges_removed),
        "hedges_added": list(hedges_added),
        "certain_added": list(certain_added),
        "avg_sentence_length_before": round(avg_orig_len, 1),
        "avg_sentence_length_after": round(avg_edit_len, 1),
        "compression_ratio": round(compression_ratio, 2),
        "tonal_signals": tonal_signals,
        "confidence_shift": confidence_shift,
        "sentence_count_before": len(orig_sentences),
        "sentence_count_after": len(edit_sentences),
    }


# ---------------------------------------------------------------------------
# LLM escalation — ambiguous edit classification
# LLM returns confidence score only. Rules-based layer makes final decision.
# ---------------------------------------------------------------------------

async def llm_classify_edit(
    original: str,
    edited: str,
    user_instruction: str,
) -> tuple[EditClass, float]:
    """
    Calls LLM to score edit classification confidence.
    Returns (edit_class, confidence_score).
    The LLM never makes the final decision — it returns a score only.
    Rules-based layer accepts or rejects based on threshold.
    """
    if not _ANTHROPIC_API_KEY:
        logger.warning("llm_escalation_skipped_no_api_key")
        return EditClass.AMBIGUOUS, 0.0

    prompt = f"""You are an edit classifier. Classify this edit as one of: voice, content, intent, factual, format.

DEFINITIONS:
- voice: changes HOW something is said (tone, directness, hedging, formality, length)
- content: changes WHAT information is included or excluded
- factual: corrects a specific fact, number, date, or name
- format: changes layout, bullets, headers, structure
- intent: changes the purpose or goal of the message

Original: {original}
Edited: {edited}
User instruction: {user_instruction}

Respond with JSON only. No preamble. Format:
{{"classification": "voice|content|intent|factual|format", "confidence": 0.0-1.0, "reasoning": "one sentence"}}"""

    try:
        # Delegate to the rendering layer — boundary contract requires LLM calls
        # to originate only from voxa-rendering. Calibration passes the prompt;
        # rendering executes the call and returns the raw text.
        from voxa_rendering.llm_boundary import classify_edit_via_llm
        return await classify_edit_via_llm(prompt)
    except Exception as e:
        logger.warning("llm_escalation_failed", error=str(e))
        return EditClass.AMBIGUOUS, 0.0


# ---------------------------------------------------------------------------
# Negative evidence tracking
# ---------------------------------------------------------------------------

def record_negative_evidence(
    rule: RuleMetadata,
    dimension: str,
    event_id: UUID,
    pattern_reversed: str,
    profile: VoiceProfile,
) -> tuple[RuleMetadata, bool]:
    """
    Records a reversal as negative evidence against a rule.
    Reduces confidence. Triggers demotion if sufficient negative evidence.
    Returns (updated_rule, demoted).
    """
    # Reduce confidence
    old_confidence = rule.confidence
    rule.confidence = max(0.0, rule.confidence - 0.08)
    rule.stability = max(0.0, rule.stability - 0.05)

    logger.info(
        "negative_evidence_recorded",
        dimension=dimension,
        event_id=str(event_id),
        pattern_reversed=pattern_reversed,
        old_confidence=old_confidence,
        new_confidence=rule.confidence,
    )

    # Demotion trigger — if confidence drops significantly below stage threshold
    stage_thresholds = {
        LifecycleStage.CORE: 0.85,
        LifecycleStage.STABLE: 0.60,
        LifecycleStage.PROVISIONAL: 0.35,
        LifecycleStage.CANDIDATE: 0.20,
    }

    threshold = stage_thresholds.get(rule.lifecycle_stage)
    if threshold and rule.confidence < threshold:
        rule = demote_rule(rule, dimension, reason=f"negative_evidence_below_threshold_{pattern_reversed}")
        return rule, True

    return rule, False


# ---------------------------------------------------------------------------
# Self-report conflict tracking
# ---------------------------------------------------------------------------

def check_self_report_conflict(
    onboarding_preference: str,
    observed_behaviour_value: str,
    contrary_edit_count: int,
    dimension: str,
) -> bool:
    """
    Tracks onboarding statements vs observed behaviour.
    Conflict is surfaced, not silently resolved.
    Returns True if conflict threshold is met.
    """
    if onboarding_preference != observed_behaviour_value:
        if contrary_edit_count >= SELF_REPORT_CONFLICT_THRESHOLD:
            logger.warning(
                "self_report_conflict_threshold_met",
                dimension=dimension,
                onboarding_preference=onboarding_preference,
                observed_value=observed_behaviour_value,
                contrary_edit_count=contrary_edit_count,
            )
            return True
    return False


# ---------------------------------------------------------------------------
# Full validation gate — CANDIDATE → PROVISIONAL
# ---------------------------------------------------------------------------

def run_validation_gate(
    candidate: RuleCandidate,
    evidence_timestamps: list[datetime],
    values_observed: list[str],
    has_active_conflict: bool = False,
) -> tuple[bool, str, float]:
    """
    Full validation gate for CANDIDATE → PROVISIONAL promotion.
    All four conditions must be met.
    Returns (approved, reason, computed_confidence).
    """
    recency = compute_recency_score(evidence_timestamps)
    consistency = compute_consistency_score(
        values_observed, str(candidate.candidate_value)
    )

    confidence_result = compute_confidence(ConfidenceInputs(
        evidence_count=candidate.evidence_count,
        consistency_score=consistency,
        recency_score=recency,
        source_weight=0.4,
    ))

    can, reason = can_promote_to_provisional(
        confidence=confidence_result.confidence,
        consistency=consistency,
        evidence_count=candidate.evidence_count,
        has_active_conflict=has_active_conflict,
    )

    logger.info(
        "validation_gate_result",
        dimension=candidate.rule_dimension,
        approved=can,
        confidence=confidence_result.confidence,
        consistency=consistency,
        recency=recency,
        evidence_count=candidate.evidence_count,
        reason=reason,
    )

    return can, reason, confidence_result.confidence
