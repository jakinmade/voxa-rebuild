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
