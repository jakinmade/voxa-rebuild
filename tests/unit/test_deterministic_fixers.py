"""
Tests for deterministic_fixers.py — the rule-based correction functions
meant to replace the LLM-based correction pass in app.py (which sends
build_correction_prompt()'s text instructions to Claude and trusts
compliance, with a bare `except: pass` on failure — see app.py ~line
770).

Root cause this exists to catch: a "safe direction" regex fixer isn't
safe just because the design doc says so. Two real bugs were found
here during development — hedge-word deletion mangling modal verbs
("This might work" -> "This work"), and a "be"-promotion regex that
dropped the sentence complement entirely ("It might be the answer" ->
"It thes answer"). Both looked correct until run against real text.
These tests exist so a future change to these functions can't silently
reintroduce either failure mode, and so each function's declared safe
direction is actually verified, not assumed.

Convention: every fixer takes (text, target, current, ...) and returns
(fixed_text, applied: bool). Tests check three things per function:
1. The safe direction actually improves the metric.
2. Every declared refusal case (wrong direction, missing content,
   ambiguous/unsafe construction) leaves the text byte-for-byte
   unchanged and reports applied=False.
3. Output is grammatically sound — not just "changed", but actually
   readable text, since a regex can easily produce syntactically valid
   but semantically broken output (see root cause above).
"""
import deterministic_fixers as df
from voice_engine import compute_baseline_metrics


# ---------------------------------------------------------------------------
# _fix_hedge_density — adverbial hedge removal (perhaps/possibly/maybe/
# somewhat/quite/rather/potentially/arguably only — NOT might/could)
# ---------------------------------------------------------------------------

def test_hedge_density_removes_pure_adverbial_hedges():
    text = "It is somewhat unclear. The results are quite promising, arguably."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_hedge_density(text, target=1.0, current=before)
    after = compute_baseline_metrics(fixed)["hedge_density"]
    assert applied is True
    assert after < before
    assert after == 0.0
    assert fixed == "It is unclear. The results are promising."


def test_hedge_density_leaves_modals_untouched():
    """The residual-signal contract: might/could are measured as hedges
    but this function must never delete them — that's _fix_modal_hedge's
    job, and deleting a modal here would break the verb (see module
    docstring root cause)."""
    text = "This might work. It could be right."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_hedge_density(text, target=1.0, current=before)
    assert applied is False
    assert fixed == text


def test_hedge_density_never_touches_absolute_claim_wording():
    """Regression: must not corrupt the claim itself, only remove the
    hedge word sitting next to it."""
    text = "This is possibly the best option available, guaranteed to work."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_hedge_density(text, target=1.0, current=before)
    assert applied is True
    assert "best option available, guaranteed to work" in fixed
    assert "possibly" not in fixed


def test_hedge_density_refuses_when_already_under_target():
    text = "It is somewhat unclear."
    fixed, applied = df._fix_hedge_density(text, target=10.0, current=5.0)
    assert applied is False
    assert fixed == text


def test_hedge_density_output_has_no_orphan_punctuation():
    """Regression guard for the original bug class: deletion must not
    leave double spaces, stray commas, or lowercase sentence starts."""
    text = "Perhaps this works. The plan is, quite frankly, solid."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_hedge_density(text, target=1.0, current=before)
    assert applied is True
    assert ",," not in fixed
    assert "  " not in fixed
    assert not fixed.startswith(",")
    for sentence in fixed.split(". "):
        if sentence:
            assert sentence[0].isupper()


# ---------------------------------------------------------------------------
# _fix_modal_hedge — might/could removal via verb promotion
# ---------------------------------------------------------------------------

def test_modal_hedge_promotes_bare_verb():
    text = "It might work. They could help with this."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is True
    assert fixed == "It works. They help with this."


def test_modal_hedge_promotes_be_without_losing_complement():
    """Regression: this is the exact bug found in development — the
    complement after 'be' ('the answer') was being dropped."""
    text = "This could be useful. It might be the answer."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is True
    assert fixed == "This is useful. It is the answer."
    assert "answer" in fixed  # explicit regression check


def test_modal_hedge_handles_irregular_have():
    text = "We might have the budget. It could have merit."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is True
    assert fixed == "We have the budget. It has merit."


def test_modal_hedge_third_person_singular_agreement():
    text = "She might improve the design. He could match the target."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is True
    assert fixed == "She improves the design. He matches the target."


def test_modal_hedge_y_ending_verb_conjugation():
    text = "It might apply here. This could clarify things."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is True
    assert "applies" in fixed
    assert "clarifies" in fixed


def test_modal_hedge_refuses_on_negation():
    """'might not work' and 'doesn't work' are different claims —
    promoting through a negation changes meaning, must stay flagged."""
    text = "It might not work. They could not help."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is False
    assert fixed == text


def test_modal_hedge_refuses_on_noun_phrase_subject():
    """Only the closed pronoun set is safe — agreement for collective
    nouns like 'the team' is genuinely ambiguous without a real parser."""
    text = "The team might fix this. Management could approve it."
    before = compute_baseline_metrics(text)["hedge_density"]
    fixed, applied = df._fix_modal_hedge(text, target=1.0, current=before)
    assert applied is False
    assert fixed == text


def test_modal_hedge_chained_after_adverbial_pass_fully_resolves():
    """End-to-end: the two hedge fixers together should resolve a case
    neither can fully resolve alone."""
    text = "This might work. It could possibly be the right approach, perhaps."
    step1, _ = df._fix_hedge_density(
        text, target=1.0, current=compute_baseline_metrics(text)["hedge_density"]
    )
    step2, applied2 = df._fix_modal_hedge(
        step1, target=1.0, current=compute_baseline_metrics(step1)["hedge_density"]
    )
    assert applied2 is True
    assert compute_baseline_metrics(step2)["hedge_density"] == 0.0
    assert step2 == "This works. It is the right approach."


# ---------------------------------------------------------------------------
# _fix_sentence_length_sd — split-only, too-uniform direction
# ---------------------------------------------------------------------------

def test_sentence_sd_splits_on_safe_coordinator():
    text = ("We shipped the release. The team was confident, and the results "
            "came in faster than expected across every region we tracked this quarter.")
    before = compute_baseline_metrics(text)["sentence_length_sd"]
    fixed, applied = df._fix_sentence_length_sd(text, target=before + 4.0, current=before)
    assert applied is True
    assert "The team was confident." in fixed
    assert "And the results came in" in fixed


def test_sentence_sd_skips_which_when_no_other_coordinator():
    """'which' is often the relative-pronoun SUBJECT of its clause —
    splitting there strips the subject. Must decline outright when it's
    the only candidate."""
    text = ("We reviewed the numbers. Sales were flat across the region, "
            "which surprised everyone on the call this week.")
    before = compute_baseline_metrics(text)["sentence_length_sd"]
    fixed, applied = df._fix_sentence_length_sd(text, target=before + 4.0, current=before)
    assert applied is False
    assert fixed == text


def test_sentence_sd_finds_safe_coordinator_past_an_unsafe_one():
    """When 'which' AND a safe coordinator both appear in the target
    (longest) sentence, must split at the safe one, not refuse just
    because 'which' is present somewhere in it."""
    text = ("We reviewed the numbers. Sales were flat across the region, "
            "which surprised everyone on the call, and it changed the plan for Q4.")
    before = compute_baseline_metrics(text)["sentence_length_sd"]
    fixed, applied = df._fix_sentence_length_sd(text, target=before + 4.0, current=before)
    assert applied is True
    assert "which surprised everyone on the call." in fixed


def test_sentence_sd_refuses_when_already_varied_enough():
    text = "Fix it. The team spent the entire afternoon working through every edge case."
    fixed, applied = df._fix_sentence_length_sd(text, target=2.0, current=8.0)
    assert applied is False
    assert fixed == text


def test_sentence_sd_refuses_with_no_coordinator_available():
    text = "We reviewed the numbers carefully across every region before the meeting concluded."
    before = compute_baseline_metrics(text)["sentence_length_sd"]
    fixed, applied = df._fix_sentence_length_sd(text, target=before + 4.0, current=before)
    assert applied is False
    assert fixed == text


def test_sentence_sd_refuses_on_single_sentence_input():
    text = "We reviewed the numbers across every region, and the meeting concluded well."
    fixed, applied = df._fix_sentence_length_sd(text, target=100.0, current=0.0)
    assert applied is False
    assert fixed == text


# ---------------------------------------------------------------------------
# _fix_first_person_ratio — impersonal-opener -> "I think" only,
# never on another party's attributed point
# ---------------------------------------------------------------------------

def test_ownership_converts_impersonal_opener():
    text = "It is worth noting that the numbers improved this quarter."
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=True
    )
    assert applied is True
    assert fixed == "I think the numbers improved this quarter."


def test_ownership_refuses_when_input_has_no_opinion_content():
    """Mirrors build_correction_prompt()'s existing gate: nothing of the
    writer's own to convert means don't fabricate ownership."""
    text = "It is worth noting that the numbers improved this quarter."
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=False
    )
    assert applied is False
    assert fixed == text


def test_ownership_refuses_on_other_party_attribution():
    """The core guard: converting 'your point' framing to first person
    is a credit error, not a style fix — must never fire here."""
    text = "It is worth noting that your point about timing was correct."
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=True
    )
    assert applied is False
    assert fixed == text
    assert "your point" in fixed


def test_ownership_refuses_on_according_to_attribution():
    text = "It seems that according to the client, the delay was unavoidable."
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=True
    )
    assert applied is False
    assert fixed == text


def test_ownership_refuses_on_quoted_material():
    text = 'It appears that "the team was ready" is what they said.'
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=True
    )
    assert applied is False
    assert fixed == text


def test_ownership_bounded_to_two_conversions_per_pass():
    text = ("It seems that sales rose. It appears that costs fell. "
            "It is worth noting that margins grew.")
    before = compute_baseline_metrics(text)["first_person_ratio"]
    fixed, applied = df._fix_first_person_ratio(
        text, target=before + 0.5, current=before, input_has_opinion_content=True
    )
    assert applied is True
    assert fixed.count("I think") == 2
    assert "It is worth noting that margins grew" in fixed


def test_ownership_refuses_when_already_at_or_above_target():
    text = "It is worth noting that the numbers improved."
    fixed, applied = df._fix_first_person_ratio(
        text, target=0.1, current=0.5, input_has_opinion_content=True
    )
    assert applied is False
    assert fixed == text


# ---------------------------------------------------------------------------
# _fix_directive_ratio — strip polite/modal wrapper off an EXISTING
# request, never invent a new one
# ---------------------------------------------------------------------------

def test_directive_strips_polite_wrapper():
    text = "Could you fix the login page? Please review the PR before Friday."
    before = compute_baseline_metrics(text)["directive_ratio"]
    fixed, applied = df._fix_directive_ratio(
        text, target=before + 0.5, current=before, input_has_directive_content=True
    )
    assert applied is True
    assert fixed == "Fix the login page. Review the PR before Friday."


def test_directive_refuses_when_no_imperative_verb_underneath():
    """Must not turn a non-request sentence into a broken fragment —
    'feel' isn't in the imperative verb list, so this must decline."""
    text = "You should feel proud of this result."
    before = compute_baseline_metrics(text)["directive_ratio"]
    fixed, applied = df._fix_directive_ratio(
        text, target=before + 0.5, current=before, input_has_directive_content=True
    )
    assert applied is False
    assert fixed == text


def test_directive_refuses_when_input_has_no_directive_content():
    """Mirrors build_correction_prompt()'s existing gate — this is the
    guard against the documented live failure where the LLM correction
    pass fabricated a call to action that wasn't in the input at all."""
    text = "Could you fix the login page?"
    before = compute_baseline_metrics(text)["directive_ratio"]
    fixed, applied = df._fix_directive_ratio(
        text, target=before + 0.5, current=before, input_has_directive_content=False
    )
    assert applied is False
    assert fixed == text


def test_directive_no_op_when_nothing_to_strip():
    text = "The login page is broken. We noticed it yesterday."
    before = compute_baseline_metrics(text)["directive_ratio"]
    fixed, applied = df._fix_directive_ratio(
        text, target=before + 0.5, current=before, input_has_directive_content=True
    )
    assert applied is False
    assert fixed == text


def test_directive_refuses_when_already_at_or_above_target():
    text = "Could you fix the login page?"
    fixed, applied = df._fix_directive_ratio(
        text, target=0.1, current=0.5, input_has_directive_content=True
    )
    assert applied is False
    assert fixed == text


# ---------------------------------------------------------------------------
# _check_uncorrected_insertions — catches collateral the LLM correction
# call introduces, which the aggregate delta re-score can miss (see the
# module-level comment above the function for the real render that
# surfaced this: an ownership-fix rewrite added "perhaps" and "might be"
# and a new closing sentence, and hedge_density still scored as held).
# ---------------------------------------------------------------------------

def test_flags_new_single_word_hedge():
    before = "This works. The approach is sound."
    after = "This perhaps works. The approach is sound."
    result = df._check_uncorrected_insertions(before, after)
    assert result["flagged"] is True
    assert result["new_hedges"] == ["perhaps"]
    assert result["sentence_growth"] == 0


def test_flags_new_modal_hedge():
    before = "This is the easier half."
    after = "This might be the easier half."
    result = df._check_uncorrected_insertions(before, after)
    assert result["flagged"] is True
    assert "might" in result["new_hedges"]


def test_flags_multiple_new_hedges_independently():
    before = "The point stands. It is unclear either way."
    after = "The point perhaps stands. It might be unclear either way."
    result = df._check_uncorrected_insertions(before, after)
    assert sorted(result["new_hedges"]) == ["might", "perhaps"]


def test_does_not_flag_hedge_already_present_before():
    """A hedge that was already in the pre-correction text isn't new —
    only extra occurrences beyond what was already there count."""
    before = "This perhaps works."
    after = "This perhaps works well."
    result = df._check_uncorrected_insertions(before, after)
    assert result["new_hedges"] == []
    assert result["flagged"] is False


def test_does_not_flag_hedge_that_moved_not_multiplied():
    """Same single occurrence, different position — count-based diff,
    not position-based, so this must not double-count."""
    before = "Perhaps this works well."
    after = "This works well, perhaps."
    result = df._check_uncorrected_insertions(before, after)
    assert result["new_hedges"] == []


def test_flags_sentence_growth():
    before = "The point stands. It holds up under scrutiny."
    after = "The point stands. It holds up under scrutiny. This could prove harder than either of us has acknowledged."
    result = df._check_uncorrected_insertions(before, after)
    assert result["sentence_growth"] == 1
    assert result["flagged"] is True


def test_sentence_drop_not_flagged_as_growth():
    """A correction pass that legitimately merges/cuts sentences isn't
    fabrication — only growth beyond the pre-correction count counts."""
    before = "The point stands. It holds up under scrutiny. Nothing more to add."
    after = "The point stands and holds up under scrutiny."
    result = df._check_uncorrected_insertions(before, after)
    assert result["sentence_growth"] == 0


def test_clean_correction_pass_not_flagged():
    """The common case: LLM correction genuinely just fixes the target
    dimension with no collateral. Must not false-positive."""
    before = "Your point about model risk management is well made."
    after = "The point about model risk management is well made."
    result = df._check_uncorrected_insertions(before, after)
    assert result["new_hedges"] == []
    assert result["sentence_growth"] == 0
    assert result["flagged"] is False


def test_uses_full_hedge_pattern_not_narrow_correction_list():
    """Must catch clause-level hedges too (curious whether, it seems),
    not just the narrower _SAFE_TO_DELETE_HEDGES adverb list used for
    correction — this is a detection question, not a correction one."""
    before = "This holds up."
    after = "I am curious whether this holds up."
    result = df._check_uncorrected_insertions(before, after)
    assert result["flagged"] is True
    assert any("curious whether" in h for h in result["new_hedges"])
