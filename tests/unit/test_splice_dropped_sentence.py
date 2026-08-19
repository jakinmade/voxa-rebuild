"""Tests for splice_dropped_sentence - the safe, append-only subset of
the inline-diff splice feature that find_source_sentence's docstring
and score_restructure_fidelity's neighbourhood both flag as deferred.
Deterministic, no API call. Makes no positional judgement about where
inside the rewrite the sentence belongs - only appends it, once,
clearly marked, and leaves placement to the person."""
import voice_engine as ve


def test_appends_sentence_with_marker():
    output = "This is the rewritten text."
    source = "Curious whether your clients have solved that."
    result = ve.splice_dropped_sentence(output, source)
    assert output in result
    assert source in result
    assert "Restored" in result


def test_empty_source_sentence_returns_output_unchanged():
    output = "This is the rewritten text."
    assert ve.splice_dropped_sentence(output, "") == output


def test_whitespace_only_source_sentence_returns_output_unchanged():
    output = "This is the rewritten text."
    assert ve.splice_dropped_sentence(output, "   ") == output


def test_does_not_duplicate_sentence_already_present():
    sentence = "Curious whether your clients have solved that."
    output = f"This is the rewritten text. {sentence}"
    result = ve.splice_dropped_sentence(output, sentence)
    assert result == output
    assert result.count("Curious whether") == 1


def test_duplicate_check_is_whitespace_normalised():
    """Guards against a source sentence that differs from the output's
    copy only in internal spacing (e.g. a double space) still being
    correctly recognised as already present."""
    output = "This is the rewritten text. Curious  whether your clients have solved that."
    source = "Curious whether your clients have solved that."
    result = ve.splice_dropped_sentence(output, source)
    assert result == output


def test_duplicate_check_is_case_insensitive():
    output = "This is the rewritten text. curious whether your clients have solved that."
    source = "Curious whether your clients have solved that."
    result = ve.splice_dropped_sentence(output, source)
    assert result == output


def test_never_rewords_the_source_sentence():
    """The appended text must be byte-identical to what was passed in
    - this function must never itself become a second uncontrolled
    edit on top of the one it exists to catch."""
    output = "Some rewritten text."
    source = "An unusual, oddly-punctuated original sentence -- kept as-is."
    result = ve.splice_dropped_sentence(output, source)
    assert source in result


def test_appends_at_end_not_start_or_middle():
    output = "First paragraph.\n\nSecond paragraph."
    source = "A dropped sentence."
    result = ve.splice_dropped_sentence(output, source)
    assert result.index(output) == 0
    assert result.index(source) > result.index(output)


def test_empty_output_text_still_appends():
    """Edge case: if output_text is empty (shouldn't happen in
    practice since this only fires when there's a render to check
    against, but the function must not crash)."""
    result = ve.splice_dropped_sentence("", "A dropped sentence.")
    assert "A dropped sentence." in result
