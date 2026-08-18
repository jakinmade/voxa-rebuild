"""
Tests for _build_content_lock_html — the 'voice can change, meaning
can't' checklist, first item built from the 18 Aug 2026 market-
landscape review's roadmap. Every check here reads data already
computed and tested elsewhere (score_semantic_drift's dropped_entities
/ attribution_swaps, _check_uncorrected_insertions' sentence_growth /
new_hedges); this module only formats it. These tests guard the
formatting logic and the pass/fail thresholds, not the underlying
detection (already covered by its own test files).

Deliberately four checks, not the five in the original design brief —
see the function's own docstring for why "no new claims detected"
was dropped rather than mapped weakly onto an unrelated signal.
"""
from app import _build_content_lock_html


def test_all_pass_shows_four_pass_states_zero_fail():
    report = {"dropped_entities": [], "attribution_swaps": []}
    insertion_check = {"sentence_growth": 0, "new_hedges": []}
    html = _build_content_lock_html(report, insertion_check)
    assert html.count("content-lock-item pass") == 4
    assert html.count("content-lock-item fail") == 0
    assert "\u2713" in html
    assert "\u2717" not in html


def test_dropped_entity_shows_as_fail_with_names_listed():
    report = {"dropped_entities": ["John", "Q3"], "attribution_swaps": []}
    insertion_check = {"sentence_growth": 0, "new_hedges": []}
    html = _build_content_lock_html(report, insertion_check)
    assert "content-lock-item fail" in html
    assert "Facts preserved" in html
    assert "John, Q3" in html
    assert "2 dropped" in html


def test_attribution_swap_shows_as_fail_without_naming_the_swap():
    """Attribution swaps don't carry a clean 'what changed' string the
    way dropped entities do -- the existing standalone warning
    (rendered separately, below this checklist) already gives the
    detailed explanation; this checklist row just needs to correctly
    flag fail."""
    report = {"dropped_entities": [], "attribution_swaps": [("your point", "my point")]}
    insertion_check = {"sentence_growth": 0, "new_hedges": []}
    html = _build_content_lock_html(report, insertion_check)
    assert "Attribution preserved" in html
    assert "content-lock-item fail" in html


def test_sentence_growth_shows_as_fail_with_count():
    report = {"dropped_entities": [], "attribution_swaps": []}
    insertion_check = {"sentence_growth": 2, "new_hedges": []}
    html = _build_content_lock_html(report, insertion_check)
    assert "No sentences invented" in html
    assert "2 sentence(s) added" in html


def test_new_hedges_shows_as_fail_with_words_listed():
    report = {"dropped_entities": [], "attribution_swaps": []}
    insertion_check = {"sentence_growth": 0, "new_hedges": ["perhaps", "might"]}
    html = _build_content_lock_html(report, insertion_check)
    assert "No new hedging introduced" in html
    assert "perhaps, might" in html


def test_missing_insertion_check_defaults_to_pass_not_crash():
    """A render where insertion_check is None (e.g. render failed
    before this was computed) must not crash the checklist -- treated
    as pass/no-signal rather than raising."""
    report = {"dropped_entities": [], "attribution_swaps": []}
    html = _build_content_lock_html(report, None)
    assert "content-lock-item pass" in html
    # Should not raise, and should still render all four rows.
    assert html.count("content-lock-item") == 4


def test_all_four_fail_simultaneously():
    """No hidden interaction between checks -- all four can fail at
    once without one masking another."""
    report = {"dropped_entities": ["Acme Corp"], "attribution_swaps": [("your idea", "my idea")]}
    insertion_check = {"sentence_growth": 1, "new_hedges": ["somewhat"]}
    html = _build_content_lock_html(report, insertion_check)
    assert html.count("content-lock-item fail") == 4
    assert html.count("content-lock-item pass") == 0


def test_checklist_title_always_present():
    report = {"dropped_entities": [], "attribution_swaps": []}
    html = _build_content_lock_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "Content Lock" in html
