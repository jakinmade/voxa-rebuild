"""
Voxa — Intent Mode Detection & Application (Layer 3 support)

This module was referenced by engine.py's render() pipeline but never
built — every render() call failed on import. Built here as a faithful
port of the live, shipped Streamlit app's intent-mode logic (prompts.py's
_detect_mode and mode_prompts), restructured to the constraints-based
API engine.py expects. The detection heuristic and the four mode
instructions are copied from the live version, not reinvented.

Four modes, matching prompts.py's mode_prompts dict:
  GET_IT_DONE          default. Tighten, don't add.
  WRITE_SOMETHING       compose original content, structure it.
  THINK_IT_THROUGH      explore/challenge, not final copy.
  HELP_ME_UNDERSTAND    explain concepts, step by step.

Auto-detection (detect_intent_mode) only distinguishes HELP_ME_UNDERSTAND
from GET_IT_DONE — this matches the live app exactly, which has no
detector for WRITE_SOMETHING or THINK_IT_THROUGH either. Those two are
reachable only via an explicit intent_mode= argument (mode_from_string),
same as the live app's design intent.

Intent mode adjusts a new "task_instruction" execution constraint only.
It never touches identity dimensions (cadence, compression, directness,
warmth, formality) — those come from the voice profile and are fixed
regardless of what the caller is trying to do with the text. This
matches engine.py's own docstring: "Intent mode adjusts execution
constraints only. Identity dimensions are never touched."
"""

from __future__ import annotations

import re
from enum import Enum


class IntentMode(str, Enum):
    GET_IT_DONE = "GET_IT_DONE"
    WRITE_SOMETHING = "WRITE_SOMETHING"
    THINK_IT_THROUGH = "THINK_IT_THROUGH"
    HELP_ME_UNDERSTAND = "HELP_ME_UNDERSTAND"


# Mode instruction copy — verbatim from prompts.py's mode_prompts dict,
# the live, shipped version. Not reinvented here.
_MODE_INSTRUCTIONS: dict[IntentMode, str] = {
    IntentMode.GET_IT_DONE: (
        "Rewrite this text. Tighten it. Remove anything that doesn't earn its place. "
        "Preserve the writer's voice exactly: their directness, their cadence, their register. "
        "Do not add warmth, hedging, or polish that isn't already there."
    ),
    IntentMode.WRITE_SOMETHING: (
        "Help compose this as original content. "
        "Structure it clearly. Preserve the writer's voice throughout. "
        "The voice is theirs. The structure is your contribution."
    ),
    IntentMode.THINK_IT_THROUGH: (
        "Explore the ideas in this text. Generate challenges, alternative angles, questions. "
        "This is not final copy. It is thinking. Expand, challenge, question. "
        "Preserve the writer's voice in any prose you produce."
    ),
    IntentMode.HELP_ME_UNDERSTAND: (
        "Explain the concepts in this text clearly. "
        "Use step-by-step structure where it helps. Use analogies where they clarify. "
        "Write with the depth needed for genuine understanding. Not brevity. "
        "Preserve the writer's voice. Never write for them. Write as them, explaining."
    ),
}

# Detection regexes — ported verbatim from prompts.py's _detect_mode.
_ACADEMIC = re.compile(
    r"\b(furthermore|moreover|nevertheless|in conclusion|it can be argued|"
    r"according to|as argued by|cited in|essay|thesis|hypothesis|"
    r"analysis|evaluate|critically|literature|methodology)\b", re.I
)
_STUDENT_EXPLICIT = re.compile(
    r"\b(help me understand|explain (to me|why|how|what)|"
    r"i (don.t|do not|can.t|cannot) understand|"
    r"my essay|my assignment|my coursework|my dissertation|"
    r"for class|my professor|my tutor|word limit|struggling with)\b", re.I
)
_ACADEMIC_HEDGES = re.compile(
    r"\b(it could be argued|it can be argued|one could argue|"
    r"to some extent|arguably|ostensibly|it is possible that|"
    r"it seems that|it appears that)\b", re.I
)
_DOMAIN = re.compile(
    r"\b(theory|argument|evidence|critique|evaluation|concept|"
    r"framework|discuss|analyse|analyze|compare|contrast|examine)\b", re.I
)


def detect_intent_mode(text: str) -> tuple[IntentMode, float]:
    """
    Auto-detects intent mode from input text. Silent — never shown to
    the user. Ported from prompts.py's _detect_mode scoring, which only
    ever distinguishes HELP_ME_UNDERSTAND from GET_IT_DONE across five
    independent academic-language signals. Returns (mode, score), where
    score is the same 0.0-1.0 confidence value the live detector computes
    (>= 0.55 triggers HELP_ME_UNDERSTAND).
    """
    if not text or not text.strip():
        return IntentMode.GET_IT_DONE, 0.0

    score = 0.0
    words = max(len(text.split()), 1)

    ac_matches = len(_ACADEMIC.findall(text))
    score += min(0.35, (ac_matches / (words / 100)) * 0.08)

    if _STUDENT_EXPLICIT.search(text):
        score += 0.35

    hedge_count = len(_ACADEMIC_HEDGES.findall(text))
    if hedge_count >= 2:
        score += 0.20
    elif hedge_count == 1:
        score += 0.10

    if len(_DOMAIN.findall(text)) >= 3:
        score += 0.15

    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) >= 4:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 18:
            score += 0.10

    score = min(score, 1.0)
    mode = IntentMode.HELP_ME_UNDERSTAND if score >= 0.55 else IntentMode.GET_IT_DONE
    return mode, round(score, 2)


def mode_from_string(value: str | None) -> IntentMode | None:
    """
    Parses an explicit caller-supplied mode string into IntentMode.
    Case-insensitive. Returns None for anything unrecognised, so the
    caller (engine.py) can fall back to auto-detection rather than crash
    — this is exactly the behaviour render() already expects (see the
    `if not parsed_mode: ... detect_intent_mode(...)` fallback).
    """
    if not value:
        return None
    try:
        return IntentMode(value.strip().upper())
    except ValueError:
        return None


def apply_intent_mode(
    constraints: dict[str, object], mode: IntentMode
) -> tuple[dict[str, object], list[str]]:
    """
    Adds the mode's task instruction to the constraints dict under a new
    "task_instruction" key. This is an execution constraint, not an
    identity dimension — cadence, compression, directness, warmth and
    formality (the profile's identity rules, already set earlier in
    _build_rendering_constraints) are never touched here. Returns the
    updated constraints dict plus the list of keys this call added or
    changed, for the reproducibility trace.
    """
    updated = dict(constraints)
    updated["task_instruction"] = _MODE_INSTRUCTIONS.get(
        mode, _MODE_INSTRUCTIONS[IntentMode.GET_IT_DONE]
    )
    return updated, ["task_instruction"]


def build_intent_mode_trace(mode: IntentMode, applied_keys: list[str]) -> dict[str, object]:
    """
    Builds the trace dict recorded on the reproducibility snapshot.
    engine.py reads trace["intent_mode"] to populate
    rule_snapshot["_intent_mode"].
    """
    return {
        "intent_mode": mode.value if isinstance(mode, IntentMode) else str(mode),
        "applied_keys": list(applied_keys),
    }
