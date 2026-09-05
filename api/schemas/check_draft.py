"""
api/schemas/check_draft.py — pydantic models for POST /api/check-draft,
matching Full Spec Section 3.5.1's documented field set exactly, with
one deliberate, flagged exception: content_lock is omitted. See
routes/check_draft.py's module docstring for why — score_draft_check's
own docstring confirms Content Lock only applies to a rewrite, and
Voice Check has none. This is a correction to the spec (tracked for
the next spec revision), not a gap in this implementation.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class CheckDraftRequest(BaseModel):
    draft_text: str = Field(..., min_length=1)
    # The extension always knows which composer surface it's running
    # in (Section 3.2: linkedin.js vs gmail.js) — required, not
    # inferred server-side.
    surface: str = Field(..., pattern="^(linkedin|gmail)$")
    # Optional per-call audience/purpose context (post vs. comment vs.
    # email-reply) — Section 3.5.1 lists this; score_draft_check has no
    # parameter for it today, so it is accepted and passed through to
    # telemetry only, not yet used to steer scoring. Flagged rather
    # than silently dropped.
    context: str | None = None
    client_version: str | None = None


class DimensionScore(BaseModel):
    label: str
    baseline: float
    output: float
    verdict: str  # "HIT" | "CLOSE" | "MISSED"


class WordDivergence(BaseModel):
    word: str
    baseline_freq: float
    output_freq: float


class BurrowsDelta(BaseModel):
    tier: str | None = None
    delta: float | None = None
    biggest_divergences: list[WordDivergence] = Field(default_factory=list)


class CheckDraftResponse(BaseModel):
    request_id: str
    overall_match: int
    dimension_scores: dict[str, DimensionScore]
    dimension_explanations: dict[str, str] = Field(default_factory=dict)
    burrows_delta: BurrowsDelta | None = None
    verdict: str  # "good" | "borderline" | "failed" — Section 3.5.1
    remaining_allowance: int | None = None
    recommended_action: str  # "fix_available" | "none"
    scoring_version: str
    timestamp: str
