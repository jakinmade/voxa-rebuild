"""
Voxa — Voice Rendering Engine (Layer 3)
Applies rules. Does not invent them.
Every rendering decision is traceable to a rule in the voice profile.

Architecture Spec v9.2.0, Section 7.

LLM Boundary Contract: LLMs operate inside this layer only.
They may never infer rules, update rules, resolve contradictions,
classify edits, bypass boundaries, or override stability scores.

Sprint 1 scope:
- Full rendering pipeline
- Bootstrap state check
- Neutral default application and tagging
- Boundary validation (failed check returns NO output)
- Reproducibility snapshot on every render
- LLM rewrite within constraints only
"""

from __future__ import annotations

import os
from uuid import UUID, uuid4

import structlog

from voxa_core.bootstrap import check_bootstrap
from voxa_core.defaults import get_neutral_default
from voxa_core.entities import (
    NeutralDefaultUsage,
    RenderedOutput,
    ReproducibilitySnapshot,
    RuleMetadata,
    VoiceProfile,
)
from voxa_core.enums import LifecycleStage

logger = structlog.get_logger(__name__)

RENDER_ENGINE_VERSION = "v9.5.0"


# ---------------------------------------------------------------------------
# Boundary Validation — delegates to semantic boundary engine
# ---------------------------------------------------------------------------

def _check_boundaries(text: str, profile: VoiceProfile) -> tuple[bool, str | None]:
    from voxa_rendering.boundary import check_boundaries
    return check_boundaries(text, profile)

# ---------------------------------------------------------------------------
# Rule extraction helpers
# ---------------------------------------------------------------------------

def _get_rule_value(rule: RuleMetadata | None, dimension: str) -> tuple[object, bool]:
    """
    Returns (value, used_neutral_default).
    If rule is None or unknown, returns neutral default.
    """
    if rule is None:
        return get_neutral_default(dimension), True
    return rule.value, False


def _build_rendering_constraints(profile: VoiceProfile) -> dict[str, object]:
    """
    Extracts all rule values from the profile, applying neutral defaults where unknown.
    Returns a flat constraints dict for the LLM system prompt.
    """
    constraints: dict[str, object] = {}
    neutral_defaults_used: list[NeutralDefaultUsage] = []

    dimension_map = {
        "cadence": profile.identity.cadence,
        "compression": profile.identity.compression,
        "directness": profile.identity.directness,
        "warmth": profile.identity.warmth,
        "formality": profile.identity.formality,
        "reasoning_style": profile.cognitive.reasoning_style,
        "confidence_expression": profile.cognitive.confidence_expression,
        "humour": profile.stylistic.humour,
        "intensity": profile.stylistic.intensity,
        "audience_positioning": profile.interaction.audience_positioning,
    }

    for dimension, rule in dimension_map.items():
        value, used_default = _get_rule_value(rule, dimension)
        constraints[dimension] = value
        if used_default:
            neutral_defaults_used.append(
                NeutralDefaultUsage(
                    dimension=dimension,
                    neutral_value=value,
                    reason="rule_unknown",
                )
            )

    # Linguistic rules
    if profile.linguistic.forbidden_phrases and profile.linguistic.forbidden_phrases.value:
        constraints["forbidden_phrases"] = profile.linguistic.forbidden_phrases.value
    else:
        constraints["forbidden_phrases"] = []

    if profile.linguistic.preferred_verbs and profile.linguistic.preferred_verbs.value:
        constraints["preferred_verbs"] = profile.linguistic.preferred_verbs.value
    else:
        constraints["preferred_verbs"] = []

    return constraints, neutral_defaults_used


def _build_system_prompt(constraints: dict[str, object]) -> str:
    """
    Builds the LLM system prompt from constraints.
    LLM rewrites within these constraints only — no rule inference, no decisions.
    """
    forbidden = constraints.get("forbidden_phrases", [])
    preferred = constraints.get("preferred_verbs", [])
    task_instruction = constraints.get("task_instruction", "")

    forbidden_str = ", ".join(forbidden) if forbidden else "none specified"
    preferred_str = ", ".join(preferred) if preferred else "none specified"

    task_block = f"\n\nTASK: {task_instruction}" if task_instruction else ""

    return f"""You are a voice rendering engine. Your only job is to rewrite the provided text to match the communication constraints below. You do not make decisions. You do not infer rules. You apply constraints exactly as specified.

CONSTRAINTS:
- Cadence: {constraints.get("cadence", "medium")}
- Compression: {constraints.get("compression", "medium")}
- Directness: {constraints.get("directness", "medium")}
- Warmth: {constraints.get("warmth", "medium")}
- Formality: {constraints.get("formality", "semi-formal")}
- Reasoning style: {constraints.get("reasoning_style", "linear")}
- Confidence expression: {constraints.get("confidence_expression", "balanced")}
- Humour: {constraints.get("humour", "none")}
- Intensity: {constraints.get("intensity", "medium")}
- Audience positioning: {constraints.get("audience_positioning", "peer")}
- Forbidden phrases: {forbidden_str}
- Preferred verbs: {preferred_str}

RULES:
1. Rewrite only. Do not add information that was not in the original.
2. Do not remove meaning. Only change expression.
3. If a forbidden phrase appears, replace it — do not delete the underlying meaning.
4. Return only the rewritten text. No preamble. No explanation. No commentary.
5. Match the length of the original. Do not shorten. Do not summarise. Every paragraph in gets a paragraph out.
6. Never use em dashes (—) or en dashes (–). Use a hyphen or restructure the sentence.{task_block}"""


# ---------------------------------------------------------------------------
# LLM call — rendering layer only
# This is the ONLY place in the codebase where the Claude API is called.
# ---------------------------------------------------------------------------

async def _call_llm(system_prompt: str, input_text: str) -> str:
    """
    Calls the Claude API via the LLM boundary module.
    All Anthropic API calls route through voxa_rendering.llm_boundary.
    LLM boundary contract: this function may not be called from any layer
    other than voxa-rendering.
    """
    from voxa_rendering.llm_boundary import rewrite_with_constraints
    return await rewrite_with_constraints(system_prompt, input_text)


# ---------------------------------------------------------------------------
# Main rendering pipeline
# ---------------------------------------------------------------------------

async def render(
    input_text: str,
    profile: VoiceProfile,
    session_id: UUID,
    context: str = "default",
    calibration_session_count: int = 0,
    intent_mode: str | None = None,
) -> RenderedOutput | None:
    """
    Full rendering pipeline with intent mode support.
    Intent mode adjusts execution constraints only.
    Identity dimensions are never touched.
    """
    from voxa_rendering.intent_modes import (
        apply_intent_mode, build_intent_mode_trace,
        mode_from_string, IntentMode, detect_intent_mode,
    )

    logger.info(
        "render_started",
        user_id=str(profile.user_id),
        session_id=str(session_id),
        context=context,
        intent_mode=intent_mode,
    )

    # Step 1 — Bootstrap check
    bootstrap_status = check_bootstrap(profile, calibration_session_count)
    if not bootstrap_status.is_renderable:
        logger.info("render_bootstrap_incomplete", user_id=str(profile.user_id),
                    missing=bootstrap_status.missing_requirements)
        generic_text = (
            "Your voice profile is still building. "
            "Complete onboarding to unlock personalised rendering. "
            f"Missing: {', '.join(bootstrap_status.missing_requirements)}"
        )
        return RenderedOutput(
            user_id=profile.user_id, session_id=session_id,
            input_text=input_text, output_text=generic_text, context=context,
            reproducibility=ReproducibilitySnapshot(
                voice_profile_version=profile.version,
                render_engine_version=RENDER_ENGINE_VERSION,
                context=context, rule_snapshot={},
            ),
            neutral_defaults_used=[], is_bootstrap_output=True,
        )

    # Step 2 — Build base constraints from profile
    constraints, neutral_defaults_used = _build_rendering_constraints(profile)

    # Step 3 — Resolve intent mode
    # If caller specifies a mode, use it. Otherwise auto-detect from input.
    # Detection is always silent — user never sees the mode name.
    if intent_mode:
        parsed_mode = mode_from_string(intent_mode)
        if not parsed_mode:
            logger.warning("invalid_intent_mode_ignored", value=intent_mode)
            parsed_mode, _ = detect_intent_mode(input_text)
    else:
        parsed_mode, detection_confidence = detect_intent_mode(input_text)

    constraints, applied_keys = apply_intent_mode(constraints, parsed_mode)
    mode_trace = build_intent_mode_trace(parsed_mode, applied_keys)

    if neutral_defaults_used:
        logger.info("neutral_defaults_applied", user_id=str(profile.user_id),
                    dimensions=[d.dimension for d in neutral_defaults_used])

    # Step 4 — LLM rewrite within constraints
    system_prompt = _build_system_prompt(constraints)
    rendered_text = await _call_llm(system_prompt, input_text)

    # Step 4b — Deterministic output cleaning
    # Not a prompt instruction. Code enforcement.
    # Runs regardless of what the LLM produced.
    from voxa_rendering.cleaner import clean_render_output
    rendered_text = clean_render_output(rendered_text)

    # Step 5 — Boundary validation
    passed, violation = _check_boundaries(rendered_text, profile)
    if not passed:
        logger.warning("boundary_check_failed", user_id=str(profile.user_id),
                       violation=violation)
        return None

    # Step 6 — Reproducibility snapshot
    rule_snapshot = {k: str(v) for k, v in constraints.items()}
    if mode_trace:
        rule_snapshot["_intent_mode"] = mode_trace.get("intent_mode", "")

    output = RenderedOutput(
        user_id=profile.user_id, session_id=session_id,
        input_text=input_text, output_text=rendered_text, context=context,
        reproducibility=ReproducibilitySnapshot(
            voice_profile_version=profile.version,
            render_engine_version=RENDER_ENGINE_VERSION,
            context=context, rule_snapshot=rule_snapshot,
        ),
        neutral_defaults_used=neutral_defaults_used,
        is_bootstrap_output=False,
    )

    logger.info("render_complete", user_id=str(profile.user_id),
                output_id=str(output.output_id),
                neutral_default_count=len(neutral_defaults_used),
                intent_mode=intent_mode or "none",
                boundary_passed=True)

    return output
