"""
Regression guard found this session: voice_match_label's evidence
string ("Held on X. Drifted on Y.") used the word "Drifted" — one of
voice_engine's own _ANALYTICAL_TELL_PHRASES. Confirmed live:
score_ai_tells(evidence_string) returned clean=False, flagged=
['AI-typical phrasing found: Drifted']. That's app-generated copy
explaining to the user why their rewrite doesn't sound like them,
itself written in a phrase the product's own detector flags as not
sounding human. Fixed by replacing "Drifted" with "Off" (see
voice_match_label's inline comment for the full reasoning).

This file pins that fix and extends the same check to every other
piece of static, user-facing report copy this session touched or
reviewed, so a future wording change that reintroduces a flagged
phrase fails a test instead of shipping unnoticed the way the
original did.
"""
import voice_engine as ve


def _assert_copy_is_clean(text: str):
    result = ve.score_ai_tells(text)
    assert result["clean"], (
        f"App copy trips its own AI-tell detector: {text!r} "
        f"-> flagged: {result['flagged']}"
    )


def test_voice_match_label_evidence_clean_when_mixed():
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.1, "delta": 0.1},
        "sentence_length_sd": {"verdict": "MISSED", "pct_diff": 0.6, "delta": 0.6},
    }
    result = ve.voice_match_label(delta)
    _assert_copy_is_clean(result["evidence"])


def test_voice_match_label_evidence_clean_when_all_missed():
    delta = {
        "hedge_density": {"verdict": "MISSED", "pct_diff": 0.6, "delta": 0.6},
        "first_person_ratio": {"verdict": "MISSED", "pct_diff": 0.4, "delta": 0.4},
    }
    result = ve.voice_match_label(delta)
    _assert_copy_is_clean(result["evidence"])


def test_voice_match_label_evidence_clean_when_all_hit():
    delta = {"hedge_density": {"verdict": "HIT", "pct_diff": 0.1, "delta": 0.1}}
    result = ve.voice_match_label(delta)
    _assert_copy_is_clean(result["evidence"])


def test_confidence_caveat_clean():
    stability = {"sample_count": 2, "stable_count": 1, "volatile_count": 3}
    caveat = ve.confidence_caveat(stability)
    assert caveat is not None
    _assert_copy_is_clean(caveat)


def test_static_tagline_clean():
    # app.py's "Written as you. Not for you." (updated 22 Aug 2026,
    # dropped "engine" — Voicova isn't referred to as an engine
    # anywhere user-facing) — already confirmed clean this session,
    # pinned here so it stays covered by the same guard as everything
    # else.
    _assert_copy_is_clean("Written as you. Not for you.")
