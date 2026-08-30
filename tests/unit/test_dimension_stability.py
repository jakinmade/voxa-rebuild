"""
Tests for voice_engine.compute_dimension_stability and the
stability-aware compute_confidence.

Why this exists: research on stylometry / idiolect (register variation
as the mechanism underlying most authorship-attribution signal) says the
only way to tell "this is genuinely how this person writes" apart from
"this is just what this scenario pulled out of them" is to compare the
same measurement across deliberately different registers. A single
blended average can't make that distinction. These tests confirm the
comparison actually happens, is deterministic, and actually moves the
Confidence badge the user sees.
"""
import math

import voice_engine as ve


def _metrics(hedge_density, sentence_length_sd, first_person_ratio, directive_ratio, word_count=100):
    return {
        "hedge_density": hedge_density,
        "sentence_length_sd": sentence_length_sd,
        "first_person_ratio": first_person_ratio,
        "directive_ratio": directive_ratio,
        "word_count": word_count,
    }


# ---------------------------------------------------------------------------
# compute_dimension_stability — basic shape and edge cases
# ---------------------------------------------------------------------------

def test_no_samples_returns_empty_shape():
    result = ve.compute_dimension_stability([])
    assert result["dimensions"] == {}
    assert result["sample_count"] == 0


def test_single_sample_is_insufficient_data_not_a_guess():
    result = ve.compute_dimension_stability([_metrics(2.0, 5.0, 0.4, 0.1)])
    assert result["sample_count"] == 1
    for dim in ("hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio"):
        assert result["dimensions"][dim] == "insufficient_data"
    assert result["stable_count"] == 0
    assert result["volatile_count"] == 0


def test_identical_samples_are_all_stable():
    samples = [_metrics(2.0, 5.0, 0.4, 0.1) for _ in range(3)]
    result = ve.compute_dimension_stability(samples)
    assert result["stable_count"] == 4
    assert result["volatile_count"] == 0
    assert result["sample_count"] == 3


def test_wildly_different_samples_flag_volatile():
    samples = [
        _metrics(0.5, 2.0, 0.1, 0.05),
        _metrics(8.0, 15.0, 0.9, 0.6),
    ]
    result = ve.compute_dimension_stability(samples)
    assert result["volatile_count"] >= 3


def test_all_zero_dimension_across_samples_is_stable_not_volatile():
    # Zero hedge words in every sample is a consistent absence, not
    # missing data - should not be penalised as volatile.
    samples = [_metrics(0.0, 5.0, 0.4, 0.1), _metrics(0.0, 5.2, 0.42, 0.11)]
    result = ve.compute_dimension_stability(samples)
    assert result["dimensions"]["hedge_density"] == "stable"


def test_deterministic_same_input_same_output():
    samples = [_metrics(2.3, 6.1, 0.35, 0.12), _metrics(3.1, 9.4, 0.28, 0.20)]
    r1 = ve.compute_dimension_stability(samples)
    r2 = ve.compute_dimension_stability(samples)
    assert r1 == r2


def test_three_samples_matches_two_required_starters_plus_screen1():
    # The actual production shape: Screen 1 + two required starters.
    samples = [
        _metrics(2.0, 5.0, 0.4, 0.1),
        _metrics(2.1, 5.3, 0.38, 0.12),
        _metrics(1.9, 4.8, 0.41, 0.09),
    ]
    result = ve.compute_dimension_stability(samples)
    assert result["sample_count"] == 3
    assert result["stable_count"] == 4


# ---------------------------------------------------------------------------
# compute_confidence — stability now gates the High/Medium tiers
# ---------------------------------------------------------------------------

def _high_tier_fitness():
    return {"tier": "gold"}


def _strong_baseline(word_count=900):
    return _metrics(2.0, 6.0, 0.4, 0.15, word_count=word_count)


def test_confidence_without_stability_data_falls_back_to_old_behaviour():
    # No stability arg at all (or sample_count < 2) - matches the old,
    # pre-stability read exactly, so callers that haven't reached
    # Screen 3 yet aren't penalised for data that doesn't exist yet.
    confidence = ve.compute_confidence(_high_tier_fitness(), _strong_baseline(), 5)
    assert confidence == "High"


def test_high_confidence_requires_mostly_stable_dimensions():
    stability = {"stable_count": 4, "volatile_count": 0, "sample_count": 3}
    confidence = ve.compute_confidence(_high_tier_fitness(), _strong_baseline(), 5, stability)
    assert confidence == "High"


def test_high_tier_word_count_downgraded_when_dimensions_are_volatile():
    # Same wordy, gold-tier baseline as the passing case above, but most
    # dimensions swing across registers - should NOT report High, since
    # a wordy register-driven sample is not more trustworthy than the
    # badge would otherwise imply.
    stability = {"stable_count": 1, "volatile_count": 3, "sample_count": 3}
    confidence = ve.compute_confidence(_high_tier_fitness(), _strong_baseline(), 5, stability)
    assert confidence != "High"


def test_medium_confidence_requires_at_least_half_stable():
    baseline = _metrics(2.0, 6.0, 0.4, 0.15, word_count=300)
    stability = {"stable_count": 2, "volatile_count": 2, "sample_count": 3}
    confidence = ve.compute_confidence({"tier": "strong"}, baseline, 2, stability)
    assert confidence == "Medium"


def test_low_confidence_when_majority_volatile_even_with_decent_word_count():
    baseline = _metrics(2.0, 6.0, 0.4, 0.15, word_count=300)
    stability = {"stable_count": 1, "volatile_count": 3, "sample_count": 3}
    confidence = ve.compute_confidence({"tier": "strong"}, baseline, 2, stability)
    assert confidence == "Low"


def test_no_baseline_is_always_low_regardless_of_stability():
    stability = {"stable_count": 4, "volatile_count": 0, "sample_count": 3}
    confidence = ve.compute_confidence(_high_tier_fitness(), None, 5, stability)
    assert confidence == "Low"


# ---------------------------------------------------------------------------
# confidence_caveat — the one UI-facing line, shown only when it's
# actionable. Never names a dimension, never fires when confidence
# is already High, never fires before Screen 3 has run.
# ---------------------------------------------------------------------------

def test_no_stability_data_no_caveat():
    assert ve.confidence_caveat(None) is None


def test_single_sample_no_caveat():
    assert ve.confidence_caveat({"stable_count": 0, "volatile_count": 0, "sample_count": 1}) is None


def test_mostly_stable_no_caveat():
    stability = {"stable_count": 4, "volatile_count": 0, "sample_count": 3}
    assert ve.confidence_caveat(stability) is None


def test_evenly_split_no_caveat_at_the_medium_threshold():
    # 2 stable / 2 volatile = 0.5 ratio, same threshold compute_confidence
    # uses for Medium - not yet the "mostly volatile" case this caveat
    # exists for.
    stability = {"stable_count": 2, "volatile_count": 2, "sample_count": 3}
    assert ve.confidence_caveat(stability) is None


def test_mostly_volatile_gives_a_plain_english_caveat():
    stability = {"stable_count": 1, "volatile_count": 3, "sample_count": 3}
    caveat = ve.confidence_caveat(stability)
    assert caveat is not None
    # No jargon leaks into the user-facing string.
    for banned in ("stable", "volatile", "dimension", "coefficient", "variation", "cv"):
        assert banned not in caveat.lower()


def test_caveat_deterministic():
    stability = {"stable_count": 1, "volatile_count": 3, "sample_count": 3}
    assert ve.confidence_caveat(stability) == ve.confidence_caveat(stability)


# ---------------------------------------------------------------------------
# compute_dimension_confidence (30 Aug 2026) — per-dimension confidence
# instead of one blended badge. See voice review discussion: a profile can
# be genuinely solid on one dimension while thin on another, and the single
# badge can't say so.
# ---------------------------------------------------------------------------

def test_no_baseline_returns_empty_dict():
    assert ve.compute_dimension_confidence(None, None, 0, None) == {}


def test_every_dimension_present_in_result():
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {d: "stable" for d in ve._STABILITY_DIMENSIONS}}
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability)
    assert set(result.keys()) == set(ve._STABILITY_DIMENSIONS)


def test_weak_overall_evidence_caps_every_dimension_at_low_regardless_of_stability():
    """The evidence-quantity floor (word count/tier/observation count,
    same gates compute_confidence uses) must dominate — a dimension
    can't read more confident than the overall evidence supports, even
    if that one dimension happens to look stable."""
    fitness = {"tier": "thin"}
    baseline = {"word_count": 50}
    stability = {"dimensions": {d: "stable" for d in ve._STABILITY_DIMENSIONS}}
    result = ve.compute_dimension_confidence(fitness, baseline, 0, stability)
    assert all(v == "Low" for v in result.values())


def test_strong_overall_evidence_still_differentiates_per_dimension():
    """The headline behaviour this function exists for: with strong
    overall evidence, per-dimension stability must still produce real
    spread, not one blended verdict repeated four times."""
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {
        "hedge_density": "stable",
        "sentence_length_sd": "stable",
        "first_person_ratio": "volatile",
        "directive_ratio": "insufficient_data",
    }}
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability)
    assert result["hedge_density"] == "High"
    assert result["sentence_length_sd"] == "High"
    assert result["first_person_ratio"] == "Medium"
    assert result["directive_ratio"] == "Low"
    # The actual point of this feature: not every dimension the same.
    assert len(set(result.values())) > 1


def test_medium_overall_evidence_volatile_dimension_reads_low_not_medium():
    fitness = {"tier": "thin"}
    baseline = {"word_count": 300}
    stability = {"dimensions": {
        "hedge_density": "stable", "sentence_length_sd": "volatile",
        "first_person_ratio": "volatile", "directive_ratio": "volatile",
    }}
    result = ve.compute_dimension_confidence(fitness, baseline, 2, stability)
    assert result["hedge_density"] == "Medium"
    assert result["sentence_length_sd"] == "Low"


def test_missing_dimension_in_stability_data_defaults_to_insufficient_data():
    """A dimension absent from stability['dimensions'] (e.g. stability
    was computed before this dimension existed, or data is partial)
    must not crash — treated as insufficient_data, same as an explicit
    insufficient_data label would be."""
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {"hedge_density": "stable"}}  # others absent
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability)
    assert result["hedge_density"] == "High"
    assert result["sentence_length_sd"] == "Low"


def test_no_stability_data_at_all_still_returns_all_dimensions():
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    result = ve.compute_dimension_confidence(fitness, baseline, 5, None)
    assert set(result.keys()) == set(ve._STABILITY_DIMENSIONS)
    assert all(v == "Low" for v in result.values())


def test_deterministic():
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {d: "stable" for d in ve._STABILITY_DIMENSIONS}}
    r1 = ve.compute_dimension_confidence(fitness, baseline, 5, stability)
    r2 = ve.compute_dimension_confidence(fitness, baseline, 5, stability)
    assert r1 == r2


# ---------------------------------------------------------------------------
# correction_evidence-driven demotion (30 Aug 2026, voice-review item #1) —
# a dimension repeatedly corrected the same direction reads one tier lower,
# independent of stability/volume, since that's evidence the stored number
# doesn't match the person's real voice on that dimension.
# ---------------------------------------------------------------------------

def _strong_profile():
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {d: "stable" for d in ve._STABILITY_DIMENSIONS}}
    return fitness, baseline, stability


def test_no_correction_evidence_leaves_confidence_unchanged():
    fitness, baseline, stability = _strong_profile()
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability, correction_evidence=None)
    assert result["hedge_density"] == "High"


def test_single_correction_does_not_demote():
    fitness, baseline, stability = _strong_profile()
    evidence = [{"evidence": {"hedge_density": {"direction": "decreased", "delta": -1.5}}}]
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability, correction_evidence=evidence)
    assert result["hedge_density"] == "High"


def test_two_consistent_corrections_demote_one_tier():
    fitness, baseline, stability = _strong_profile()
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -1.5}}},
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -2.0}}},
    ]
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability, correction_evidence=evidence)
    assert result["hedge_density"] == "Medium"
    # Other dimensions must be completely unaffected.
    assert result["sentence_length_sd"] == "High"


def test_conflicting_direction_corrections_do_not_demote():
    fitness, baseline, stability = _strong_profile()
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -1.5}}},
        {"evidence": {"hedge_density": {"direction": "increased", "delta": 1.5}}},
    ]
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability, correction_evidence=evidence)
    assert result["hedge_density"] == "High"


def test_medium_can_demote_to_low():
    fitness = {"tier": "gold"}
    baseline = {"word_count": 1000}
    stability = {"dimensions": {"hedge_density": "volatile"}}  # -> Medium before demotion
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -1.5}}},
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -2.0}}},
    ]
    result = ve.compute_dimension_confidence(fitness, baseline, 5, stability, correction_evidence=evidence)
    assert result["hedge_density"] == "Low"


def test_low_stays_low_after_demotion():
    fitness = {"tier": "thin"}
    baseline = {"word_count": 50}
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -1.5}}},
        {"evidence": {"hedge_density": {"direction": "decreased", "delta": -2.0}}},
    ]
    result = ve.compute_dimension_confidence(fitness, baseline, 0, None, correction_evidence=evidence)
    assert result["hedge_density"] == "Low"


def test_consistent_correction_count_majority_direction():
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased"}}},
        {"evidence": {"hedge_density": {"direction": "decreased"}}},
        {"evidence": {"hedge_density": {"direction": "increased"}}},
    ]
    assert ve._consistent_correction_count(evidence, "hedge_density") == 2


def test_consistent_correction_count_even_split_returns_zero():
    evidence = [
        {"evidence": {"hedge_density": {"direction": "decreased"}}},
        {"evidence": {"hedge_density": {"direction": "increased"}}},
    ]
    assert ve._consistent_correction_count(evidence, "hedge_density") == 0


def test_consistent_correction_count_ignores_entries_missing_the_dimension():
    evidence = [
        {"evidence": {"sentence_length_sd": {"direction": "decreased"}}},
    ]
    assert ve._consistent_correction_count(evidence, "hedge_density") == 0


def test_consistent_correction_count_handles_none_and_empty():
    assert ve._consistent_correction_count(None, "hedge_density") == 0
    assert ve._consistent_correction_count([], "hedge_density") == 0
