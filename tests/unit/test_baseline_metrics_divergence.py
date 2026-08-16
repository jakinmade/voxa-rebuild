"""
Pins the documented divergence between voice_engine.compute_baseline_metrics
and voxa_api.recalibrate.compute_baseline_metrics — see the cross-reference
notes in both functions' docstrings for why the two implementations exist
side by side rather than being merged.

This file is NOT asserting the two should ever converge. It exists so that
if either implementation's hedge detection or sentence splitting changes,
these tests fail loudly instead of the gap silently widening or narrowing
unnoticed. If a test here starts failing because you deliberately changed
one side, update the docstring notes in both files to match, then update
this test to pin the new reality.
"""
from voice_engine import compute_baseline_metrics as engine_metrics
from voxa_api.recalibrate import compute_baseline_metrics as recalibrate_metrics


def test_clause_level_hedge_detected_by_engine_not_recalibrate():
    """voice_engine's _HEDGE_PATTERN catches clause-level hedges
    (Hyland taxonomy); recalibrate.py's regex is single-word only and
    does not catch "curious whether" as a hedge."""
    text = (
        "I am curious whether this will work. The plan seems solid. "
        "We should proceed either way."
    )
    engine_result = engine_metrics(text)
    recal_result = recalibrate_metrics(text)

    assert engine_result["hedge_density"] > 0, (
        "voice_engine.compute_baseline_metrics should catch the "
        "clause-level hedge 'curious whether' via _HEDGE_PATTERN"
    )
    assert recal_result["hedge_density"] == 0, (
        "recalibrate.compute_baseline_metrics is documented as "
        "single-word-hedge-only — if this now catches 'curious whether' "
        "the two implementations have converged on this point and the "
        "docstring notes in both files should be updated to say so"
    )


def test_single_word_hedge_detected_by_both():
    """Both implementations agree on the original single-word hedge list
    (perhaps, possibly, maybe, etc) — this is the part they DO share."""
    text = "This is perhaps the clearest example we have. It works well."
    engine_result = engine_metrics(text)
    recal_result = recalibrate_metrics(text)

    assert engine_result["hedge_density"] > 0
    assert recal_result["hedge_density"] > 0


def test_abbreviation_sentence_split_differs():
    """voice_engine's _extract_sentences protects abbreviations like
    'Dr.' from being read as a sentence boundary; recalibrate.py's raw
    re.split does not, so it over-counts sentences on abbreviation-heavy
    text and produces a different sentence_length_sd."""
    text = (
        "Dr. Smith called this morning to confirm the appointment. "
        "The details were straightforward and the timing worked for "
        "everyone involved in the project this quarter."
    )
    engine_result = engine_metrics(text)
    recal_result = recalibrate_metrics(text)

    # recalibrate.py's naive split treats "Dr." as a sentence boundary,
    # producing an extra (very short) sentence that voice_engine's
    # abbreviation guard prevents. This pulls their sentence_length_sd
    # apart on the same input — documented, not a bug to fix here.
    assert engine_result["sentence_length_sd"] != recal_result["sentence_length_sd"], (
        "If these now match, the abbreviation-guard divergence this "
        "test documents may have been resolved — update the docstring "
        "notes in voice_engine.py and recalibrate.py to reflect that"
    )
