"""
Tests for two real bugs found and fixed against a live render this
session (15 Aug 2026), same standard as everything else this session —
grounded in an actual failure, not a hypothetical.

Bug: "surface" AI-tell false positive. The analytical-tell pattern
existed to catch the AI-essay habit of pressing a noun into service as
a verb ("issues surface", "concerns surfaced") but matched regardless
of grammar — "tied to change in the agent's surface" (a genuine noun,
a real technical term) was flagged even though it's the person's own
authentic phrasing, unchanged by the render.

Gap: no deterministic correction existed for OVER-owned first person.
_fix_first_person_ratio only ever handled the opposite case (adding
"I think" when a person under-uses first person). A render added
"I am curious whether..." where the original had none at all
("Curious whether..."), and nothing caught it. _fix_first_person_
over_ratio closes that gap.
"""
import voice_engine as ve
import deterministic_fixers as df


# ------------------------------------------------------------------
# "surface" AI-tell false positive
# ------------------------------------------------------------------

def test_surface_as_genuine_noun_not_flagged():
    text = "tied to change in the agent's surface rather than to a calendar."
    assert ve._ANALYTICAL_TELL_PHRASES.search(text) is None


def test_surface_as_noun_with_different_possessive_not_flagged():
    text = "The model's surface changes as tools are added."
    assert ve._ANALYTICAL_TELL_PHRASES.search(text) is None


def test_surface_as_genuine_verb_still_flagged():
    """The fix must be narrow — the actual AI-essay habit this pattern
    exists to catch must still be caught, not swept up in the fix."""
    assert ve._ANALYTICAL_TELL_PHRASES.search("These issues surface when nobody is watching.")
    assert ve._ANALYTICAL_TELL_PHRASES.search("Concerns surfaced during the review.")


def test_surface_as_determiner_noun_not_flagged():
    """Regression guard: the original fix only excluded possessive forms
    ('s, its). A determiner-led noun usage with the same grammar ("the
    surface is...") was still a false positive — found against a real
    render, same false-positive class as the possessive case above."""
    text = "It is harder, but the surface is at least more legible."
    assert ve._ANALYTICAL_TELL_PHRASES.search(text) is None
    assert ve._ANALYTICAL_TELL_PHRASES.search("This surface needs monitoring.") is None
    assert ve._ANALYTICAL_TELL_PHRASES.search("A surface reading misses it.") is None


def test_surface_area_exclusion_still_works():
    """Pre-existing exclusion, must survive the new one."""
    assert ve._ANALYTICAL_TELL_PHRASES.search("Its surface area is large.") is None


def test_other_analytical_tells_still_caught():
    """The fix only touched the surface pattern — confirm the rest of
    this list is untouched."""
    assert ve._ANALYTICAL_TELL_PHRASES.search("I would push back on that.")
    assert ve._ANALYTICAL_TELL_PHRASES.search("This is closer to X than to Y.")
    assert ve._ANALYTICAL_TELL_PHRASES.search("I suspect that's not quite right.")


# ------------------------------------------------------------------
# Over-owned first person fixer
# ------------------------------------------------------------------

def test_catches_the_real_session_failure_case():
    """The exact real case: a render added 'I am curious' where the
    original had no first person there at all."""
    original_input = "Curious whether your clients have solved that, because the methodology is the easier half."
    render_output = "I am curious whether your clients have solved that, because the methodology is the easier half."
    fixed, changed = df._fix_first_person_over_ratio(
        render_output, target=1.0, current=8.0, original_input_text=original_input
    )
    assert changed
    assert fixed == original_input


def test_declines_when_not_over_target():
    text = "I think this works."
    fixed, changed = df._fix_first_person_over_ratio(text, target=8.0, current=1.0, original_input_text="")
    assert not changed
    assert fixed == text


def test_full_strip_openers_reduce_to_a_complete_sentence():
    """I think/I believe/I suspect/I imagine/I would say all take a
    complete independent clause as their object — stripping the whole
    opener must leave a grammatically complete sentence."""
    cases = [
        ("I think this is the right approach.", "This is the right approach."),
        ("I believe the numbers hold up.", "The numbers hold up."),
        ("I suspect that is not quite right.", "That is not quite right."),
        ("I imagine this will take a while.", "This will take a while."),
        ("I would say your approach has merit.", "Your approach has merit."),
    ]
    for text, expected in cases:
        fixed, changed = df._fix_first_person_over_ratio(
            text, target=1.0, current=8.0, original_input_text=""
        )
        assert changed, f"Expected a change for: {text!r}"
        assert fixed == expected, f"Expected {expected!r}, got {fixed!r}"


def test_partial_strip_openers_keep_the_adjective_as_a_fragment():
    """'I am curious/certain/confident/not sure/unsure' do NOT reduce
    to a bare statement the way 'I think X' does -- 'I am curious
    whether X' stripped entirely would leave 'whether X', not a
    sentence. Only 'I am ' should be stripped, keeping the adjective."""
    cases = [
        ("I am certain this will work.", "Certain this will work."),
        ("I am confident the deal closes.", "Confident the deal closes."),
        ("I am not sure whether this holds.", "Not sure whether this holds."),
        ("I am unsure if that is right.", "Unsure if that is right."),
        ("I am curious whether that changes things.", "Curious whether that changes things."),
    ]
    for text, expected in cases:
        fixed, changed = df._fix_first_person_over_ratio(
            text, target=1.0, current=8.0, original_input_text=""
        )
        assert changed, f"Expected a change for: {text!r}"
        assert fixed == expected, f"Expected {expected!r}, got {fixed!r}"


def test_does_not_strip_an_opener_genuinely_in_the_original_input():
    """The core safety check: if the person's own original input
    already had this exact opener, it's their genuine writing, not
    something the render introduced -- must be left alone."""
    original = "I think we should move fast on this deal."
    render = "I think we should move fast on this deal, given the timeline."
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert not changed
    assert fixed == render


def test_does_not_touch_quoted_material():
    text = 'I think "this is fine" was the wrong call.'
    fixed, changed = df._fix_first_person_over_ratio(
        text, target=1.0, current=8.0, original_input_text=""
    )
    assert not changed
    assert fixed == text


def test_does_not_over_block_on_unrelated_second_person_words():
    """Regression guard for a real design mistake caught before this
    shipped: an earlier version reused the UNDER-owned fixer's
    attribution check, which blocked this exact case purely because
    'your' appeared elsewhere in the sentence -- not because the
    opinion was genuinely attributed to someone else."""
    original = "Curious whether your clients have solved that."
    render = "I am curious whether your clients have solved that."
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert changed
    assert fixed == original


def test_respects_max_conversions_per_pass():
    text = (
        "I think the first point stands. I believe the second point stands too. "
        "I suspect the third point also stands here today."
    )
    fixed, changed = df._fix_first_person_over_ratio(
        text, target=1.0, current=8.0, original_input_text=""
    )
    assert changed
    # At most 2 conversions (df._MAX_CONVERSIONS_PER_PASS) -- the third
    # sentence's opener should survive untouched.
    assert "I suspect" in fixed
