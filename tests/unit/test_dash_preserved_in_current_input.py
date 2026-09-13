"""
Regression test — 13 Sept 2026 real-render bug: keep_dashes required
2+ em-dash occurrences across calibration+current input combined
before protecting a dash from being stripped/converted, even when the
dash is already present in the actual text being rewritten. Real
case: "Scott — following up..." (one em dash, calibration had none)
had its dash converted to a comma ("Scott, following up...").

keep_contractions already correctly ORs corpus-alone with input-alone
evidence (see render_pipeline.py); keep_dashes did not — this fix
brought it into line.

SUPERSEDED, same session, later that night: an explicit user
statement ("we don't use em dashes") overrode the whole
preserve-if-present mechanism this file originally tested. render_
pipeline.py now hardcodes keep_dashes = False unconditionally — the
OR-structure formula this test asserted is no longer live code (it
now only exists in this old docstring for history). See
tests/unit/test_em_dashes_never_preserved.py for the current,
authoritative behaviour. test_render_pipeline_keep_dashes_true_for_
real_case below is kept only as a documented historical marker, not a
regression guard — it reimplements a formula that no longer runs in
production, so it passing again is not evidence dashes are preserved
live; test_em_dashes_never_preserved.py's tests, which call the real
_build_voice_dna/_split_dashes_deterministic code paths, are what to
trust for that question.
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
    # This asserts uses_em_dashes' own general-purpose behaviour, which
    # is unchanged and still correct as a standalone utility — it's the
    # CALL SITE in render_pipeline.py that now ignores this result
    # unconditionally (see module docstring above), not this function.
    assert uses_em_dashes(REAL_INPUT_ONE_DASH, min_count=1)


def test_render_pipeline_no_longer_uses_this_formula_live():
    """Historical marker only (see module docstring) — confirms
    render_pipeline.py's actual source now hardcodes keep_dashes to
    False, rather than re-testing the superseded OR-formula in
    isolation the way this file previously did (which kept passing
    even after that formula stopped being live code — a real gap this
    replacement closes)."""
    import inspect
    import render_pipeline
    source = inspect.getsource(render_pipeline)
    assert "keep_dashes = False" in source


def test_corpus_only_single_dash_still_not_enough_on_its_own():
    # Unchanged behaviour: calibration corpus with just one dash and no
    # dash anywhere in current input should NOT be treated as a
    # confirmed habit (the min_count=2 habit-bar is intentionally kept
    # for corpus-only evidence). Also now moot in practice per the
    # override above, but the underlying utility's behaviour is still
    # correct and worth locking in.
    corpus_one_dash = "I went to the shop — it was closed."
    input_no_dash = "Following up on the report from last week."
    assert not uses_em_dashes(corpus_one_dash, min_count=2)
    assert not uses_em_dashes(input_no_dash, min_count=1)
