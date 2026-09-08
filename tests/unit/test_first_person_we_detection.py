"""
Tests for voice_engine.compute_baseline_metrics' first_person_ratio
regex — specifically the 8 Sept 2026 fix closing the "we vs I" gap
flagged (and deliberately deferred) on 7 Sept 2026.

Direct motivation: the detector only ever matched "I" forms
(I/I'm/I've/I'd/I'll/my/mine/myself) — "we/our/ours/ourselves" were
entirely invisible to this dimension, even though they're
grammatically first-person (uncontested — Scribbr et al.) and common
in exactly the professional/business voice VOICOVA's actual target
market (ghostwriters, founders, consultants) writes in. Confirmed
against a real calibration-style example (the narrative_storyteller
test persona, dev_tools/personas/narrative_storyteller.json): its
authentic mixed I/we voice scored first_person_ratio 0.333 (3/9
sentences) before this fix and 0.556 (5/9) after — two genuinely
self-referential "we" sentences ("we're three days from launch",
"the time we shipped the wrong build... how we fix this one") were
being silently missed.

test_narrative_storyteller_persona_gap_closed below is the direct
regression guard for that real gap. The false-positive tests guard
the other direction — the \b-anchored, trailing-space convention
already used for "my" must not start matching "hour", "your",
"flavour", "colour", "however", or "Andrew's" once "our"/"ours" join
the same alternation.

Deliberately NOT covered here: any attempt to distinguish exclusive
"we" (genuine self-reference) from inclusive "we" (writer+reader) or
distancing/corporate "we" (diffusing personal accountability) — see
this fix's own comment in voice_engine.py for why that's out of scope
by design, not an oversight. Also deliberately not covered: bare
object-case "us" (e.g. "the bug found us") — excluded for the same
reason bare "me" was never matched in the "I" bucket either;
structural parity, not an omission.
"""
import voice_engine as ve


def test_narrative_storyteller_persona_gap_closed():
    """The real calibration-style example that motivated this fix."""
    sample1 = (
        "So picture this. It's 11pm, we're three days from launch, and the "
        "payment integration just silently stops working with zero error logs. "
        "No stack trace, nothing. Turned out to be a timezone mismatch in a "
        "config file someone had touched eight months earlier. Found it by "
        "accident, scrolling through git blame at 2am out of pure desperation."
    )
    completions = [
        "So this landed and honestly, it's not the brief, not even close, "
        "reminds me of the time we shipped the wrong build entirely, let me "
        "tell you how we fix this one.",
        "I basically spend my days chasing down the one weird bug that's "
        "breaking everything else, and occasionally I find it at 2am.",
        "I've decided we're delaying launch a week, I know how that sounds, "
        "but I've been burned by rushing before and I'm not doing it again.",
        "When someone says a bug 'should be quick to fix' without looking at "
        "it first, that's the moment I brace myself.",
    ]
    combined = sample1 + " " + " ".join(completions)

    metrics = ve.compute_baseline_metrics(combined)

    assert metrics["first_person_sentence_count"] == 5, (
        "Expected 5 first-person sentences (3 'I'-only + 2 'we'-only) — "
        f"got {metrics['first_person_sentence_count']}"
    )
    assert metrics["first_person_ratio"] == 0.556


def test_genuine_we_forms_are_detected():
    cases = [
        "We shipped the release on time.",
        "Our approach worked well.",
        "We built this ourselves.",
        "We're proud of ours.",
        "We've been over this before.",
        "We'd already flagged the risk.",
        "We'll ship it Friday.",
    ]
    for text in cases:
        metrics = ve.compute_baseline_metrics(text)
        assert metrics["first_person_ratio"] == 1.0, f"Expected {text!r} to be detected as first-person"


def test_existing_i_forms_still_detected_unchanged():
    """Regression guard: the fix must not disturb the existing 'I' bucket."""
    cases = [
        "I shipped the release on time.",
        "My approach worked well.",
        "I built this myself.",
        "That copy is mine.",
        "I've been over this before.",
        "I'd already flagged the risk.",
        "I'll ship it Friday.",
    ]
    for text in cases:
        metrics = ve.compute_baseline_metrics(text)
        assert metrics["first_person_ratio"] == 1.0, f"Expected {text!r} to still be detected as first-person"


def test_second_person_and_lookalike_words_not_falsely_flagged():
    """Guards the \\b + trailing-space convention against the specific
    words that share a substring with 'our'/'ours'/'we' once those join
    the alternation — 'hour', 'your', 'flavour', 'colour', 'however',
    and a possessive name ending in a similar sequence."""
    cases = [
        "An hour passed quietly.",
        "Your report is due Friday.",
        "The flavour was excellent, colour perfect.",
        "However, the numbers held.",
        "Andrew's report was late.",
        "The bug hours later resurfaced.",
        "This wedding was lovely.",  # contains "wed", not "we "
    ]
    for text in cases:
        metrics = ve.compute_baseline_metrics(text)
        assert metrics["first_person_ratio"] == 0.0, f"Expected {text!r} to NOT be flagged first-person"


def test_bare_object_case_us_not_matched_matching_existing_me_asymmetry():
    """Deliberate scope boundary, not a bug: bare object-case 'us' is
    excluded the same way bare object-case 'me' was already excluded
    from the 'I' bucket before this fix — structural parity."""
    text = "The outage really hurt us this quarter."
    metrics = ve.compute_baseline_metrics(text)
    assert metrics["first_person_ratio"] == 0.0

    text_me = "The client thanked me for the fast turnaround."
    metrics_me = ve.compute_baseline_metrics(text_me)
    assert metrics_me["first_person_ratio"] == 0.0


def test_mixed_i_and_we_sentence_counts_once_not_twice():
    """A sentence containing both 'I' and 'we' markers is still one
    first-person sentence, not double-counted — matches the existing
    sentence-level (not occurrence-level) definition of this ratio."""
    text = "I've decided we're delaying launch, and I stand by that call."
    metrics = ve.compute_baseline_metrics(text)
    assert metrics["first_person_sentence_count"] == 1
    assert metrics["first_person_ratio"] == 1.0
