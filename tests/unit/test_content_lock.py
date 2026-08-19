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
from app import _build_content_lock_html, _build_content_lock_banner_html, _build_what_changed_html


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


# ---------------------------------------------------------------------------
# _build_content_lock_banner_html — the leading pass/fail summary shown
# above the full checklist (19 Aug 2026, VOICOVA UX review: Content Lock
# should be a visible status, not buried inside the report).
# ---------------------------------------------------------------------------

def test_banner_shows_content_safe_when_all_pass():
    report = {"dropped_entities": [], "attribution_swaps": []}
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "content-lock-banner pass" in html
    assert "Content safe" in html
    assert "content-lock-banner fail" not in html


def test_banner_shows_needs_your_eyes_on_any_failure():
    report = {"dropped_entities": ["Scott"], "attribution_swaps": []}
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "content-lock-banner fail" in html
    assert "Needs your eyes" in html
    assert "Scott" in html


def test_banner_lists_every_failure_reason_not_just_first():
    report = {"dropped_entities": ["Scott"], "attribution_swaps": [("your idea", "my idea")]}
    html = _build_content_lock_banner_html(report, {"sentence_growth": 1, "new_hedges": ["perhaps"]})
    assert html.count("content-lock-banner-reason") == 4
    assert "Scott" in html
    assert "Attribution may have changed" in html
    assert "1 sentence(s)" in html
    assert "perhaps" in html


def test_banner_missing_insertion_check_defaults_to_safe():
    report = {"dropped_entities": [], "attribution_swaps": []}
    html = _build_content_lock_banner_html(report, None)
    assert "content-lock-banner pass" in html


# ---------------------------------------------------------------------------
# lexical_fidelity_breaks note (19 Aug 2026) — fixes the gap where this
# signal was computed (voice_engine.detect_lexical_fidelity_breaks) but
# never actually shown anywhere. Deliberately a separate amber note,
# not one of the four fail reasons - a watchlist hit must never flip
# this banner to fail on its own, per detect_lexical_fidelity_breaks'
# own docstring (informational only, JA: "flag it for review rather
# than block").
# ---------------------------------------------------------------------------

def test_lexical_fidelity_break_shows_as_note_not_failure_when_otherwise_clean():
    report = {
        "dropped_entities": [], "attribution_swaps": [],
        "lexical_fidelity_breaks": ["'surfaces' became 'brings up' - breaks grammar"],
    }
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    # Still reads as safe overall - the whole point is this must not
    # be treated as a content-integrity failure.
    assert "content-lock-banner pass" in html
    assert "content-lock-banner fail" not in html
    assert "content-lock-banner-note" in html
    assert "Worth a look" in html
    assert "brings up" in html


def test_lexical_fidelity_break_still_shows_alongside_a_real_failure():
    # A real hard-fail (dropped entity) and a lexical-fidelity note can
    # both be true of the same render - the note must not get lost
    # inside the fail state either.
    report = {
        "dropped_entities": ["Scott"], "attribution_swaps": [],
        "lexical_fidelity_breaks": ["'surfaces' became 'brings up' - breaks grammar"],
    }
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "content-lock-banner fail" in html
    assert "Scott" in html
    assert "content-lock-banner-note" in html
    assert "brings up" in html


def test_no_lexical_fidelity_note_when_list_empty():
    report = {"dropped_entities": [], "attribution_swaps": [], "lexical_fidelity_breaks": []}
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "content-lock-banner-note" not in html


def test_missing_lexical_fidelity_breaks_key_does_not_crash():
    # Older report dicts (or any call site that hasn't been touched)
    # won't have this key at all - must default cleanly, not KeyError.
    report = {"dropped_entities": [], "attribution_swaps": []}
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert "content-lock-banner pass" in html
    assert "content-lock-banner-note" not in html


def test_multiple_lexical_fidelity_breaks_all_shown():
    report = {
        "dropped_entities": [], "attribution_swaps": [],
        "lexical_fidelity_breaks": [
            "'surfaces' became 'brings up' - breaks grammar",
            "'if' became 'whether' - unforced swap",
        ],
    }
    html = _build_content_lock_banner_html(report, {"sentence_growth": 0, "new_hedges": []})
    assert html.count("content-lock-banner-note") == 2
    assert "brings up" in html
    assert "whether" in html


# ---------------------------------------------------------------------------
# _build_what_changed_html — the leading chip row summarising
# biggest_changes ("Label +NN%" strings) as direction-only chips.
# ---------------------------------------------------------------------------

def test_what_changed_empty_shows_no_drift_line():
    html = _build_what_changed_html([])
    assert "No significant drift" in html
    assert "what-changed-chip" not in html


def test_what_changed_formats_negative_as_down_arrow():
    html = _build_what_changed_html(["Hedging -54%"])
    assert "Hedging \u2193" in html
    assert "-54%" not in html


def test_what_changed_formats_positive_as_up_arrow():
    html = _build_what_changed_html(["Ownership (first person) +12%"])
    assert "Ownership (first person) \u2191" in html
    assert "+12%" not in html


def test_what_changed_caps_at_three_chips():
    changes = ["Hedging -54%", "Sentence rhythm -47%", "Ownership +12%", "Directness -8%"]
    html = _build_what_changed_html(changes)
    assert html.count("what-changed-chip") == 3
    assert "Directness" not in html
