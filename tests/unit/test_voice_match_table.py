"""
Tests for _build_voice_match_table_html and _format_voice_match_value —
the second item from the 18 Aug 2026 market-landscape review's
roadmap: the per-dimension baseline-vs-output evidence behind the
single 'Voice consistency' badge, shown as a table rather than left
implicit in the evidence sentence's prose.

All underlying data is score_render_delta's own output; these tests
guard formatting and verdict-state handling, not the scoring itself
(covered by voice_engine.py's own test files).
"""
from app import _build_voice_match_table_html, _format_voice_match_value


def _delta(**overrides):
    d = {
        "hedge_density": {"baseline": 4.0, "output": 4.0, "delta": 0.0, "pct_diff": 0.0, "verdict": "HIT"},
        "sentence_length_sd": {"baseline": 7.4, "output": 7.4, "delta": 0.0, "pct_diff": 0.0, "verdict": "HIT"},
        "first_person_ratio": {"baseline": 0.15, "output": 0.15, "delta": 0.0, "pct_diff": 0.0, "verdict": "HIT"},
        "directive_ratio": {"baseline": 0.02, "output": 0.02, "delta": 0.0, "pct_diff": 0.0, "verdict": "HIT"},
    }
    d.update(overrides)
    return d


# ------------------------------------------------------------------
# Per-dimension formatting -- different scales, must not share one formatter
# ------------------------------------------------------------------

def test_hedge_density_formats_as_percentage_scale_value():
    """Already a percentage-scale number from compute_baseline_metrics
    (4.0 means 4%), not a 0-1 fraction -- must not be multiplied or
    treated like the ratio dimensions."""
    assert _format_voice_match_value("hedge_density", 4.0) == "4.0%"
    assert _format_voice_match_value("hedge_density", 17.65) == "17.6%"  # Python round-half-to-even


def test_sentence_length_sd_formats_as_plain_number_no_percent_sign():
    """A word-count standard deviation has no percentage meaning at
    all -- must not get a % sign."""
    assert _format_voice_match_value("sentence_length_sd", 7.4) == "7.4"
    assert "%" not in _format_voice_match_value("sentence_length_sd", 7.4)


def test_first_person_and_directive_ratio_format_as_0_to_1_percentage():
    """These ARE raw 0-1 proportions, unlike hedge_density -- 0.15
    must display as 15%, not 0% (which a naive shared formatter
    would produce if it assumed hedge_density's scale)."""
    assert _format_voice_match_value("first_person_ratio", 0.15) == "15%"
    assert _format_voice_match_value("directive_ratio", 0.02) == "2%"


# ------------------------------------------------------------------
# Table structure and verdict states
# ------------------------------------------------------------------

def test_all_hit_shows_four_green_badges():
    html = _build_voice_match_table_html(_delta())
    assert html.count("badge-green") == 4
    assert html.count("badge-amber") == 0
    assert html.count("badge-red") == 0


def test_close_gets_amber_not_green_or_red():
    """CLOSE must be visually distinct from both HIT and MISSED, not
    collapsed into either — this was a real bug fixed earlier in
    voice_match_label's evidence sentence (test_voice_match_close_
    verdict.py); the table must not reintroduce it."""
    delta = _delta(sentence_length_sd={
        "baseline": 7.0, "output": 9.0, "delta": 2.0, "pct_diff": 0.28, "verdict": "CLOSE"
    })
    html = _build_voice_match_table_html(delta)
    assert "CLOSE" in html
    rows = html.split("<tr>")
    close_row = next(r for r in rows if "Sentence rhythm" in r)
    assert "badge-amber" in close_row


def test_missed_gets_red_badge():
    delta = _delta(hedge_density={
        "baseline": 4.0, "output": 0.5, "delta": -3.5, "pct_diff": 0.9, "verdict": "MISSED"
    })
    html = _build_voice_match_table_html(delta)
    rows = html.split("<tr>")
    missed_row = next(r for r in rows if "Hedging" in r)
    assert "badge-red" in missed_row


def test_skipped_no_content_shows_correct_explanation():
    delta = _delta(directive_ratio={
        "baseline": 0.02, "output": 0.9, "delta": 0.88, "pct_diff": 44.0,
        "verdict": "SKIPPED", "skip_reason": "no_content",
    })
    html = _build_voice_match_table_html(delta)
    assert "SKIPPED" in html
    assert "nothing to convert in the original" in html
    assert "cutting real content" not in html


def test_skipped_content_ceiling_shows_correct_explanation():
    """Must NOT show the no_content message -- the exact live bug
    this session found and fixed in voice_match_label's evidence
    sentence, now guarded here too for the table."""
    delta = _delta(first_person_ratio={
        "baseline": 0.08, "output": 0.15, "delta": 0.07, "pct_diff": 0.37,
        "verdict": "SKIPPED", "skip_reason": "content_ceiling",
    })
    html = _build_voice_match_table_html(delta)
    assert "SKIPPED" in html
    assert "cutting real content" in html
    assert "nothing to convert in the original" not in html


def test_skipped_missing_skip_reason_defaults_to_no_content_message():
    """Backward compatibility, same as voice_match_label -- an entry
    with verdict SKIPPED but no skip_reason key must default to the
    no_content explanation, not crash or show nothing."""
    delta = _delta(directive_ratio={
        "baseline": 0.02, "output": 0.9, "delta": 0.88, "pct_diff": 44.0, "verdict": "SKIPPED",
    })
    html = _build_voice_match_table_html(delta)
    assert "nothing to convert in the original" in html


def test_multiple_skipped_dimensions_each_get_own_explanation():
    delta = _delta(
        first_person_ratio={
            "baseline": 0.08, "output": 0.15, "delta": 0.07, "pct_diff": 0.37,
            "verdict": "SKIPPED", "skip_reason": "content_ceiling",
        },
        directive_ratio={
            "baseline": 0.02, "output": 0.9, "delta": 0.88, "pct_diff": 44.0,
            "verdict": "SKIPPED", "skip_reason": "no_content",
        },
    )
    html = _build_voice_match_table_html(delta)
    assert "Ownership (first person): your own writing" in html
    assert "Directness: nothing to convert" in html


def test_no_skip_notes_section_when_nothing_skipped():
    html = _build_voice_match_table_html(_delta())
    assert "voice-match-explain" not in html


def test_all_four_dimensions_present_in_fixed_order():
    html = _build_voice_match_table_html(_delta())
    hedge_pos = html.find("Hedging")
    rhythm_pos = html.find("Sentence rhythm")
    ownership_pos = html.find("Ownership")
    directness_pos = html.find("Directness")
    assert hedge_pos < rhythm_pos < ownership_pos < directness_pos


def test_empty_delta_returns_empty_string_not_broken_table():
    assert _build_voice_match_table_html({}) == ""


def test_missing_dimension_in_delta_is_skipped_not_crashed():
    """A delta dict missing one of the four expected keys (shouldn't
    normally happen, but defensive) must not crash -- just renders
    the dimensions actually present."""
    partial = {
        "hedge_density": {"baseline": 4.0, "output": 4.0, "delta": 0.0, "pct_diff": 0.0, "verdict": "HIT"},
    }
    html = _build_voice_match_table_html(partial)
    assert "Hedging" in html
    assert "Sentence rhythm" not in html
