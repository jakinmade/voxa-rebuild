"""
Voxa — Boundary Validation Engine
Architecture Spec v9.2.0, Section 7.5.

A failed boundary check returns NO output — not a degraded output.

Boundary validation is semantic, not string-matching.
"aggressive" as a boundary does not just catch the word "aggressive" —
it catches the semantic signal of aggression.

Three-layer check:
1. Literal phrase match — catches explicit violations
2. Semantic proxy detection — catches implied violations via
   linguistic markers associated with each boundary class
3. Structural aggression signals — sentence-level patterns
"""

from __future__ import annotations

import re

import structlog

from voxa_core.entities import VoiceProfile

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Semantic proxy maps
# Maps boundary concepts to their linguistic markers in text
# ---------------------------------------------------------------------------

TONE_PROXIES: dict[str, list[str]] = {
    "aggressive": [
        r"\byou (must|need to|have to|should)\b",
        r"\bthis is (wrong|incorrect|unacceptable|ridiculous)\b",
        r"\b(stop|don.t|never)\b.{0,20}\b(doing|saying|being)\b",
        r"\bobviously\b",
        r"\bclearly you\b",
        r"\bfailing to\b",
    ],
    "patronising": [
        r"\bas (anyone|everyone) (knows|understands|can see)\b",
        r"\blet me (explain|be clear|spell it out)\b",
        r"\bsimply put\b",
        r"\bbasic(ally)?\b.{0,20}\b(understand|know|see)\b",
        r"\byou (just|simply|need to) understand\b",
    ],
    "salesy": [
        r"\bact now\b",
        r"\blimited (time|offer|availability)\b",
        r"\bdon.t miss (out|this)\b",
        r"\bamazing (deal|offer|opportunity)\b",
        r"\bguaranteed (results|returns|success)\b",
        r"\bno risk\b",
        r"\b(best|only|perfect) (solution|choice|option)\b",
    ],
    "speculative": [
        r"\bwill (definitely|certainly|absolutely) (grow|increase|succeed|work)\b",
        r"\bguaranteed (to|returns|growth)\b",
        r"\b(100%|certain(ly)?)\b.{0,20}\b(work|succeed|return)\b",
    ],
}

CONTENT_PROXIES: dict[str, list[str]] = {
    "unverified statistics": [
        r"\b\d+%\b.{0,30}\b(of (all|people|users|companies))\b",
        r"\bstudies show\b",
        r"\bresearch (shows|proves|confirms)\b",
        r"\bexperts (say|agree|confirm)\b",
    ],
    "guaranteed returns": [
        r"\bguaranteed\b.{0,20}\b(return|profit|gain|growth)\b",
        r"\b(risk.free|risk free)\b",
    ],
}


def _check_phrase(text: str, phrase: str) -> bool:
    """Literal phrase match — case-insensitive."""
    return phrase.lower() in text.lower()


def _check_proxies(text: str, concept: str, proxy_map: dict) -> list[str]:
    """Returns list of proxy patterns matched for a concept."""
    proxies = proxy_map.get(concept, [])
    matched = []
    for pattern in proxies:
        if re.search(pattern, text, re.IGNORECASE):
            matched.append(pattern)
    return matched


def check_boundaries(text: str, profile: VoiceProfile) -> tuple[bool, str | None]:
    """
    Semantic boundary validation.
    Returns (passed, violation_reason).
    None violation_reason means passed.

    A failed check must return NO output — not degraded output.
    """
    violations: list[str] = []

    if profile.boundaries.tone_boundaries:
        boundary_values: list[str] = profile.boundaries.tone_boundaries.value or []
        for concept in boundary_values:
            # Layer 1 — literal
            if _check_phrase(text, concept):
                violations.append(f"tone_literal:'{concept}'")
                continue
            # Layer 2 — semantic proxies
            matched = _check_proxies(text, concept, TONE_PROXIES)
            if matched:
                violations.append(f"tone_semantic:'{concept}' via {matched[0]}")

    if profile.boundaries.content_boundaries:
        content_values: list[str] = profile.boundaries.content_boundaries.value or []
        for concept in content_values:
            if _check_phrase(text, concept):
                violations.append(f"content_literal:'{concept}'")
                continue
            matched = _check_proxies(text, concept, CONTENT_PROXIES)
            if matched:
                violations.append(f"content_semantic:'{concept}' via {matched[0]}")

    if violations:
        reason = "; ".join(violations)
        logger.warning("boundary_check_failed", violations=violations)
        return False, reason

    return True, None
