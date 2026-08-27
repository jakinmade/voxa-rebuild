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


# ---------------------------------------------------------------------
# export_authenticity_report_pdf (27 Aug 2026) — branded one-pager for
# the agency/client-deliverable use case. Beyond "it doesn't crash":
# these extract real text from the generated PDF bytes and check it
# actually contains the report's values, and specifically regression-
# guard the color-polarity bug caught during development (Confidence
# is inverted from Risk — High is good for one, bad for the other; a
# shared High/Medium/Low->color map gets this backwards for one of
# them, confirmed by generating and visually inspecting an actual
# rendered PDF before this test existed).
# ---------------------------------------------------------------------

def _pdf_text(pdf_bytes: bytes) -> str:
    from io import BytesIO
    import pdfplumber
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def test_pdf_export_produces_valid_pdf_bytes():
    report = _build()
    pdf_bytes = ar.export_authenticity_report_pdf(report)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 500


def test_pdf_export_contains_the_actual_report_values():
    report = _build()
    text = _pdf_text(ar.export_authenticity_report_pdf(report))
    assert "Good" in text  # voice_match_tier
    assert "98" in text  # semantic_match
    assert "Low" in text  # confidence
    assert "High" in text  # risk
    assert "Flagged" in text  # ai_tell_clean is False
    assert "ownership (first person) 66%" in text  # biggest_changes
    assert report["render_id"] in text
    assert report["baseline_hash"] in text
    assert report["integrity_hash"] in text


def test_pdf_export_handles_no_biggest_changes():
    """A clean render with nothing flagged has an empty biggest_changes
    list — the PDF must render without a 'What changed' section, not
    crash on an empty list."""
    report = ar.build_authenticity_report(
        {**_SAMPLE_VOICE_REPORT, "biggest_changes": []}, _SAMPLE_BASELINE,
        render_id="r3", created_at="2026-08-18T14:00:00Z",
        scoring_rules_version="1.1.0",
    )
    pdf_bytes = ar.export_authenticity_report_pdf(report)
    text = _pdf_text(pdf_bytes)
    assert "WHAT CHANGED" not in text


def test_pdf_export_handles_missing_fields_gracefully():
    """Same missing-fields case as
    test_missing_voice_report_fields_become_none_not_a_crash above,
    but for the PDF path specifically — None values must render as
    'n/a' text, not crash reportlab on a None where it expects a str."""
    report = ar.build_authenticity_report(
        {}, _SAMPLE_BASELINE,
        render_id="r4", created_at="2026-08-18T14:00:00Z",
        scoring_rules_version="1.1.0",
    )
    pdf_bytes = ar.export_authenticity_report_pdf(report)
    assert pdf_bytes.startswith(b"%PDF-")


def test_pdf_export_risk_and_confidence_are_not_the_same_polarity():
    """Regression guard for the bug caught during development: Risk
    and Confidence are inverted (High risk is bad, High confidence is
    good) - a shared tier->color map gets one of them backwards. Can't
    directly assert on rendered color from extracted text, so this
    confirms the two color maps genuinely differ for the same key
    rather than accidentally being the same dict/reference."""
    assert ar._PDF_RISK_COLOR["High"] != ar._PDF_CONFIDENCE_COLOR["High"]
    assert ar._PDF_RISK_COLOR["Low"] != ar._PDF_CONFIDENCE_COLOR["Low"]
    assert ar._PDF_RISK_COLOR["High"] == ar._PDF_DANGER
    assert ar._PDF_CONFIDENCE_COLOR["High"] == ar._PDF_SUCCESS


def test_pdf_export_voice_match_tier_uses_real_tier_values():
    """Regression guard: voice_match_tier is never 'High'/'Medium'/
    'Low' in production (see voice_match_label(), voice_engine.py) -
    it's Strong/Good/Developing/Limited. A color map keyed on the
    wrong value set silently falls through to a default color for
    every real render. Confirms all four real values are covered."""
    for tier in ("Strong", "Good", "Developing", "Limited"):
        assert tier in ar._PDF_VOICE_MATCH_COLOR, (
            f"{tier!r} is a real voice_match_tier value with no color mapped"
        )
