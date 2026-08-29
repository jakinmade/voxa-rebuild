"""
Tests for build_voice_profile_markdown (app.py, added 29 Aug 2026) —
the readable, exportable Voice Profile document. Reformats data
already computed elsewhere (observations, confidence,
baseline_fingerprint, dimension_stability) into Markdown; adds no new
extraction or detection logic of its own, so these tests check
formatting and graceful handling of missing data, not measurement
correctness (that's voice_engine.py's own test coverage).
"""
from app import build_voice_profile_markdown


OBSERVATIONS = [
    {"headline": "You open with the point, not the setup.",
     "body": 'e.g. "We should move fast on this."'},
    {"headline": "You keep sentences short under pressure.",
     "body": 'e.g. "Focus on the core problem first."'},
]

BASELINE = {
    "hedge_density": 2.1,
    "sentence_length_sd": 4.3,
    "first_person_ratio": 0.6,
    "directive_ratio": 0.2,
    "word_count": 140,
}

STABILITY = {
    "dimensions": {
        "hedge_density": "stable",
        "sentence_length_sd": "volatile",
        "first_person_ratio": "stable",
        "directive_ratio": "insufficient_data",
    },
    "stable_count": 2,
    "volatile_count": 1,
    "sample_count": 2,
}


def test_includes_confidence_and_metadata():
    doc = build_voice_profile_markdown(
        observations=OBSERVATIONS, confidence="Medium",
        baseline_fingerprint=BASELINE, dimension_stability=STABILITY,
        cumulative_words=140, cumulative_docs=2, updated_at="28 August 2026, 14:00",
    )
    assert "**Confidence:** Medium" in doc
    assert "140 words across 2 documents" in doc
    assert "28 August 2026, 14:00" in doc
    assert "Scoring rules version:" in doc


def test_includes_observation_headlines_and_evidence():
    doc = build_voice_profile_markdown(
        observations=OBSERVATIONS, confidence="Medium",
        baseline_fingerprint=BASELINE, dimension_stability=STABILITY,
        cumulative_words=140, cumulative_docs=2, updated_at=None,
    )
    assert "You open with the point, not the setup." in doc
    assert '"We should move fast on this."' in doc


def test_stability_table_uses_shared_dimension_labels():
    # Same _VOICE_MATCH_LABELS dict the render-time Voice Report table
    # already uses - labels must match exactly, not a re-derived name.
    doc = build_voice_profile_markdown(
        observations=[], confidence="High",
        baseline_fingerprint=BASELINE, dimension_stability=STABILITY,
        cumulative_words=140, cumulative_docs=2, updated_at=None,
    )
    assert "| Hedging | Stable" in doc
    assert "| Sentence rhythm | Varies by register |" in doc
    assert "| Ownership (first person) | Stable" in doc
    assert "| Directness | Not enough samples yet |" in doc


def test_baseline_metrics_table_present():
    doc = build_voice_profile_markdown(
        observations=[], confidence="High",
        baseline_fingerprint=BASELINE, dimension_stability=None,
        cumulative_words=140, cumulative_docs=2, updated_at=None,
    )
    assert "## Baseline metrics" in doc
    assert "| Hedging | 2.1 |" in doc


def test_closing_not_editable_note_always_present():
    doc = build_voice_profile_markdown(
        observations=[], confidence=None,
        baseline_fingerprint=None, dimension_stability=None,
        cumulative_words=0, cumulative_docs=0, updated_at=None,
    )
    assert "not a set of" in doc
    assert "instructions" in doc


def test_handles_all_missing_data_without_raising():
    # No profile at all - must not crash, same defensive standard as
    # screen_my_voice's own empty-state handling.
    doc = build_voice_profile_markdown(
        observations=[], confidence=None,
        baseline_fingerprint=None, dimension_stability=None,
        cumulative_words=0, cumulative_docs=0, updated_at=None,
    )
    assert doc.startswith("# Your Voice Profile")


def test_dimension_stability_with_insufficient_data_only_still_renders():
    # compute_dimension_stability's own single-sample shape: every
    # dimension "insufficient_data", stable_count/volatile_count both 0.
    single_sample_stability = {
        "dimensions": {d: "insufficient_data" for d in
                       ("hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio")},
        "stable_count": 0, "volatile_count": 0, "sample_count": 1,
    }
    doc = build_voice_profile_markdown(
        observations=[], confidence="Low",
        baseline_fingerprint=BASELINE, dimension_stability=single_sample_stability,
        cumulative_words=60, cumulative_docs=1, updated_at=None,
    )
    assert doc.count("Not enough samples yet") == 4


def test_singular_document_word_used_for_one_doc():
    doc = build_voice_profile_markdown(
        observations=[], confidence="Low",
        baseline_fingerprint=None, dimension_stability=None,
        cumulative_words=60, cumulative_docs=1, updated_at=None,
    )
    assert "1 document" in doc
    assert "1 documents" not in doc
