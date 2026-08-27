"""
Regression guard for the sentence-fragment bug found in a live render
(27 Aug 2026): prompts.py's rule 9 told the model it could split a long
sentence "to match sentence-rhythm targets" with no constraint on
where, so it split at an appositive/parenthetical comma instead of a
coordinating-conjunction one, leaving a subject-less fragment behind -
"The scenario you described, dashboard green while an agent quietly
does the wrong thing, is exactly what CLEARANCE is built to catch."
came back as three pieces, the first and third of which are not
complete sentences on their own.

Rule 9 was tightened to name the failure mode directly - see that
commit - but a prompt instruction is not a guarantee of model
behaviour, which is exactly why this exists as a second, independent,
deterministic layer: no test in the existing suite exercised real
model output at all (every test either mocks the Anthropic call or
tests deterministic code), so nothing could have caught a bug that
only a live render produced. This closes that specific gap for this
specific failure shape - it does not replace a live-render smoke test
for grammaticality in general, which no purely deterministic check
can give.

_detect_sentence_fragments / score_ai_tells' fragment fields
(voice_engine.py) are the implementation under test.
"""
from voice_engine import score_ai_tells, _detect_sentence_fragments


# Verbatim from the real broken render that prompted this fix.
BROKEN_RENDER = (
    "Scott, following up on the CLEARANCE test link from a while back. "
    "Curious if you got a chance to run it or if it fell off the desk "
    "with everything going on.\n"
    "Timing feels right off the back of your Workflow Agent Manager "
    "post. The scenario you described. Dashboard green while an agent "
    "quietly does the wrong thing. Is exactly what CLEARANCE is built "
    "to catch. It is the deterministic proof layer underneath the "
    "governance moat point from our earlier thread."
)

# The same content, correctly punctuated - what the render should have
# produced.
CORRECT_RENDER = (
    "Scott, following up on the CLEARANCE test link from a while back. "
    "Curious if you got a chance to run it or if it fell off the desk "
    "with everything going on.\n"
    "Timing feels right off the back of your Workflow Agent Manager "
    "post. The scenario you described, dashboard green while an agent "
    "quietly does the wrong thing, is exactly what CLEARANCE is built "
    "to catch. It's the deterministic proof layer underneath the "
    "governance moat point from our earlier thread."
)


def test_detects_the_real_fragment_that_shipped():
    fragments = _detect_sentence_fragments(BROKEN_RENDER)
    assert fragments == ["Is exactly what CLEARANCE is built to catch."]


def test_score_ai_tells_flags_the_broken_render_as_not_clean():
    result = score_ai_tells(BROKEN_RENDER)
    assert result["clean"] is False
    assert result["fragment_count"] == 1
    assert "Is exactly what CLEARANCE is built to catch." in result["flagged_fragments"]
    assert any("fragment" in f.lower() for f in result["flagged"])


def test_correctly_punctuated_version_is_clean():
    """The fix (rule 9) turns the broken render back into this - confirms
    the guardrail doesn't flag correct output, not just that it flags
    broken output."""
    result = score_ai_tells(CORRECT_RENDER)
    assert result["clean"] is True
    assert result["fragment_count"] == 0
    assert result["flagged_fragments"] == []


def test_genuine_question_is_not_a_false_positive():
    """A bare-auxiliary opener ending in '?' is ordinary, grammatical
    English - only a '.'/'!' ending signals a severed fragment."""
    text = "Is this the right call? I think so. Was it worth the wait? Absolutely."
    fragments = _detect_sentence_fragments(text)
    assert fragments == []


def test_normal_prose_without_bare_aux_openers_is_not_flagged():
    text = (
        "We shipped the fix this afternoon. It closes the gap the "
        "review flagged, and the full suite is green. Worth a look "
        "when you have a minute."
    )
    fragments = _detect_sentence_fragments(text)
    assert fragments == []


def test_fragment_hits_do_not_suppress_other_ai_tell_flags():
    """Fragments are additive to the existing checks, not a replacement
    for them - a render with both an em dash and a fragment should
    surface both, not just one."""
    text = "This changes everything \u2014 completely. Is exactly the proof we needed."
    result = score_ai_tells(text)
    assert result["clean"] is False
    assert result["em_dash_count"] == 1
    assert result["fragment_count"] == 1
