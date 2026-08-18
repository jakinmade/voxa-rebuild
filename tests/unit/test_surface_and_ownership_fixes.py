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
    """I think/I believe/I suspect/I imagine/I would say/I find all
    take a complete independent clause as their object — stripping the
    whole opener must leave a grammatically complete sentence."""
    cases = [
        ("I think this is the right approach.", "This is the right approach."),
        ("I believe the numbers hold up.", "The numbers hold up."),
        ("I suspect that is not quite right.", "That is not quite right."),
        ("I imagine this will take a while.", "This will take a while."),
        ("I would say your approach has merit.", "Your approach has merit."),
        ("I find nobody catches it through monitoring.", "Nobody catches it through monitoring."),
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


# ------------------------------------------------------------------
# Mid-sentence "I think" injection — found live 18 Aug 2026,
# alongside the "I find"/"I see" cases above, in the same render.
# Genuinely different shape from every case above: "I think" inserted
# as a parenthetical AFTER a fronted phrase, not at the sentence's
# start, so the sentence-initial FULL_STRIP regex never sees it.
# ------------------------------------------------------------------

def test_catches_mid_sentence_i_think_after_a_fronted_wh_clause():
    original = "What nobody has done is extend that from models that predict to models that act."
    render = "What I think nobody has done is extend that from models that predict to models that act."
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert changed
    assert fixed == original


def test_catches_mid_sentence_i_think_after_a_fronted_adverbial():
    original = "In most organisations qualification would fall down the same gap the agents themselves fall down."
    render = "In most organisations I think qualification would fall down the same gap the agents themselves fall down."
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert changed
    assert fixed == original


def test_declines_i_see_with_a_bare_noun_phrase_object():
    """'see' takes a bare noun phrase here, not a clause -- stripping
    'I see ' would leave 'four reasons not to fold it into governance',
    a sentence fragment with no verb. Must decline rather than ship a
    broken sentence, unlike the FULL_STRIP group where the object is
    always a complete clause."""
    text = "I see four reasons not to fold it into governance."
    fixed, changed = df._fix_first_person_over_ratio(
        text, target=1.0, current=8.0, original_input_text=""
    )
    assert not changed
    assert fixed == text


def test_mid_sentence_check_does_not_block_on_unrelated_genuine_i_think():
    """The bug this session actually found: a document-wide 'does
    "i think" appear ANYWHERE in the original' check wrongly blocked
    fixing a later, unrelated sentence just because the person's own
    OPENING line genuinely said 'I think you have found the gap' --
    real text, real failure, confirmed against the live render before
    fixing. Sentence-level alignment (word overlap against the actual
    corresponding original sentence) must not carry that false block
    across to a different sentence with no such content originally."""
    original = (
        "Distinct stage, and I think you have found the gap rather than a subdivision of one. "
        "What nobody has done is extend that from models that predict to models that act."
    )
    render = (
        "Distinct stage, and I think you have found the gap rather than a subdivision of one. "
        "What I think nobody has done is extend that from models that predict to models that act."
    )
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert changed
    assert "What I think nobody" not in fixed
    assert "What nobody has done" in fixed
    # The genuine, unrelated first-person sentence must survive untouched.
    assert "I think you have found the gap" in fixed


def test_mid_sentence_check_still_declines_when_the_aligned_original_sentence_itself_has_it():
    """Companion to the case above: when the SAME sentence (not a
    different one elsewhere) genuinely had 'I think' in the original,
    the aligned check must still catch that and decline -- sentence-
    level alignment must not become permissive across the board just
    because it stopped being wrongly document-wide."""
    original = "Distinct stage, and I think you have found the gap rather than a subdivision of one."
    render = "Distinct stage, and I think you have found the gap rather than a subdivision of one, given everything."
    fixed, changed = df._fix_first_person_over_ratio(
        render, target=1.0, current=8.0, original_input_text=original
    )
    assert not changed
    assert fixed == render


def test_full_pipeline_two_pass_simulation_reaches_hit():
    """End-to-end simulation of app.py's two call sites (initial
    deterministic pass, then the post-correction still_missed retry)
    against the real 18 Aug 2026 render this fix was built against,
    verbatim — not a shortened paraphrase. Kept verbatim deliberately:
    a shorter synthetic version was tried first and stayed MISSED,
    because the one sentence this fixer correctly declines to touch
    ("I see four reasons...", see test_declines_i_see_with_a_bare_
    noun_phrase_object above) carries proportionally more weight in a
    short excerpt. The real text is long enough that fixing the other
    three drags the aggregate back to HIT even with that one gap
    still open — the actual, verified behaviour, not an idealised one.
    """
    original = (
        "Hi John, Distinct stage, and I think you have found the gap rather than a "
        "subdivision of one. My test for whether two controls are really one is "
        "whether they fail the same way. These do not. A governance failure is "
        "loud. An agent does something it should not have, and there is an "
        "incident, a trace and someone to ask. A qualification failure is silent. "
        "The system runs correctly for eighteen months, every action inside "
        "policy, every log clean, and it should never have been deployed for that "
        "purpose in the first place. Nobody finds it through monitoring, because "
        "monitoring confirms it is behaving exactly as built. It surfaces when "
        "someone finally asks who decided this was suitable, and the answer turns "
        "out to be nobody in particular. Different failure mode, different "
        "evidence, different owner, different point in time. That is four reasons "
        "not to fold it into governance. What nobody has done is extend that from "
        "models that predict to models that act. In most organisations "
        "qualification would fall down the same gap the agents themselves fall "
        "down. Curious whether your clients have solved that, because the "
        "methodology is the easier half."
    )
    render = (
        "Hi John. Distinct stage, and I think you have found the gap rather than a "
        "subdivision of one. My test for whether two controls are really one is "
        "whether they fail the same way. These do not. A governance failure is "
        "loud. An agent does something it should not have, and there is an "
        "incident, a trace and someone to ask. A qualification failure is silent. "
        "The system runs correctly for eighteen months, every action inside "
        "policy, every log clean, and it should never have been deployed for that "
        "purpose in the first place. I find nobody catches it through monitoring, "
        "because monitoring confirms it is behaving exactly as built. It brings up "
        "when someone finally asks who decided this was suitable, and the answer "
        "turns out to be nobody in particular. Different failure mode, different "
        "evidence, different owner, different point in time. I see four reasons "
        "not to fold it into governance. What I think nobody has done is extend "
        "that from models that predict to models that act. In most organisations "
        "I think qualification would fall down the same gap the agents themselves "
        "fall down. Curious whether your clients have solved that, because the "
        "methodology is the easier half."
    )
    baseline = ve.compute_baseline_metrics(original)

    clean = render
    delta = ve.score_render_delta(baseline, clean)
    d = delta["first_person_ratio"]
    assert d["verdict"] == "MISSED"
    clean, _ = df._fix_first_person_over_ratio(clean, d["baseline"], d["output"], original)

    delta2 = ve.score_render_delta(baseline, clean)
    d2 = delta2["first_person_ratio"]
    if d2["verdict"] == "MISSED":
        clean, _ = df._fix_first_person_over_ratio(clean, d2["baseline"], d2["output"], original)

    final_delta = ve.score_render_delta(baseline, clean)
    assert final_delta["first_person_ratio"]["verdict"] == "HIT"
    # The one intentionally-declined sentence must still be present,
    # untouched -- this test is about the other three, not about
    # silently guessing at "I see" too.
    assert "I see four reasons" in clean


# ------------------------------------------------------------------
# ownership_miss_is_content_driven — distinguishes a genuine unfixed
# defect from a residual that's simply the person's own opinion-dense
# writing, which no safe fixer can reduce further. Found live 18 Aug
# 2026: a 72% ownership drift on a genuinely opinionated email only
# dropped to ~37% after both fixer passes, and every remaining
# first-person sentence traced back to a genuine marker in the
# original. An initially-proposed fix (restore exact original wording
# for sentences the fixer can't safely strip) turned out to change
# nothing, since first_person_ratio counts SENTENCES with a marker,
# not word density, and the original wording was ALSO first-person in
# every residual case checked.
# ------------------------------------------------------------------

def test_content_driven_when_every_residual_sentence_traces_to_original():
    original = (
        "Distinct stage, and I think you have found the gap. "
        "My test for whether two controls are really one is whether they fail the same way. "
        "Where I would push back slightly, or at least add friction. "
        "The part I have no good answer to: who owns it."
    )
    render = (
        "Distinct stage, and I think you have found the gap. "
        "My test for whether two controls are really one is whether they fail the same way. "
        "Where I disagree slightly, or at least add friction. "
        "The part I have no good answer to: who owns it."
    )
    assert df.ownership_miss_is_content_driven(render, original) is True


def test_not_content_driven_when_a_sentence_has_no_original_first_person():
    """The exact regression this exists to prevent: a genuine defect
    (first-person injected where the original had none) must NOT get
    waved through as 'just content'."""
    original = "Nobody finds it through monitoring. That is four reasons not to fold it into governance."
    render = "I find nobody catches it through monitoring. I see four reasons not to fold it into governance."
    assert df.ownership_miss_is_content_driven(render, original) is False


def test_not_content_driven_with_no_original_text_at_all():
    """No original to check against -- must default to treating it as
    a defect, not silently excusing it. The burden is on demonstrating
    the marker was genuinely there, not assuming it."""
    assert df.ownership_miss_is_content_driven("I think this is a problem.", "") is False


def test_trivially_content_driven_when_render_has_no_first_person_at_all():
    """Nothing to excuse if there's no first-person marker left in the
    render in the first place."""
    assert df.ownership_miss_is_content_driven("This is neutral.", "Also neutral.") is True


def test_mixed_case_only_one_genuine_sentence_still_fails():
    """One genuinely-fabricated first-person sentence among several
    genuinely content-driven ones must still fail the whole check --
    this is a per-render gate for the aggregate verdict, not a
    per-sentence score, so a single real defect anywhere blocks the
    downgrade for the whole dimension."""
    original = (
        "Distinct stage, and I think you have found the gap. "
        "Nobody finds it through monitoring."
    )
    render = (
        "Distinct stage, and I think you have found the gap. "
        "I find nobody catches it through monitoring."
    )
    assert df.ownership_miss_is_content_driven(render, original) is False


# ------------------------------------------------------------------
# restore_fabricated_ownership_sentences — the general, alignment-
# based fix that supersedes pattern-matching specific verbs for this
# failure class. Built after THREE successive live failures required
# THREE separate pattern additions ("I find", "I see", then "I would
# never find") — the pattern was clear enough to justify a general
# fix rather than a fourth one-off. See the function's own docstring
# for the full reasoning.
# ------------------------------------------------------------------

def test_fixes_every_fabrication_pattern_found_this_session():
    """The actual 'no holes' test: every distinct phrasing found
    across this entire session, fixed by the SAME general mechanism,
    not four separate patterns."""
    cases = [
        ("I find nobody catches it through monitoring.",
         "Nobody finds it through monitoring."),
        ("I see four reasons not to fold it into governance.",
         "That is four reasons not to fold it into governance."),
        ("I would never find it through monitoring, because monitoring confirms it is behaving exactly as built.",
         "Nobody finds it through monitoring, because monitoring confirms it is behaving exactly as built."),
        ("What I think nobody has done is extend that from models that predict to models that act.",
         "What nobody has done is extend that from models that predict to models that act."),
    ]
    for render, original in cases:
        fixed, changed = df.restore_fabricated_ownership_sentences(render, original)
        assert changed, f"failed to fix: {render}"
        assert fixed.strip() == original.strip(), f"wrong result for: {render} -> {fixed}"


def test_fixes_the_previously_declined_i_see_fragment_case():
    """_fix_first_person_over_ratio explicitly declines this exact
    case (see test_declines_i_see_with_a_bare_noun_phrase_object in
    this same file) because partial stripping produces a sentence
    fragment. Whole-sentence substitution has no such failure mode —
    confirms this is a strictly more capable fix, not just a
    different one."""
    text = "I see four reasons not to fold it into governance."
    original = "That is four reasons not to fold it into governance."
    fixed, changed = df.restore_fabricated_ownership_sentences(text, original)
    assert changed
    assert fixed == original


def test_does_not_touch_genuine_preserved_ownership():
    """Safety-critical: must never replace a sentence whose aligned
    original ALSO carries a first-person marker, regardless of exact
    wording difference."""
    cases = [
        ("Distinct stage, and I think you have found the gap rather than a subdivision of one.",
         "Distinct stage, and I think you have found the gap rather than a subdivision of one."),
        ("Where I disagree slightly, or at least add friction.",
         "Where I would push back slightly, or at least add friction."),
        ("So I think qualification is not a gate but a gate plus an expiry.",
         "So I suspect qualification is not a gate but a gate plus an expiry."),
    ]
    for render, original in cases:
        fixed, changed = df.restore_fabricated_ownership_sentences(render, original)
        assert not changed, f"false positive on genuine ownership: {render}"
        assert fixed == render


def test_does_not_touch_sentences_with_no_first_person_marker():
    text = "This sentence has no ownership marker at all."
    fixed, changed = df.restore_fabricated_ownership_sentences(text, "Also neutral.")
    assert not changed
    assert fixed == text


def test_no_conversion_cap_unlike_the_pattern_based_fixer():
    """Deliberately no max_conversions limit — every offending
    sentence gets fixed in one pass, not throttled to 2 like the
    pattern-based fixer above. A render with many fabricated
    sentences must have ALL of them fixed, not just the first two.

    Sentences deliberately kept to realistic prose length (10+ words),
    not compressed to the shortest possible example — alignment
    confidence is proportional to sentence length here: the "I would
    never" fabrication overhead (3 extra words) is a small fraction of
    a 14-word sentence but a decisive fraction of a 7-word one,
    confirmed directly: the identical fabrication pattern aligns at
    0.62 Jaccard overlap on a 14-word sentence and drops to 0.40 (below
    the 0.5 threshold) on a 7-word version of the same idea. That's a
    genuine, honest boundary of this mechanism on short sentences, not
    a bug — see restore_fabricated_ownership_sentences' docstring and
    this session's own findings. This test uses realistic sentence
    lengths so it isolates the conversion-cap behaviour specifically."""
    render = (
        "I find nobody catches the pattern hiding in this quarter's data. "
        "I see three separate problems buried in the report's appendix. "
        "I would never find the drift sitting in these adjusted numbers. "
        "I think there is a real gap somewhere in the review process."
    )
    original = (
        "Nobody catches the pattern hiding in this quarter's data. "
        "That is three separate problems buried in the report's appendix. "
        "Nobody finds the drift sitting in these adjusted numbers. "
        "There is a real gap somewhere in the review process."
    )
    fixed, changed = df.restore_fabricated_ownership_sentences(render, original)
    assert changed
    assert "I find" not in fixed
    assert "I see" not in fixed
    assert "I would never" not in fixed
    assert "I think" not in fixed


def test_full_document_reaches_exact_baseline_match():
    """End-to-end confirmation against the real session's full email,
    used verbatim (not a shortened version) — a shortened synthetic
    version was tried first and landed on CLOSE rather than HIT, not
    because the fix was incomplete but because a 5-sentence document
    has too few sentences for first_person_ratio's granularity
    (fp_sents / total_sents) to land exactly on most baseline values.
    The real text is long enough that this isn't a factor, and it's
    the one case actually worth confirming exactly, since it's the
    literal render this whole mechanism was built against."""
    original = (
        "Hi John, Distinct stage, and I think you have found the gap rather than a "
        "subdivision of one. My test for whether two controls are really one is "
        "whether they fail the same way. These do not. A governance failure is "
        "loud. An agent does something it should not have, and there is an "
        "incident, a trace and someone to ask. A qualification failure is silent. "
        "The system runs correctly for eighteen months, every action inside "
        "policy, every log clean, and it should never have been deployed for that "
        "purpose in the first place. Nobody finds it through monitoring, because "
        "monitoring confirms it is behaving exactly as built. It surfaces when "
        "someone finally asks who decided this was suitable, and the answer turns "
        "out to be nobody in particular. Different failure mode, different "
        "evidence, different owner, different point in time. That is four reasons "
        "not to fold it into governance. What nobody has done is extend that from "
        "models that predict to models that act. In most organisations "
        "qualification would fall down the same gap the agents themselves fall "
        "down. Curious whether your clients have solved that, because the "
        "methodology is the easier half."
    )
    render = (
        "Hi John. Distinct stage, and I think you have found the gap rather than a "
        "subdivision of one. My test for whether two controls are really one is "
        "whether they fail the same way. These do not. A governance failure is "
        "loud. An agent does something it should not have, and there is an "
        "incident, a trace and someone to ask. A qualification failure is silent. "
        "The system runs correctly for eighteen months, every action inside "
        "policy, every log clean, and it should never have been deployed for that "
        "purpose in the first place. I would never find it through monitoring, "
        "because monitoring confirms it is behaving exactly as built. It brings "
        "up when someone finally asks who decided this was suitable, and the "
        "answer turns out to be nobody in particular. Different failure mode, "
        "different evidence, different owner, different point in time. That is "
        "four reasons not to fold it into governance. What nobody has done is "
        "extend that from models that predict to models that act. In most "
        "organisations qualification would fall down the same gap the agents "
        "themselves fall down. Curious whether your clients have solved that, "
        "because the methodology is the easier half."
    )
    baseline = ve.compute_baseline_metrics(original)
    fixed, changed = df.restore_fabricated_ownership_sentences(render, original)
    assert changed
    final = ve.score_render_delta(baseline, fixed)
    assert final["first_person_ratio"]["verdict"] == "HIT"
    # Genuine ownership must survive.
    assert "I think you have found the gap" in fixed


def test_returns_unchanged_with_no_original_text():
    text = "I find this concerning."
    fixed, changed = df.restore_fabricated_ownership_sentences(text, "")
    assert not changed
    assert fixed == text


def test_documented_boundary_short_sentences_may_not_align():
    """Not a defect — a documented, honest limit. On a short sentence
    (under ~10 words), the fabrication overhead ('I would never' adds
    3 words) can be enough of the sentence that word-overlap alignment
    drops below the 0.5 confidence threshold, and the function
    correctly declines rather than guess at a low-confidence match.
    This is the same sentence pattern that reliably fixes on a longer
    version (see test_no_conversion_cap_unlike_the_pattern_based_fixer
    and test_fixes_every_fabrication_pattern_found_this_session) --
    length, not the pattern itself, is what determines coverage here.
    Recorded as a test so this boundary is documented and visible,
    not just known and unwritten."""
    short_render = "I would never find the drift in the numbers."
    short_original = "Nobody finds the drift in the numbers."
    fixed, changed = df.restore_fabricated_ownership_sentences(short_render, short_original)
    assert not changed  # declines -- correct, not a false fix
    assert fixed == short_render  # fabrication survives uncorrected by THIS mechanism
