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

router = APIRouter()

# Each dimension: (scorer function, data key that carries the signal, human label)
_DIMENSIONS = [
    ("conclusion_position", score_conclusion_position, "point_first", "Conclusion position"),
    ("hedging_signature", score_hedging_signature, "owns_statements", "Hedging signature"),
    ("reader_assumption", score_reader_assumption, "assumes_peer", "Reader assumption"),
    ("compression_philosophy", score_compression_philosophy, "structural", "Compression philosophy"),
    ("energy_signature", score_energy_signature, "verb_dominant", "Energy signature"),
]


class CheckRequest(BaseModel):
    reference_text: str = Field(..., min_length=20, description="Sample(s) of the user's own established writing")
    draft_text: str = Field(..., min_length=10, description="The new draft to check against it")


class DimensionResult(BaseModel):
    id: str
    label: str
    matched: bool
    evidence: str | None = None


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


class CheckProfileRequest(BaseModel):
    email: str = Field(..., min_length=3)
    draft_text: str = Field(..., min_length=10)


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

        results.append(DimensionResult(id=dim_id, label=label, matched=matched, evidence=evidence))
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
    all_samples = prior_samples + request.samples

    baseline = _build_baseline(all_samples)
    profile = {"raw_samples": all_samples, "dimensions": baseline}
    profile_store.save_profile(request.email, profile)

    return ProfileResponse(email=request.email, sample_count=len(all_samples), dimensions=baseline)


@router.get("/profile/{email}", response_model=ProfileResponse)
async def get_profile(email: str) -> ProfileResponse:
    profile = profile_store.get_profile(email)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found for this email yet. Build one first with /profile/build.")
    return ProfileResponse(email=email, sample_count=len(profile["raw_samples"]), dimensions=profile["dimensions"])


@router.post("/check-profile", response_model=CheckResponse)
async def check_against_profile(request: CheckProfileRequest) -> CheckResponse:
    profile = profile_store.get_profile(request.email)
    if profile is None:
        raise HTTPException(status_code=404, detail="No profile found for this email yet. Build one first with /profile/build.")

    draft_scores = _score_text(request.draft_text)
    results: list[DimensionResult] = []
    matched_count = 0

    for dim_id, _fn, data_key, label in _DIMENSIONS:
        dim_profile = profile["dimensions"].get(dim_id)
        if dim_profile is None:
            continue
        draft_value = draft_scores[dim_id]["data"].get(data_key)
        matched = (draft_value == dim_profile["value"])

        evidence = None
        if not matched:
            quotes = draft_scores[dim_id]["evidence"]
            evidence = quotes[0] if quotes else None

        results.append(DimensionResult(id=dim_id, label=label, matched=matched, evidence=evidence))
        if matched:
            matched_count += 1

    match_score = round((matched_count / len(results)) * 100) if results else 0
    voiceprint = "".join("o" if r.matched else "x" for r in results)

    return CheckResponse(match_score=match_score, dimensions=results, voiceprint=voiceprint)
