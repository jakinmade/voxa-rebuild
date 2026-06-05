"""
Voxa — LLM Boundary
All LLM calls route through this module.
This is the ONLY point of contact with the Anthropic API across the entire codebase.

Architecture Spec v9.2.0, Section 3.1 — LLM Boundary Contract.

The calibration layer may request LLM assistance (edit classification scoring)
but must delegate the actual API call here. The rendering layer owns the LLM boundary.
"""

from __future__ import annotations

import json
import os

import httpx
import structlog

from voxa_core.enums import EditClass

logger = structlog.get_logger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
_ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


async def _call_anthropic(prompt: str, max_tokens: int = 150) -> str:
    """
    Single entry point for all Anthropic API calls.
    Called only from within the rendering layer.
    """
    if not _ANTHROPIC_API_KEY:
        logger.warning("llm_api_key_missing")
        return ""

    async with httpx.AsyncClient() as client:
        response = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": _ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=15.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"].strip()


async def rewrite_with_constraints(system_prompt: str, input_text: str) -> str:
    """
    Voice rendering rewrite. Called by the rendering engine only.
    Returns rewritten text within the constraints defined by the system prompt.
    """
    if not _ANTHROPIC_API_KEY:
        logger.warning("llm_api_key_missing_using_passthrough")
        return input_text

    async with httpx.AsyncClient() as client:
        response = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": _ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": [{"role": "user", "content": input_text}],
            },
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]


async def classify_edit_via_llm(prompt: str) -> tuple[EditClass, float]:
    """
    Edit classification scoring — called by the calibration layer via delegation.
    The calibration layer builds the prompt; this function executes the API call.
    The LLM returns a confidence score only. Rules-based layer makes the final decision.
    """
    text = await _call_anthropic(prompt, max_tokens=150)
    if not text:
        return EditClass.AMBIGUOUS, 0.0

    try:
        parsed = json.loads(text)
        classification = parsed.get("classification", "ambiguous")
        confidence = float(parsed.get("confidence", 0.0))

        logger.info(
            "llm_edit_classification_result",
            classification=classification,
            confidence=confidence,
            reasoning=parsed.get("reasoning", ""),
        )

        class_map = {
            "voice": EditClass.VOICE,
            "content": EditClass.CONTENT,
            "intent": EditClass.INTENT,
            "factual": EditClass.FACTUAL,
            "format": EditClass.FORMAT,
        }
        return class_map.get(classification, EditClass.AMBIGUOUS), confidence

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("llm_classification_parse_failed", error=str(e))
        return EditClass.AMBIGUOUS, 0.0
