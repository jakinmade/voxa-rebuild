"""
Tests for compute_passive_voice — the regex-based be-form + past-
participle heuristic added 18 Aug 2026 as part of the Preserve/Elevate
mode groundwork. Deliberately standalone, same guarantee as
compute_sentence_economy: must never be wired into
compute_baseline_metrics, score_render_delta, or the correction-pass
targeting pipeline.
"""
import voice_engine as ve


def test_active_voice_scores_zero():
    text = "The dog chased the ball. She wrote the report. We build things fast."
    result = ve.compute_passive_voice(text)
    assert result["passive_count"] == 0
    assert result["passive_sentence_ratio"] == 0.0


def test_passive_voice_detected():
    text = (
        "The ball was chased by the dog. The report was written yesterday. "
        "The decision was made without me."
    )
    result = ve.compute_passive_voice(text)
    assert result["passive_count"] == 3
    assert result["passive_sentence_ratio"] == 1.0


def test_mixed_active_and_passive():
    text = "I sent the email. The email was sent by mistake. It arrived late."
    result = ve.compute_passive_voice(text)
    assert result["passive_count"] == 1
    assert 0.0 < result["passive_sentence_ratio"] < 1.0


def test_be_plus_non_participle_adjective_not_flagged():
    """
    "The door was open" — "open" isn't a participle at all (no -ed,
    not in the irregular list), so this is correctly never flagged.
    """
    text = "The door was open. The room was quiet. She was ready."
    result = ve.compute_passive_voice(text)
    assert result["passive_count"] == 0


def test_known_limitation_regular_ed_adjective_is_a_false_positive():
    """
    Documents a genuine, accepted limitation rather than hiding it:
    "the window was closed" is surface-identical to true passive voice
    ("the window was closed by the wind") for a regex that only sees
    be-form + -ed word, with no dependency parse to disambiguate.
    This is expected to match — pinning it so the behaviour is a
    documented trade-off, not a silent surprise.
    """
    text = "The window was closed."
    result = ve.compute_passive_voice(text)
    assert result["passive_count"] == 1


def test_empty_text_returns_zeros_not_error():
    result = ve.compute_passive_voice("")
    assert result == {"passive_count": 0, "passive_sentence_ratio": 0.0}


def test_returns_expected_keys():
    result = ve.compute_passive_voice("This was written by me.")
    assert set(result.keys()) == {"passive_count", "passive_sentence_ratio"}


def test_does_not_affect_baseline_metrics():
    """Same coupling guard as test_sentence_economy.py."""
    text = "This is a test. It has three sentences. Here is the third one."
    baseline = ve.compute_baseline_metrics(text)
    assert set(baseline.keys()) == {
        "hedge_density", "sentence_length_sd",
        "first_person_ratio", "directive_ratio", "word_count",
    }
