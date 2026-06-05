"""
Voxa — Calibration Engine (Layer 4)
Converts user edits into rule candidates.
The most failure-prone layer. Designed conservatively.

Architecture Spec v9.2.0, Section 8.

Sprint 1 scope:
- Rules-based edit classifier: { voice | content | intent | factual | format }
- Voice changes proceed. All others discarded.
- Basic semantic diff — lexical and structural changes
- Rule candidate extraction from classified voice edits
- OBSERVED → CANDIDATE promotion (basic repetition check)
- Positive evidence tracking only
- CalibrationEvent stored on every accepted edit

Sprint 2 adds:
- LLM escalation for ambiguous edits
- Full semantic diff (tonal, confidence changes)
- Negative evidence tracking
- Full validation gate with confidence thresholds
- CANDIDATE → PROVISIONAL RULE promotion
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
    VoiceProfileVersion,
)
from voxa_core.enums import EditClass

logger = structlog.get_logger(__name__)

# Minimum observations before a pattern becomes a candidate
CANDIDATE_PROMOTION_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Edit Classifier — rules-based only (Sprint 1)
# Sprint 2 adds LLM escalation for ambiguous edits
# ---------------------------------------------------------------------------

# Voice change signals — edits that change HOW something is said
VOICE_CHANGE_PATTERNS = [
    re.compile(r"\b(shorter|longer|more direct|less formal|more formal|simpler|plainer)\b", re.I),
    re.compile(r"\b(remove the|cut the|drop the)\b.*(hedge|filler|jargon|waffle|fluff)\b", re.I),
    re.compile(r"\b(make it|keep it)\b.*(brief|concise|tight|direct|warm|casual|formal)\b", re.I),
    re.compile(r"\b(stop|don.t|avoid)\b.*(saying|using|hedging|waffling)\b", re.I),
    re.compile(r"\b(more confident|less hedging|sound more|tone down)\b", re.I),
]

# Content change signals — edits that change WHAT is said
CONTENT_CHANGE_PATTERNS = [
    re.compile(r"\b(add|include|mention|say that|tell them|explain)\b", re.I),
    re.compile(r"\b(remove|delete|take out|don.t mention|leave out)\b.*(fact|point|detail|section)\b", re.I),
    re.compile(r"\b(change the|update the)\b.*(number|date|figure|stat|fact)\b", re.I),
]

# Factual change signals
FACTUAL_CHANGE_PATTERNS = [
    re.compile(r"\b(wrong|incorrect|that.s not right|should be|it.s actually)\b", re.I),
    re.compile(r"\b\d+\b.*\b(should be|not)\b.*\b\d+\b", re.I),
]

# Format change signals
FORMAT_CHANGE_PATTERNS = [
    re.compile(r"\b(bullet|numbered|list|paragraph|section|header|table)\b", re.I),
    re.compile(r"\b(indent|spacing|layout|format)\b", re.I),
]

# Intent change signals
INTENT_CHANGE_PATTERNS = [
    re.compile(r"\b(actually|instead|rather|change the purpose|different angle)\b", re.I),
    re.compile(r"\b(this should be|make it a|turn it into)\b.*(request|proposal|complaint|apology)\b", re.I),
]


def classify_edit(original: str, edited: str, user_instruction: str = "") -> EditClass:
    """
    Classifies an edit as voice, content, intent, factual, or format.

    Rules-based only in Sprint 1.
    Sprint 2 adds LLM escalation for edits this classifier cannot resolve.

    Voice changes proceed into the calibration pipeline.
    All others are discarded.
    """
    text = (user_instruction + " " + edited).strip()

    # Check voice first — it's what we're looking for
    for pattern in VOICE_CHANGE_PATTERNS:
        if pattern.search(text) or pattern.search(user_instruction):
            logger.info("edit_classified", edit_class="voice")
            return EditClass.VOICE

    # Factual before content — more specific
    for pattern in FACTUAL_CHANGE_PATTERNS:
        if pattern.search(text):
            logger.info("edit_classified", edit_class="factual")
            return EditClass.FACTUAL

    for pattern in CONTENT_CHANGE_PATTERNS:
        if pattern.search(text):
            logger.info("edit_classified", edit_class="content")
            return EditClass.CONTENT

    for pattern in FORMAT_CHANGE_PATTERNS:
        if pattern.search(text):
            logger.info("edit_classified", edit_class="format")
            return EditClass.FORMAT

    for pattern in INTENT_CHANGE_PATTERNS:
        if pattern.search(text):
            logger.info("edit_classified", edit_class="intent")
            return EditClass.INTENT

    # Check structural diff between original and edited text as a voice signal
    if _structural_diff_is_voice_signal(original, edited):
        logger.info("edit_classified", edit_class="voice", method="structural_diff")
        return EditClass.VOICE

    # Sprint 1: ambiguous edits are discarded (Sprint 2: LLM escalation)
    logger.info("edit_classified", edit_class="ambiguous")
    return EditClass.AMBIGUOUS


def _structural_diff_is_voice_signal(original: str, edited: str) -> bool:
    """
    Basic structural diff — detects voice-level changes without instruction text.
    Sprint 2 extends this to tonal and confidence changes.
    """
    orig_words = original.lower().split()
    edit_words = edited.lower().split()

    if not orig_words:
        return False

    length_ratio = len(edit_words) / len(orig_words) if orig_words else 1.0

    # Significant compression or expansion — likely a voice change
    if length_ratio < 0.6 or length_ratio > 1.6:
        return True

    # Hedging words removed
    hedge_words = {"might", "could", "perhaps", "possibly", "maybe", "somewhat"}
    orig_hedges = len([w for w in orig_words if w in hedge_words])
    edit_hedges = len([w for w in edit_words if w in hedge_words])
    if orig_hedges > 0 and edit_hedges == 0:
        return True

    return False


# ---------------------------------------------------------------------------
# Semantic diff — lexical and structural (Sprint 1)
# ---------------------------------------------------------------------------

def semantic_diff(original: str, edited: str) -> dict[str, object]:
    """
    Detects what changed between original and edited text.
    Sprint 1: lexical and structural changes.
    Sprint 2: adds tonal and confidence change detection.
    """
    orig_words = set(original.lower().split())
    edit_words = set(edited.lower().split())

    added = edit_words - orig_words
    removed = orig_words - edit_words

    hedge_words = {"might", "could", "perhaps", "possibly", "maybe", "somewhat", "quite"}
    hedges_removed = hedge_words & removed
    hedges_added = hedge_words & added

    orig_sentences = re.split(r"[.!?]", original)
    edit_sentences = re.split(r"[.!?]", edited)
    avg_orig_len = sum(len(s.split()) for s in orig_sentences) / max(len(orig_sentences), 1)
    avg_edit_len = sum(len(s.split()) for s in edit_sentences) / max(len(edit_sentences), 1)

    return {
        "words_added": list(added),
        "words_removed": list(removed),
        "hedges_removed": list(hedges_removed),
        "hedges_added": list(hedges_added),
        "avg_sentence_length_before": round(avg_orig_len, 1),
        "avg_sentence_length_after": round(avg_edit_len, 1),
        "compression_ratio": round(len(edited) / max(len(original), 1), 2),
    }


# ---------------------------------------------------------------------------
# Rule candidate extraction
# ---------------------------------------------------------------------------

DIFF_TO_DIMENSION: list[tuple[str, str, object]] = [
    ("hedges_removed", "confidence_expression", "certain"),
    ("hedges_added", "confidence_expression", "hedged"),
]

COMPRESSION_THRESHOLD_HIGH = 0.75
COMPRESSION_THRESHOLD_LOW = 1.30


def extract_rule_observations(
    diff: dict[str, object],
    user_id: UUID,
    session_id: UUID,
    edit_event_id: UUID,
) -> list[RuleObservation]:
    """
    Converts a semantic diff into RuleObservations.
    Observations are pre-candidate — no profile impact.
    """
    observations: list[RuleObservation] = []

    # Hedging signal
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

    # Compression signal
    ratio = diff.get("compression_ratio", 1.0)
    if isinstance(ratio, float):
        if ratio < COMPRESSION_THRESHOLD_HIGH:
            observations.append(RuleObservation(
                user_id=user_id,
                rule_dimension="compression",
                observed_value="high",
                source_edit_id=edit_event_id,
                session_id=session_id,
            ))
        elif ratio > COMPRESSION_THRESHOLD_LOW:
            observations.append(RuleObservation(
                user_id=user_id,
                rule_dimension="compression",
                observed_value="low",
                source_edit_id=edit_event_id,
                session_id=session_id,
            ))

    return observations


def promote_to_candidates(
    observations: list[RuleObservation],
    existing_observations: list[RuleObservation],
) -> list[RuleCandidate]:
    """
    Checks if any observation pattern has been seen enough times
    to promote from OBSERVED to CANDIDATE.

    Sprint 1: basic repetition check — CANDIDATE_PROMOTION_THRESHOLD consistent observations.
    Sprint 2: full validation gate with confidence thresholds.
    """
    all_observations = existing_observations + observations

    # Group by dimension and value
    groups: dict[tuple[str, str], list[RuleObservation]] = {}
    for obs in all_observations:
        key = (obs.rule_dimension, str(obs.observed_value))
        groups.setdefault(key, []).append(obs)

    candidates: list[RuleCandidate] = []
    for (dimension, value), obs_list in groups.items():
        if len(obs_list) >= CANDIDATE_PROMOTION_THRESHOLD:
            candidate = RuleCandidate(
                user_id=obs_list[0].user_id,
                rule_dimension=dimension,
                candidate_value=value,
                confidence=0.35,  # Sprint 2 replaces with formula
                evidence_count=len(obs_list),
                supporting_observations=[o.observation_id for o in obs_list],
            )
            candidates.append(candidate)
            logger.info(
                "candidate_promoted",
                dimension=dimension,
                value=value,
                evidence_count=len(obs_list),
            )

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
    """
    Main calibration pipeline.

    Returns:
    - CalibrationEvent (if voice edit) or None (if discarded)
    - New RuleObservations
    - New RuleCandidates (if promotion threshold met)
    """
    logger.info(
        "calibration_started",
        user_id=str(profile.user_id),
        output_id=str(rendered_output.output_id),
    )

    # Step 1 — Classify edit
    edit_class = classify_edit(original_text, edited_text, user_instruction)

    # Step 2 — Discard non-voice edits
    if edit_class != EditClass.VOICE:
        logger.info(
            "calibration_discarded",
            edit_class=edit_class.value,
            reason="not_a_voice_edit",
        )
        return None, [], []

    # Step 3 — Semantic diff
    diff = semantic_diff(original_text, edited_text)

    # Step 4 — Extract rule observations
    edit_event_id = uuid4()
    session_id = rendered_output.session_id
    observations = extract_rule_observations(diff, profile.user_id, session_id, edit_event_id)

    # Step 5 — CalibrationEvent
    event = CalibrationEvent(
        event_id=edit_event_id,
        user_id=profile.user_id,
        session_id=session_id,
        rendered_output_id=rendered_output.output_id,
        edit_class=edit_class,
        direction="positive",  # Sprint 2 adds negative evidence tracking
        rule_dimension=observations[0].rule_dimension if observations else None,
        pattern_detected=str(diff),
        raw_edit=edited_text,
        profile_version_before=profile.version,
    )

    # Step 6 — Promote observations to candidates if threshold met
    candidates = promote_to_candidates(observations, existing_observations)

    logger.info(
        "calibration_complete",
        user_id=str(profile.user_id),
        edit_class=edit_class.value,
        observations=len(observations),
        candidates_promoted=len(candidates),
    )

    return event, observations, candidates
