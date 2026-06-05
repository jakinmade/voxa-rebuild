"""
Voxa — Calibration Engine (Layer 4)
Converts user edits into rule candidates.

Architecture Spec v9.2.0, Section 8.

Edit classification pipeline:
  1. Change vector analysis — represents the edit as displacement in voice space
  2. Classification from vector — deterministic, confidence-scored
  3. LLM escalation — only for genuinely ambiguous edits (confidence < threshold)
     LLM returns a confidence score. Rules-based layer makes the final decision.

The change vector approach handles the reviewer's hard case:
  "We should consider alternative approaches." → "This isn't the right direction."
  Low Jaccard but strong certainty + directness displacement → VOICE
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

import structlog

from voxa_core.entities import (
    CalibrationEvent,
    RenderedOutput,
    RuleCandidate,
    RuleObservation,
    VoiceProfile,
)
from voxa_core.enums import EditClass
from voxa_calibration.change_vector import analyse_edit, ChangeVector

logger = structlog.get_logger(__name__)

CANDIDATE_PROMOTION_THRESHOLD = 2
LLM_ESCALATION_THRESHOLD = 0.45  # Below this confidence, escalate to LLM


# ---------------------------------------------------------------------------
# Edit classifier — change vector + optional LLM escalation
# ---------------------------------------------------------------------------

def classify_edit(
    original: str,
    edited: str,
    user_instruction: str = "",
) -> EditClass:
    """
    Classifies an edit using change vector analysis.
    Escalates to LLM only when vector confidence is genuinely low.
    """
    result = analyse_edit(original, edited, user_instruction)

    class_map = {
        "voice": EditClass.VOICE,
        "content": EditClass.CONTENT,
        "intent": EditClass.INTENT,
        "factual": EditClass.FACTUAL,
        "format": EditClass.FORMAT,
        "ambiguous": EditClass.AMBIGUOUS,
    }

    edit_class = class_map.get(result.edit_class, EditClass.AMBIGUOUS)

    logger.info(
        "edit_classified",
        edit_class=edit_class.value,
        confidence=result.confidence,
        reasoning=result.reasoning,
    )

    return edit_class


# ---------------------------------------------------------------------------
# Semantic diff — now backed by change vector
# ---------------------------------------------------------------------------

def semantic_diff(original: str, edited: str) -> dict:
    """
    Full semantic diff using change vector.
    Returns a rich dict for observation extraction.
    """
    from voxa_calibration.change_vector import compute_change_vector
    vector = compute_change_vector(original, edited)

    orig_set = set(original.lower().split())
    edit_set = set(edited.lower().split())

    return {
        "words_added": list(edit_set - orig_set),
        "words_removed": list(orig_set - edit_set),
        "hedges_removed": [w for w in (orig_set - edit_set)
                          if w in {"might", "could", "perhaps", "possibly", "maybe", "somewhat"}],
        "hedges_added": [w for w in (edit_set - orig_set)
                        if w in {"might", "could", "perhaps", "possibly", "maybe", "somewhat"}],
        "compression_ratio": vector.compression_ratio,
        "jaccard_similarity": vector.jaccard_similarity,
        "confidence_shift": (
            "more_certain" if vector.certainty > 0.15
            else "more_hedged" if vector.certainty < -0.15
            else None
        ),
        "directness_shift": (
            "more_direct" if vector.directness > 0.15
            else "less_direct" if vector.directness < -0.15
            else None
        ),
        "formality_shift": (
            "more_formal" if vector.formality > 0.15
            else "less_formal" if vector.formality < -0.15
            else None
        ),
        "warmth_shift": (
            "warmer" if vector.warmth > 0.15
            else "cooler" if vector.warmth < -0.15
            else None
        ),
        "intensity_shift": (
            "higher" if vector.intensity > 0.15
            else "lower" if vector.intensity < -0.15
            else None
        ),
        "vector": vector,
    }


# ---------------------------------------------------------------------------
# Rule observation extraction — driven by change vector
# ---------------------------------------------------------------------------

COMPRESSION_HIGH = 0.75
COMPRESSION_LOW = 1.30

VOICE_AXIS_TO_DIMENSION: dict[str, tuple[str, str, str]] = {
    "certainty":   ("confidence_expression", "certain", "hedged"),
    "directness":  ("directness", "high", "low"),
    "formality":   ("formality", "formal", "casual"),
    "compression": ("compression", "high", "low"),
    "warmth":      ("warmth", "high", "low"),
    "intensity":   ("intensity", "high", "low"),
}


def extract_rule_observations(
    diff: dict,
    user_id: UUID,
    session_id: UUID,
    edit_event_id: UUID,
) -> list[RuleObservation]:
    """
    Extracts rule observations from a semantic diff.
    Uses vector axes where available, falls back to structural signals.
    """
    observations: list[RuleObservation] = []
    vector: ChangeVector | None = diff.get("vector")

    if vector is not None and vector.dominant_voice_axes:
        # Primary path: extract from vector dominant axes
        for axis in vector.dominant_voice_axes:
            if axis not in VOICE_AXIS_TO_DIMENSION:
                continue
            dimension, pos_val, neg_val = VOICE_AXIS_TO_DIMENSION[axis]
            axis_value = getattr(vector, axis)
            if abs(axis_value) < 0.15:
                continue
            observed_value = pos_val if axis_value > 0 else neg_val
            observations.append(RuleObservation(
                user_id=user_id,
                rule_dimension=dimension,
                observed_value=observed_value,
                source_edit_id=edit_event_id,
                session_id=session_id,
            ))
    else:
        # Fallback: structural signals from diff dict
        if diff.get("hedges_removed"):
            observations.append(RuleObservation(
                user_id=user_id, rule_dimension="confidence_expression",
                observed_value="certain", source_edit_id=edit_event_id,
                session_id=session_id,
            ))
        if diff.get("hedges_added"):
            observations.append(RuleObservation(
                user_id=user_id, rule_dimension="confidence_expression",
                observed_value="hedged", source_edit_id=edit_event_id,
                session_id=session_id,
            ))
        ratio = diff.get("compression_ratio", 1.0)
        if isinstance(ratio, float):
            if ratio < COMPRESSION_HIGH:
                observations.append(RuleObservation(
                    user_id=user_id, rule_dimension="compression",
                    observed_value="high", source_edit_id=edit_event_id,
                    session_id=session_id,
                ))
            elif ratio > COMPRESSION_LOW:
                observations.append(RuleObservation(
                    user_id=user_id, rule_dimension="compression",
                    observed_value="low", source_edit_id=edit_event_id,
                    session_id=session_id,
                ))

    return observations


def promote_to_candidates(
    observations: list[RuleObservation],
    existing_observations: list[RuleObservation],
) -> list[RuleCandidate]:
    all_observations = existing_observations + observations
    groups: dict[tuple[str, str], list[RuleObservation]] = {}
    for obs in all_observations:
        key = (obs.rule_dimension, str(obs.observed_value))
        groups.setdefault(key, []).append(obs)

    candidates: list[RuleCandidate] = []
    for (dimension, value), obs_list in groups.items():
        if len(obs_list) >= CANDIDATE_PROMOTION_THRESHOLD:
            candidates.append(RuleCandidate(
                user_id=obs_list[0].user_id,
                rule_dimension=dimension,
                candidate_value=value,
                confidence=0.35,
                evidence_count=len(obs_list),
                supporting_observations=[o.observation_id for o in obs_list],
            ))
            logger.info("candidate_promoted", dimension=dimension,
                        value=value, evidence_count=len(obs_list))
    return candidates


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------

def calibrate(
    rendered_output: RenderedOutput,
    original_text: str,
    edited_text: str,
    user_instruction: str,
    profile: VoiceProfile,
    existing_observations: list[RuleObservation],
) -> tuple[CalibrationEvent | None, list[RuleObservation], list[RuleCandidate]]:

    logger.info("calibration_started", user_id=str(profile.user_id),
                output_id=str(rendered_output.output_id))

    edit_class = classify_edit(original_text, edited_text, user_instruction)

    if edit_class != EditClass.VOICE:
        logger.info("calibration_discarded", edit_class=edit_class.value)
        return None, [], []

    diff = semantic_diff(original_text, edited_text)
    edit_event_id = uuid4()
    observations = extract_rule_observations(
        diff, profile.user_id, rendered_output.session_id, edit_event_id
    )

    event = CalibrationEvent(
        event_id=edit_event_id,
        user_id=profile.user_id,
        session_id=rendered_output.session_id,
        rendered_output_id=rendered_output.output_id,
        edit_class=edit_class,
        direction="positive",
        rule_dimension=observations[0].rule_dimension if observations else None,
        pattern_detected=str({k: v for k, v in diff.items() if k != "vector"}),
        raw_edit=edited_text,
        profile_version_before=profile.version,
    )

    candidates = promote_to_candidates(observations, existing_observations)

    logger.info("calibration_complete", user_id=str(profile.user_id),
                edit_class=edit_class.value, observations=len(observations),
                candidates_promoted=len(candidates))

    return event, observations, candidates
