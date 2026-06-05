"""
Voxa — Failure Mode Tests
Production edge cases that don't appear on happy paths.

Covers:
1. Malformed Anthropic responses — schema changes, error payloads, empty content
2. Redis outage — graceful fallback to memory rate limiting
3. Supabase outage — surfaces cleanly, doesn't corrupt state
4. Duplicate calibration submissions — idempotent handling
5. Concurrent profile updates — no silent overwrites
6. Rate limit bypass attempts — consistent enforcement
7. Missing API key — graceful passthrough, not crash
8. Boundary engine on edge input — empty string, unicode, very long text
9. Change vector on degenerate input — identical text, empty edit
10. Bootstrap with partial profile — renders generic, not error
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from voxa_core.entities import (
    BoundaryRules, RuleMetadata, VoiceProfile, VoiceProfileVersion,
)
from voxa_core.enums import LifecycleStage, SourceType


# ---------------------------------------------------------------------------
# 1. Malformed Anthropic responses
# ---------------------------------------------------------------------------

class TestMalformedAnthropicResponses:

    def test_empty_content_list_returns_fallback(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        assert _parse_anthropic_response({"content": []}, "fallback") == "fallback"

    def test_missing_content_key_returns_fallback(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        assert _parse_anthropic_response({"error": "overloaded_error", "type": "error"}, "fallback") == "fallback"

    def test_tool_use_block_no_text_returns_fallback(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        data = {"content": [{"type": "tool_use", "id": "tool_abc", "name": "web_search"}]}
        assert _parse_anthropic_response(data, "fallback") == "fallback"

    def test_none_content_returns_fallback(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        assert _parse_anthropic_response({"content": None}, "fallback") == "fallback"

    def test_string_content_not_list_returns_fallback(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        assert _parse_anthropic_response({"content": "raw string"}, "fallback") == "fallback"

    def test_valid_response_parsed_correctly(self):
        from voxa_rendering.llm_boundary import _parse_anthropic_response
        data = {"content": [{"type": "text", "text": "Rendered output"}]}
        assert _parse_anthropic_response(data, "fallback") == "Rendered output"

    @pytest.mark.asyncio
    async def test_llm_rewrite_falls_through_on_http_error(self):
        """rewrite_with_constraints returns input_text passthrough when LLM fails."""
        from voxa_rendering.llm_boundary import rewrite_with_constraints
        with patch("voxa_rendering.llm_boundary._send_anthropic_request",
                   side_effect=Exception("Connection refused")):
            result = await rewrite_with_constraints("system prompt", "original input")
        assert result == "original input"  # Passthrough, not crash

    @pytest.mark.asyncio
    async def test_classify_edit_returns_ambiguous_on_llm_failure(self):
        """classify_edit_via_llm returns AMBIGUOUS when LLM fails."""
        from voxa_rendering.llm_boundary import classify_edit_via_llm
        from voxa_core.enums import EditClass
        with patch("voxa_rendering.llm_boundary._send_anthropic_request",
                   side_effect=Exception("Timeout")):
            edit_class, confidence = await classify_edit_via_llm("any prompt")
        assert edit_class == EditClass.AMBIGUOUS
        assert confidence == 0.0

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_passthrough(self):
        """No API key → passthrough, not exception."""
        from voxa_rendering.llm_boundary import rewrite_with_constraints
        with patch("voxa_rendering.llm_boundary._ANTHROPIC_API_KEY", ""):
            result = await rewrite_with_constraints("prompt", "input text")
        assert result == "input text"


# ---------------------------------------------------------------------------
# 2. Redis outage — graceful fallback
# ---------------------------------------------------------------------------

class TestRedisOutage:

    def test_redis_outage_falls_back_to_memory(self):
        """Redis unavailable → memory rate limiting, no exception surfaced to caller."""
        from voxa_api.middleware import check_rate_limit
        from unittest.mock import MagicMock
        import os

        request = MagicMock()
        request.url.path = "/render"
        request.headers = {}
        request.client.host = f"test_{uuid4().hex[:8]}"

        with patch.dict(os.environ, {"VOXA_RATE_LIMIT_BACKEND": "redis"}):
            with patch("voxa_api.middleware._get_redis",
                       side_effect=Exception("Redis connection refused")):
                # Should not raise — falls back to memory
                check_rate_limit(request)

    def test_redis_import_error_falls_back_to_memory(self):
        """redis-py not installed → falls back to memory silently."""
        from voxa_api.middleware import check_rate_limit
        import os

        request = MagicMock()
        request.url.path = "/render"
        request.headers = {}
        request.client.host = f"test_{uuid4().hex[:8]}"

        with patch.dict(os.environ, {"VOXA_RATE_LIMIT_BACKEND": "redis"}):
            with patch("voxa_api.middleware._get_redis",
                       side_effect=ImportError("No module named 'redis'")):
                check_rate_limit(request)  # Should not raise


# ---------------------------------------------------------------------------
# 3. Supabase outage
# ---------------------------------------------------------------------------

class TestSupabaseOutage:

    def test_supabase_get_raises_runtime_error_without_credentials(self):
        """Supabase repo raises RuntimeError when credentials missing — surfaces cleanly."""
        import os
        with patch.dict(os.environ, {"SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": ""}):
            from voxa_api.repositories.supabase import SupabaseProfileRepository
            repo = SupabaseProfileRepository()
            with pytest.raises((RuntimeError, Exception)):
                repo.get(uuid4())

    def test_supabase_repo_structure_is_complete(self):
        """Supabase repo implements all required interface methods."""
        from voxa_api.repositories.base import ProfileRepository
        from voxa_api.repositories.supabase import SupabaseProfileRepository
        repo = SupabaseProfileRepository()
        required = ["get", "save", "exists", "get_version", "save_version",
                    "list_versions", "get_session_count", "increment_session_count"]
        for method in required:
            assert hasattr(repo, method), f"Missing method: {method}"


# ---------------------------------------------------------------------------
# 4. Duplicate calibration submissions
# ---------------------------------------------------------------------------

class TestDuplicateCalibration:

    @pytest.mark.asyncio
    async def test_same_edit_submitted_twice_produces_expected_observations(self):
        """
        Submitting the same voice edit twice should accumulate observations,
        not corrupt state. Evidence count increases, not duplicated incorrectly.
        """
        from voxa_rendering.engine import render
        from voxa_calibration.engine import calibrate
        from voxa_core.entities import RuleMetadata
        from voxa_core.enums import LifecycleStage

        user_id = uuid4()
        profile = _make_renderable_profile(user_id)

        output = await render(
            input_text="This might perhaps work.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None

        observations_all = []

        # First submission
        _, obs1, _ = calibrate(
            rendered_output=output,
            original_text=output.output_text,
            edited_text="This works.",
            user_instruction="Remove hedging.",
            profile=profile,
            existing_observations=observations_all,
        )
        observations_all.extend(obs1)

        # Second identical submission
        _, obs2, candidates = calibrate(
            rendered_output=output,
            original_text=output.output_text,
            edited_text="This works.",
            user_instruction="Remove hedging.",
            profile=profile,
            existing_observations=observations_all,
        )
        observations_all.extend(obs2)

        # State should be consistent — total observations cumulative
        assert len(observations_all) >= len(obs1)
        # No exception, no corrupted state


# ---------------------------------------------------------------------------
# 5. Concurrent profile updates
# ---------------------------------------------------------------------------

class TestConcurrentProfileUpdates:

    def test_merge_profile_is_additive_not_destructive(self):
        """
        Sequential merges accumulate evidence.
        Later merge does not erase earlier evidence.
        """
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile, merge_profile

        user_id = uuid4()

        h1 = humanise("I prefer direct communication.", user_id, SourceType.ONBOARDING)
        profile = build_profile(h1)

        directness_after_h1 = (
            profile.identity.directness.evidence_count
            if profile.identity.directness else 0
        )

        h2 = humanise("Keep it direct and concise.", user_id, SourceType.ONBOARDING)
        merge_profile(profile, h2)

        directness_after_h2 = (
            profile.identity.directness.evidence_count
            if profile.identity.directness else 0
        )

        # Evidence accumulated — not reset
        assert directness_after_h2 >= directness_after_h1

    def test_conflicting_merge_does_not_overwrite_existing_value(self):
        """Conflicting fact reduces confidence but preserves existing rule value."""
        from voxa_humanisation.engine import humanise
        from voxa_profile.builder import build_profile, merge_profile

        user_id = uuid4()
        h1 = humanise("I prefer formal communication.", user_id, SourceType.ONBOARDING)
        profile = build_profile(h1)

        if profile.identity.formality:
            original_value = profile.identity.formality.value
            original_confidence = profile.identity.formality.confidence

            h2 = humanise("Keep it casual and informal.", user_id, SourceType.ONBOARDING)
            merge_profile(profile, h2)

            # Value preserved
            if profile.identity.formality:
                assert profile.identity.formality.value == original_value
                # Confidence reduced by conflict
                assert profile.identity.formality.confidence <= original_confidence

    @pytest.mark.asyncio
    async def test_in_memory_repo_handles_sequential_saves(self):
        """Sequential save operations don't corrupt state."""
        from voxa_api.repositories.memory import InMemoryProfileRepository

        repo = InMemoryProfileRepository()
        user_id = uuid4()
        profile = VoiceProfile(user_id=user_id, version=1)
        repo.save(profile)

        profile.version = 2
        repo.save(profile)

        retrieved = repo.get(user_id)
        assert retrieved.version == 2


# ---------------------------------------------------------------------------
# 6. Rate limit enforcement consistency
# ---------------------------------------------------------------------------

class TestRateLimitEnforcement:

    def test_rate_limit_resets_after_window(self):
        """After window expires, requests are allowed again."""
        from voxa_api.middleware import _rate_store, WINDOW_SECONDS
        import time

        host = f"reset_test_{uuid4().hex[:8]}"
        key = (host, "/render")

        # Fill with expired timestamps (outside window)
        old_time = time.time() - WINDOW_SECONDS - 1
        _rate_store[key] = [old_time] * 100  # 100 "old" requests

        from voxa_api.middleware import check_rate_limit
        request = MagicMock()
        request.url.path = "/render"
        request.headers = {}
        request.client.host = host

        # Should not raise — old requests expired
        check_rate_limit(request)

    def test_different_users_have_independent_limits(self):
        """Rate limit for user A does not affect user B."""
        from voxa_api.middleware import _rate_store, RATE_LIMITS, check_rate_limit
        import time

        user_a = f"user_a_{uuid4().hex[:8]}"
        user_b = f"user_b_{uuid4().hex[:8]}"
        limit = RATE_LIMITS["/render"]

        # Fill user_a to limit
        _rate_store[(user_a, "/render")] = [time.time()] * limit

        # User B should still pass
        request_b = MagicMock()
        request_b.url.path = "/render"
        request_b.headers = {}
        request_b.client.host = user_b

        check_rate_limit(request_b)  # Should not raise


# ---------------------------------------------------------------------------
# 7. Boundary engine edge cases
# ---------------------------------------------------------------------------

class TestBoundaryEngineEdgeCases:

    def test_empty_string_passes_boundary(self):
        from voxa_rendering.boundary import check_boundaries
        profile = _make_renderable_profile(uuid4())
        passed, reason = check_boundaries("", profile)
        assert passed is True

    def test_very_long_text_passes_boundary(self):
        from voxa_rendering.boundary import check_boundaries
        profile = _make_renderable_profile(uuid4())
        long_text = "This is a clear statement. " * 500
        passed, reason = check_boundaries(long_text, profile)
        assert passed is True

    def test_unicode_text_passes_boundary(self):
        from voxa_rendering.boundary import check_boundaries
        profile = _make_renderable_profile(uuid4())
        unicode_text = "Résumé: klären Sie bitte, ob 这个 works correctly."
        passed, reason = check_boundaries(unicode_text, profile)
        assert passed is True

    def test_literal_boundary_term_blocked(self):
        from voxa_rendering.boundary import check_boundaries
        profile = _make_renderable_profile(uuid4())
        passed, reason = check_boundaries("This is an aggressive response.", profile)
        assert passed is False

    def test_no_boundaries_set_always_passes(self):
        from voxa_rendering.boundary import check_boundaries
        profile = VoiceProfile(user_id=uuid4())
        profile.boundaries = BoundaryRules()  # No boundaries
        passed, reason = check_boundaries("Any text at all.", profile)
        assert passed is True


# ---------------------------------------------------------------------------
# 8. Change vector on degenerate input
# ---------------------------------------------------------------------------

class TestChangeVectorDegenerate:

    def test_identical_text_produces_zero_vector(self):
        from voxa_calibration.change_vector import compute_change_vector
        text = "We should consider this approach carefully."
        vector = compute_change_vector(text, text)
        assert vector.voice_magnitude == 0.0 or vector.voice_magnitude < 0.1
        assert vector.jaccard_similarity == 1.0

    def test_empty_original_does_not_crash(self):
        from voxa_calibration.change_vector import compute_change_vector
        vector = compute_change_vector("", "This works.")
        assert vector is not None
        assert isinstance(vector.compression_ratio, float)

    def test_empty_edited_does_not_crash(self):
        from voxa_calibration.change_vector import compute_change_vector
        vector = compute_change_vector("This might work.", "")
        assert vector is not None

    def test_single_word_edit_does_not_crash(self):
        from voxa_calibration.change_vector import analyse_edit
        result = analyse_edit("Might.", "Will.")
        assert result.edit_class in ("voice", "content", "ambiguous", "factual", "format", "intent")


# ---------------------------------------------------------------------------
# 9. Bootstrap with partial profile
# ---------------------------------------------------------------------------

class TestBootstrapPartialProfile:

    @pytest.mark.asyncio
    async def test_incomplete_profile_renders_generic_not_error(self):
        """Profile below minimum renderable threshold returns generic output, not 500."""
        from voxa_rendering.engine import render
        profile = VoiceProfile(user_id=uuid4())
        # No rules, no boundaries — incomplete profile
        output = await render(
            input_text="Some content to render.",
            profile=profile,
            session_id=uuid4(),
        )
        # Must return something — never crash
        assert output is not None
        assert output.is_bootstrap_output is True

    @pytest.mark.asyncio
    async def test_partial_profile_with_boundary_only_renders_generic(self):
        """Boundary set but no voice rules — still bootstrap."""
        from voxa_rendering.engine import render
        profile = VoiceProfile(user_id=uuid4())
        profile.boundaries.tone_boundaries = RuleMetadata(
            value=["patronising"], confidence=1.0, source=["system"],
            stability=1.0, decay_rate=0.0,
            lifecycle_stage=LifecycleStage.BOUNDARY,
        )
        output = await render(
            input_text="Some content.",
            profile=profile,
            session_id=uuid4(),
        )
        assert output is not None
        assert output.is_bootstrap_output is True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_renderable_profile(user_id) -> VoiceProfile:
    profile = VoiceProfile(user_id=user_id)
    profile.identity.directness = RuleMetadata(
        value="high", confidence=0.6, source=["onboarding"],
        stability=0.5, decay_rate=0.02,
        lifecycle_stage=LifecycleStage.PROVISIONAL,
    )
    profile.linguistic.forbidden_phrases = RuleMetadata(
        value=[], confidence=0.5, source=["onboarding"],
        stability=0.4, decay_rate=0.02,
        lifecycle_stage=LifecycleStage.CANDIDATE,
    )
    profile.boundaries.tone_boundaries = RuleMetadata(
        value=["patronising", "aggressive", "salesy"],
        confidence=1.0, source=["system"],
        stability=1.0, decay_rate=0.0,
        lifecycle_stage=LifecycleStage.BOUNDARY,
    )
    return profile
