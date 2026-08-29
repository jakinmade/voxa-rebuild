"""
Tests for _regex_sweep's salutation-comma restoration (prompts.py step
13, added 29 Aug 2026).

Background: prompts.py's own _grammar_fix_pass system prompt (rule 7,
"DO NOT TOUCH") explicitly tells the model the opening salutation's
terminal punctuation is a fixed comma ("Hi John,") and to never
convert it to a full stop - the salutation line is not a sentence
with independent clauses, so rule 10 (run-ons/comma splices) must
never fire on it. Confirmed live and reported recurring: the model
does not reliably obey this "never" instruction - "Hi John," is
sometimes still turned into "Hi John." A prompt instruction cannot be
verified from inside the prompt; only code running after generation
can actually enforce it. This adds a deterministic, code-level catch
for exactly that failure, in the same category and same file location
as the existing ",." orphan-punctuation safety net a few lines above
it (step 12) - both exist because _grammar_fix_pass is non-
deterministic and can't be regex-fixed at the source.
"""
import prompts as pr


def test_restores_comma_after_greeting_plus_name():
    text = pr._regex_sweep("Hi John. Thanks for sending this over.")
    assert text.startswith("Hi John,")


def test_restores_comma_after_dear_plus_name():
    text = pr._regex_sweep("Dear Sarah. I wanted to follow up on our call.")
    assert text.startswith("Dear Sarah,")


def test_restores_comma_after_hello_plus_name():
    text = pr._regex_sweep("Hello Josh. Quick update on the project.")
    assert text.startswith("Hello Josh,")


def test_restores_comma_after_multi_word_name():
    text = pr._regex_sweep("Hi John Smith. Following up on yesterday.")
    assert text.startswith("Hi John Smith,")


def test_restores_comma_for_bare_name_on_its_own_line():
    text = pr._regex_sweep("Josh.\nQuick update on the project.")
    assert text.startswith("Josh,")


def test_leaves_correct_salutation_comma_untouched():
    text = pr._regex_sweep("Hi John, thanks for sending this over.")
    assert text.startswith("Hi John,")


def test_does_not_touch_genuine_sentence_starting_with_greeting_word():
    # "Hi" as a genuine opening word without a name shouldn't match at
    # all - the pattern requires a capitalised name token after it.
    text = pr._regex_sweep("Hi there. Thanks for reaching out.")
    assert text.startswith("Hi there.")


def test_does_not_touch_bare_name_used_as_sentence_subject():
    # No line break right after "John." - this is a real, unrelated
    # sentence, not a standalone salutation line, and must be left
    # alone.
    text = pr._regex_sweep("John really impressed everyone in the room.")
    assert text.startswith("John really impressed everyone")


def test_does_not_touch_short_sentence_elsewhere_in_the_text():
    # Anchored to the absolute start of the text - a short two-word
    # sentence mid-render must never be mistaken for a salutation.
    text = pr._regex_sweep(
        "Thanks for the update. Fair point. I'll take another look at it."
    )
    assert "Fair point." in text


def test_only_fires_once_even_with_repeated_shape():
    # count=1 in both substitutions - this only ever touches the very
    # first line, never a coincidentally similar shape later on.
    text = pr._regex_sweep("Hi John. Dear Team. is not a real salutation here.")
    assert text.startswith("Hi John,")
    assert "Dear Team." in text
