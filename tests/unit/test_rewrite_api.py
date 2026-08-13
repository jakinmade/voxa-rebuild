"""
Tests for voxa_api.rewrite.suggest_rewrite.

No dedicated test coverage existed for this module before this file —
found during the August 2026 guardrail-consolidation audit as the
weakest of four independent output paths in the codebase: it returned
raw LLM output with zero deterministic cleanup, the only one of the
four paths that did. These tests mock the Anthropic API call (no real
spend, per standing cost discipline) and confirm the sweep is actually
wired into the return path.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from voxa_api.rewrite import suggest_rewrite


def _mock_anthropic_response(text: str):
    """Builds a minimal object matching the shape suggest_rewrite reads:
    response.content[0].text
    """
    block = type("Block", (), {"text": text})()
    response = type("Response", (), {"content": [block]})()
    return response


@pytest.mark.asyncio
async def test_no_api_key_returns_early():
    with patch.dict(os.environ, {}, clear=True):
        rewritten, status = await suggest_rewrite(
            "I think this might work.", "directness", {"value": True}
        )
    assert rewritten is None
    assert status == "no_api_key"


@pytest.mark.asyncio
async def test_raw_llm_output_is_swept_before_return():
    # The specific gap this closes: previously "We will leverage this—
    # it's seamless." would have been returned to the user completely
    # untouched. Now it must come back cleaned.
    raw = "We will leverage this—it's a seamless approach."
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.messages.create = AsyncMock(
                return_value=_mock_anthropic_response(raw)
            )
            rewritten, status = await suggest_rewrite(
                "Original sentence.", "directness", {"value": True}
            )

    assert status == "ok"
    assert rewritten is not None
    assert "\u2014" not in rewritten
    assert "leverage" not in rewritten.lower()
    assert "seamless" not in rewritten.lower()


@pytest.mark.asyncio
async def test_plausibility_shield_is_swept_before_return():
    raw = "I see it as the more direct version of your point."
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.messages.create = AsyncMock(
                return_value=_mock_anthropic_response(raw)
            )
            rewritten, status = await suggest_rewrite(
                "Original sentence.", "directness", {"value": True}
            )

    assert status == "ok"
    assert "I see it as" not in rewritten
    assert rewritten.startswith("It is")


@pytest.mark.asyncio
async def test_swept_output_matching_original_sentence_returns_no_change():
    # If the sweep reduces the rewrite to exactly the original sentence,
    # that must still be caught as "no_change_returned", not shown as a
    # fake improvement.
    original = "This is the strongest angle we have."
    raw = "I think that this is the strongest angle we have."
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.messages.create = AsyncMock(
                return_value=_mock_anthropic_response(raw)
            )
            rewritten, status = await suggest_rewrite(
                original, "directness", {"value": True}
            )

    assert rewritten is None
    assert status == "no_change_returned"


@pytest.mark.asyncio
async def test_api_error_returns_diagnosable_status():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.AsyncAnthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.messages.create = AsyncMock(side_effect=RuntimeError("boom"))
            rewritten, status = await suggest_rewrite(
                "Original sentence.", "directness", {"value": True}
            )

    assert rewritten is None
    assert status.startswith("api_error:")
