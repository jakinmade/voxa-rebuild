"""Tests for find_source_sentence - the small, safe piece of the
inline-diff concept: shows the actual dropped sentence, not just the
bare word, in the existing dropped-entities warning. Deterministic,
read-only, no text-splicing."""
import voice_engine as ve


def test_finds_sentence_containing_the_entity():
    text = "Curious if you got a chance to run it. Other stuff here."
    result = ve.find_source_sentence(text, "Curious")
    assert result == "Curious if you got a chance to run it."


def test_case_insensitive_match():
    text = "Scott was curious about the report."
    result = ve.find_source_sentence(text, "Curious")
    assert result is not None
    assert "curious" in result.lower()


def test_returns_none_when_not_found():
    text = "Nothing relevant here."
    assert ve.find_source_sentence(text, "Curious") is None


def test_whole_word_only_no_partial_match():
    text = "Curiosity killed the cat."
    assert ve.find_source_sentence(text, "Curious") is None


def test_returns_first_matching_sentence_when_multiple():
    text = "Scott is here. Scott left early."
    result = ve.find_source_sentence(text, "Scott")
    assert result == "Scott is here."
