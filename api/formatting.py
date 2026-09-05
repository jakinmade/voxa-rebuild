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

Shared between check_draft.py and (when built) fix.py, since both
responses describe the same six dimensions — written here once rather
than duplicated a second time when fix.py needs the same table.
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
