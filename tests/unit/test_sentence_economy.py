"""
Tests for compute_sentence_economy — the hand-rolled Flesch-Kincaid
grade-level function added 18 Aug 2026 as part of the Preserve/Elevate
mode groundwork. Deliberately standalone: this function must never be
wired into compute_baseline_metrics, score_render_delta, or the
correction-pass targeting pipeline, so these tests also guard that it
stays a pure, independent calculation with no side effects on those.
"""
import voice_engine as ve


def test_returns_none_for_short_input():
    assert ve.compute_sentence_economy("Hi John.") is None


def test_returns_none_for_empty_input():
    assert ve.compute_sentence_economy("") is None


def test_returns_none_below_three_sentences():
    two_sentences = "This is one sentence. This is another."
    assert ve.compute_sentence_economy(two_sentences) is None


def test_simple_text_scores_lower_than_complex_text():
    simple = (
        "The cat sat on the mat. It was warm in the sun. "
        "The dog ran past. Birds sang in the tree."
    )
    complex_text = (
        "The multifaceted ramifications of institutional accountability "
        "mechanisms necessitate comprehensive interdisciplinary "
        "examination. Organizational stakeholders frequently "
        "underestimate the compounding administrative complexities "
        "inherent in regulatory compliance frameworks. Furthermore, the "
        "epistemological foundations underpinning contemporary "
        "governance paradigms remain substantially underexplored."
    )
    simple_score = ve.compute_sentence_economy(simple)
    complex_score = ve.compute_sentence_economy(complex_text)

    assert simple_score is not None
    assert complex_score is not None
    assert simple_score["grade_level"] < complex_score["grade_level"]


def test_returns_expected_keys():
    text = "This is a test. It has three sentences. Here is the third one."
    result = ve.compute_sentence_economy(text)
    assert result is not None
    assert set(result.keys()) == {
        "grade_level", "avg_sentence_length", "avg_syllables_per_word",
    }


def test_does_not_affect_baseline_metrics():
    """
    Guard against future accidental coupling: compute_baseline_metrics
    must keep returning its original five reported keys, unaffected by
    compute_sentence_economy's existence.

    UPDATE, 27 Aug 2026: six sufficient-statistic keys were added
    deliberately (independent codebase review's baseline-merge P0 fix
    — see _merge_baseline's docstring) so _merge_baseline can combine
    samples by summing raw counts instead of averaging already-derived
    metrics, which was mathematically wrong for three of the four.
    This guard now checks the five original reported keys are still
    exactly present (unaffected by sentence-economy or anything else
    coupling in) without asserting the key set is closed, since new,
    intentional keys are expected to keep appearing here as this
    baseline gets used for more things.
    """
    text = "This is a test. It has three sentences. Here is the third one."
    baseline = ve.compute_baseline_metrics(text)
    assert {
        "hedge_density", "sentence_length_sd",
        "first_person_ratio", "directive_ratio", "word_count",
    }.issubset(baseline.keys())
