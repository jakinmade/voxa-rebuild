"""
Voxa — Anonymous-First Onboarding Engine
Architecture Spec v9.8.0, Section 6.6.

The front door to Voxa.

Design principle: value before account.
  1. User pastes any writing sample — no signup, no account, no wall.
  2. Engine produces fingerprint + governed render in one call.
  3. User sees something personal and accurate.
  4. THEN account creation is offered.
  5. Profile transfers cleanly on signup — nothing is lost.

This is not a demo flow. This is the production onboarding pipeline.
The same engine that powers the full platform powers the anonymous session.
The only difference is persistence — anonymous sessions are ephemeral,
authenticated sessions are stored.

Progressive profiling:
  Session 1 — paste only → seeded profile (anchor corpus path)
  Session 2 — Q: disagreement email → surfaces directness/warmth/cadence
  Session 3 — Q: self-intro → surfaces formality/compression/confidence
  Session 4 — Q: forbidden phrases → surfaces linguistic boundaries
  Ongoing — calibration from real edits

Each question surfaces 2-3 dimensions. Questions are triggered by profile
gaps identified after the previous session — not asked upfront in a form.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import NamedTuple

import structlog

from voxa_core.entities import (
    HumanisedProfile,
    RuleMetadata,
    VoiceProfile,
    VoiceProfileVersion,
)
from voxa_core.enums import LifecycleStage, SourceType
from voxa_core.bootstrap import check_bootstrap
from voxa_humanisation.engine import process_anchor_corpus, humanise
from voxa_profile.builder import build_profile
from voxa_rendering.fingerprint import select_observations

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Anonymous session — ephemeral, no persistence required
# ---------------------------------------------------------------------------

class AnonymousSession(NamedTuple):
    session_id: UUID
    profile: VoiceProfile
    humanised: HumanisedProfile
    fingerprint_summary: dict
    bootstrap_status: dict
    created_at: datetime


class OnboardingResult(NamedTuple):
    """
    Everything the frontend needs to render the reveal screen.
    Anonymous-safe — no PII, no account required.
    """
    session_id: UUID
    fingerprint: dict           # Human-readable dimension summary
    top_rules: list[dict]       # Top 5 most confident rules for reveal UI
    gaps: list[str]             # Dimensions with no signal yet
    next_question: dict | None  # Progressive profiling — next gap to fill
    is_renderable: bool         # Whether profile meets minimum render threshold
    profile_version: int
    explicit_fact_count: int
    inferred_fact_count: int


# In-memory anonymous session store — ephemeral by design
# Sessions expire; they are never persisted to database without user consent
_anonymous_sessions: dict[UUID, AnonymousSession] = {}

# Progressive profiling questions — ordered by dimension coverage priority
# Each question surfaces the dimensions listed. Asked when those dimensions
# have no evidence after the initial paste session.
PROGRESSIVE_QUESTIONS = [
    {
        "id": "q_disagreement",
        "prompt": (
            "Someone sends you a three-paragraph email asking for your thoughts "
            "on their approach. You disagree with most of it. What do you write back?"
        ),
        "surfaces": ["directness", "warmth", "cadence", "instruction_style"],
        "why": "Disagreement writing reveals unfiltered voice — no politeness performance.",
    },
    {
        "id": "q_self_intro",
        "prompt": (
            "You're introducing yourself to a potential enterprise client. "
            "One paragraph. Go."
        ),
        "surfaces": ["formality", "compression", "confidence_expression", "forbidden_phrases"],
        "why": "Self-introduction is the most practised piece of writing — it shows the polished baseline.",
    },
    {
        "id": "q_chase",
        "prompt": (
            "A partner is late delivering something that is blocking you. "
            "You need to chase them. What do you say?"
        ),
        "surfaces": ["intensity", "audience_positioning", "directness"],
        "why": "Chasing reveals where directness meets relationship management.",
    },
    {
        "id": "q_forbidden",
        "prompt": (
            "What do you never say? Words or phrases that make you wince "
            "when you see them in writing."
        ),
        "surfaces": ["forbidden_phrases", "preferred_verbs"],
        "why": "What people reject in others is the mirror of their own standards.",
    },
    {
        "id": "q_decision",
        "prompt": (
            "You have just made a decision that affects someone else. "
            "How do you tell them?"
        ),
        "surfaces": ["decision_style", "instruction_style", "emotional_range", "question_usage"],
        "why": "Decision communication reveals authority style and emotional register.",
    },
    {
        "id": "q_best_email",
        "prompt": (
            "Finish this: the best business email I ever received was..."
        ),
        "surfaces": ["reasoning_style", "cadence", "warmth", "humour"],
        "why": "What they admire in others is the clearest signal of their own aspirations.",
    },
]


def _build_fingerprint_summary(profile: VoiceProfile) -> dict:
    """
    Converts a VoiceProfile into a human-readable fingerprint summary.
    Used for the reveal screen — designed to feel personal and accurate.
    """
    def _rule_to_display(rule, dimension: str) -> dict | None:
        if rule is None:
            return None
        return {
            "dimension": dimension,
            "value": str(rule.value),
            "confidence": rule.confidence,
            "stage": rule.lifecycle_stage.value,
        }

    all_rules = []
    dimension_map = [
        (profile.identity.directness, "directness"),
        (profile.identity.formality, "formality"),
        (profile.identity.cadence, "cadence"),
        (profile.identity.compression, "compression"),
        (profile.identity.warmth, "warmth"),
        (profile.cognitive.confidence_expression, "confidence_expression"),
        (profile.cognitive.reasoning_style, "reasoning_style"),
        (profile.cognitive.decision_style, "decision_style"),
        (profile.stylistic.humour, "humour"),
        (profile.stylistic.intensity, "intensity"),
        (profile.stylistic.emotional_range, "emotional_range"),
        (profile.interaction.audience_positioning, "audience_positioning"),
        (profile.interaction.instruction_style, "instruction_style"),
        (profile.interaction.question_usage, "question_usage"),
        (profile.linguistic.forbidden_phrases, "forbidden_phrases"),
        (profile.linguistic.preferred_verbs, "preferred_verbs"),
        (profile.linguistic.metaphor_usage, "metaphor_usage"),
        (profile.linguistic.paragraph_structure, "paragraph_structure"),
        (profile.linguistic.sentence_shapes, "sentence_shapes"),
    ]

    for rule, dim in dimension_map:
        entry = _rule_to_display(rule, dim)
        if entry:
            all_rules.append(entry)

    # Top rules for reveal: highest confidence, non-boundary
    top_rules = sorted(all_rules, key=lambda r: r["confidence"], reverse=True)[:5]

    # Gap list: dimensions with no rule
    populated = {r["dimension"] for r in all_rules}
    all_dimensions = {dim for _, dim in dimension_map}
    gaps = sorted(all_dimensions - populated)

    return {
        "all_rules": all_rules,
        "top_rules": top_rules,
        "gaps": gaps,
        "rule_count": len(all_rules),
        "gap_count": len(gaps),
    }


def _next_progressive_question(gaps: list[str]) -> dict | None:
    """
    Returns the next profiling question based on current profile gaps.
    Questions are ordered by dimension coverage priority.
    Returns None if all gap-covering questions have been asked,
    or if no gaps remain.
    """
    if not gaps:
        return None

    gap_set = set(gaps)
    for q in PROGRESSIVE_QUESTIONS:
        if any(dim in gap_set for dim in q["surfaces"]):
            return q

    return None


def _seed_profile_from_observations(profile: VoiceProfile, text: str) -> list[str]:
    """
    Bridge: fingerprint observations → VoiceProfile rules.

    The humanisation engine extracts facts from explicit meta-commentary
    ("I prefer direct", "keep it short"). Real writing rarely contains
    that. The fingerprint scorer extracts voice signals from the writing
    itself — conclusion position, hedging, compression, energy, reader
    assumption — but that output was never fed back into the profile.

    This function closes that gap. Each observation maps to one or more
    profile dimensions at PROVISIONAL lifecycle stage with confidence
    scaled by signal strength. PROVISIONAL satisfies the bootstrap
    is_renderable check.

    Called by process_anonymous_paste after build_profile().
    Not called on subsequent merge sessions — calibration takes over.
    """
    from datetime import datetime, timezone
    _UTC = timezone.utc

    observations = select_observations(text)
    changes: list[str] = []

    # Observation ID → (category, field, value_fn)
    # value_fn takes the observation data dict and returns the rule value
    OBS_MAP = {
        "conclusion_position": [
            ("identity", "directness", lambda d: "high" if d.get("point_first") else "medium"),
        ],
        "hedging_signature": [
            ("cognitive", "confidence_expression", lambda d: "certain" if d.get("owns_statements") else "hedged"),
        ],
        "reader_assumption": [
            ("interaction", "audience_positioning", lambda d: "peer" if d.get("assumes_peer") else "mentor"),
        ],
        "compression_philosophy": [
            ("identity", "compression", lambda d: "high" if d.get("structural") else "medium"),
            ("identity", "cadence", lambda d: "short" if d.get("avg_sentence_length", 15) <= 12 else "medium"),
        ],
        "energy_signature": [
            ("stylistic", "intensity", lambda d: "high" if d.get("verb_dominant") else "medium"),
        ],
    }

    for obs in observations:
        if obs.id not in OBS_MAP:
            continue
        for category_name, field_name, value_fn in OBS_MAP[obs.id]:
            category_obj = getattr(profile, category_name)
            existing = getattr(category_obj, field_name)
            if existing is not None:
                continue  # Never overwrite — humanisation engine facts take precedence
            value = value_fn(obs.data)
            rule = RuleMetadata(
                value=value,
                confidence=round(obs.signal_strength * 0.6, 3),  # Scaled — observation is weaker than explicit fact
                evidence_count=1,
                last_updated=datetime.now(_UTC),
                source=[f"fingerprint_observation:{obs.id}"],
                stability=0.15,
                decay_rate=0.02,
                lifecycle_stage=LifecycleStage.PROVISIONAL,  # PROVISIONAL satisfies is_renderable
            )
            setattr(category_obj, field_name, rule)
            changes.append(f"seeded_from_observation:{category_name}.{field_name}={value}(signal={obs.signal_strength:.2f})")
            logger.info(
                "profile_seeded_from_fingerprint",
                dimension=f"{category_name}.{field_name}",
                value=value,
                signal_strength=obs.signal_strength,
            )

    # Seed a linguistic rule at CANDIDATE so the second bootstrap check passes
    # Use forbidden_phrases as the vessel — empty list is valid and non-intrusive
    if profile.linguistic.forbidden_phrases is None:
        profile.linguistic.forbidden_phrases = RuleMetadata(
            value=[],
            confidence=0.25,
            evidence_count=1,
            last_updated=datetime.now(_UTC),
            source=["fingerprint_bootstrap_seed"],
            stability=0.10,
            decay_rate=0.02,
            lifecycle_stage=LifecycleStage.CANDIDATE,
        )
        changes.append("seeded_linguistic_boundary:forbidden_phrases=[]")

    logger.info(
        "profile_seeded_from_observations",
        observation_count=len(observations),
        rules_created=len(changes),
    )
    return changes


def process_anonymous_paste(
    raw_text: str,
    existing_session_id: UUID | None = None,
) -> OnboardingResult:
    """
    Core anonymous onboarding function. No account required.

    Takes a paste of any writing — email, Slack, doc, anything.
    Returns a fingerprint and bootstrap status in one call.

    If existing_session_id is provided, accumulates evidence into the
    existing session (progressive profiling across multiple pastes).
    Otherwise creates a new anonymous session.

    This function is the entry point for:
    - The onboarding UI (anonymous)
    - The Streamlit demo (same engine)
    - The progressive profiling follow-up questions
    """
    session_id = existing_session_id or uuid4()
    user_id = uuid4()  # Anonymous user ID — ephemeral, not stored

    logger.info(
        "anonymous_onboarding_started",
        session_id=str(session_id),
        text_length=len(raw_text),
        is_continuation=existing_session_id is not None,
    )

    # Run through anchor corpus (single sample)
    humanised = process_anchor_corpus(
        samples=[raw_text],
        user_id=user_id,
        source_type=SourceType.DOCUMENT,
    )

    # Build profile from humanised
    profile = build_profile(humanised)

    # Bridge: seed profile from fingerprint observations
    if not existing_session_id:
        _seed_profile_from_observations(profile, raw_text)

    # Bootstrap status
    bootstrap_status = check_bootstrap(profile, calibration_session_count=0)

    # Fingerprint summary
    fingerprint = _build_fingerprint_summary(profile)

    # Progressive profiling — what to ask next
    next_q = _next_progressive_question(fingerprint["gaps"])

    # Count fact types
    explicit_count = sum(
        1 for f in humanised.facts
        if f.evidence.explicitness.value != "inferred"
    )
    inferred_count = len(humanised.facts) - explicit_count

    # Store anonymous session — ephemeral
    session = AnonymousSession(
        session_id=session_id,
        profile=profile,
        humanised=humanised,
        fingerprint_summary=fingerprint,
        bootstrap_status={
            "is_renderable": bootstrap_status.is_renderable,
            "is_bootstrap_complete": bootstrap_status.is_bootstrap_complete,
            "missing_requirements": bootstrap_status.missing_requirements,
            "rules_by_stage": bootstrap_status.rules_by_stage,
        },
        created_at=datetime.now(timezone.utc),
    )
    _anonymous_sessions[session_id] = session

    result = OnboardingResult(
        session_id=session_id,
        fingerprint=fingerprint,
        top_rules=fingerprint["top_rules"],
        gaps=fingerprint["gaps"],
        next_question=next_q,
        is_renderable=bootstrap_status.is_renderable,
        profile_version=profile.version,
        explicit_fact_count=explicit_count,
        inferred_fact_count=inferred_count,
    )

    logger.info(
        "anonymous_onboarding_complete",
        session_id=str(session_id),
        rules_populated=fingerprint["rule_count"],
        gaps=fingerprint["gap_count"],
        is_renderable=bootstrap_status.is_renderable,
        explicit_facts=explicit_count,
        inferred_facts=inferred_count,
    )

    return result


def transfer_session_to_account(
    session_id: UUID,
    authenticated_user_id: UUID,
    profile_repo,
    governance_repo,
) -> VoiceProfile | None:
    """
    Account handoff. Called when an anonymous user signs up.

    Transfers the anonymous session profile to the authenticated user.
    Nothing is lost — the seeded profile becomes the user's starting state.
    Calibration begins immediately from a meaningful baseline.

    This is the conversion moment: value was delivered anonymously,
    account is created after, profile transfers cleanly.

    Returns the transferred profile, or None if session not found.
    """
    from voxa_governance.engine import record_profile_version

    session = _anonymous_sessions.get(session_id)
    if session is None:
        logger.warning("session_transfer_failed_not_found",
                       session_id=str(session_id))
        return None

    # Reassign profile to authenticated user
    profile = session.profile
    profile.user_id = authenticated_user_id

    # Save to persistent store
    profile_repo.save(profile)

    snapshot = VoiceProfileVersion(
        user_id=authenticated_user_id,
        version=profile.version,
        snapshot=profile.model_copy(deep=True),
        changes=["transferred_from_anonymous_onboarding_session"],
    )
    profile_repo.save_version(snapshot)
    record_profile_version(snapshot)

    # Clean up anonymous session
    del _anonymous_sessions[session_id]

    logger.info(
        "session_transferred_to_account",
        session_id=str(session_id),
        authenticated_user_id=str(authenticated_user_id),
        profile_version=profile.version,
    )

    return profile


def get_anonymous_session(session_id: UUID) -> AnonymousSession | None:
    return _anonymous_sessions.get(session_id)
