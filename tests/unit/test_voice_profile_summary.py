"""
Tests for _generate_voice_profile_summary (app.py) — the one-time
distillation call that condenses a person's raw writing into a short
natural-language profile of their habits, injected into the render
prompt alongside the existing anchor sentences and numeric baseline
targets.

Grounded in a specific research finding (see build_voice_profile_
summary_prompt's docstring in prompts.py): generating from a distilled
profile measurably outperforms generating from raw context directly.

Deliberately fails open throughout — this is a quality enhancement,
never a required part of the pipeline. Every test here confirms a
failure mode returns None cleanly rather than raising, matching the
standard set by persistence.py and every other new module this
session.
"""
import os
from unittest.mock import patch, MagicMock

import app


def _fake_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def test_returns_none_when_no_api_key():
    with patch.dict(os.environ, {}, clear=True):
        assert app._generate_voice_profile_summary("some corpus text") is None


def test_returns_none_when_corpus_empty():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        assert app._generate_voice_profile_summary("") is None
        assert app._generate_voice_profile_summary("   ") is None
        assert app._generate_voice_profile_summary(None) is None


def test_returns_summary_text_on_success():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _fake_response(
                "Writes short, direct sentences. Rarely hedges. Opens with the concrete problem."
            )
            result = app._generate_voice_profile_summary("some corpus text of real writing")
    assert result == "Writes short, direct sentences. Rarely hedges. Opens with the concrete problem."


def test_uses_the_cost_guardrail_max_tokens():
    """Pinned at 200 — a distilled profile only needs 3-5 sentences,
    per the standing cost-guardrail rule checked before this was
    built. A regression here (e.g. someone bumping it to 4096 to
    'be safe') would silently multiply the cost of every first render
    for every person."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _fake_response("A profile.")
            app._generate_voice_profile_summary("some corpus text")
            call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["max_tokens"] == 200


def test_returns_none_on_api_failure_without_raising():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = Exception("API down")
            result = app._generate_voice_profile_summary("some corpus text")
    assert result is None


def test_no_auto_retry_on_failure():
    """Cost guardrail: exactly one attempt, never a silent retry loop
    that could multiply cost on a flaky connection."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.side_effect = Exception("API down")
            app._generate_voice_profile_summary("some corpus text")
    assert mock_client.messages.create.call_count == 1
