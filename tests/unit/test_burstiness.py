"""
Tests for burstiness (12 Sept 2026, PR 4 of 5 — register-aware voice
fidelity build). See voice_engine.py's comments on _derive_baseline_
metrics and compute_baseline_metrics for the research basis: human
writing varies sentence length a lot (stdev/mean typically 0.6-1.2);
AI text is unusually uniform (typically 0.2-0.4).

Covers: the field is present and consistent between compute_baseline_
metrics and _derive_baseline_metrics (the two independently-maintained
implementations, per the existing duplication this codebase already
has for conclusion_opener_ratio/scaffolding_density), the _merge_baseline
legacy fallback doesn't crash on old baselines missing it, and
_build_restoration_targets surfaces it as a concrete instruction when
present without changing behaviour when absent.
"""
import prompts as pr
import voice_engine as ve


def test_burstiness_present_in_compute_baseline_metrics():
    text = (
        "Short one. Here is a considerably longer sentence with much more "
        "going on inside it. Brief again."
    )
    metrics = ve.compute_baseline_metrics(text)
    assert "burstiness" in metrics
    assert "mean_sentence_length" in metrics
    assert metrics["burstiness"] > 0


def test_burstiness_is_sd_over_mean():
    text = "One two three. One two three four five six seven eight nine ten."
    metrics = ve.compute_baseline_metrics(text)
    expected = round(metrics["sentence_length_sd"] / metrics["mean_sentence_length"], 3)
    assert metrics["burstiness"] == expected


def test_uniform_sentence_lengths_give_low_burstiness():
    # Every sentence exactly 5 words -> SD is 0 -> burstiness 0,
    # the AI-typical end of the research-cited range.
    text = "One two three four five. Six seven eight nine ten. A b c d e."
    metrics = ve.compute_baseline_metrics(text)
    assert metrics["burstiness"] == 0.0


def test_varied_sentence_lengths_give_higher_burstiness():
    varied = (
        "Short. This one is quite a lot longer with several more words in it. "
        "Brief again here. And here is another considerably longer sentence "
        "with plenty of additional words packed into it."
    )
    uniform = "One two three four. Five six seven eight. Nine ten eleven twelve."
    varied_metrics = ve.compute_baseline_metrics(varied)
    uniform_metrics = ve.compute_baseline_metrics(uniform)
    assert varied_metrics["burstiness"] > uniform_metrics["burstiness"]


def test_derive_baseline_metrics_matches_compute_baseline_metrics():
    # The two independently-maintained implementations (see docstring)
    # must agree on the same underlying sufficient statistics.
    text = "Short one. A much longer sentence follows with extra words here."
    fresh = ve.compute_baseline_metrics(text)
    stats = {k: fresh[k] for k in ve._SUFFICIENT_STAT_KEYS}
    stats["word_count"] = fresh["word_count"]
    derived = ve._derive_baseline_metrics(stats)
    assert derived["burstiness"] == fresh["burstiness"]
    assert derived["mean_sentence_length"] == fresh["mean_sentence_length"]


def test_merge_baseline_legacy_fallback_does_not_crash_missing_burstiness():
    legacy_existing = {
        "hedge_density": 2.5, "sentence_length_sd": 6.0,
        "first_person_ratio": 0.8, "directive_ratio": 0.1, "word_count": 100,
    }
    new_sample = ve.compute_baseline_metrics(
        "I think this works well. We should move forward with it now."
    )
    merged = ve._merge_baseline(legacy_existing, new_sample)
    assert "burstiness" in merged
    assert "mean_sentence_length" in merged


def test_restoration_targets_includes_burstiness_line_when_present():
    baseline = ve.compute_baseline_metrics(
        "Short one. A much longer sentence follows with quite a few extra words in it."
    )
    result = pr._build_restoration_targets(baseline)
    assert "Burstiness" in result


def test_restoration_targets_omits_burstiness_line_when_absent():
    # Legacy baseline dict, exactly the shape a pre-12-Sept profile has —
    # must reproduce prior behaviour exactly, no KeyError, no burstiness line.
    legacy_baseline = {
        "hedge_density": 2.5, "sentence_length_sd": 6.0,
        "first_person_ratio": 0.8, "directive_ratio": 0.1, "word_count": 100,
    }
    result = pr._build_restoration_targets(legacy_baseline)
    assert "Burstiness" not in result
    assert "Sentence rhythm" in result  # the pre-existing line still there


def test_restoration_targets_high_burstiness_says_vary():
    baseline = {
        "hedge_density": 2.5, "sentence_length_sd": 6.0,
        "first_person_ratio": 0.8, "directive_ratio": 0.1, "word_count": 100,
        "burstiness": 0.9,
    }
    result = pr._build_restoration_targets(baseline)
    assert "vary a lot" in result


def test_restoration_targets_low_burstiness_says_steady():
    baseline = {
        "hedge_density": 2.5, "sentence_length_sd": 6.0,
        "first_person_ratio": 0.8, "directive_ratio": 0.1, "word_count": 100,
        "burstiness": 0.25,
    }
    result = pr._build_restoration_targets(baseline)
    assert "relatively consistent" in result
