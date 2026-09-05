"""
Scaffolding-density reintroduction guard (5 Sept 2026, live finding).

_fix_scaffolding_density correctly removes scaffolding phrases when it
first fires, but a real live test showed the general dimension-
correction pass that runs afterward (to fix an unrelated dimension,
e.g. directive_ratio) can reintroduce scaffolding phrases as a side
effect of its free rewrite. Neither new_hedges nor sentence_growth in
_check_uncorrected_insertions catch this class of regression — a
reintroduced "Basically," or "Background:" is neither a hedge nor
added sentence count.

Fixed by re-checking scaffolding_density's delta verdict after the
correction pass recomputes it, and re-applying the same fixer if it
regressed back to MISSED — mirrors the existing new_hedges catch
(_fix_hedge_density re-run on correction_insertion_check's new_hedges)
that already existed for the same reintroduction risk on a different
dimension.

Reads source as text (same style as test_llm_boundary_contract.py) —
the property under test is "is this guard still written in the code
near the correction pass", not "does it produce a particular output"
(deterministic_fixers.py's own test suite covers the fixer's actual
behaviour).

UPDATED 5 Sept 2026: this guard moved from app.py to render_pipeline.py
during the _run_render extraction — same guard, new location.
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
RENDER_PIPELINE_PY = (_REPO_ROOT / "render_pipeline.py").read_text()
HARNESS_PY = (_REPO_ROOT / "dev_tools" / "harness.py").read_text()


def _lines_around(source: str, anchor: str, before: int = 5, after: int = 40) -> str:
    lines = source.splitlines()
    idx = next((i for i, l in enumerate(lines) if anchor in l), None)
    assert idx is not None, f"Anchor not found — may have moved or been removed: {anchor!r}"
    start = max(0, idx - before)
    end = min(len(lines), idx + after)
    return "\n".join(lines[start:end])


def test_render_pipeline_rechecks_scaffolding_density_after_correction_pass():
    window = _lines_around(RENDER_PIPELINE_PY, "insertion_check = full_check", before=0, after=25)
    assert 'delta.get("scaffolding_density", {}).get("verdict") == "MISSED"' in window, (
        "render_pipeline.py's correction pass no longer re-checks "
        "scaffolding_density after recomputing delta — this is the guard "
        "against the correction pass reintroducing scaffolding phrases it "
        "(or an earlier fixer pass) had already removed."
    )
    assert "_fix_scaffolding_density(clean, d[\"baseline\"], d[\"output\"])" in window


def test_harness_py_rechecks_scaffolding_density_after_correction_pass():
    window = _lines_around(HARNESS_PY, "correction_applied = True", before=0, after=20)
    assert 'delta.get("scaffolding_density", {}).get("verdict") == "MISSED"' in window, (
        "harness.py's correction pass no longer re-checks "
        "scaffolding_density after recomputing delta — parity with "
        "app.py's equivalent guard has been lost."
    )
