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


def _parse_anthropic_response(data: dict, fallback: str = "") -> str:
    """Safely parses Anthropic API response — guards against schema changes."""
    content_blocks = data.get("content", [])
    if not content_blocks or not isinstance(content_blocks, list):
        logger.warning("llm_empty_or_malformed_response", data=str(data)[:200])
        return fallback
    first_block = content_blocks[0]
    if not isinstance(first_block, dict) or "text" not in first_block:
        logger.warning("llm_unexpected_block_shape", block=str(first_block)[:200])
        return fallback
    return first_block["text"]


async def _send_anthropic_request(
    messages: list[dict],
    max_tokens: int = 1024,
    system: str | None = None,
    timeout: float = 30.0,
) -> dict:
    """
    Single shared Anthropic transport. All API calls route here.
    Raises httpx.HTTPStatusError on non-2xx responses.
    """
    if not _ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    payload: dict = {
        "model": CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        payload["system"] = system

    async with httpx.AsyncClient() as client:
        response = await client.post(
            ANTHROPIC_API_URL,
            headers={
                "x-api-key": _ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()


async def _call_anthropic(prompt: str, max_tokens: int = 150) -> str:
    """Convenience wrapper for single-turn prompts."""
    if not _ANTHROPIC_API_KEY:
        logger.warning("llm_api_key_missing")
        return ""
    try:
        data = await _send_anthropic_request(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            timeout=15.0,
        )
        return _parse_anthropic_response(data, fallback="")
    except Exception as e:
        logger.warning("llm_call_failed", error=str(e))
        return ""


async def rewrite_with_constraints(system_prompt: str, input_text: str) -> str:
    """
    Voice rendering rewrite. Called by the rendering engine only.
    Uses shared transport — single Anthropic request path.
    """
    if not _ANTHROPIC_API_KEY:
        logger.warning("llm_api_key_missing_using_passthrough")
        return input_text
    try:
        data = await _send_anthropic_request(
            messages=[{"role": "user", "content": input_text}],
            system=system_prompt,
            max_tokens=1024,
            timeout=30.0,
        )
        return _parse_anthropic_response(data, fallback=input_text)
    except Exception as e:
        logger.warning("llm_rewrite_failed", error=str(e))
        return input_text  # Passthrough on failure


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
