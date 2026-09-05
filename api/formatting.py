"""
api/formatting.py — human-readable dimension labels and explanations
for API responses (Full Spec Section 3.5.1's dimension_scores /
dimension_explanations fields).

Duplicates app.py's _VOICE_MATCH_LABELS convention deliberately, for
the same reason stated there: voice_engine.py's dimension names are a
measurement-library contract, not a display-string one, and a small
duplicated label dict for six stable, rarely-changing dimension names
is a fair trade against reaching into that library's internals for
display strings. Extended here to all six dimensions score_render_delta
scores today (Section 4 Sept added conclusion_opener_ratio and
scaffolding_density as enforced dimensions) — app.py's own table still
only displays the original four, so this is genuinely new labelling,
not a divergence from an existing one.

Shared between check_draft.py and fix.py, since both responses
describe the same six dimensions — written here once rather than
duplicated a second time now that fix.py needs the same table.

Also holds two fix.py-only adapters (content_lock_result,
seal_result_from_voice_report) that translate render_pipeline's
RenderResult.voice_report into the shapes fix.py's response and
seal.py's `result` parameter respectively expect — kept in this file
rather than a new module since they're pure formatting over already-
computed data, the same category as everything else here.
"""
from __future__ import annotations

DIMENSION_LABELS = {
    "hedge_density": "Hedging",
    "sentence_length_sd": "Sentence rhythm",
    "first_person_ratio": "Ownership (first person)",
    "directive_ratio": "Directness",
    "conclusion_opener_ratio": "Conclusion placement",
    "scaffolding_density": "Scaffolding",
}

# Kept deliberately simple for V1: one static explanation per
# dimension, shown only when that dimension's verdict is MISSED (the
# same threshold the correction pass itself uses to decide something
# needs fixing — CLOSE is not flagged here either). Not direction-aware
# (more vs. less hedging than baseline) — that would need careful
# testing to avoid getting the direction backwards, and the plain
# static version is still an honest, correct explanation of WHAT
# differs, just not which way. A reasonable enrichment for a later
# session, not a V1 blocker.
_MISSED_EXPLANATIONS = {
    "hedge_density": "Hedging language differs from your usual baseline.",
    "sentence_length_sd": "Sentence-length rhythm differs from your usual pattern.",
    "first_person_ratio": "How much first-person ownership language appears differs from your baseline.",
    "directive_ratio": "How direct/imperative the phrasing is differs from your baseline.",
    "conclusion_opener_ratio": "Where the conclusion lands differs from your usual structure.",
    "scaffolding_density": "The amount of structural scaffolding (transitions, signposting) differs from your baseline.",
}


def dimension_scores(delta: dict) -> dict:
    """Pure formatting over score_render_delta's output (Section 4.4:
    'this mapping function is the only new logic, and it is pure
    formatting, not scoring'). Returns every dimension present in
    delta, labelled, in the shape Section 3.5.1 documents."""
    return {
        dim: {
            "label": DIMENSION_LABELS.get(dim, dim),
            "baseline": d["baseline"],
            "output": d["output"],
            "verdict": d["verdict"],
        }
        for dim, d in delta.items()
    }


def dimension_explanations(delta: dict) -> dict:
    """Short plain-language reason per flagged (MISSED) dimension only
    — Section 3.5.1: 'short plain-language reason per flagged
    dimension, for the borderline-result panel'. Empty dict when
    nothing is flagged."""
    return {
        dim: _MISSED_EXPLANATIONS.get(dim, f"{DIMENSION_LABELS.get(dim, dim)} differs from your baseline.")
        for dim, d in delta.items()
        if d["verdict"] == "MISSED"
    }


def classify_result(verdict: str, ai_tells_clean: bool) -> str:
    """Three-value good | borderline | failed classification (Section
    3.5.1/5.5), from the engine's native two-value PASS | REVIEW
    verdict plus the separate ai_tells_clean signal. Moved here from
    check_draft.py (was a private, route-local function) once fix.py
    needed the identical mapping for its own telemetry `result` field
    — one classifier shared by both routes, not two copies that could
    quietly diverge. "good" only when both signals are clean; a PASS
    verdict with a flagged AI-tell is "borderline", not silently
    rolled into "good"."""
    if verdict == "PASS" and ai_tells_clean:
        return "good"
    if verdict == "PASS" or ai_tells_clean:
        return "borderline"
    return "failed"


def content_lock_result(
    report: dict, insertion_check: dict | None, content_integrity_hard_fail: bool
) -> dict:
    """Plain-text counterpart to app.py's _build_content_lock_banner_html
    (Section 11.2's content_lock_result, POST /api/fix only — check_draft
    has none, see that route's own docstring). Reads the same four
    signals that banner does (dropped_entities, attribution_swaps,
    sentence_growth, new_hedges) for the human-readable `reasons` list,
    exactly as that function does, minus the HTML — the panel is a
    Chrome extension surface, not Streamlit markup.

    `pass` deliberately follows content_integrity_hard_fail directly
    (same source of truth render_history.write_render_history's
    content_lock_pass column already uses in app.py — not "reasons is
    non-empty"), since new_hedges is included in `reasons` for
    visibility but is NOT one of has_content_integrity_hard_fail's own
    checks — a render can show a hedges-added reason and still pass.
    Keeping `pass` tied to the same boolean everywhere this product
    already reports it (row history there, the sealed receipt here)
    means one render can never be "Content Lock: passed" in one place
    and "failed" in another.
    """
    dropped = report.get("dropped_entities", [])
    swaps = report.get("attribution_swaps", [])
    sentence_growth = (insertion_check or {}).get("sentence_growth", 0)
    new_hedges = (insertion_check or {}).get("new_hedges", [])

    reasons = []
    if dropped:
        reasons.append(f"Facts dropped: {', '.join(dropped)}")
    if swaps:
        reasons.append("Attribution may have changed. Check before sending.")
    if sentence_growth:
        noun = "sentence" if sentence_growth == 1 else "sentences"
        reasons.append(f"Added {sentence_growth} new {noun} not in the original")
    if new_hedges:
        reasons.append(f"New hedging added: {', '.join(new_hedges)}")

    return {"pass": not content_integrity_hard_fail, "reasons": reasons}


def seal_result_from_voice_report(report: dict, delta: dict) -> dict:
    """Adapts render_pipeline.RenderResult.voice_report (plus its
    sibling .delta field — the same object build_voice_report derived
    biggest_changes from, not duplicated inside voice_report itself)
    into the shape api/evidence/seal.py's `result` parameter expects.

    match_pct and dimension_scores are included specifically so the
    seal can hash the actual displayed match percentage and per-
    dimension scores (independent architecture review, finding #4:
    the seal previously covered only the verdict/tier/evidence
    SUMMARY, not the number a user actually sees — see seal.py's own
    comment on why that's now fixed). dimension_scores reuses this
    module's own dimension_scores() so the sealed shape is identical
    to what score_draft_check's native result already carries for
    check_draft.py's call into the same seal() function — one
    canonical per-dimension shape, not two that could quietly drift
    apart.

    verdict here uses voice_match_badge, the exact same "PASS iff the
    badge is green" rule score_draft_check's own docstring documents
    — kept as one definition of what "PASS" means for a voice-match
    verdict, not a second, fix-specific one.
    """
    burrows = {
        "tier": report.get("function_word_delta_tier"),
        "delta": report.get("function_word_delta"),
        "biggest_divergences": report.get("function_word_biggest_divergences", []),
    }
    return {
        "verdict": "PASS" if report.get("voice_match_badge") == "badge-green" else "REVIEW",
        "tier": report.get("voice_match_tier"),
        "evidence": report.get("voice_match_evidence"),
        "ai_tells_clean": report.get("ai_tell_clean"),
        "ai_tells_flagged": report.get("ai_tell_flags"),
        "burrows_delta": burrows,
        "match_pct": report.get("voice_match"),
        "dimension_scores": dimension_scores(delta),
    }
