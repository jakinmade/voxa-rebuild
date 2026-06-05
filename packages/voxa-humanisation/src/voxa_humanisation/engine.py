"""
Voxa — Humanisation Engine (Layer 1)
Converts unstructured user input into structured facts.
Not voice. Not style. Not personality. Only structure.

Architecture Spec v9.2.0, Section 4.

Pipeline:
  Step 1 — Fact Extraction (explicit statements only, no inference)
  Step 2 — Normalisation (representation change only, no semantic change)
  Step 3 — Contradiction Resolution (preserved, not resolved)
  Step 4 — Gap Filling (unknown if no evidence)

Output: HumanisedProfile
"""

from __future__ import annotations

import re
from datetime import date
from uuid import UUID, uuid4

import structlog

from voxa_core.entities import (
    ConflictRecord,
    EvidenceStrength,
    ExtractedFact,
    HumanisedProfile,
)
from voxa_core.enums import (
    Explicitness,
    SemanticDomain,
    SourceType,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Explicit preference patterns — Step 1
# Detects only what is explicitly stated. No inference.
# ---------------------------------------------------------------------------

EXPLICIT_PATTERNS: list[tuple[re.Pattern, SemanticDomain]] = [
    # Tone preferences
    (re.compile(r"\b(hate|dislike|avoid|never use)\b.*?\b(corporate|jargon|buzzword|fluffy|formal)\b", re.I), SemanticDomain.TONE_PREFERENCE),
    (re.compile(r"\b(prefer|like|want|use)\b.*?\b(direct|blunt|straight|plain)\b", re.I), SemanticDomain.TONE_PREFERENCE),
    (re.compile(r"\b(keep it|stay)\b.*?\b(short|brief|concise|tight)\b", re.I), SemanticDomain.TONE_PREFERENCE),
    (re.compile(r"\b(don.t|do not|never)\b.*?\b(hedge|waffle|pad|beat around)\b", re.I), SemanticDomain.TONE_PREFERENCE),
    (re.compile(r"\b(formal|informal|casual|professional)\b.*?\b(tone|style|language|voice)\b", re.I), SemanticDomain.TONE_PREFERENCE),
    # Content preferences
    (re.compile(r"\b(include|always include|make sure to include)\b.*?\b(examples|data|evidence|proof)\b", re.I), SemanticDomain.CONTENT_PREFERENCE),
    (re.compile(r"\b(don.t|avoid|skip|no)\b.*?\b(filler|fluff|waffle|padding)\b", re.I), SemanticDomain.CONTENT_PREFERENCE),
    # Behavioural patterns
    (re.compile(r"\b(always|usually|tend to|typically)\b", re.I), SemanticDomain.BEHAVIOURAL_PATTERN),
    # Constraints
    (re.compile(r"\b(never|must not|should not|cannot|won.t)\b", re.I), SemanticDomain.CONSTRAINT),
    # Goals
    (re.compile(r"\b(want to|need to|trying to|goal is|aim is)\b", re.I), SemanticDomain.GOAL),
]

# Phrases that indicate an explicit statement (not implied)
EXPLICIT_MARKERS = re.compile(
    r"\b(I hate|I dislike|I prefer|I like|I want|I never|I always|I avoid|"
    r"don.t use|never use|always use|make sure|keep it|I tend to)\b",
    re.I,
)


def _classify_explicitness(text: str) -> Explicitness:
    if EXPLICIT_MARKERS.search(text):
        return Explicitness.EXPLICIT
    if re.search(r"\b(usually|often|sometimes|tend|typically)\b", text, re.I):
        return Explicitness.IMPLIED
    return Explicitness.AMBIGUOUS


def _detect_domain(text: str) -> SemanticDomain:
    for pattern, domain in EXPLICIT_PATTERNS:
        if pattern.search(text):
            return domain
    return SemanticDomain.TONE_PREFERENCE  # default if pattern matched but domain unclear


# ---------------------------------------------------------------------------
# Step 1 — Fact Extraction
# ---------------------------------------------------------------------------

def extract_facts(
    raw_input: str,
    source_type: SourceType,
    user_id: UUID,
) -> list[ExtractedFact]:
    """
    Extracts only what is explicitly stated.
    Inference is not permitted.

    Returns a list of ExtractedFact — may be empty if no explicit
    preference statements are found.
    """
    facts: list[ExtractedFact] = []

    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", raw_input.strip())

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        matched_domain: SemanticDomain | None = None
        for pattern, domain in EXPLICIT_PATTERNS:
            if pattern.search(sentence):
                matched_domain = domain
                break

        if matched_domain is None:
            # No explicit preference detected — skip. Do not infer.
            continue

        explicitness = _classify_explicitness(sentence)
        source_weight = _source_weight(source_type, explicitness)

        fact = ExtractedFact(
            fact_id=uuid4(),
            preference=_normalise(sentence),
            domain=matched_domain,
            evidence=EvidenceStrength(
                explicitness=explicitness,
                source_type=source_type,
                recency=date.today(),
                source_weight=source_weight,
            ),
            raw_source=sentence,
        )
        facts.append(fact)

        logger.info(
            "fact_extracted",
            user_id=str(user_id),
            domain=matched_domain.value,
            explicitness=explicitness.value,
            source_weight=source_weight,
        )

    return facts


def _source_weight(source_type: SourceType, explicitness: Explicitness) -> float:
    """
    Source weight per architecture spec Section 5.2.
    Behavioural edit: 1.0 | Implied onboarding: 0.4 | Explicit onboarding: 0.2
    Onboarding statements are weak priors — repeated behavioural evidence wins.
    """
    if source_type == SourceType.EDIT:
        return 1.0
    if source_type == SourceType.ONBOARDING:
        return 0.2 if explicitness == Explicitness.EXPLICIT else 0.4
    return 0.4  # email, document, chat


# ---------------------------------------------------------------------------
# Step 2 — Normalisation
# Representation change only. No semantic change.
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    return _normalise(text)


def _normalise(text: str) -> str:
    """
    Converts shorthand, inconsistent tense, inconsistent perspective.
    No semantic change — representation change only.
    """
    # Standardise to first-person present tense
    text = re.sub(r"\bI.m going to\b", "I", text, flags=re.I)
    text = re.sub(r"\bI.ve been\b", "I am", text, flags=re.I)
    text = re.sub(r"\bdon.t\b", "do not", text, flags=re.I)
    text = re.sub(r"\bwon.t\b", "will not", text, flags=re.I)
    text = re.sub(r"\bcan.t\b", "cannot", text, flags=re.I)
    text = re.sub(r"\bisn.t\b", "is not", text, flags=re.I)
    # Normalise whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Step 3 — Contradiction Detection
# Conflicts are preserved in full. Never hidden. Never auto-resolved.
# ---------------------------------------------------------------------------

CONTRADICTORY_PAIRS: list[tuple[str, str, SemanticDomain]] = [
    ("formal", "casual", SemanticDomain.TONE_PREFERENCE),
    ("formal", "informal", SemanticDomain.TONE_PREFERENCE),
    ("direct", "hedge", SemanticDomain.TONE_PREFERENCE),
    ("short", "long", SemanticDomain.TONE_PREFERENCE),
    ("avoid corporate", "professional tone", SemanticDomain.TONE_PREFERENCE),
]


def detect_contradictions(facts: list[ExtractedFact]) -> list[ConflictRecord]:
    """
    Detects contradictions across extracted facts.
    Conflicts are preserved — resolution deferred to calibration engine.
    """
    conflicts: list[ConflictRecord] = []
    preferences = [f.preference.lower() for f in facts]

    for term_a, term_b, domain in CONTRADICTORY_PAIRS:
        matches_a = [p for p in preferences if term_a in p]
        matches_b = [p for p in preferences if term_b in p]
        if matches_a and matches_b:
            conflicts.append(
                ConflictRecord(
                    domain=domain,
                    statements=matches_a + matches_b,
                )
            )

    return conflicts


# ---------------------------------------------------------------------------
# Step 4 — Gap Filling
# Structural completion only. Unknown if no evidence. No defaults assumed.
# ---------------------------------------------------------------------------

KNOWN_DIMENSIONS = [
    "cadence", "compression", "directness", "warmth", "formality",
    "reasoning_style", "decision_style", "confidence_expression",
    "preferred_verbs", "forbidden_phrases", "sentence_shapes",
    "paragraph_structure", "metaphor_usage",
    "humour", "intensity", "emotional_range",
    "audience_positioning", "instruction_style", "question_usage",
]


def identify_gaps(facts: list[ExtractedFact]) -> dict[str, str]:
    """
    Returns a map of dimension -> "unknown" for every dimension
    not evidenced by any extracted fact.

    Correct: { "humour": "unknown" }
    Wrong:   { "humour": "low" }
    """
    evidenced_dimensions: set[str] = set()

    domain_dimension_map = {
        SemanticDomain.TONE_PREFERENCE: [
            "directness", "formality", "warmth", "confidence_expression"
        ],
        SemanticDomain.CONTENT_PREFERENCE: [
            "compression", "paragraph_structure"
        ],
        SemanticDomain.BEHAVIOURAL_PATTERN: [
            "cadence", "reasoning_style"
        ],
        SemanticDomain.CONSTRAINT: [
            "forbidden_phrases", "tone_boundaries"
        ],
        SemanticDomain.GOAL: [],
    }

    for fact in facts:
        mapped = domain_dimension_map.get(fact.domain, [])
        evidenced_dimensions.update(mapped)

    gaps = {
        dim: "unknown"
        for dim in KNOWN_DIMENSIONS
        if dim not in evidenced_dimensions
    }
    return gaps


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------

def humanise(
    raw_input: str,
    user_id: UUID,
    source_type: SourceType = SourceType.ONBOARDING,
) -> HumanisedProfile:
    """
    Full four-step humanisation pipeline.
    Returns HumanisedProfile. Never used directly for rendering.
    """
    logger.info("humanisation_started", user_id=str(user_id), source_type=source_type.value)

    # Step 1 — Extract
    facts = extract_facts(raw_input, source_type, user_id)

    # Step 2 — Normalise (already applied inside extract_facts per sentence)

    # Step 3 — Contradiction detection
    conflicts = detect_contradictions(facts)
    if conflicts:
        logger.warning(
            "contradictions_detected",
            user_id=str(user_id),
            count=len(conflicts),
        )

    # Step 4 — Gap identification (logged, not stored in profile)
    gaps = identify_gaps(facts)
    logger.info(
        "gaps_identified",
        user_id=str(user_id),
        gap_count=len(gaps),
        gaps=list(gaps.keys()),
    )

    profile = HumanisedProfile(
        user_id=user_id,
        facts=facts,
        conflicts=conflicts,
        source_type=source_type,
    )

    logger.info(
        "humanisation_complete",
        user_id=str(user_id),
        fact_count=len(facts),
        conflict_count=len(conflicts),
        gap_count=len(gaps),
    )

    return profile
