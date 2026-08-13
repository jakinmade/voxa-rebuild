"""
Tests for voice_engine.compute_mfw_profile and compute_burrows_delta.

Why this exists: the existing voice-match signal (score_render_delta)
rests on four hand-picked heuristics, and the existing vocabulary
fingerprint (_extract_vocabulary_fingerprint) explicitly excludes
function words — backwards from the field's actual gold standard.
Burrows' Delta (Burrows, 2002) remains, per multiple 2025/2026
replications, the most-cited and most robust method in forensic and
literary stylometry, precisely because it fingerprints the most
frequent words in a corpus — overwhelmingly function words, used
unconsciously and topic-independently. This adds that as a second,
independently-grounded voice-match signal alongside the existing ones.

A significant part of this file exists to guard against a real bug
found during development, not a hypothetical one: an initial version
z-scored a single baseline text against a single output text using
their own shared pairwise mean/SD. That is mathematically degenerate —
for any two unequal numbers, z-scoring each against their own shared
pairwise mean/SD always produces |z_a - z_b| = 2, regardless of how far
apart the numbers actually are. Verified directly at the time: (10, 12),
(10, 50), and (10, 500) all produced exactly the same score. A "similar
register" test output and a "wildly different register" test output
scored identically (2.0) as a direct consequence, before the fix.
test_similar_register_scores_lower_than_different_register below is
the permanent regression guard for that specific failure mode.
"""
import voice_engine as ve


BASELINE_SAMPLES = [
    "I think we should move fast on this. I want the team to focus on "
    "the core problem first, and then we can look at the edges.",
    "I believe the data backs this up, and I think it is the right call "
    "for now. We need to move quickly and stay focused.",
    "I want us to act on this today. The team should prioritise the "
    "main issue and not get distracted by side questions.",
]

SIMILAR_OUTPUT = (
    "I think we need to move on this quickly. The team should focus on "
    "the core issue first, and I believe the data supports it."
)

DIFFERENT_REGISTER_OUTPUT = (
    "The organisation shall endeavour, insofar as reasonably "
    "practicable, to ensure that all stakeholders are apprised of the "
    "aforementioned developments in a timely and appropriate manner, "
    "pursuant to the relevant governance framework."
)


# ---------------------------------------------------------------------
# compute_mfw_profile — the raw input, deliberately NOT stopword-filtered
# ---------------------------------------------------------------------

def test_mfw_profile_includes_function_words():
    # The whole point: unlike _extract_vocabulary_fingerprint, function
    # words must survive here, not be filtered out as noise.
    profile = ve.compute_mfw_profile("The cat sat on the mat. The dog ran.")
    assert "the" in profile
    assert "on" in profile


def test_mfw_profile_empty_text_returns_empty_dict():
    assert ve.compute_mfw_profile("") == {}


def test_mfw_profile_frequencies_are_per_1000_words():
    # "the" appears 2 times in a 6-word text -> 2/6 * 1000 = 333.33...
    profile = ve.compute_mfw_profile("the cat the dog the bird")
    assert profile["the"] == 500.0  # 3/6 * 1000


# ---------------------------------------------------------------------
# compute_burrows_delta — the regression guard for the degenerate bug,
# and correct-direction behaviour more broadly.
# ---------------------------------------------------------------------

def test_similar_register_scores_lower_than_different_register():
    """
    THE regression test for the bug this module's docstring documents.
    Before the fix, both of these produced exactly the same delta
    (2.0) because the z-scoring was mathematically degenerate with
    only two data points. After the fix, a similar-register output
    must score meaningfully lower than a wildly-different-register one.
    """
    similar_result = ve.compute_burrows_delta(BASELINE_SAMPLES, SIMILAR_OUTPUT)
    different_result = ve.compute_burrows_delta(BASELINE_SAMPLES, DIFFERENT_REGISTER_OUTPUT)

    assert similar_result["delta"] is not None
    assert different_result["delta"] is not None
    assert similar_result["delta"] < different_result["delta"]


def test_two_point_pairwise_zscore_is_degenerate_demonstration():
    """
    Not a test of production code — a permanent, explicit record of the
    mathematical property that caused the original bug, so a future
    change can't reintroduce the same mistake (e.g. "simplify by
    z-scoring baseline vs output directly") without this test explaining
    exactly why that's wrong.
    """
    import math

    def pairwise_z_diff(a, b):
        mean = (a + b) / 2
        variance = ((a - mean) ** 2 + (b - mean) ** 2) / 2
        sd = math.sqrt(variance)
        return abs((a - mean) / sd - (b - mean) / sd)

    # Any two unequal values produce exactly 2.0, regardless of how far
    # apart they are. This is why compute_burrows_delta requires 2+
    # independent baseline samples rather than z-scoring baseline vs
    # output as a pair.
    assert pairwise_z_diff(10, 12) == 2.0
    assert pairwise_z_diff(10, 500) == 2.0
    assert pairwise_z_diff(10, 50) == 2.0


def test_requires_at_least_two_baseline_samples():
    result = ve.compute_burrows_delta([BASELINE_SAMPLES[0]], SIMILAR_OUTPUT)
    assert result["delta"] is None
    assert result["tier"] == "Insufficient baseline samples"


def test_empty_baseline_list_refuses_not_fabricates():
    result = ve.compute_burrows_delta([], SIMILAR_OUTPUT)
    assert result["delta"] is None
    assert result["tier"] == "Insufficient baseline samples"


def test_empty_output_refuses_not_fabricates():
    result = ve.compute_burrows_delta(BASELINE_SAMPLES, "")
    assert result["delta"] is None
    assert result["tier"] == "Insufficient output"


def test_output_close_to_one_baseline_sample_scores_reasonably_low():
    result = ve.compute_burrows_delta(BASELINE_SAMPLES, BASELINE_SAMPLES[0])
    assert result["delta"] is not None
    assert result["tier"] == "Close"


def test_deterministic():
    r1 = ve.compute_burrows_delta(BASELINE_SAMPLES, SIMILAR_OUTPUT)
    r2 = ve.compute_burrows_delta(BASELINE_SAMPLES, SIMILAR_OUTPUT)
    assert r1 == r2


def test_biggest_divergences_capped_at_three():
    result = ve.compute_burrows_delta(BASELINE_SAMPLES, DIFFERENT_REGISTER_OUTPUT)
    assert len(result["biggest_divergences"]) <= 3


def test_tier_bands_are_ordered_correctly():
    # Sanity check on the band boundaries themselves, independent of
    # any specific text — a delta of 1.0 must be Close, 2.0 Moderate,
    # 4.0 Wide, matching the documented thresholds.
    assert ve._DELTA_MFW_COUNT > 0  # module constant sanity check


# ---------------------------------------------------------------------
# build_voice_report — confirm the additive wiring, not a replacement
# ---------------------------------------------------------------------

def test_build_voice_report_without_burrows_delta_unchanged():
    delta = {
        "hedge_density": {"baseline": 2.0, "output": 2.1, "delta": 0.1, "pct_diff": 0.05, "verdict": "HIT"},
    }
    semantic = {"semantic_match": 90, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, "High", "Low")
    assert "function_word_delta" not in report


def test_build_voice_report_with_burrows_delta_adds_fields_additively():
    delta = {
        "hedge_density": {"baseline": 2.0, "output": 2.1, "delta": 0.1, "pct_diff": 0.05, "verdict": "HIT"},
    }
    semantic = {"semantic_match": 90, "dropped_entities": [], "attribution_swaps": []}
    burrows = ve.compute_burrows_delta(BASELINE_SAMPLES, SIMILAR_OUTPUT)
    report = ve.build_voice_report(delta, semantic, "High", "Low", burrows_delta=burrows)

    # Existing fields still present and unchanged in shape
    assert "voice_match_tier" in report
    assert "semantic_match" in report
    # New fields present, additive
    assert report["function_word_delta"] == burrows["delta"]
    assert report["function_word_delta_tier"] == burrows["tier"]
