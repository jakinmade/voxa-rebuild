"""SKIPPED verdict (18 Aug 2026): a MISSED-looking gap where the input
had no content of that kind to convert (no ownership content, no
directive content) must be labelled distinctly, not shown as a failure.
"""
from voice_engine import voice_match_label, build_voice_report, compute_risk


def _delta(first_person_verdict):
    return {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.05},
        "first_person_ratio": {"verdict": first_person_verdict, "pct_diff": 0.86, "delta": 0.86},
        "sentence_length_sd": {"verdict": "HIT", "pct_diff": 0.02, "delta": 0.02},
        "directive_ratio": {"verdict": "HIT", "pct_diff": 0.02, "delta": 0.02},
    }


def test_skipped_produces_na_not_off_in_evidence_sentence():
    evidence = voice_match_label(_delta("SKIPPED"))["evidence"]
    assert "N/A on ownership" in evidence
    assert "Off on ownership" not in evidence


def test_missed_still_produces_off_unaffected():
    evidence = voice_match_label(_delta("MISSED"))["evidence"]
    assert "Off on ownership" in evidence


def test_skipped_excluded_from_biggest_changes():
    report = build_voice_report(_delta("SKIPPED"), {"semantic_match": 0.96}, "Low", "High")
    assert not any("ownership" in c for c in report["biggest_changes"])


def test_missed_still_appears_in_biggest_changes():
    report = build_voice_report(_delta("MISSED"), {"semantic_match": 0.96}, "Low", "High")
    assert any("ownership" in c for c in report["biggest_changes"])


def test_skipped_does_not_count_toward_missed_risk():
    delta = _delta("SKIPPED")
    semantic = {"semantic_match": 1.0}
    ai_tells = {"clean": True}
    insertion_check = {"flagged": False}
    missed_count = sum(1 for d in delta.values() if d["verdict"] == "MISSED")
    assert missed_count == 0
