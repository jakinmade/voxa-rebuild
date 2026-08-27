"""
Regression tests for _merge_baseline's sufficient-statistics rewrite
(27 Aug 2026 hardening pass, independent codebase review's P0 finding).

The bug: the previous implementation combined multiple calibration
samples by taking a word-count-weighted average of each sample's
already-computed hedge_density/sentence_length_sd/first_person_ratio/
directive_ratio. That's valid for hedge_density (a genuine per-word
rate) but mathematically wrong for the other three:

- sentence_length_sd: averaging two SDs ignores whatever variance
  exists BETWEEN the samples' own means, not just within them. Two
  samples that are each internally perfectly uniform (SD=0) but have
  different mean sentence lengths produce a combined baseline the old
  code called "perfectly uniform" (sd=0.0) when the true pooled SD was
  18.5 — confirmed independently before this fix, not a hypothetical.

- first_person_ratio / directive_ratio: these are SENTENCE-level
  ratios (fraction of sentences with a marker), but the old merge
  weighted them by WORD count — a 500-word sample got five times the
  influence of a 100-word sample even with the identical number of
  sentences.

The fix: store sufficient statistics (sentence count, sentence-length
sum and sum-of-squares, hedge count, first-person/directive sentence
counts) alongside the four reported metrics, and merge by SUMMING
those raw totals (always mathematically valid, regardless of how the
data is split across samples) before re-deriving the four metrics from
the combined totals — not by averaging the four metrics themselves.
"""
import math

import voice_engine as ve


def test_single_sample_baseline_is_byte_identical_to_pre_rewrite():
    """existing=None must be completely unaffected by this rewrite —
    pinned against the exact values the pre-rewrite code produced for
    this input, confirmed by running the old code directly before this
    fix was written."""
    sample = (
        "I think we should move fast on this. I want the team to focus on "
        "the core problem first, and then we can look at the edges."
    )
    metrics = ve.compute_baseline_metrics(sample)
    assert metrics["hedge_density"] == 0.0
    assert metrics["sentence_length_sd"] == 5.5
    assert metrics["first_person_ratio"] == 1.0
    assert metrics["directive_ratio"] == 0.0
    assert metrics["word_count"] == 27


def test_worst_case_sd_pooling_matches_true_pooled_variance_not_zero():
    """The exact worst-case constructed during development: two
    samples, each internally perfectly uniform (sd=0 individually),
    with very different sentence-length means. The old merge concluded
    the combined baseline was ALSO perfectly uniform (sd=0.0) — this
    confirms the fix produces the true pooled SD instead."""
    short_sentences = " ".join(["Yes ok fine."] * 10)
    long_sentences = " ".join([" ".join(["word"] * 40) + "."] * 10)

    metrics_a = ve.compute_baseline_metrics(short_sentences)
    metrics_b = ve.compute_baseline_metrics(long_sentences)
    assert metrics_a["sentence_length_sd"] == 0.0
    assert metrics_b["sentence_length_sd"] == 0.0

    merged = ve._merge_baseline(metrics_a, metrics_b)

    # Independently computed true pooled SD over the raw combined
    # sentence lengths, not reusing _derive_baseline_metrics — an
    # honest ground truth, not the same formula checking itself.
    all_lengths = [3] * 10 + [40] * 10
    n = len(all_lengths)
    mean = sum(all_lengths) / n
    true_sd = round(math.sqrt(sum((l - mean) ** 2 for l in all_lengths) / n), 2)

    assert merged["sentence_length_sd"] == true_sd
    assert merged["sentence_length_sd"] > 15.0, (
        "Expected a large pooled SD reflecting the real between-sample "
        "variance, not the old code's falsely-flat 0.0"
    )


def test_ratio_merge_weights_by_sentence_count_not_word_count():
    """A short, dense sample and a long, sparse sample with the SAME
    number of sentences must contribute EQUALLY to the merged
    first_person_ratio/directive_ratio — under the old word-count
    weighting, the longer sample would dominate even though both
    contribute the same number of data points (sentences) to the
    ratio."""
    # 4 sentences, all first-person, short.
    sample_a = "I ran. I jumped. I laughed. I won."
    # 4 sentences, none first-person, much longer (padding word count
    # way up without changing sentence count).
    padding = " ".join(["word"] * 30)
    sample_b = (
        f"The team met today with {padding} present. "
        f"Reports were reviewed at length with {padding} noted. "
        f"Decisions were made after {padding} of discussion. "
        f"Actions were assigned to everyone {padding} involved."
    )

    metrics_a = ve.compute_baseline_metrics(sample_a)
    metrics_b = ve.compute_baseline_metrics(sample_b)
    assert metrics_a["first_person_ratio"] == 1.0
    assert metrics_b["first_person_ratio"] == 0.0
    assert metrics_a["sentence_count"] == metrics_b["sentence_count"] == 4
    # Sanity: sample_b really is much longer in words, the exact
    # condition that made the old word-count weighting misbehave.
    assert metrics_b["word_count"] > metrics_a["word_count"] * 5

    merged = ve._merge_baseline(metrics_a, metrics_b)
    # Equal sentence counts -> equal weight -> simple average of the
    # two ratios, regardless of the large word-count imbalance.
    assert merged["first_person_ratio"] == 0.5


def test_legacy_baseline_missing_sufficient_stats_falls_back_gracefully():
    """A baseline persisted before this fix has only the five original
    keys. Must not crash, and must carry sufficient stats forward so
    every merge AFTER this one is fully correct."""
    legacy_existing = {
        "hedge_density": 2.5, "sentence_length_sd": 6.0,
        "first_person_ratio": 0.8, "directive_ratio": 0.1, "word_count": 100,
    }
    new_sample = ve.compute_baseline_metrics(
        "I think this works well. We should move forward with it now."
    )
    merged = ve._merge_baseline(legacy_existing, new_sample)
    assert merged["word_count"] == 100 + new_sample["word_count"]
    assert all(k in merged for k in ve._SUFFICIENT_STAT_KEYS)

    # The merge immediately after this one must use the correct
    # sum-based path, not fall back again.
    next_sample = ve.compute_baseline_metrics(
        "This is a much longer sentence that adds real variance here. Short one."
    )
    merged_again = ve._merge_baseline(merged, next_sample)
    expected_sentence_count = merged["sentence_count"] + next_sample["sentence_count"]
    assert merged_again["sentence_count"] == expected_sentence_count


def test_three_way_merge_matches_computing_all_sentences_at_once():
    """Sequential merging (A then B then C) must produce the same
    sufficient-statistic totals as computing them directly over all
    three samples' text combined in one pass — confirms summing is
    associative/order-independent, as it must be for raw counts."""
    a = "I like this a lot. It works well for me."
    b = "The team should review this. Consider the timeline carefully."
    c = "We are moving fast. I am confident in the plan. Let's go."

    m_a, m_b, m_c = (ve.compute_baseline_metrics(t) for t in (a, b, c))
    merged_sequential = ve._merge_baseline(ve._merge_baseline(m_a, m_b), m_c)

    combined_directly = ve.compute_baseline_metrics(f"{a} {b} {c}")

    # Word count and sentence count must match exactly (both are exact
    # sums either way). The four derived metrics may differ by a
    # rounding hair since _extract_sentences' abbreviation/paragraph
    # handling can tokenise "a b c" concatenated slightly differently
    # than three separate calls - checked to 1 decimal place, not
    # exact equality, for that reason.
    assert merged_sequential["word_count"] == combined_directly["word_count"]
    assert merged_sequential["sentence_count"] == combined_directly["sentence_count"]
    assert round(merged_sequential["sentence_length_sd"], 1) == round(
        combined_directly["sentence_length_sd"], 1
    )
