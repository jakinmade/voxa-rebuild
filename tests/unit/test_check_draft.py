"""
Tests for voice_engine.score_draft_check — the compare-only "check a
draft against my voice" function added 26 Aug 2026 (v1 fast-track item
from the Voice QA enterprise angle: prove scoring-without-rewriting
works end to end before building any multi-client/agency layer on top
of it).

score_draft_check is deliberately a thin composition of three already-
tested functions (score_render_delta, voice_match_label, score_ai_tells)
plus compute_burrows_delta — these tests are less about re-proving those
functions individually (see test_burrows_delta.py, test_voice_match_
table.py, test_ai_tell_original_input_exemption.py for that) and more
about the composition itself: the verdict field, the shape of the
returned dict, and the "no baseline_texts supplied" and "insufficient
baseline_texts" edge cases a caller (the new Streamlit screen) actually
hits.
"""
import voice_engine as ve


DIRECT_BASELINE_SAMPLES = [
    "I reviewed the deck last night. It holds up. I want to send this "
    "to the board today, not next week.",
    "I checked the numbers myself. They are solid. Let's ship this "
    "now rather than waiting on another round of review.",
]

HEDGY_DRAFT = (
    "It could perhaps be argued that, in certain circumstances, "
    "further review might potentially be advisable before any "
    "materials are sent to stakeholders, depending on various "
    "factors that may or may not apply here."
)

MATCHING_DRAFT = (
    "I looked at the figures again this morning. They hold up. "
    "I think we should send this to the board today rather than "
    "wait for another pass."
)


def _baseline():
    baseline = None
    for sample in DIRECT_BASELINE_SAMPLES:
        baseline = ve._merge_baseline(baseline, ve.compute_baseline_metrics(sample))
    return baseline


def test_returns_expected_keys():
    result = ve.score_draft_check(_baseline(), MATCHING_DRAFT)
    for key in (
        "verdict", "tier", "badge", "match_pct", "evidence",
        "delta", "ai_tells_clean", "ai_tells_flagged", "burrows_delta",
    ):
        assert key in result


def test_verdict_is_pass_or_review_only():
    result = ve.score_draft_check(_baseline(), MATCHING_DRAFT)
    assert result["verdict"] in ("PASS", "REVIEW")


def test_verdict_tracks_the_green_badge_exactly():
    """verdict must never disagree with voice_match_label's own badge —
    this is the one invariant the whole function exists to preserve, so
    Screen 4's Voice Report and this compare-only check can never
    silently define "matches" two different ways."""
    for text in (MATCHING_DRAFT, HEDGY_DRAFT, "Short."):
        result = ve.score_draft_check(_baseline(), text)
        delta = ve.score_render_delta(_baseline(), text)
        match = ve.voice_match_label(delta)
        expected = "PASS" if match["badge"] == "badge-green" else "REVIEW"
        assert result["verdict"] == expected


def test_a_clearly_drifting_draft_does_not_pass():
    result = ve.score_draft_check(_baseline(), HEDGY_DRAFT)
    assert result["verdict"] == "REVIEW"


def test_missing_baseline_texts_reports_insufficient_not_a_crash():
    """No baseline_texts arg at all (the common case — most callers
    won't always have fingerprint_sample_texts on hand) must degrade
    gracefully, matching compute_burrows_delta's own contract."""
    result = ve.score_draft_check(_baseline(), MATCHING_DRAFT)
    assert result["burrows_delta"]["tier"] == "Insufficient baseline samples"
    assert result["burrows_delta"]["delta"] is None


def test_single_baseline_text_also_reports_insufficient():
    result = ve.score_draft_check(
        _baseline(), MATCHING_DRAFT, baseline_texts=[DIRECT_BASELINE_SAMPLES[0]],
    )
    assert result["burrows_delta"]["tier"] == "Insufficient baseline samples"


def test_two_or_more_baseline_texts_produces_a_real_tier():
    result = ve.score_draft_check(
        _baseline(), MATCHING_DRAFT, baseline_texts=DIRECT_BASELINE_SAMPLES,
    )
    assert result["burrows_delta"]["tier"] in ("Close", "Moderate", "Wide")


def test_no_llm_call_involved():
    """This is the entire point of the feature (compare-only, no
    rewrite): confirm score_draft_check's source does not reference
    anything that would make an Anthropic API call."""
    import inspect
    source = inspect.getsource(ve.score_draft_check)
    assert "anthropic" not in source.lower()
    assert "api_key" not in source.lower()


def test_ai_tells_field_matches_score_ai_tells_directly():
    result = ve.score_draft_check(_baseline(), HEDGY_DRAFT)
    direct = ve.score_ai_tells(HEDGY_DRAFT)
    assert result["ai_tells_clean"] == direct["clean"]
    assert result["ai_tells_flagged"] == direct["flagged"]
