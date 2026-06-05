"""
Voxa — Core Enums
All valid enumerated values across the system.
Single source of truth. No string literals in business logic.
"""

from enum import Enum


# --- Rule Lifecycle ---

class LifecycleStage(str, Enum):
    OBSERVED = "observed"
    CANDIDATE = "candidate"
    PROVISIONAL = "provisional"
    STABLE = "stable"
    CORE = "core"
    BOUNDARY = "boundary"  # Boundary rules — lifecycle does not apply


# --- Rule Categories ---

class RuleCategory(str, Enum):
    IDENTITY = "identity"
    COGNITIVE = "cognitive"
    LINGUISTIC = "linguistic"
    STYLISTIC = "stylistic"
    INTERACTION = "interaction"
    BOUNDARY = "boundary"


# --- Identity Rule Values ---

class Cadence(str, Enum):
    SHORT = "short"
    MEDIUM = "medium"
    LONG = "long"
    MIXED = "mixed"


class Compression(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Directness(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Warmth(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Formality(str, Enum):
    CASUAL = "casual"
    SEMI_FORMAL = "semi-formal"
    FORMAL = "formal"


# --- Cognitive Rule Values ---

class ReasoningStyle(str, Enum):
    LINEAR = "linear"
    BRANCHING = "branching"
    ANALOGY = "analogy"
    NARRATIVE = "narrative"
    BULLET_FIRST = "bullet-first"


class DecisionStyle(str, Enum):
    DECISIVE = "decisive"
    EXPLORATORY = "exploratory"
    CONDITIONAL = "conditional"
    PROBABILISTIC = "probabilistic"


class ConfidenceExpression(str, Enum):
    CERTAIN = "certain"
    BALANCED = "balanced"
    HEDGED = "hedged"


# --- Stylistic Rule Values ---

class Humour(str, Enum):
    NONE = "none"
    DRY = "dry"
    PLAYFUL = "playful"
    SARCASTIC = "sarcastic"
    ABSURDIST = "absurdist"


class Intensity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EmotionalRange(str, Enum):
    NARROW = "narrow"
    MODERATE = "moderate"
    WIDE = "wide"


# --- Interaction Rule Values ---

class AudiencePositioning(str, Enum):
    PEER = "peer"
    MENTOR = "mentor"
    TEACHER = "teacher"
    CHALLENGER = "challenger"


class InstructionStyle(str, Enum):
    IMPERATIVE = "imperative"
    SUGGESTIVE = "suggestive"
    COLLABORATIVE = "collaborative"


class QuestionUsage(str, Enum):
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class ParagraphStructure(str, Enum):
    SINGLE_IDEA = "single-idea"
    ROLLING = "rolling"
    MIXED = "mixed"


class MetaphorUsage(str, Enum):
    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"


# --- Evidence ---

class Explicitness(str, Enum):
    EXPLICIT = "explicit"
    IMPLIED = "implied"
    AMBIGUOUS = "ambiguous"


class SourceType(str, Enum):
    ONBOARDING = "onboarding"
    EMAIL = "email"
    DOCUMENT = "document"
    CHAT = "chat"
    EDIT = "edit"


class SemanticDomain(str, Enum):
    TONE_PREFERENCE = "tone_preference"
    CONTENT_PREFERENCE = "content_preference"
    BEHAVIOURAL_PATTERN = "behavioural_pattern"
    CONSTRAINT = "constraint"
    GOAL = "goal"


# --- Edit Classification ---

class EditClass(str, Enum):
    VOICE = "voice"
    CONTENT = "content"
    INTENT = "intent"
    FACTUAL = "factual"
    FORMAT = "format"
    AMBIGUOUS = "ambiguous"


# --- Source Weight (for confidence formula, Sprint 2) ---

class SourceWeight(float, Enum):
    BEHAVIOURAL_EDIT = 1.0
    IMPLIED_ONBOARDING = 0.4
    EXPLICIT_ONBOARDING = 0.2
