"""
Tests for _restore_dropped_subject_openers (deterministic_fixers.py).

No prior test coverage existed for this function despite it being a
real production fixer (added 7 Sept 2026). Written 12 Sept 2026 after
a real user-reported failure: a genuine render on a real document
("Scott — following up on the CLEARANCE test link...") showed both
'Curious if...' and 'Built out for...' — the exact two sentences this
function's own docstring cites as the original finding it was built
to fix — still uncorrected in production output.

Root cause found by direct reproduction: the function's sentence-
splitting and replacement-boundary logic only recognized ASCII quote
characters ('"), not the Unicode curly/typographic quotes (\u2019 \u201d)
Claude's own generation uses by default. When the sentence preceding
a dropped-subject restoration ends in a curly closing quote, two bugs
compounded: (1) the input-splitting regex swallowed the next sentence
entirely (silently no-op'ing the whole function for it — a
recurrence of the exact bug class the 7 Sept fix already covered for
ASCII quotes), and (2) even once the sentence-boundary detection was
widened, the replacement's start-boundary skip-loop still didn't
recognize the curly quote character, so the splice consumed the quote
AND the paragraph-break whitespace after it with nothing put back —
producing two sentences merged with no space ("...didn't.Built out...").
"""
from deterministic_fixers import _restore_dropped_subject_openers, _restore_dropped_contractions


def test_restores_a_single_dropped_subject_sentence_straight_quotes():
    original = "Built out for US Financial Services specifically. Report in minutes."
    rewrite = "I built this out for US Financial Services specifically. Report in minutes."
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert "Built out for US Financial Services specifically." in fixed
    assert "I built this out for" not in fixed
    assert restored == ["Built out for US Financial Services specifically."]


def test_leaves_text_unchanged_when_no_dropped_subject_pattern_present():
    original = "The report shows a steady increase over the last three months."
    rewrite = "The report shows a steady increase over the last three months."
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert fixed == rewrite
    assert restored == []


def test_does_not_touch_sentences_that_already_have_a_normal_subject():
    original = "I built this myself over the weekend. It works well."
    rewrite = "I built this myself over the weekend. It works well."
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert fixed == rewrite
    assert restored == []


# ------------------------------------------------------------------
# Regression guards — curly-quote bugs found and fixed 12 Sept 2026
# ------------------------------------------------------------------

def test_restores_sentence_following_a_curly_quote_closed_sentence():
    """Bug 1: the splitting regex only recognized ASCII quote
    characters, so a sentence ending in a curly closing quote (") ate
    the next sentence whole, silently no-op'ing the entire function
    for it — even though the dropped-subject pattern was genuinely
    present and should have been restored."""
    original = (
        'It is the proof layer, not "the agent ran," but "here\u2019s the evidence.\u201d\n\n'
        "Built out for US Financial Services specifically, the state AI laws now live."
    )
    rewrite = (
        'It is the proof layer, not "the agent ran," but "here\u2019s the evidence.\u201d\n\n'
        "I built this out for US Financial Services specifically, the state AI laws now live."
    )
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert "Built out for US Financial Services specifically, the state AI laws now live." in fixed
    assert "I built this out for" not in fixed
    assert len(restored) == 1


def test_restoration_after_curly_quote_preserves_paragraph_break_and_spacing():
    """Bug 2: even once bug 1 was fixed, the replacement's start-
    boundary skip-loop still didn't recognize the curly quote
    character, so it landed ON the quote instead of after it — the
    splice then consumed the quote AND the paragraph-break whitespace
    that followed with nothing put back, merging two sentences with
    no space or line break between them
    ("...here's the evidence.\u201dBuilt out for...")."""
    original = (
        "Not the agent ran, but here\u2019s the evidence it did the right thing, "
        'and here\u2019s what happens when it didn\u2019t.\u201d\n\n'
        "Built out for US Financial Services specifically, the state AI laws now live."
    )
    rewrite = (
        "Not the agent ran, but here\u2019s the evidence it did the right thing, "
        'and here\u2019s what happens when it didn\u2019t.\u201d\n\n'
        "I built this out for US Financial Services specifically, the state AI laws now live."
    )
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert len(restored) == 1
    # The critical regression guard: the curly quote and the paragraph
    # break must both survive, and the two sentences must not merge.
    assert "didn\u2019t.\u201d\n\nBuilt out for" in fixed
    assert "didn\u2019t.Built out" not in fixed
    assert "\u201dBuilt out" not in fixed


def test_end_boundary_also_handles_a_curly_quote_closed_output_sentence():
    """Same widening applied to the end-boundary regex, for the
    (rarer) case where the sentence being REPLACED (in the model's
    output) itself ends inside curly-quoted material rather than the
    preceding sentence. Uses a straight apostrophe for the contraction
    itself (matching what the real production failure actually showed)
    to isolate this from the separate, still-open question of whether
    _DROPPED_SUBJECT_INJECTIONS should also match a curly-apostrophe
    contraction — a real but distinct finding, not covered by this fix."""
    original = 'Curious if you got a chance to run it or if it fell off the desk.'
    rewrite = 'I\'m curious if you got a chance to run it or if it fell off the desk.\u201d Report in minutes.'
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert "Curious if you got a chance to run it or if it fell off the desk." in fixed
    assert len(restored) == 1


def test_multiple_dropped_subject_sentences_in_one_document_both_restored():
    """The exact real-world document this bug was found against had
    TWO separate dropped-subject sentences in the same render — both
    must be independently restored, not just the first one found."""
    original = (
        "Curious if you got a chance to run it or if it fell off the desk with everything going on.\n\n"
        "The scenario you described is exactly what it\u2019s built to catch. "
        'It is the proof layer: not "the agent ran," but "here\u2019s the evidence.\u201d\n\n'
        "Built out for US Financial Services specifically, the state AI laws now live."
    )
    rewrite = (
        "I'm curious if you got a chance to run it, or if it fell off the desk with everything going on.\n\n"
        "The scenario you described is exactly what it's built to catch. "
        'It is the proof layer: not "the agent ran," but "here\u2019s the evidence.\u201d\n\n'
        "I built this out for US Financial Services specifically, the state AI laws now live."
    )
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert len(restored) == 2
    assert "Curious if you got a chance to run it or if it fell off the desk with everything going on." in fixed
    assert "Built out for US Financial Services specifically, the state AI laws now live." in fixed
    assert "I'm curious" not in fixed
    assert "I built this out" not in fixed


# ------------------------------------------------------------------
# Article restoration — extended 12 Sept 2026, real recurrence
# ------------------------------------------------------------------

def test_restores_a_dropped_article_not_just_a_dropped_subject():
    """Real production finding: SENTENCE COMPLETENESS explicitly covers
    'a subject, article, or connective word' as one violation, but
    _DROPPED_SUBJECT_INJECTIONS only ever covered subjects. 'Timing
    feels right...' (dropped article, economy construction) rendered
    as 'The timing feels right...' — confirmed live, same document as
    the subject-restoration bugs above."""
    original = "Timing feels right off the back of your Workflow Agent Manager post."
    rewrite = "The timing feels right off the back of your Workflow Agent Manager post."
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert fixed == original
    assert restored == [original]


def test_article_restoration_does_not_touch_a_sentence_that_genuinely_needs_one():
    """A sentence that's ungrammatical without the article in the
    ORIGINAL itself must not spuriously 'restore' anything — there's
    nothing to restore since the original never had this sentence."""
    original = "This report covers three regions."
    rewrite = "This report covers three regions."
    fixed, restored = _restore_dropped_subject_openers(rewrite, original)
    assert fixed == rewrite
    assert restored == []


# ------------------------------------------------------------------
# Contraction restoration — added 12 Sept 2026, real recurrence
# ------------------------------------------------------------------

def test_restores_a_dropped_contraction():
    """Real production finding, twice now on the same document: 'It's
    the deterministic proof layer...' rendered as 'It is the
    deterministic proof layer...' despite the CONTRACTIONS instruction
    being present and correct. No deterministic backstop existed for
    this before this fix."""
    original = "It's the deterministic proof layer underneath the governance moat point from our earlier thread."
    rewrite = "It is the deterministic proof layer underneath the governance moat point from our earlier thread."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert fixed == original
    assert restored == [original]


def test_contraction_restoration_leaves_correctly_contracted_output_unchanged():
    original = "It's the deterministic proof layer."
    rewrite = "It's the deterministic proof layer."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert fixed == rewrite
    assert restored == []


def test_contraction_restoration_does_not_touch_a_possessive():
    """'its' (no apostrophe, possessive) must never be mistaken for a
    contraction — the whitelist only ever matches 'it's' (with
    apostrophe), never bare 'its'."""
    original = "The system checks its own output before returning."
    rewrite = "The system checks its own output before returning."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert fixed == rewrite
    assert restored == []


def test_contraction_restoration_is_case_insensitive_but_preserves_output_case():
    original = "That's the whole point of the exercise."
    rewrite = "That is the whole point of the exercise."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert fixed == original


def test_contraction_restoration_skips_when_rest_of_sentence_also_changed():
    """Evidence-gated by design: if the rest of the sentence isn't
    byte-identical to the original (beyond the one contraction), this
    safely does nothing rather than guess at a partial match."""
    original = "It's the deterministic proof layer underneath the point."
    rewrite = "It is basically the proof layer underneath the point."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert fixed == rewrite
    assert restored == []


def test_multiple_dropped_contractions_across_different_sentences_both_restored():
    original = "It's the proof layer. That's the whole point of the exercise."
    rewrite = "It is the proof layer. That is the whole point of the exercise."
    fixed, restored = _restore_dropped_contractions(rewrite, original)
    assert len(restored) == 2
    assert fixed == original



