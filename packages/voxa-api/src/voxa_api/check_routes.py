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

from fastapi import APIRouter
from pydantic import BaseModel, Field

from voxa_rendering.fingerprint import (
    _extract_sentences,
    score_conclusion_position,
    score_hedging_signature,
    score_reader_assumption,
    score_compression_philosophy,
    score_energy_signature,
)

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
