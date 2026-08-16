"""
Tests for two real bugs found and fixed against a live render this
session (15 Aug 2026), not hypothetical edge cases.

Bug 1 — double punctuation: _extract_sentences unconditionally
inserted ". " at every paragraph break, even when the paragraph
already ended in terminal punctuation, producing literal "?." and
".." artifacts. Corrupted real output, not just internal
tokenisation, because every deterministic fixer rebuilds its output
text from this function's sentence list.

Bug 2 — imperative false positive: "Report in minutes, no build
required on your side" was scored as a directive/imperative sentence
purely because "Report" opens the _IMPERATIVE_VERBS list, inflating
directive_ratio and producing a wildly exaggerated percentage-drift
reading (830%) given how close to zero this dimension's baseline
typically sits for non-imperative writers.
"""
import voice_engine as ve
from prompts import _regex_sweep


# ------------------------------------------------------------------
# Bug 1 — double punctuation from paragraph-break normalisation
# ------------------------------------------------------------------

def test_no_double_period_when_paragraph_already_ends_in_period():
    text = "First paragraph ends here.\n\nSecond paragraph starts here."
    sentences = ve._extract_sentences(text)
    assert not any(".." in s for s in sentences), f"Found double period in: {sentences}"


def test_no_period_after_question_mark_across_paragraph_break():
    """The exact real-render failure case: a paragraph ending in '?'
    followed by a new paragraph must not become '?.'."""
    text = "Did you get a chance to run it, or did it get lost in the noise?\n\nThe timing feels right."
    sentences = ve._extract_sentences(text)
    assert sentences[0] == "Did you get a chance to run it, or did it get lost in the noise?"
    assert not any("?." in s for s in sentences), f"Found '?.' artifact in: {sentences}"


def test_paragraph_without_terminal_punctuation_still_gets_one():
    """A paragraph that doesn't end in terminal punctuation (a
    fragment, or an editing artifact) should still get exactly one
    period added, same as before this fix — only the DOUBLING is
    fixed, not the original normalisation behaviour."""
    text = "This paragraph has no ending punctuation\n\nSecond paragraph here."
    sentences = ve._extract_sentences(text)
    assert sentences[0].endswith("."), f"Expected a period added, got: {sentences[0]!r}"
    assert not sentences[0].endswith(".."), f"Expected exactly one period, got: {sentences[0]!r}"


def test_exclamation_mark_across_paragraph_break_not_doubled():
    text = "This is great news!\n\nLet's move forward with it."
    sentences = ve._extract_sentences(text)
    assert not any("!." in s for s in sentences), f"Found '!.' artifact in: {sentences}"


def test_real_session_render_produces_no_punctuation_artifacts():
    """Full real-render text from the session that surfaced this bug —
    permanent regression guard against the exact case, not just an
    isolated fragment."""
    render = (
        "Scott, following up on the Clearance test link I sent a while back. "
        "Did you get a chance to run it, or did it get lost in the noise?\n\n"
        "The timing feels right off the back of your Workflow Agent Manager post. "
        "The scenario you described, dashboard green whilst an agent quietly "
        "does the wrong thing, is exactly what Clearance is built to catch.\n\n"
        "Built specifically for US Financial Services: SEC, FINRA, SR 11-7, "
        "and the state AI laws now live. Report in minutes, no build required "
        "on your side."
    )
    sentences = ve._extract_sentences(render)
    for s in sentences:
        assert not any(bad in s for bad in ["?.", "!.", ".."]), (
            f"Found a punctuation artifact in sentence: {s!r}"
        )


# ------------------------------------------------------------------
# Bug 3 — quote+period double punctuation from _regex_sweep, found live
# 16 Aug 2026 (Scott/CLEARANCE render, same family as Bug 1 but a
# different site: a closing quote followed by a redundant terminal
# mark, e.g. 'here is what happens when it did not.".' — the quoted
# content already ends in '.', so the mark after the closing quote is
# always a duplicate, never a legitimate second sentence-ender.
# ------------------------------------------------------------------

def test_period_after_closing_quote_collapsed():
    text = 'Not "the agent ran" but "here is what happens when it did not.".'
    result = _regex_sweep(text)
    assert '.".' not in result
    assert result.endswith('did not."')


def test_question_mark_after_closing_quote_collapsed():
    text = 'She asked "is this working?".'
    result = _regex_sweep(text)
    assert '?".' not in result


def test_quote_without_trailing_period_untouched():
    """A quote NOT already ending in terminal punctuation must keep its
    outer period — this fix only targets the doubled case."""
    text = 'He calls it "the moat argument".'
    result = _regex_sweep(text)
    assert result.rstrip() == 'He calls it "the moat argument".'


# ------------------------------------------------------------------
# Bug 2 — "Report in minutes" imperative false positive
# ------------------------------------------------------------------

def test_report_in_minutes_not_flagged_as_imperative():
    sentences = ve._extract_sentences("Report in minutes, no build required on your side.")
    directive = ve._imperative_sentences(sentences)
    assert directive == [], f"Expected no false positive, got: {directive}"


def test_similar_noun_phrase_fragments_not_flagged():
    examples = [
        "Response in hours, not days.",
        "Delivery in days, guaranteed.",
        "Results in minutes, every time.",
    ]
    for text in examples:
        sentences = ve._extract_sentences(text)
        directive = ve._imperative_sentences(sentences)
        assert directive == [], f"Expected no false positive for: {text!r}, got: {directive}"


def test_genuine_imperatives_still_detected():
    """The fix must be narrow — real commands starting with the same
    ambiguous verbs must still be caught, not swept up in the
    exclusion."""
    examples = [
        "Fix this before the meeting.",
        "Send the report to the board.",
        "Report to your manager immediately.",
        "Check the numbers before Thursday.",
    ]
    for text in examples:
        sentences = ve._extract_sentences(text)
        directive = ve._imperative_sentences(sentences)
        assert directive, f"Expected '{text}' to still be flagged as imperative"


def test_directive_ratio_on_real_render_no_longer_inflated():
    """The real render from this session should measure zero genuine
    imperatives, not the false-positive-inflated 0.083 it scored
    before this fix."""
    render = (
        "Scott, following up on the Clearance test link I sent a while back. "
        "Did you get a chance to run it, or did it get lost in the noise?\n\n"
        "The timing feels right off the back of your Workflow Agent Manager post. "
        "The scenario you described, dashboard green whilst an agent quietly "
        "does the wrong thing, is exactly what Clearance is built to catch. "
        "It is the deterministic proof layer underneath the governance point "
        "from our earlier thread. Not the agent ran, but here is the evidence "
        "it did the right thing, and here is what happened when it did not.\n\n"
        "Built specifically for US Financial Services: SEC, FINRA, SR 11-7, "
        "and the state AI laws now live. Report in minutes, no build required "
        "on your side.\n\n"
        "If it holds up, it could be the concrete proof point Matt was pushing "
        "for on that thread. Something you might put in front of a portfolio "
        "company this week, not a framework to workshop.\n\n"
        "Worth another look? Or should I just send a fresh report so you are "
        "not chasing the old link?"
    )
    metrics = ve.compute_baseline_metrics(render)
    assert metrics["directive_ratio"] == 0.0, (
        f"Expected zero genuine imperatives in this text, got {metrics['directive_ratio']}"
    )
