"""
Tests for the two missing over-direction correction-prompt branches
found via offline analysis of real render_history data (7 Sept 2026
session): first_person_ratio and directive_ratio both had a real
under-direction branch in build_correction_prompt() but silently did
nothing when the render overshot instead of undershot - unlike
hedge_density and sentence_length_sd, which both handle either
direction. first_person_ratio at least had a deterministic fixer for
the over case (_fix_first_person_over_ratio); directive_ratio had no
correction at all in either direction for an overshoot until this fix.

Root cause found via zero-cost analysis: pulled real (baseline,
output_text) pairs from the live render_history/voice_profiles Supabase
tables and ran score_render_delta() offline - no API calls. 71 real
renders across 7 devices; first_person_ratio MISSED 64.6% of the time
in the persisted final output despite three layers of correction
existing for the under-direction. Of those misses, a real over-shoot
case (baseline 0.523 -> output 1.0) had nothing available to correct
it in the LLM-correction fallback. directive_ratio showed a smaller
but real over-direction miss rate too (2 of 48).
"""
import prompts as pr


def _delta_for(key, baseline, output, verdict="MISSED"):
    return {key: {"baseline": baseline, "output": output, "verdict": verdict,
                  "delta": round(output - baseline, 3), "pct_diff": 1.0}}


def test_first_person_over_direction_now_produces_a_correction():
    delta = _delta_for("first_person_ratio", 0.523, 1.0)
    result = pr.build_correction_prompt(delta, input_has_opinion_content=True)
    assert result is not None
    assert "too high" in result
    assert "pull back" in result or "Never touch first-person language" in result


def test_first_person_over_direction_never_fabricates_from_nothing():
    # Over-direction correction should still fire regardless of
    # input_has_opinion_content - that gate exists to stop FABRICATING
    # ownership that isn't there (under-direction risk), not to stop
    # REMOVING ownership the rewrite itself over-added (a different
    # risk entirely). Confirms the new branch doesn't accidentally
    # inherit the wrong gate.
    delta = _delta_for("first_person_ratio", 0.0, 0.8)
    result = pr.build_correction_prompt(delta, input_has_opinion_content=False)
    assert result is not None
    assert "too high" in result


def test_first_person_under_direction_unaffected_by_new_branch():
    delta = _delta_for("first_person_ratio", 0.53, 0.0)
    result = pr.build_correction_prompt(delta, input_has_opinion_content=True)
    assert result is not None
    assert "too low" in result
    assert "too high" not in result


def test_directive_over_direction_now_produces_a_correction():
    delta = _delta_for("directive_ratio", 0.10, 0.9)
    result = pr.build_correction_prompt(delta, input_has_directive_content=True)
    assert result is not None
    assert "too strong" in result


def test_directive_under_direction_unaffected_by_new_branch():
    delta = _delta_for("directive_ratio", 0.10, 0.0)
    result = pr.build_correction_prompt(delta, input_has_directive_content=True)
    assert result is not None
    assert "missing" in result
    assert "too strong" not in result


def test_directive_over_direction_below_b_val_floor_still_skipped():
    # b_val >= 0.06 gate is checked before either branch - a baseline
    # under that floor should still produce nothing, same as before
    # this change, for either direction.
    delta = _delta_for("directive_ratio", 0.03, 0.9)
    result = pr.build_correction_prompt(delta)
    assert result is None
