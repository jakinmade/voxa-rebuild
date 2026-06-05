"""
Voxa — Change Vector
Represents an edit as movement in a multi-dimensional voice space.

The fundamental shift from heuristic classification:
  OLD: "does this edit contain voice signals?" → label
  NEW: "where does this edit move the text in voice space?" → vector

Every dimension of voice has a direction and magnitude.
An edit is a displacement on multiple axes simultaneously.
Classification emerges from the dominant axes of displacement.

The reviewer's example:
  Original: "We should consider alternative approaches."
  Edited:   "This isn't the right direction."

Old approach: low Jaccard → content? hedge removal → voice? ambiguous.
Vector approach:
  certainty:    +0.6  (should/consider → isn't/right — strong certainty shift)
  directness:   +0.5  (we should consider → this isn't — ownership and decisiveness)
  compression:  +0.4  (shorter, denser)
  content:      -0.2  (different subject matter signal)
  dominant axes: certainty + directness → VOICE edit with confidence_expression + directness observations

Voice axes: certainty, directness, formality, compression, warmth, intensity
Non-voice axes: content_shift, intent_shift, factual_correction, format_change

When non-voice axes dominate → non-voice classification
When voice axes dominate → voice classification with dimension observations
When mixed → confidence score, LLM escalation if below threshold
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import NamedTuple

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Lexical maps for each voice axis
# Each entry: (pattern, direction, weight)
# direction: +1 = more of this quality, -1 = less
# ---------------------------------------------------------------------------

CERTAINTY_INCREASING = [
    (r"\b(will|must|is|are|clearly|definitely|certainly|always|never)\b", 1, 0.30),
    (r"\b(this (is|isn.t|works|doesn.t))\b", 1, 0.35),
    (r"\b(the (right|wrong|correct|incorrect))\b", 1, 0.40),
    (r"\b(not the right|wrong direction|wrong approach)\b", 1, 0.50),
]

CERTAINTY_DECREASING = [
    (r"\b(might|could|perhaps|possibly|maybe|somewhat|consider|explore)\b", 1, 0.25),
    (r"\b(we should (consider|think about|look at|explore))\b", 1, 0.35),
    (r"\b(it (may|might|could) be)\b", 1, 0.30),
]

DIRECTNESS_INCREASING = [
    (r"\bthis (is|isn.t|works|doesn.t|needs)\b", 1, 0.40),
    (r"\b(stop|don.t|never|always|must)\b", 1, 0.30),
    (r"^(no\.|yes\.|done\.|clear\.)", 1, 0.50),  # Short declarative
    (r"\b(the answer is|the problem is|the issue is)\b", 1, 0.35),
    (r"^[A-Z][a-z]+ (it|this|them|now|today)\.", 1, 0.45),  # Imperative: "Do it." "Take action now."
    (r"\b(do|take|make|send|call|fix|build|close|launch|ship) (it|this|that|action|the)\b", 1, 0.35),
]

DIRECTNESS_DECREASING = [
    (r"\b(we (should|could|might)|let.s (consider|think|explore))\b", 1, 0.30),
    (r"\b(one (option|approach|possibility) (is|would be))\b", 1, 0.25),
    (r"\b(it (might|may|could) be worth)\b", 1, 0.30),
]

FORMALITY_INCREASING = [
    (r"\b(pursuant|accordingly|therefore|furthermore|henceforth)\b", 1, 0.50),
    (r"\b(I would (suggest|recommend|propose))\b", 1, 0.30),
    (r"\b(with respect to|in relation to|with regard to)\b", 1, 0.40),
]

FORMALITY_DECREASING = [
    (r"\b(basically|honestly|look|listen|here.s the thing)\b", 1, 0.35),
    (r"\b(you know|right\?|fair enough|fair point)\b", 1, 0.30),
    (r"\b(yeah|nope|yep|ok|okay)\b", 1, 0.25),
    (r"\b(hey|just wanted to|checking in|just checking)\b", 1, 0.40),
    (r"\b(wanted to (reach out|check|let you know))\b", 1, 0.35),
]

WARMTH_INCREASING = [
    (r"\b(appreciate|thank|grateful|glad|happy to)\b", 1, 0.35),
    (r"\b(great (point|question|idea|work))\b", 1, 0.30),
    (r"\b(I understand|I see|that makes sense)\b", 1, 0.25),
]

WARMTH_DECREASING = [
    (r"\b(incorrect|wrong|unacceptable|not good enough)\b", 1, 0.40),
    (r"\b(this (fails|misses|doesn.t work))\b", 1, 0.35),
    (r"\b(as I (said|mentioned|noted))\b", 1, 0.20),
]

COMPRESSION_SIGNAL = [
    # Compression is measured structurally, not lexically
    # These patterns signal deliberate compression choices
    (r"\b(in short|in brief|simply|put simply|bottom line)\b", 1, 0.30),
    (r"\b(cut to|to summarise|the point is)\b", 1, 0.35),
]

INTENSITY_INCREASING = [
    (r"\b(critical|urgent|essential|vital|crucial|must)\b", 1, 0.35),
    (r"\b(immediately|now|today|asap)\b", 1, 0.30),
    (r"\b(serious(ly)?|significant(ly)?|major)\b", 1, 0.25),
]

INTENSITY_DECREASING = [
    (r"\b(minor|small|slight|gradual|eventual(ly)?)\b", 1, 0.25),
    (r"\b(when (possible|convenient|ready))\b", 1, 0.20),
]

# Non-voice axes
CONTENT_SHIFT = [
    (r"\b(add|include|mention|also|additionally|furthermore)\b", 1, 0.30),
    (r"\b(remove|delete|omit|leave out|skip)\b", 1, 0.35),
    (r"\b(instead|rather than|not .+ but)\b", 1, 0.25),
    (r"\b(add (the|a|an)|include (the|a|an)|mention (the|a|an))\b", 1, 0.50),  # Explicit content instruction
    (r"\b(leave out|take out|cut|drop) the\b", 1, 0.45),
]

FACTUAL_CORRECTION = [
    (r"\b(wrong|incorrect|should be|not \d+|actually \d+)\b", 1, 0.60),
    (r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b", 1, 0.70),
    (r"\b(the (date|time|number|figure|name|price) (is|should be|was))\b", 1, 0.65),
    (r"\b(correct(ion)?|fix|update) the (fact|number|date|stat)\b", 1, 0.60),
]

INTENT_SHIFT = [
    (r"\b(actually|instead|change the (purpose|goal|aim|angle))\b", 1, 0.45),
    (r"\b(make (it|this) a (request|proposal|apology|complaint))\b", 1, 0.60),
    (r"\b(this should (be|ask|propose|suggest))\b", 1, 0.50),
]

FORMAT_CHANGE = [
    (r"\b(bullet(s)?|numbered|list|header|table|indent|paragraph|section)\b", 1, 0.70),
    (r"\b(format(ting)?|layout|structure|spacing)\b", 1, 0.60),
]


# ---------------------------------------------------------------------------
# ChangeVector
# ---------------------------------------------------------------------------

@dataclass
class ChangeVector:
    """
    Represents an edit as displacement in voice space.
    Each axis carries a net magnitude and direction.
    Positive = more of that quality. Negative = less.
    """
    # Voice axes (displacement from original)
    certainty: float = 0.0       # +certainty = more certain expression
    directness: float = 0.0      # +directness = more direct
    formality: float = 0.0       # +formality = more formal
    compression: float = 0.0     # +compression = shorter/denser
    warmth: float = 0.0          # +warmth = warmer
    intensity: float = 0.0       # +intensity = higher intensity

    # Non-voice axes
    content_shift: float = 0.0
    factual_correction: float = 0.0
    intent_shift: float = 0.0
    format_change: float = 0.0

    # Structural measures
    jaccard_similarity: float = 1.0
    compression_ratio: float = 1.0

    # Derived
    voice_magnitude: float = 0.0
    non_voice_magnitude: float = 0.0
    dominant_voice_axes: list[str] = field(default_factory=list)
    dominant_non_voice_axis: str | None = None


class ClassificationResult(NamedTuple):
    edit_class: str   # voice | content | intent | factual | format | ambiguous
    confidence: float
    vector: ChangeVector
    voice_observations: list[tuple[str, str]]  # [(dimension, value), ...]
    reasoning: str


# ---------------------------------------------------------------------------
# Vector computation
# ---------------------------------------------------------------------------

def _score_axis(text: str, patterns: list[tuple[str, int, float]]) -> float:
    score = 0.0
    for pattern, direction, weight in patterns:
        matches = len(re.findall(pattern, text, re.IGNORECASE))
        score += matches * direction * weight
    return round(min(1.0, max(-1.0, score)), 3)


def _jaccard(a: str, b: str) -> float:
    sa = set(a.lower().split())
    sb = set(b.lower().split())
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def _compression_ratio(original: str, edited: str) -> float:
    return len(edited) / max(len(original), 1)


def compute_change_vector(
    original: str,
    edited: str,
    instruction: str = "",
) -> ChangeVector:
    """
    Computes the change vector for an edit.
    Analyses the DELTA — what changed — not just the final state.

    Key insight: we compute signals on the instruction AND on the
    delta between original and edited, not on the edited text alone.
    """
    # Compute delta vocabulary
    orig_words = set(original.lower().split())
    edit_words = set(edited.lower().split())
    words_added = " ".join(edit_words - orig_words)
    words_removed = " ".join(orig_words - edit_words)

    # Signal text = instruction + words added (what appeared) + negated words removed
    signal_text = f"{instruction} {words_added}"
    removed_signal = words_removed  # What disappeared — often the strongest signal

    # Full edited text for pattern matching on expression
    full_edited = edited

    v = ChangeVector()
    v.jaccard_similarity = _jaccard(original, edited)
    v.compression_ratio = _compression_ratio(original, edited)

    # --- Voice axes ---
    # Certainty: additions drive it up, removals of hedges drive it up
    v.certainty = (
        _score_axis(signal_text, CERTAINTY_INCREASING)
        + _score_axis(removed_signal, CERTAINTY_DECREASING)  # removing hedges = more certain
        - _score_axis(signal_text, CERTAINTY_DECREASING)
    )

    # Directness: additions of direct constructs, removal of tentative ones
    v.directness = (
        _score_axis(signal_text, DIRECTNESS_INCREASING)
        + _score_axis(removed_signal, DIRECTNESS_DECREASING)
        - _score_axis(signal_text, DIRECTNESS_DECREASING)
    )

    # Formality
    v.formality = (
        _score_axis(signal_text, FORMALITY_INCREASING)
        - _score_axis(signal_text, FORMALITY_DECREASING)
        + _score_axis(removed_signal, FORMALITY_DECREASING)
    )

    # Compression: structural + lexical
    structural_compression = 0.0
    if v.compression_ratio < 0.75:
        structural_compression = (1.0 - v.compression_ratio) * 0.8
    elif v.compression_ratio > 1.35:
        structural_compression = -(v.compression_ratio - 1.0) * 0.6
    v.compression = structural_compression + _score_axis(signal_text, COMPRESSION_SIGNAL)

    # Warmth
    v.warmth = (
        _score_axis(signal_text, WARMTH_INCREASING)
        - _score_axis(signal_text, WARMTH_DECREASING)
        + _score_axis(removed_signal, WARMTH_DECREASING)
    )

    # Intensity
    v.intensity = (
        _score_axis(signal_text, INTENSITY_INCREASING)
        - _score_axis(signal_text, INTENSITY_DECREASING)
    )

    # --- Non-voice axes ---
    v.content_shift = _score_axis(signal_text, CONTENT_SHIFT)
    v.factual_correction = _score_axis(f"{instruction} {signal_text}", FACTUAL_CORRECTION)
    v.intent_shift = _score_axis(signal_text, INTENT_SHIFT)
    v.format_change = _score_axis(f"{instruction} {signal_text}", FORMAT_CHANGE)

    # --- Derived magnitudes ---
    voice_axes = {
        "certainty": abs(v.certainty),
        "directness": abs(v.directness),
        "formality": abs(v.formality),
        "compression": abs(v.compression),
        "warmth": abs(v.warmth),
        "intensity": abs(v.intensity),
    }
    non_voice_axes = {
        "content_shift": v.content_shift,
        "factual_correction": v.factual_correction,
        "intent_shift": v.intent_shift,
        "format_change": v.format_change,
    }

    v.voice_magnitude = sum(voice_axes.values())
    v.non_voice_magnitude = sum(non_voice_axes.values())

    # Dominant voice axes (above threshold)
    v.dominant_voice_axes = [
        ax for ax, mag in sorted(voice_axes.items(), key=lambda x: -x[1])
        if mag > 0.15
    ]

    # Dominant non-voice axis
    dominant_nv = max(non_voice_axes.items(), key=lambda x: x[1])
    if dominant_nv[1] > 0.30:
        v.dominant_non_voice_axis = dominant_nv[0]

    return v


# ---------------------------------------------------------------------------
# Classification from vector
# ---------------------------------------------------------------------------

VOICE_AXIS_TO_OBSERVATION: dict[str, tuple[str, str, str]] = {
    # axis -> (dimension, positive_value, negative_value)
    "certainty":    ("confidence_expression", "certain", "hedged"),
    "directness":   ("directness", "high", "low"),
    "formality":    ("formality", "formal", "casual"),
    "compression":  ("compression", "high", "low"),
    "warmth":       ("warmth", "high", "low"),
    "intensity":    ("intensity", "high", "low"),
}


def classify_from_vector(vector: ChangeVector) -> ClassificationResult:
    """
    Classifies an edit from its change vector.
    Returns a ClassificationResult with confidence and voice observations.
    """
    # --- Unambiguous non-voice: strong factual or format signal ---
    if vector.factual_correction > 0.50:
        return ClassificationResult(
            edit_class="factual",
            confidence=min(0.95, 0.60 + vector.factual_correction * 0.5),
            vector=vector,
            voice_observations=[],
            reasoning=f"factual_correction={vector.factual_correction:.2f}",
        )

    if vector.format_change > 0.50:
        return ClassificationResult(
            edit_class="format",
            confidence=min(0.95, 0.60 + vector.format_change * 0.5),
            vector=vector,
            voice_observations=[],
            reasoning=f"format_change={vector.format_change:.2f}",
        )

    if vector.intent_shift > 0.40:
        return ClassificationResult(
            edit_class="intent",
            confidence=min(0.92, 0.55 + vector.intent_shift * 0.5),
            vector=vector,
            voice_observations=[],
            reasoning=f"intent_shift={vector.intent_shift:.2f}",
        )

    # --- Voice vs content decision ---
    # Key insight: voice edits preserve meaning while changing expression.
    # High Jaccard + voice displacement = voice edit.
    # Low Jaccard + non-voice displacement = content edit.
    # Mixed = score both and take the higher confidence.

    voice_score = 0.0
    content_score = 0.0

    # Voice evidence
    if vector.voice_magnitude > 0.20:
        voice_score += vector.voice_magnitude * 0.5
    if vector.jaccard_similarity > 0.35:
        voice_score += (vector.jaccard_similarity - 0.35) * 0.6  # High overlap supports voice
    if vector.dominant_voice_axes:
        voice_score += 0.20  # Having dominant voice axes is itself evidence

    # Content evidence
    if vector.content_shift > 0.20:
        content_score += vector.content_shift * 0.5
    if vector.content_shift > 0.40:
        content_score += 0.30  # Strong content instruction is definitive
    if vector.jaccard_similarity < 0.30 and vector.voice_magnitude < 0.30:
        content_score += (0.30 - vector.jaccard_similarity) * 0.8  # Low overlap + weak voice = content

    # The reviewer's hard case:
    # "We should consider alternative approaches." → "This isn't the right direction."
    # Jaccard: ~0.15 (low), but certainty: +0.6, directness: +0.5
    # Strong voice axes override low Jaccard
    if vector.voice_magnitude > 0.5 and vector.dominant_voice_axes:
        voice_score += 0.25  # Strong voice displacement overrides low Jaccard

    # Dominant content instruction overrides moderate voice signals
    if vector.content_shift > 0.45 and vector.dominant_non_voice_axis == "content_shift":
        return ClassificationResult(
            edit_class="content",
            confidence=min(0.90, 0.50 + vector.content_shift * 0.5),
            vector=vector,
            voice_observations=[],
            reasoning=f"content_instruction_dominant: content_shift={vector.content_shift:.2f}",
        )

    if voice_score > content_score and voice_score > 0.25:
        # Voice edit — extract observations from dominant axes
        observations = _extract_observations(vector)
        confidence = min(0.95, 0.45 + voice_score * 0.4)
        return ClassificationResult(
            edit_class="voice",
            confidence=confidence,
            vector=vector,
            voice_observations=observations,
            reasoning=(
                f"voice_score={voice_score:.2f} content_score={content_score:.2f} "
                f"jaccard={vector.jaccard_similarity:.2f} "
                f"dominant_axes={vector.dominant_voice_axes}"
            ),
        )

    if content_score > 0.25:
        return ClassificationResult(
            edit_class="content",
            confidence=min(0.88, 0.40 + content_score * 0.5),
            vector=vector,
            voice_observations=[],
            reasoning=f"content_score={content_score:.2f} jaccard={vector.jaccard_similarity:.2f}",
        )

    # Genuinely ambiguous
    return ClassificationResult(
        edit_class="ambiguous",
        confidence=max(voice_score, content_score),
        vector=vector,
        voice_observations=[],
        reasoning=f"ambiguous: voice={voice_score:.2f} content={content_score:.2f}",
    )


def _extract_observations(vector: ChangeVector) -> list[tuple[str, str]]:
    """
    Extracts voice dimension observations from a vector's dominant axes.
    Returns [(dimension, value), ...] for each significant axis.
    """
    observations = []
    for axis in vector.dominant_voice_axes:
        if axis not in VOICE_AXIS_TO_OBSERVATION:
            continue
        dimension, pos_value, neg_value = VOICE_AXIS_TO_OBSERVATION[axis]
        axis_value = getattr(vector, axis)
        if abs(axis_value) > 0.15:
            value = pos_value if axis_value > 0 else neg_value
            observations.append((dimension, value))
    return observations


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyse_edit(
    original: str,
    edited: str,
    instruction: str = "",
) -> ClassificationResult:
    """
    Full change vector analysis of an edit.
    Returns classification, confidence, and dimension observations.
    """
    vector = compute_change_vector(original, edited, instruction)
    result = classify_from_vector(vector)

    logger.info(
        "change_vector_computed",
        edit_class=result.edit_class,
        confidence=result.confidence,
        voice_magnitude=vector.voice_magnitude,
        non_voice_magnitude=vector.non_voice_magnitude,
        dominant_voice_axes=vector.dominant_voice_axes,
        dominant_non_voice=vector.dominant_non_voice_axis,
        jaccard=vector.jaccard_similarity,
        reasoning=result.reasoning,
    )

    return result
