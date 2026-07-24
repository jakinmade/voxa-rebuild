"""
Voxa — Checker Routes (v1)
Iteration 1 scope: stateless. Compares a draft against reference writing
samples passed in the same request. No accounts, no persistence, no payment.

Reuses the existing five-dimension fingerprint scorers in voxa_rendering.fingerprint
exactly as built — no new scoring logic invented here.

Iteration 2 will replace `reference_text` with a stored, persisted profile
built up over many sessions. The comparison logic below does not change,
only where the baseline values come from.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from voxa_rendering.fingerprint import (
    _extract_sentences,
    score_conclusion_position,
    score_hedging_signature,
    score_reader_assumption,
    score_compression_philosophy,
    score_energy_signature,
)
from voxa_api import profile_store
from voxa_api.rewrite import suggest_and_verify
from voxa_api.fitness import score_sample_fitness, fitness_gate
from voxa_api.recalibrate import compute_baseline_metrics, merge_baseline, recalibrate_draft

router = APIRouter()

# Each dimension: (scorer function, data key that carries the signal, human label)
_DIMENSIONS = [
    ("conclusion_position", score_conclusion_position, "point_first", "Conclusion position"),
    ("hedging_signature", score_hedging_signature, "owns_statements", "Hedging signature"),
    ("reader_assumption", score_reader_assumption, "assumes_peer", "Reader assumption"),
    ("compression_philosophy", score_compression_philosophy, "structural", "Compression philosophy"),
    ("energy_signature", score_energy_signature, "verb_dominant", "Energy signature"),
]

# Human-readable reveal, not the internal dimension name — per fingerprint.py's own
# documented design principle: "No dimension names exposed to the user... 'You lead
# with the answer' is the reveal. No horoscope language."
# Keyed by (dimension_id, profile_baseline_value) -> (matches_text, drifted_text)
_REVEAL_TEXT = {
    "conclusion_position": {
        True: ("You usually open with your point.", "This builds up to the point instead of leading with it."),
        False: ("You usually build up to your point.", "This opens with the point instead of building up to it."),
    },
    "hedging_signature": {
        True: ("You usually own your statements directly.", "This hedges more than you usually do."),
        False: ("You usually cushion your statements.", "This is more direct than you usually are."),
    },
    "reader_assumption": {
        True: ("You usually write like your reader already knows the context.", "This explains more than you usually would, like the reader's a stranger."),
        False: ("You usually spell things out for the reader.", "This assumes more background knowledge than you usually would."),
    },
    "compression_philosophy": {
        True: ("You usually write short, structured sentences.", "This runs longer and looser than you usually do."),
        False: ("You usually let sentence length vary deliberately.", "This is more uniform and structural than you usually are."),
    },
    "energy_signature": {
        True: ("Your energy usually comes through verbs.", "This leans on adjectives more than you usually do."),
        False: ("Your energy usually comes through adjectives and emphasis.", "This leans on verbs more than you usually do."),
    },
}


def _reveal(dim_id: str, profile_value: bool, matched: bool) -> str:
    matches_text, drifted_text = _REVEAL_TEXT[dim_id][profile_value]
    return matches_text if matched else drifted_text


class CheckRequest(BaseModel):
    reference_text: str = Field(..., min_length=20, description="Sample(s) of the user's own established writing")
    draft_text: str = Field(..., min_length=10, description="The new draft to check against it")


class DimensionResult(BaseModel):
    id: str
    label: str
    matched: bool
    reveal: str
    evidence: str | None = None
    suggested_rewrite: str | None = None
    rewrite_status: str | None = None  # internal diagnostic - UI should not show this to end users


class CheckResponse(BaseModel):
    match_score: int
    dimensions: list[DimensionResult]
    voiceprint: str  # e.g. "----o----x----o----o----o"  (o = match, x = break)


class BuildProfileRequest(BaseModel):
    email: str = Field(..., min_length=3)
    samples: list[str] = Field(..., min_length=1)


class ProfileResponse(BaseModel):
    email: str
    sample_count: int
    dimensions: dict
    status: str  # "ready", "nudge", "accumulate"
    confidence: str  # "high", "medium", "provisional"
    nudge: str | None = None
    tier: str  # of the most recently added sample
    cumulative_words: int


class CheckProfileRequest(BaseModel):
    email: str = Field(..., min_length=3)
    draft_text: str = Field(..., min_length=10)


class RecalibrateRequest(BaseModel):
    email: str = Field(..., min_length=3)
    draft_text: str = Field(..., min_length=20)


class RecalibrateResponse(BaseModel):
    original_match_score: int
    rewritten_text: str | None
    rewritten_match_score: int | None
    status: str


def _score_text(text: str) -> dict[str, dict]:
    sentences = _extract_sentences(text)
    out = {}
    for dim_id, fn, data_key, _label in _DIMENSIONS:
        obs = fn(sentences, text)
        out[dim_id] = {"data": obs.data, "key": data_key, "evidence": obs.evidence_quotes}
    return out


@router.post("/check", response_model=CheckResponse)
async def check(request: CheckRequest) -> CheckResponse:
    reference_scores = _score_text(request.reference_text)
    draft_scores = _score_text(request.draft_text)

    results: list[DimensionResult] = []
    matched_count = 0

    for dim_id, _fn, data_key, label in _DIMENSIONS:
        ref_value = reference_scores[dim_id]["data"].get(data_key)
        draft_value = draft_scores[dim_id]["data"].get(data_key)
        matched = (ref_value == draft_value)

        evidence = None
        if not matched:
            quotes = draft_scores[dim_id]["evidence"]
            evidence = quotes[0] if quotes else None

        results.append(DimensionResult(
            id=dim_id, label=label, matched=matched,
            reveal=_reveal(dim_id, bool(ref_value), matched),
            evidence=evidence,
        ))
        if matched:
            matched_count += 1

    match_score = round((matched_count / len(_DIMENSIONS)) * 100)
    voiceprint = "".join("o" if r.matched else "x" for r in results)

    return CheckResponse(match_score=match_score, dimensions=results, voiceprint=voiceprint)


def _build_baseline(samples: list[str]) -> dict:
    """
    Majority vote per dimension across all samples given so far.
    More samples = more confident baseline, per the original design —
    this never overwrites on a single new sample, it recomputes across
    everything the user has given.
    """
    per_dim_votes: dict[str, list[bool]] = {d[0]: [] for d in _DIMENSIONS}
    per_dim_evidence: dict[str, str | None] = {d[0]: None for d in _DIMENSIONS}

    for sample in samples:
        scores = _score_text(sample)
        for dim_id, _fn, data_key, _label in _DIMENSIONS:
            value = scores[dim_id]["data"].get(data_key)
            if value is not None:
                per_dim_votes[dim_id].append(bool(value))
            if per_dim_evidence[dim_id] is None and scores[dim_id]["evidence"]:
                per_dim_evidence[dim_id] = scores[dim_id]["evidence"][0]

    baseline = {}
    for dim_id, _fn, _data_key, label in _DIMENSIONS:
        votes = per_dim_votes[dim_id]
        if not votes:
            continue
        true_count = sum(votes)
        baseline[dim_id] = {
            "label": label,
            "value": true_count > len(votes) / 2,
            "confidence": round(max(true_count, len(votes) - true_count) / len(votes), 2),
            "example": per_dim_evidence[dim_id],
        }
    return baseline


@router.post("/profile/build", response_model=ProfileResponse)
async def build_profile(request: BuildProfileRequest) -> ProfileResponse:
    existing = profile_store.get_profile(request.email)
    prior_samples = existing["raw_samples"] if existing else []
    prior_cumulative_words = existing.get("cumulative_words", 0) if existing else 0
    prior_cumulative_docs = existing.get("cumulative_docs", 0) if existing else 0

    all_samples = prior_samples + request.samples
    baseline = _build_baseline(all_samples)

    # Fitness is scored on the newest submission (may be several samples in
    # one call, joined) so the nudge reflects what was just pasted.
    newest_text = "\n\n".join(request.samples)
    fitness = score_sample_fitness(newest_text)
    cumulative_words = prior_cumulative_words + fitness["word_count"]
    cumulative_docs = prior_cumulative_docs + len(request.samples)
    gate = fitness_gate(fitness, cumulative_words, cumulative_docs)

    # Numeric baseline (hedge density, sentence rhythm, ownership, directness) —
    # richer, continuous data that /recalibrate needs. Weighted merge across
    # all samples given so far, same as the dimension baseline.
    prior_restoration_metrics = existing.get("restoration_metrics") if existing else None
    restoration_metrics = prior_restoration_metrics
    for sample in request.samples:
        restoration_metrics = merge_baseline(restoration_metrics, compute_baseline_metrics(sample))

    profile = {
        "raw_samples": all_samples,
        "dimensions": baseline,
        "cumulative_words": cumulative_words,
        "cumulative_docs": cumulative_docs,
        "restoration_metrics": restoration_metrics,
    }
    profile_store.save_profile(request.email, profile)

    return ProfileResponse(
        email=request.email, sample_count=len(all_samples), dimensions=baseline,
        status=gate["action"], confidence=gate["confidence"], nudge=gate["message"],
        tier=fitness["tier"], cumulative_words=cumulative_words,
    )


@router.get("/profile/{email}", response_model=ProfileResponse)
async def get_profile(email: str) -> ProfileResponse:
    profile = profile_store.get_profile(email)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found for this email yet. Build one first with /profile/build.")
    return ProfileResponse(
        email=email, sample_count=len(profile["raw_samples"]), dimensions=profile["dimensions"],
        status="ready", confidence="existing", nudge=None, tier="n/a",
        cumulative_words=profile.get("cumulative_words", 0),
    )


@router.post("/check-profile", response_model=CheckResponse)
async def check_against_profile(request: CheckProfileRequest) -> CheckResponse:
    profile = profile_store.get_profile(request.email)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found for this email yet. Build one first with /profile/build.")

    draft_scores = _score_text(request.draft_text)
    results: list[DimensionResult] = []
    matched_count = 0

    for dim_id, fn, data_key, label in _DIMENSIONS:
        dim_profile = profile["dimensions"].get(dim_id)
        if dim_profile is None:
            continue
        draft_value = draft_scores[dim_id]["data"].get(data_key)
        matched = (draft_value == dim_profile["value"])

        evidence = None
        suggestion = None
        rewrite_status = None
        if not matched:
            quotes = draft_scores[dim_id]["evidence"]
            evidence = quotes[0] if quotes else None
            if evidence:
                suggestion, rewrite_status = await suggest_and_verify(
                    sentence=evidence,
                    dim_id=dim_id,
                    dimension_label=label,
                    data_key=data_key,
                    scorer_fn=fn,
                    profile_dimension=dim_profile,
                )

        results.append(DimensionResult(
            id=dim_id, label=label, matched=matched,
            reveal=_reveal(dim_id, dim_profile["value"], matched),
            evidence=evidence, suggested_rewrite=suggestion, rewrite_status=rewrite_status,
        ))
        if matched:
            matched_count += 1

    match_score = round((matched_count / len(results)) * 100) if results else 0
    voiceprint = "".join("o" if r.matched else "x" for r in results)

    return CheckResponse(match_score=match_score, dimensions=results, voiceprint=voiceprint)


def _quick_match_score(draft_text: str, dimensions: dict) -> int:
    """Match score only, no evidence/suggestions - used for before/after comparison."""
    scores = _score_text(draft_text)
    matched_count = 0
    total = 0
    for dim_id, _fn, data_key, _label in _DIMENSIONS:
        dim_profile = dimensions.get(dim_id)
        if dim_profile is None:
            continue
        total += 1
        draft_value = scores[dim_id]["data"].get(data_key)
        if draft_value == dim_profile["value"]:
            matched_count += 1
    return round((matched_count / total) * 100) if total else 0


@router.post("/recalibrate", response_model=RecalibrateResponse)
async def recalibrate(request: RecalibrateRequest) -> RecalibrateResponse:
    """
    Full-draft recalibration against the persisted profile - not a single
    flagged line, the whole draft. Per JA's explicit direction (25 July
    2026): use the baseline to rework a new draft that needs it, not just
    point at what's wrong.

    Self-checked the same way as everything else in this file: the
    rewritten draft is re-scored against the same profile before being
    returned, so the response always shows whether it genuinely landed
    closer to the user's voice, not just that a rewrite happened.
    """
    profile = profile_store.get_profile(request.email)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found for this email yet. Build one first with /profile/build.")

    restoration_metrics = profile.get("restoration_metrics")
    if restoration_metrics is None:
        raise HTTPException(status_code=422, detail="Profile exists but has no restoration baseline yet - build or add to your profile again to populate it.")

    original_score = _quick_match_score(request.draft_text, profile["dimensions"])

    result = await recalibrate_draft(request.draft_text, restoration_metrics)

    if result["rewritten"] is None:
        return RecalibrateResponse(
            original_match_score=original_score,
            rewritten_text=None,
            rewritten_match_score=None,
            status=result["status"],
        )

    rewritten_score = _quick_match_score(result["rewritten"], profile["dimensions"])

    return RecalibrateResponse(
        original_match_score=original_score,
        rewritten_text=result["rewritten"],
        rewritten_match_score=rewritten_score,
        status=result["status"],
    )
