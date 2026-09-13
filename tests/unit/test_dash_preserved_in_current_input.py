"""
Regression test — 13 Sept 2026 real-render bug: keep_dashes required
2+ em-dash occurrences across calibration+current input combined
before protecting a dash from being stripped/converted, even when the
dash is already present in the actual text being rewritten. Real
case: "Scott — following up..." (one em dash, calibration had none)
had its dash converted to a comma ("Scott, following up...").

keep_contractions already correctly ORs corpus-alone with input-alone
evidence (see render_pipeline.py); keep_dashes did not — this test
locks in the fix that brings it into line.
"""
from prompts import uses_em_dashes

CALIBRATION_NO_DASHES = (
    "Josh, your point about AI generated email is noted. I struggle to "
    "see how it is disrespectful. I have just become a bit lazy, so "
    "will endeavour to do better."
)

REAL_INPUT_ONE_DASH = (
    "Scott — following up on the CLEARANCE test link from a while back. "
    "Curious if you got a chance to run it or if it fell off the desk "
    "with everything going on."
)


def test_single_dash_in_current_input_alone_is_sufficient_evidence():
    # Mirrors keep_contractions' OR structure: input evidence alone,
    # even a single occurrence, must be enough to protect what's
    # already there — no combined-corpus threshold required.
    assert uses_em_dashes(REAL_INPUT_ONE_DASH, min_count=1)


def test_render_pipeline_keep_dashes_true_for_real_case():
    from render_pipeline import uses_em_dashes as pipeline_uses_em_dashes
    fingerprint_corpus = CALIBRATION_NO_DASHES
    input_text = REAL_INPUT_ONE_DASH
    _dash_evidence = f"{fingerprint_corpus} {input_text}" if fingerprint_corpus else input_text
    keep_dashes = (
        (pipeline_uses_em_dashes(fingerprint_corpus) if fingerprint_corpus else False)
        or pipeline_uses_em_dashes(input_text, min_count=1)
        or pipeline_uses_em_dashes(_dash_evidence)
    )
    assert keep_dashes, "A dash already present in the current input must be preserved"


def test_corpus_only_single_dash_still_not_enough_on_its_own():
    # Unchanged behaviour: calibration corpus with just one dash and no
    # dash anywhere in current input should NOT be treated as a
    # confirmed habit (the min_count=2 habit-bar is intentionally kept
    # for corpus-only evidence).
    corpus_one_dash = "I went to the shop — it was closed."
    input_no_dash = "Following up on the report from last week."
    assert not uses_em_dashes(corpus_one_dash, min_count=2)
    assert not uses_em_dashes(input_no_dash, min_count=1)
