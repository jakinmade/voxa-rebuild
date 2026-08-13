"""
Tests for plausibility-shield stripping (prompts._strip_plausibility_shields,
called from _regex_sweep) and detection (voice_engine.score_ai_tells).

Root cause this exists to catch: the previous opener_hedge / opener_hedge2 /
opener_hedge3 regexes anchored on (?m)^ - a LINE start, not a SENTENCE
start. That only matched a hedge opening the entire text or sitting right
after a literal newline. A hedge buried in sentence two or three of an
ordinary paragraph - the common case, first found in John's Scott Kosch
outreach draft ("Timing feels right off the back of your post. I see it
as the deterministic proof layer...") - was invisible to it regardless of
which phrases were on the list. Same failure class as the em-dash-
laundering bug fixed earlier: a rule that looks like it covers the whole
text but only ever touched the first line.

Category grounding, not an ad hoc phrase list: Prince, Frader & Bosk
(1982) named this class "plausibility shields" - first-person lexical
verbs (think, believe, see, view, take) that attribute a claim to the
speaker's own judgement rather than stating it as fact. Hyland's hedging
taxonomy (1998), the standard reference in this field, groups the same
verbs under "lexical verb hedges" alongside the modals and epistemic
adverbs _HEDGE_WORD_PATTERN already covers elsewhere in prompts.py.
"""
from prompts import _regex_sweep, _strip_plausibility_shields
from voice_engine import score_ai_tells


# ---------------------------------------------------------------------
# The actual failing case that started this - mid-paragraph, not sentence 1
# ---------------------------------------------------------------------

def test_replace_shield_mid_paragraph_is_caught():
    text = (
        "Timing feels right off the back of your post. I see it as the "
        "deterministic proof layer underneath the governance point from "
        "our earlier thread."
    )
    result = _regex_sweep(text)
    assert "I see it as" not in result
    assert "It is the deterministic proof layer" in result


def test_drop_shield_mid_paragraph_is_caught():
    # This exact phrase (opener_hedge) worked before IF it was line 1.
    # This test proves it now also works when buried in sentence 2 -
    # the actual regression the old line-anchored regex had.
    text = "Good to connect. I think that this matters a lot."
    result = _regex_sweep(text)
    assert "I think" not in result
    assert "This matters a lot." in result


# ---------------------------------------------------------------------
# Confirm the previously-working case (line 1) still works post-rebuild
# ---------------------------------------------------------------------

def test_drop_shield_still_works_at_sentence_one():
    text = "I think that this is the strongest angle we have."
    result = _regex_sweep(text)
    assert result == "This is the strongest angle we have."


def test_i_believe_that_still_works_at_sentence_one():
    text = "I believe that this deserves another look."
    result = _regex_sweep(text)
    assert "I believe" not in result


def test_i_would_argue_that_still_works_at_sentence_one():
    text = "I would argue that this is the stronger position."
    result = _regex_sweep(text)
    assert "I would argue" not in result


# ---------------------------------------------------------------------
# Replace-shape shields: deleting the shield alone would leave a
# sentence with no verb, so these get swapped for "It is" instead.
# ---------------------------------------------------------------------

def test_i_see_this_as_is_replaced_not_just_dropped():
    text = "I see this as a strong signal for Q3."
    result = _regex_sweep(text)
    assert result == "It is a strong signal for Q3."


def test_i_view_it_as_is_replaced():
    text = "The market is shifting fast. I view it as the clearest signal yet."
    result = _regex_sweep(text)
    assert "I view it as" not in result
    assert "It is the clearest signal yet." in result


# ---------------------------------------------------------------------
# Mid-sentence, comma-bounded shields - the case that broke the fix
# during development (concatenation bug: "This, is..." then "Thisis...")
# ---------------------------------------------------------------------

def test_midsentence_shield_leaves_no_orphan_comma():
    text = "This, in my view, is the strongest angle we have."
    result = _regex_sweep(text)
    assert result == "This is the strongest angle we have."
    assert ",  " not in result
    assert ", is" not in result


def test_midsentence_shield_leaves_single_space_not_concatenated():
    text = "The evidence, as I see it, points one way."
    result = _regex_sweep(text)
    assert result == "The evidence points one way."


def test_sentence_initial_in_my_view_capitalised_correctly():
    text = "In my view, this is the strongest angle we have."
    result = _regex_sweep(text)
    assert result == "This is the strongest angle we have."


def test_midsentence_shield_at_end_of_sentence_no_trailing_punctuation():
    # Real bug found during adversarial testing: the mid-sentence pattern
    # required something AFTER the phrase to match on (comma or
    # whitespace), so a shield sitting at the very end of a sentence with
    # nothing following it - not even a period - passed through untouched.
    text = "This works, in my view"
    result = _regex_sweep(text)
    assert result == "This works"


def test_midsentence_shield_at_end_of_sentence_with_period():
    text = "This works, in my view."
    result = _regex_sweep(text)
    assert result == "This works."


def test_stress_three_midsentence_shields_one_sentence():
    text = (
        "This is, in my view, right, but that, in my opinion, is wrong, "
        "and this, as I see it, works."
    )
    result = _regex_sweep(text)
    assert result == "This is right, but that is wrong, and this works."


# ---------------------------------------------------------------------
# Multiple shields in one passage - realistic outreach-message shape
# ---------------------------------------------------------------------

def test_multiple_shields_all_caught_independently():
    text = (
        "I think that this works. I see it as a strong signal. "
        "In my view, timing matters."
    )
    result = _regex_sweep(text)
    assert "I think" not in result
    assert "I see it as" not in result
    assert "in my view" not in result.lower()


# ---------------------------------------------------------------------
# Must not fire on a non-hedge "I think" that isn't sentence-initial
# (e.g. reported speech) - guards against over-stripping real content.
# ---------------------------------------------------------------------

def test_non_sentence_initial_i_think_is_left_alone():
    text = "She told me I think you are right about this."
    result = _regex_sweep(text)
    assert result == text


# ---------------------------------------------------------------------
# Detection side - score_ai_tells must flag a surviving shield. This is
# the check that would have caught the "AI-tell check: Clean" false
# positive shown in Voicova's UI when the strip has a coverage gap.
# ---------------------------------------------------------------------

def test_score_ai_tells_flags_surviving_replace_shield():
    result = score_ai_tells("I see it as the deterministic proof layer.")
    assert result["clean"] is False
    assert result["phrase_hit_count"] >= 1


def test_score_ai_tells_flags_surviving_midsentence_shield():
    result = score_ai_tells("This, in my view, is worth another look.")
    assert result["clean"] is False


def test_score_ai_tells_clean_after_sweep_removes_shields():
    original = "I see it as the deterministic proof layer, and in my view that matters."
    swept = _strip_plausibility_shields(original)
    result = score_ai_tells(swept)
    assert result["clean"] is True
    assert result["phrase_hit_count"] == 0
