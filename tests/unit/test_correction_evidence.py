"""
Tests for score_correction_evidence (30 Aug 2026, voice-review item #1).

_add_writing_sample_to_fingerprint already folds an edited Learn-from-
edit sample into the blended baseline — that says something changed.
This function says WHICH dimension changed, in WHICH direction, by
HOW much: the specific predicted-vs-corrected delta, not just a new
data point averaged in. Reuses DELTA_BAND_MIN_ABS_DIFF (scoring_rules.py)
as the meaningful-change floor — the same threshold score_render_delta
already uses to decide real drift from noise near a zero denominator.
"""
import voice_engine as ve


# ------------------------------------------------------------------
# Basic shape and edge cases
# ------------------------------------------------------------------

def test_empty_predicted_returns_empty_dict():
    assert ve.score_correction_evidence("", "Something the person wrote.") == {}


def test_empty_corrected_returns_empty_dict():
    assert ve.score_correction_evidence("Something the model wrote.", "") == {}


def test_both_empty_returns_empty_dict():
    assert ve.score_correction_evidence("", "") == {}


def test_identical_text_produces_no_evidence():
    """No correction happened — nothing to learn from."""
    text = "This is a longer sample sentence used to test identical input behavior here."
    assert ve.score_correction_evidence(text, text) == {}


def test_trivial_edit_below_the_floor_produces_no_evidence():
    """A cosmetic word swap that doesn't meaningfully move any
    dimension must not be treated as a signal — this is the entire
    point of reusing DELTA_BAND_MIN_ABS_DIFF rather than flagging any
    nonzero difference."""
    result = ve.score_correction_evidence(
        "I think this is good.", "I think this is great."
    )
    assert result == {}


# ------------------------------------------------------------------
# Real, meaningful corrections — the actual feature
# ------------------------------------------------------------------

def test_heavy_hedge_removal_is_captured_as_decreased():
    """The exact worked example from the original review document:
    predicted 'This could potentially suggest...', corrected to
    'This suggests...' -> learn: hedging decreased."""
    predicted = (
        "This could potentially suggest that the approach might work, "
        "though it may need more testing perhaps."
    )
    corrected = "This suggests the approach works. It needs more testing."
    result = ve.score_correction_evidence(predicted, corrected)
    assert "hedge_density" in result
    assert result["hedge_density"]["direction"] == "decreased"
    assert result["hedge_density"]["delta"] < 0


def test_hedge_addition_is_captured_as_increased():
    predicted = "This works. It is finished. It is correct."
    corrected = (
        "This might work, I think, though I could be wrong. It seems "
        "mostly finished, perhaps correct, though it's hard to say for sure."
    )
    result = ve.score_correction_evidence(predicted, corrected)
    assert "hedge_density" in result
    assert result["hedge_density"]["direction"] == "increased"
    assert result["hedge_density"]["delta"] > 0


def test_only_dimensions_that_cleared_the_floor_are_present():
    """A correction that meaningfully moves one dimension but leaves
    others essentially untouched must only report the one that
    actually cleared the floor — not pad the result with near-zero
    deltas on every dimension."""
    predicted = (
        "This could potentially suggest that the approach might work, "
        "though it may need more testing perhaps."
    )
    corrected = "This suggests the approach works. It needs more testing."
    result = ve.score_correction_evidence(predicted, corrected)
    # Only the dimension that genuinely moved should be present.
    assert set(result.keys()).issubset(set(ve._STABILITY_DIMENSIONS))
    for dim, entry in result.items():
        assert abs(entry["delta"]) >= ve.DELTA_BAND_MIN_ABS_DIFF.get(dim, 0.0)


def test_evidence_includes_both_predicted_and_corrected_values():
    predicted = "This could potentially suggest that the approach might work."
    corrected = "This suggests the approach works."
    result = ve.score_correction_evidence(predicted, corrected)
    assert "hedge_density" in result
    entry = result["hedge_density"]
    assert "predicted" in entry and "corrected" in entry
    assert entry["corrected"] < entry["predicted"]


def test_deterministic():
    predicted = "This could potentially suggest that the approach might work."
    corrected = "This suggests the approach works."
    r1 = ve.score_correction_evidence(predicted, corrected)
    r2 = ve.score_correction_evidence(predicted, corrected)
    assert r1 == r2
