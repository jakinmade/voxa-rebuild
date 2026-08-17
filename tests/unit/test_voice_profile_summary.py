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


# ------------------------------------------------------------------
# AI-tell guardrails on the summary itself.
#
# Everything else in the render pipeline (main render, correction
# pass) gets both a prompt instruction AND a deterministic regex
# sweep to strip AI tells (em dashes, corporate filler, verbose
# openers). This call previously had neither - its output feeds
# directly into every render's system prompt as "WRITER'S
# DISTINCTIVE HABITS", so AI-toned text here contaminates the exact
# prompt meant to prevent AI-toned output.
# ------------------------------------------------------------------

def test_em_dash_in_model_output_gets_swept():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _fake_response(
                "Writes short sentences — often trailing off mid-thought."
            )
            result = app._generate_voice_profile_summary("some corpus text")
    assert "—" not in result
    assert "-" not in result or "—" not in result  # no em dash survives either way


def test_corporate_filler_in_model_output_gets_swept():
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_client = mock_cls.return_value
            mock_client.messages.create.return_value = _fake_response(
                "It is worth noting that they leverage robust, seamless phrasing."
            )
            result = app._generate_voice_profile_summary("some corpus text")
    lowered = result.lower()
    assert "it is worth noting" not in lowered
    assert "leverage" not in lowered
    assert "robust" not in lowered
    assert "seamless" not in lowered


def test_prompt_instructs_plain_english_and_bans_ai_tells():
    """The prompt-level guardrail, not just the regex backstop -
    catches the case where the model would otherwise need the sweep
    to do all the work."""
    from prompts import build_voice_profile_summary_prompt
    prompt = build_voice_profile_summary_prompt()
    assert "em dash" in prompt.lower()
    assert "plain" in prompt.lower()
    assert "leverage" in prompt.lower()  # named as a banned word
    assert "furthermore" in prompt.lower() or "filler transition" in prompt.lower()
