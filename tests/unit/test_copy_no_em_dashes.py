"""
Regression guard for VOICOVA's own user-facing copy, found and fixed
this session: the product that detects and strips em dashes from
other people's writing had em dashes scattered through its own
interface (Screens 1-4 and the post-rewrite Voice Report). Not
hypothetical -- caught directly from a real report the user saw
("Held on directness — drifted on ownership...") and a live audit of
every user-facing string in app.py and voice_engine.py.

Scope deliberately excludes: code comments, docstrings, and any text
that only ever reaches the LLM system prompt (voice_dna, restoration
targets, correction instructions) rather than the person using the
app directly -- those are a different category with a different
audience, checked and confirmed to be out of scope for this pass.
"""
import voice_engine as ve


def _no_em_dash(text: str) -> bool:
    return "\u2014" not in text and "—" not in text


# ------------------------------------------------------------------
# voice_match_label — the exact phrase from the real report that
# surfaced this: "Held on X — drifted on Y."
# ------------------------------------------------------------------

def test_voice_match_label_evidence_has_no_em_dash_when_mixed():
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.1, "delta": 0.1},
        "sentence_length_sd": {"verdict": "MISSED", "pct_diff": 0.6, "delta": 0.6},
    }
    result = ve.voice_match_label(delta)
    assert _no_em_dash(result["evidence"]), result["evidence"]
    assert "Held on" in result["evidence"]
    assert "Drifted on" in result["evidence"]


def test_voice_match_label_evidence_has_no_em_dash_all_hit():
    delta = {"hedge_density": {"verdict": "HIT", "pct_diff": 0.1, "delta": 0.1}}
    result = ve.voice_match_label(delta)
    assert _no_em_dash(result["evidence"])


def test_voice_match_label_evidence_has_no_em_dash_all_missed():
    delta = {"hedge_density": {"verdict": "MISSED", "pct_diff": 0.6, "delta": 0.6}}
    result = ve.voice_match_label(delta)
    assert _no_em_dash(result["evidence"])


# ------------------------------------------------------------------
# confidence_caveat — the "your two samples read pretty differently"
# message, also shown directly in the real report.
# ------------------------------------------------------------------

def test_confidence_caveat_has_no_em_dash():
    stability = {"sample_count": 2, "stable_count": 1, "volatile_count": 3}
    caveat = ve.confidence_caveat(stability)
    assert caveat is not None
    assert _no_em_dash(caveat), caveat


# ------------------------------------------------------------------
# Fitness-gate nudges — Screen 1 guidance messages
# ------------------------------------------------------------------

def test_fitness_gate_nudges_have_no_em_dash():
    low_fitness_samples = [
        "ok",
        "This is a general statement about things and stuff.",
    ]
    for text in low_fitness_samples:
        result = ve._score_sample_fitness(text)
        nudge = result.get("nudge")
        if nudge:
            assert _no_em_dash(nudge), nudge
