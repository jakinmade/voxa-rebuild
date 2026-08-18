"""
Tests for authenticity_report.py — the exportable, tamper-evident
proof of a render's Voice Report. See that module's docstring for the
rationale (the Pangram/deBoer case, why a bare score isn't enough).
"""
import authenticity_report as ar


_SAMPLE_VOICE_REPORT = {
    "voice_match_tier": "Good",
    "semantic_match": 98,
    "confidence": "Low",
    "risk": "High",
    "ai_tell_clean": False,
    "biggest_changes": ["ownership (first person) 66%"],
    # A field NOT in _VOICE_REPORT_FIELDS, to prove it's excluded.
    "voice_match": 91,
}

_SAMPLE_BASELINE = {
    "hedge_density": 0.04,
    "sentence_length_sd": 6.2,
    "first_person_ratio": 0.18,
    "directive_ratio": 0.02,
}


def _build():
    return ar.build_authenticity_report(
        _SAMPLE_VOICE_REPORT, _SAMPLE_BASELINE,
        render_id="11111111-1111-1111-1111-111111111111",
        created_at="2026-08-18T14:00:00Z",
        scoring_rules_version="1.1.0",
    )


def test_build_includes_expected_fields():
    report = _build()
    assert report["render_id"] == "11111111-1111-1111-1111-111111111111"
    assert report["created_at"] == "2026-08-18T14:00:00Z"
    assert report["scoring_rules_version"] == "1.1.0"
    assert report["voice_match_tier"] == "Good"
    assert report["semantic_match"] == 98
    assert report["confidence"] == "Low"
    assert report["risk"] == "High"
    assert report["ai_tell_clean"] is False
    assert report["biggest_changes"] == ["ownership (first person) 66%"]


def test_build_excludes_fields_not_in_the_allowlist():
    """voice_match (the raw internal percentage) is deliberately not
    surfaced in build_voice_report's public-facing tier/badge either —
    this report must not reintroduce it via a stray **kwargs-style copy."""
    report = _build()
    assert "voice_match" not in report


def test_baseline_is_hashed_not_included_raw():
    report = _build()
    assert "baseline_hash" in report
    assert report["baseline_hash"] != ""
    for key in _SAMPLE_BASELINE:
        assert key not in report


def test_same_baseline_produces_same_hash():
    h1 = ar.compute_baseline_hash(_SAMPLE_BASELINE)
    h2 = ar.compute_baseline_hash(dict(_SAMPLE_BASELINE))
    assert h1 == h2


def test_different_baseline_produces_different_hash():
    other = dict(_SAMPLE_BASELINE)
    other["hedge_density"] = 0.09
    assert ar.compute_baseline_hash(_SAMPLE_BASELINE) != ar.compute_baseline_hash(other)


def test_empty_baseline_returns_empty_hash():
    assert ar.compute_baseline_hash(None) == ""
    assert ar.compute_baseline_hash({}) == ""


def test_verify_passes_on_untouched_report():
    report = _build()
    assert ar.verify_authenticity_report(report) is True


def test_verify_fails_if_a_score_is_edited_after_export():
    report = _build()
    report["risk"] = "Low"  # tampering — softening a High risk verdict
    assert ar.verify_authenticity_report(report) is False


def test_verify_fails_if_semantic_match_is_edited():
    report = _build()
    report["semantic_match"] = 100
    assert ar.verify_authenticity_report(report) is False


def test_verify_fails_on_missing_hash():
    report = _build()
    del report["integrity_hash"]
    assert ar.verify_authenticity_report(report) is False


def test_verify_fails_on_non_dict_input():
    assert ar.verify_authenticity_report(None) is False
    assert ar.verify_authenticity_report("not a report") is False
    assert ar.verify_authenticity_report([]) is False


def test_two_reports_from_the_same_render_are_identical():
    """Determinism: same inputs, same output, every time — no
    timestamp-of-generation or randomness leaking into the payload
    beyond what the caller explicitly passed in."""
    r1 = _build()
    r2 = _build()
    assert r1 == r2


def test_export_json_round_trips_through_verify():
    import json
    report = _build()
    exported = ar.export_authenticity_report_json(report)
    reloaded = json.loads(exported)
    assert ar.verify_authenticity_report(reloaded) is True


def test_missing_voice_report_fields_become_none_not_a_crash():
    report = ar.build_authenticity_report(
        {}, _SAMPLE_BASELINE,
        render_id="r2", created_at="2026-08-18T14:00:00Z",
        scoring_rules_version="1.1.0",
    )
    assert report["voice_match_tier"] is None
    assert report["semantic_match"] is None
    assert ar.verify_authenticity_report(report) is True
