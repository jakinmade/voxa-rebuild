"""Tests for highlight_flagged_phrases - the combined, single-pass
successor to highlight_attribution_swaps used at the actual output
call site. Covers everything highlight_attribution_swaps' own tests
cover for the attribution_swaps path, plus the lexical_fidelity_breaks
path and - the actual new risk - what happens when both signals are
present and their spans are close together or overlap.
"""
import voice_engine as ve


# ---------------------------------------------------------------------------
# attribution_swaps alone - same behaviour as highlight_attribution_swaps
# ---------------------------------------------------------------------------

def test_attribution_swap_highlighted_red():
    output = "I think my point was clear."
    swaps = ["'your point' became 'my point', credit moved from them to you"]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps)
    assert '<span' in result
    assert 'my point' in result
    assert 'title=' in result
    assert '#B3382C' in result  # red border, matches highlight_attribution_swaps


def test_multiple_attribution_swaps_each_highlighted():
    output = "My idea and my plan were both good."
    swaps = [
        "'your idea' became 'my idea', credit moved from them to you",
        "'your plan' became 'my plan', credit moved from them to you",
    ]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps)
    assert result.count('<span') == 2


# ---------------------------------------------------------------------------
# lexical_fidelity_breaks alone - the new signal, amber not red
# ---------------------------------------------------------------------------

def test_lexical_fidelity_break_highlighted_amber():
    output = "It brings up when someone finally asks."
    breaks = ["'surfaces' became 'brings up' - breaks grammar used intransitively"]
    result = ve.highlight_flagged_phrases(output, lexical_fidelity_breaks=breaks)
    assert '<span' in result
    assert 'brings up' in result
    assert '#A5690B' in result  # amber border, matches content-lock-banner-note
    assert '#B3382C' not in result  # must NOT be styled red


def test_multiple_lexical_fidelity_breaks_each_highlighted():
    output = "It brings up the point, and whether it matters."
    breaks = [
        "'surfaces' became 'brings up' - breaks grammar",
        "'if' became 'whether' - unforced swap",
    ]
    result = ve.highlight_flagged_phrases(output, lexical_fidelity_breaks=breaks)
    assert result.count('<span') == 2


# ---------------------------------------------------------------------------
# Both signals together - the actual new risk this function exists for
# ---------------------------------------------------------------------------

def test_both_signals_present_both_highlighted_correct_colours():
    output = "My point brings up something worth saying."
    swaps = ["'your point' became 'my point', credit moved from them to you"]
    breaks = ["'surfaces' became 'brings up' - breaks grammar"]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps, lexical_fidelity_breaks=breaks)
    assert result.count('<span') == 2
    assert '#B3382C' in result
    assert '#A5690B' in result


def test_overlapping_spans_attribution_swap_wins():
    # Contrived but real: if a lexical-fidelity phrase and an
    # attribution-swap phrase would claim the exact same text, the
    # attribution swap (hard content-integrity fail) must win - a
    # real failure must never end up hidden behind a lower-severity
    # amber note claiming the same span.
    output = "My plan was solid."
    swaps = ["'your plan' became 'my plan', credit moved from them to you"]
    breaks = ["'plan' became 'my plan' - some contrived overlap note"]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps, lexical_fidelity_breaks=breaks)
    # Only one span should win the contested text - not two nested
    # or corrupted spans.
    assert result.count('<span') == 1
    assert '#B3382C' in result
    assert '#A5690B' not in result


def test_adjacent_non_overlapping_spans_both_render_cleanly():
    output = "My point brings up a real concern here."
    swaps = ["'your point' became 'my point', credit moved from them to you"]
    breaks = ["'surfaces' became 'brings up' - breaks grammar"]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps, lexical_fidelity_breaks=breaks)
    # Both phrases survive intact, in order, nothing corrupted between them.
    assert result.count('<span') == 2
    my_point_idx = result.find('my point')
    brings_up_idx = result.find('brings up')
    assert my_point_idx != -1 and brings_up_idx != -1
    assert my_point_idx < brings_up_idx


def test_second_pass_never_reparses_first_passs_injected_html():
    # The specific failure mode a naive "call highlight_attribution_
    # swaps then highlight again" approach risks: a second regex pass
    # matching text INSIDE an already-injected <span title="..."> from
    # the first pass. Single-pass-over-one-escaped-string design means
    # this can't happen - confirm no nested or malformed spans.
    output = "My point surfaces a real concern."
    swaps = ["'your point' became 'my point', credit moved from them to you"]
    breaks = ["'surfaces' became 'point' - contrived, targets a word already inside the first span"]
    result = ve.highlight_flagged_phrases(output, attribution_swaps=swaps, lexical_fidelity_breaks=breaks)
    assert '<span><span' not in result
    assert result.count('<span') <= 2


# ---------------------------------------------------------------------------
# Fail-safe / empty-input behaviour
# ---------------------------------------------------------------------------

def test_no_flags_returns_escaped_text_unchanged_visually():
    output = "Plain text with no issues."
    result = ve.highlight_flagged_phrases(output)
    assert '<span' not in result
    assert 'Plain text with no issues.' in result


def test_html_escapes_the_output_text():
    output = "A <script> tag & an ampersand."
    result = ve.highlight_flagged_phrases(output)
    assert '<script>' not in result
    assert '&lt;script&gt;' in result
    assert '&amp;' in result


def test_malformed_entries_skipped_safely():
    output = "Some text here."
    result = ve.highlight_flagged_phrases(
        output, attribution_swaps=["not a real format"], lexical_fidelity_breaks=["also not real"],
    )
    assert '<span' not in result
    assert 'Some text here.' in result


def test_none_args_default_safely():
    # Call-site convenience: passing nothing for either list must not
    # crash (matches the call site's own optional lexical_fidelity_
    # breaks key, which may be absent on an older report shape).
    result = ve.highlight_flagged_phrases("Plain text.")
    assert result == "Plain text."


def test_phrase_not_present_in_output_is_simply_not_highlighted():
    # No crash, no phantom span - the phrase genuinely isn't there.
    output = "Something else entirely."
    breaks = ["'surfaces' became 'brings up' - breaks grammar"]
    result = ve.highlight_flagged_phrases(output, lexical_fidelity_breaks=breaks)
    assert '<span' not in result
