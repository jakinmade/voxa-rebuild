"""Tests for highlight_attribution_swaps - real inline highlighting
for attribution swaps, safe because the swapped phrase genuinely
exists at a position in the output text (unlike a dropped entity)."""
import voice_engine as ve


def test_highlights_the_swapped_phrase():
    output = "I think my point was clear."
    swaps = ["'your point' became 'my point', credit moved from them to you"]
    result = ve.highlight_attribution_swaps(output, swaps)
    assert '<span' in result
    assert 'my point' in result
    assert 'title=' in result


def test_no_swaps_returns_escaped_text_unchanged_visually():
    output = "Plain text with no issues."
    result = ve.highlight_attribution_swaps(output, [])
    assert '<span' not in result
    assert 'Plain text with no issues.' in result


def test_html_escapes_the_output_text():
    output = "A <script> tag & an ampersand."
    result = ve.highlight_attribution_swaps(output, [])
    assert '<script>' not in result
    assert '&lt;script&gt;' in result
    assert '&amp;' in result


def test_multiple_swaps_each_highlighted():
    output = "My idea and my plan were both good."
    swaps = [
        "'your idea' became 'my idea', credit moved from them to you",
        "'your plan' became 'my plan', credit moved from them to you",
    ]
    result = ve.highlight_attribution_swaps(output, swaps)
    assert result.count('<span') == 2


def test_malformed_swap_string_skipped_safely():
    output = "Some text here."
    result = ve.highlight_attribution_swaps(output, ["not a real swap format"])
    assert '<span' not in result
    assert 'Some text here.' in result
