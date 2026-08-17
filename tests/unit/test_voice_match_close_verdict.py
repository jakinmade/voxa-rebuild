"""
Regression guard: voice_match_label's evidence sentence ("Held on X.
Off on Y.") previously only ever mentioned HIT and MISSED dimensions.
A dimension with verdict CLOSE (the third tier score_render_delta can
produce, between HIT and MISSED) fell into neither list and vanished
from the sentence entirely - even though build_voice_report's
biggest_changes list includes anything that isn't a HIT, so a CLOSE
dimension DID still show up there as a numeric change.

Caught live: a real report showed "sentence rhythm 31%" in Biggest
Changes with no corresponding mention in "Held on hedging,
directness. Off on ownership (first person)." - the prose sentence
and the numbers next to it told two different stories about the same
render. Fixed by giving CLOSE its own "Close on X." clause.
"""
import voice_engine as ve


def test_close_dimension_appears_in_evidence_sentence():
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.31, "delta": 0.5},
    }
    result = ve.voice_match_label(delta)
    assert "Close on" in result["evidence"]
    assert "sentence rhythm" in result["evidence"]


def test_reproduces_the_live_report_exactly():
    """The exact shape of the real report that surfaced this: one HIT,
    one CLOSE, one MISSED, one more HIT."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.31, "delta": 0.5},
        "first_person_ratio": {"verdict": "MISSED", "pct_diff": 0.60, "delta": -0.3},
        "directive_ratio": {"verdict": "HIT", "pct_diff": 0.02, "delta": 0.01},
    }
    result = ve.voice_match_label(delta)
    assert result["evidence"] == (
        "Held on hedging, directness. Close on sentence rhythm. "
        "Off on ownership (first person)."
    )


def test_all_three_verdicts_present_and_correctly_grouped():
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.3, "delta": 0.3},
        "first_person_ratio": {"verdict": "MISSED", "pct_diff": 0.6, "delta": 0.6},
        "directive_ratio": {"verdict": "MISSED", "pct_diff": 0.7, "delta": 0.7},
    }
    result = ve.voice_match_label(delta)
    evidence = result["evidence"]
    assert "Held on hedging." in evidence
    assert "Close on sentence rhythm." in evidence
    assert "ownership (first person)" in evidence.split("Off on")[1]
    assert "directness" in evidence.split("Off on")[1]


def test_close_only_no_hits_no_misses():
    delta = {"sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.3, "delta": 0.3}}
    result = ve.voice_match_label(delta)
    assert result["evidence"] == "Close on sentence rhythm."


def test_evidence_stays_clean_against_ai_tell_scanner_with_close_present():
    """The fix must not reintroduce an AI-tell word the way 'Drifted'
    did originally - checked directly against the real scanner, not
    assumed clean by construction."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.31, "delta": 0.5},
        "first_person_ratio": {"verdict": "MISSED", "pct_diff": 0.60, "delta": -0.3},
    }
    result = ve.voice_match_label(delta)
    ai_tells = ve.score_ai_tells(result["evidence"])
    assert ai_tells["clean"], f"Evidence sentence flagged as AI-toned: {ai_tells}"


def test_no_em_dash_in_close_clause():
    delta = {"sentence_length_sd": {"verdict": "CLOSE", "pct_diff": 0.3, "delta": 0.3}}
    result = ve.voice_match_label(delta)
    assert "\u2014" not in result["evidence"]
    assert "—" not in result["evidence"]


def test_existing_hit_and_missed_only_behaviour_unaffected():
    """No CLOSE dimensions present - confirms the fix didn't change
    behaviour for the simpler, more common case."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "first_person_ratio": {"verdict": "MISSED", "pct_diff": 0.60, "delta": -0.3},
    }
    result = ve.voice_match_label(delta)
    assert result["evidence"] == "Held on hedging. Off on ownership (first person)."
