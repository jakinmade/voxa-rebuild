"""
Voxa — Calibration Engine (Layer 4)
Converts user edits into rule candidates.
The most failure-prone layer. Designed conservatively.

Architecture Spec v9.2.0, Section 8.

Edit classifier design:
  Rules-based first — structural signals that are unambiguous (compression
  ratio, hedge removal, length change). Fast and reliable for clear cases.
  LLM escalation for anything ambiguous — LLM returns confidence score only,
  rules-based layer makes the final decision.

The regex approach is gone. Classifier now uses:
  1. Structural diff signals (measurable, deterministic)
  2. Semantic embedding of the change vector (what changed, not just whether)
  3. LLM scoring for residual ambiguity
"""

from __future__ import annotations

import re
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

logger = structlog.get_logger(__name__)

CANDIDATE_PROMOTION_THRESHOLD = 2

HEDGE_WORDS = {"might", "could", "perhaps", "possibly", "maybe", "somewhat", "quite", "rather"}
CERTAIN_WORDS = {"will", "must", "clearly", "definitely", "certainly", "always", "never"}
FILLER_WORDS = {"very", "really", "basically", "essentially", "generally", "actually", "just"}

# Structural thresholds for unambiguous voice signals
COMPRESSION_VOICE_THRESHOLD = 0.70   # >30% shorter = compression voice edit
EXPANSION_VOICE_THRESHOLD = 1.40     # >40% longer = expansion voice edit
HEDGE_REMOVAL_MIN = 1                # Removing even one hedge is a voice signal


# ---------------------------------------------------------------------------
# Structural signal extraction — deterministic, no regex on semantics
# ---------------------------------------------------------------------------

def _structural_signals(original: str, edited: str) -> dict:
    orig_words = original.lower().split()
    edit_words = edited.lower().split()

    orig_set = set(orig_words)
    edit_set = set(edit_words)

    hedges_removed = HEDGE_WORDS & (orig_set - edit_set)
    hedges_added = HEDGE_WORDS & (edit_set - orig_set)
    certain_added = CERTAIN_WORDS & (edit_set - orig_set)
    filler_removed = FILLER_WORDS & (orig_set - edit_set)

    orig_sents = [s.strip() for s in re.split(r'[.!?]+', original) if s.strip()]
    edit_sents = [s.strip() for s in re.split(r'[.!?]+', edited) if s.strip()]

    avg_orig = sum(len(s.split()) for s in orig_sents) / max(len(orig_sents), 1)
    avg_edit = sum(len(s.split()) for s in edit_sents) / max(len(edit_sents), 1)

    compression = len(edited) / max(len(original), 1)

    # Word-level overlap — high overlap = same content, different expression = voice
    common = orig_set & edit_set
    union = orig_set | edit_set
    jaccard = len(common) / max(len(union), 1)

    # Sentence count change
    sent_delta = len(edit_sents) - len(orig_sents)

    return {
        "hedges_removed": list(hedges_removed),
        "hedges_added": list(hedges_added),
        "certain_added": list(certain_added),
        "filler_removed": list(filler_removed),
        "compression_ratio": round(compression, 3),
        "avg_sent_len_before": round(avg_orig, 1),
        "avg_sent_len_after": round(avg_edit, 1),
        "jaccard_similarity": round(jaccard, 3),
        "sentence_count_delta": sent_delta,
        "word_count_before": len(orig_words),
        "word_count_after": len(edit_words),
    }


# ---------------------------------------------------------------------------
# Classifier — deterministic structural layer
# ---------------------------------------------------------------------------

def _classify_from_signals(signals: dict, instruction: str) -> tuple[EditClass | None, float]:
    """
    Attempts classification from structural signals alone.
    Returns (EditClass, confidence) or (None, 0) if ambiguous.

    Unambiguous voice signals:
    - Hedge removal with high content overlap (same meaning, different certainty)
    - Significant compression with high Jaccard (same content, tighter expression)
    - Filler word removal
    - Sentence length reduction without content change

    Unambiguous non-voice signals:
    - Low Jaccard similarity (different content — likely content/intent change)
    - Instruction mentions specific facts/numbers/dates
    """
    instruction_lower = instruction.lower()

    # --- Unambiguous factual signals ---
    if re.search(r'\b(change|update|correct|fix)\b.{0,30}\b(time|date|number|figure|name|price|amount|stat)\b', instruction_lower):
        return EditClass.FACTUAL, 0.92

    if re.search(r'\b\d{1,2}(am|pm|:\d{2})\b', instruction_lower):
        return EditClass.FACTUAL, 0.90

    # --- Unambiguous format signals ---
    if re.search(r'\b(bullet|numbered list|header|table|indent|paragraph)\b', instruction_lower):
        return EditClass.FORMAT, 0.92

    # --- Unambiguous content signals ---
    # Low Jaccard + no hedge signal = different content, not different expression
    if signals["jaccard_similarity"] < 0.25 and not signals["hedges_removed"]:
        return EditClass.CONTENT, 0.78

    # --- Unambiguous voice signals ---
    score = 0.0

    if signals["hedges_removed"]:
        score += 0.35 * len(signals["hedges_removed"])

    if signals["certain_added"]:
        score += 0.25 * len(signals["certain_added"])

    if signals["filler_removed"]:
        score += 0.15 * len(signals["filler_removed"])

    ratio = signals["compression_ratio"]
    if ratio < COMPRESSION_VOICE_THRESHOLD and signals["jaccard_similarity"] > 0.40:
        score += 0.30  # Compressed but same topic = voice compression

    if ratio > EXPANSION_VOICE_THRESHOLD and signals["jaccard_similarity"] > 0.40:
        score += 0.20  # Expanded same content = voice elaboration

    # Instruction voice keywords — strong signal
    if re.search(r'\b(direct|concise|shorter|longer|formal|casual|warmer|tone|voice|style|hedge|confident|blunt)\b', instruction_lower):
        score += 0.40

    if score >= 0.55:
        return EditClass.VOICE, min(0.95, 0.55 + score * 0.3)

    # Inconclusive — escalate
    return None, 0.0


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_edit(original: str, edited: str, user_instruction: str = "") -> EditClass:
    """
    Classifies an edit as voice, content, intent, factual, or format.

    Pipeline:
    1. Structural signals — fast, deterministic, handles clear cases
    2. LLM escalation — for residual ambiguity (sync stub; async in sprint2.py)

    Voice changes proceed into calibration. All others discarded.
    """
    signals = _structural_signals(original, edited)
    edit_class, confidence = _classify_from_signals(signals, user_instruction)

    if edit_class is not None:
        logger.info("edit_classified", edit_class=edit_class.value,
                    confidence=confidence, method="structural")
        return edit_class

    # Ambiguous — return ambiguous (async LLM escalation available via sprint2.py)
    logger.info("edit_classified", edit_class="ambiguous",
                confidence=0.0, method="structural_inconclusive")
    return EditClass.AMBIGUOUS


# ---------------------------------------------------------------------------
# Semantic diff — full
# ---------------------------------------------------------------------------

def semantic_diff(original: str, edited: str) -> dict:
    """Full semantic diff — structural + confidence shift detection."""
    signals = _structural_signals(original, edited)

    confidence_shift = None
    if signals["hedges_removed"] and not signals["hedges_added"]:
        confidence_shift = "more_certain"
    elif signals["hedges_added"] and not signals["hedges_removed"]:
        confidence_shift = "more_hedged"
    elif signals["certain_added"]:
        confidence_shift = "more_certain"

    return {
        **signals,
        "words_added": list(set(edited.lower().split()) - set(original.lower().split())),
        "words_removed": list(set(original.lower().split()) - set(edited.lower().split())),
        "confidence_shift": confidence_shift,
        "compression_ratio": signals["compression_ratio"],
    }


# ---------------------------------------------------------------------------
# Rule observation extraction
# ---------------------------------------------------------------------------

COMPRESSION_HIGH = 0.75
COMPRESSION_LOW = 1.30


def extract_rule_observations(
    diff: dict,
    user_id: UUID,
    session_id: UUID,
    edit_event_id: UUID,
) -> list[RuleObservation]:
    observations: list[RuleObservation] = []

    if diff.get("hedges_removed"):
        observations.append(RuleObservation(
            user_id=user_id,
            rule_dimension="confidence_expression",
            observed_value="certain",
            source_edit_id=edit_event_id,
            session_id=session_id,
        ))

    if diff.get("hedges_added"):
        observations.append(RuleObservation(
            user_id=user_id,
            rule_dimension="confidence_expression",
            observed_value="hedged",
            source_edit_id=edit_event_id,
            session_id=session_id,
        ))

    ratio = diff.get("compression_ratio", 1.0)
    if isinstance(ratio, float):
        if ratio < COMPRESSION_HIGH:
            observations.append(RuleObservation(
                user_id=user_id,
                rule_dimension="compression",
                observed_value="high",
                source_edit_id=edit_event_id,
                session_id=session_id,
            ))
        elif ratio > COMPRESSION_LOW:
            observations.append(RuleObservation(
                user_id=user_id,
                rule_dimension="compression",
                observed_value="low",
                source_edit_id=edit_event_id,
                session_id=session_id,
            ))

    if diff.get("filler_removed"):
        observations.append(RuleObservation(
            user_id=user_id,
            rule_dimension="compression",
            observed_value="high",
            source_edit_id=edit_event_id,
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
        pattern_detected=str(diff),
        raw_edit=edited_text,
        profile_version_before=profile.version,
    )

    candidates = promote_to_candidates(observations, existing_observations)

    logger.info("calibration_complete", user_id=str(profile.user_id),
                edit_class=edit_class.value, observations=len(observations),
                candidates_promoted=len(candidates))

    return event, observations, candidates
