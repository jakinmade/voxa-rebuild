"""
Voxa — Core Data Entities
All data entities as defined in Architecture Specification v9.2.0, Section 11.
Pydantic v2 enforces schema at every boundary.
No bare rule values — every rule carries full metadata.
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from voxa_core.enums import (
    AudiencePositioning,
    Cadence,
    Compression,
    ConfidenceExpression,
    DecisionStyle,
    Directness,
    EditClass,
    Explicitness,
    Formality,
    Humour,
    InstructionStyle,
    Intensity,
    EmotionalRange,
    LifecycleStage,
    MetaphorUsage,
    ParagraphStructure,
    QuestionUsage,
    ReasoningStyle,
    RuleCategory,
    SemanticDomain,
    SourceType,
    Warmth,
)


# ---------------------------------------------------------------------------
# Evidence Strength
# ---------------------------------------------------------------------------

class EvidenceStrength(BaseModel):
    """Metadata attached to every extracted fact and every rule update."""
    explicitness: Explicitness
    source_type: SourceType
    recency: date
    source_weight: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# Extracted Fact (Layer 1 output atom)
# ---------------------------------------------------------------------------

class ExtractedFact(BaseModel):
    """
    A single fact extracted by the Humanisation Engine.
    Inference is not permitted — only what is explicitly stated.
    """
    fact_id: UUID = Field(default_factory=uuid4)
    preference: str
    domain: SemanticDomain
    evidence: EvidenceStrength
    raw_source: str  # Original text that produced this fact


# ---------------------------------------------------------------------------
# Contradiction Record
# ---------------------------------------------------------------------------

class ConflictRecord(BaseModel):
    """
    Preserved contradiction. Never hidden. Never auto-resolved.
    Resolution is deferred to the calibration engine.
    """
    conflict_id: UUID = Field(default_factory=uuid4)
    domain: SemanticDomain
    statements: list[str] = Field(min_length=2)
    detected_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# HumanisedProfile (Layer 1 output)
# ---------------------------------------------------------------------------

class HumanisedProfile(BaseModel):
    """
    Output of the Humanisation Engine (Layer 1).
    Never used directly for rendering.
    """
    profile_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    created_at: datetime = Field(default_factory=datetime.utcnow)
    facts: list[ExtractedFact] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    source_type: SourceType


# ---------------------------------------------------------------------------
# Rule Metadata
# ---------------------------------------------------------------------------

BOUNDARY_CONFIDENCE = 1.0
BOUNDARY_DECAY_RATE = 0.0

class RuleMetadata(BaseModel):
    """
    Full metadata required on every rule in the Canonical Voice Profile.
    A bare value with no metadata is invalid — schema enforces this.
    """
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0, default=0)
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: list[str] = Field(default_factory=list)
    stability: float = Field(ge=0.0, le=1.0, default=0.0)
    decay_rate: float = Field(ge=0.0, le=1.0, default=0.02)
    lifecycle_stage: LifecycleStage = LifecycleStage.OBSERVED

    @model_validator(mode="after")
    def boundary_rules_are_immutable(self) -> RuleMetadata:
        if self.lifecycle_stage == LifecycleStage.BOUNDARY:
            assert self.confidence == BOUNDARY_CONFIDENCE, (
                "Boundary rules must have confidence 1.0"
            )
            assert self.decay_rate == BOUNDARY_DECAY_RATE, (
                "Boundary rules must have decay_rate 0.0"
            )
        return self


# ---------------------------------------------------------------------------
# Rule Sets (per category)
# ---------------------------------------------------------------------------

class IdentityRules(BaseModel):
    cadence: RuleMetadata | None = None
    compression: RuleMetadata | None = None
    directness: RuleMetadata | None = None
    warmth: RuleMetadata | None = None
    formality: RuleMetadata | None = None


class CognitiveRules(BaseModel):
    reasoning_style: RuleMetadata | None = None
    decision_style: RuleMetadata | None = None
    confidence_expression: RuleMetadata | None = None


class LinguisticRules(BaseModel):
    preferred_verbs: RuleMetadata | None = None       # value: list[str]
    forbidden_phrases: RuleMetadata | None = None     # value: list[str]
    sentence_shapes: RuleMetadata | None = None       # value: list[str]
    paragraph_structure: RuleMetadata | None = None
    metaphor_usage: RuleMetadata | None = None


class StylisticRules(BaseModel):
    humour: RuleMetadata | None = None
    intensity: RuleMetadata | None = None
    emotional_range: RuleMetadata | None = None


class InteractionRules(BaseModel):
    audience_positioning: RuleMetadata | None = None
    instruction_style: RuleMetadata | None = None
    question_usage: RuleMetadata | None = None


class BoundaryRules(BaseModel):
    """Hard constraints. Confidence 1.0. Decay rate 0. Cannot be overridden."""
    tone_boundaries: RuleMetadata | None = None       # value: list[str]
    content_boundaries: RuleMetadata | None = None    # value: list[str]


# ---------------------------------------------------------------------------
# Canonical Voice Profile (Layer 2)
# ---------------------------------------------------------------------------

class VoiceProfile(BaseModel):
    """
    Single source of truth.
    Every rule carries metadata. No rule exists without it.
    """
    profile_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    identity: IdentityRules = Field(default_factory=IdentityRules)
    cognitive: CognitiveRules = Field(default_factory=CognitiveRules)
    linguistic: LinguisticRules = Field(default_factory=LinguisticRules)
    stylistic: StylisticRules = Field(default_factory=StylisticRules)
    interaction: InteractionRules = Field(default_factory=InteractionRules)
    boundaries: BoundaryRules = Field(default_factory=BoundaryRules)

    is_bootstrap: bool = True  # True until minimum renderable profile is met


class VoiceProfileVersion(BaseModel):
    """Immutable snapshot of a VoiceProfile at a point in time."""
    snapshot_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    version: int
    snapshot: VoiceProfile
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    changes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# RuleObservation (pre-candidate, no profile impact)
# ---------------------------------------------------------------------------

class RuleObservation(BaseModel):
    """
    Raw pattern observation. Pre-candidate stage.
    No profile impact. Stored as raw observation only.
    """
    observation_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    rule_dimension: str
    observed_value: Any
    source_edit_id: UUID
    observed_at: datetime = Field(default_factory=datetime.utcnow)
    session_id: UUID


# ---------------------------------------------------------------------------
# RuleCandidate
# ---------------------------------------------------------------------------

class RuleCandidate(BaseModel):
    """
    Pattern observed in multiple edits. Passes initial repetition check.
    Awaiting validation batch. No profile impact yet.
    """
    candidate_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    rule_dimension: str
    candidate_value: Any
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence_count: int = Field(ge=0, default=0)
    supporting_observations: list[UUID] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# CalibrationEvent
# ---------------------------------------------------------------------------

class CalibrationEvent(BaseModel):
    """
    Every accepted calibration action.
    Append-only. Never mutated.
    """
    event_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    session_id: UUID
    rendered_output_id: UUID
    edit_class: EditClass
    direction: str = Field(pattern="^(positive|negative)$")
    rule_dimension: str | None = None
    pattern_detected: str | None = None
    raw_edit: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    profile_version_before: int
    profile_version_after: int | None = None


# ---------------------------------------------------------------------------
# RenderedOutput
# ---------------------------------------------------------------------------

class ReproducibilitySnapshot(BaseModel):
    voice_profile_version: int
    render_engine_version: str
    context: str
    rule_snapshot: dict[str, Any]


class NeutralDefaultUsage(BaseModel):
    """Records which dimensions used neutral fallback, not the user's profile."""
    dimension: str
    neutral_value: Any
    reason: str = "rule_unknown"


class RenderedOutput(BaseModel):
    """
    Every generated output with full metadata.
    Every rendered output carries a rule trace.
    """
    output_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    session_id: UUID
    input_text: str
    output_text: str
    context: str = "default"
    rendered_at: datetime = Field(default_factory=datetime.utcnow)
    reproducibility: ReproducibilitySnapshot
    neutral_defaults_used: list[NeutralDefaultUsage] = Field(default_factory=list)
    is_bootstrap_output: bool = False  # True if rendered before minimum profile met


# ---------------------------------------------------------------------------
# DriftEvent
# ---------------------------------------------------------------------------

class DriftEvent(BaseModel):
    """Recorded when a drift monitor threshold is breached (Sprint 3)."""
    event_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    trigger: str
    readings: dict[str, float]
    profile_frozen: bool = False
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
