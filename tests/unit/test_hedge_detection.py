"""
Tests for voice_engine._HEDGE_PATTERN — the single canonical hedge
detector, replacing three previously separate, hand-copied word lists
(score_hedging_signature, compute_baseline_metrics, and a third copy
in deterministic_fixers.py) that had already drifted out of sync
before this fix.

Direct motivation: a real render this session ("Curious whether it
holds up... because") was scored as a clean hedge-density HIT against
baseline, even though it contains an unmistakable hedge in plain
English. The old pattern only ever matched single lexical items
(might, could, perhaps...) — this was a genuine detection gap, not a
correction-pass failure, per Hyland's (1998, 2005) hedging taxonomy,
which distinguishes single-word hedges from clause-level epistemic
constructions ("it seems that", "I wonder whether").

test_catches_the_real_session_failure_case below is the direct
regression guard for that specific gap. test_does_not_flag_direct_
opinion_as_hedged guards the other direction — "I think"/"I believe"
were deliberately excluded (too ambiguous in casual/direct writing
registers to flag unconditionally) and must stay excluded.
"""
import voice_engine as ve
import deterministic_fixers as df


def test_catches_the_real_session_failure_case():
    """The exact phrase that slipped through undetected this session."""
    text = "Curious whether it holds up against what you are seeing in practice, because if it does, it is the concrete proof point."
    matches = ve._HEDGE_PATTERN.findall(text)
    assert matches, "Expected 'Curious whether' to be detected as a hedge"


def test_original_single_word_hedges_still_caught():
    for word in ["might", "could", "perhaps", "possibly", "maybe", "somewhat",
                 "quite", "rather", "potentially", "arguably"]:
        text = f"This {word} works for the team."
        assert ve._HEDGE_PATTERN.search(text), f"Expected '{word}' to still be caught"


def test_new_single_word_adverbs_caught():
    for word in ["presumably", "apparently", "allegedly", "seemingly", "supposedly"]:
        text = f"This is {word} the right approach."
        assert ve._HEDGE_PATTERN.search(text), f"Expected '{word}' to be caught"


def test_clause_level_epistemic_hedges_caught():
    examples = [
        "It seems the numbers are off this quarter.",
        "It appears the deal fell through last week.",
        "This seems like the right call for now.",
        "The approach appears to work in most cases.",
        "I wonder if we should reconsider the timeline.",
        "I am not sure if this holds up under scrutiny.",
        "Hard to say whether this will land well.",
    ]
    for text in examples:
        assert ve._HEDGE_PATTERN.search(text), f"Expected a hedge match in: {text!r}"


def test_softening_quantifiers_caught():
    for phrase in ["kind of", "sort of", "to some extent", "in some ways"]:
        text = f"This is {phrase} what we discussed last time."
        assert ve._HEDGE_PATTERN.search(text), f"Expected '{phrase}' to be caught"


def test_does_not_flag_direct_opinion_as_hedged():
    """
    I think / I believe are deliberately excluded — too ambiguous in
    casual, direct-opinion writing registers (as opposed to the
    academic-research-article register the taxonomy this is based on
    was built for) to flag unconditionally. A direct writer saying
    "I think you're wrong" is stating an opinion plainly, not hedging.
    """
    direct_examples = [
        "I think you are wrong about this and here is why.",
        "I believe this is the right call, full stop.",
        "This is not acceptable and it will not happen twice.",
        "Ship it today, no excuses.",
        "The numbers do not add up and I have said so directly.",
    ]
    for text in direct_examples:
        assert not ve._HEDGE_PATTERN.search(text), (
            f"Expected no hedge match in direct-opinion text: {text!r}"
        )


def test_compute_baseline_metrics_reflects_the_wider_detection():
    original = "The numbers look fine on paper but the timeline bothers me."
    render = (
        "Curious whether it holds up against what you are seeing in "
        "practice, because if it does, it is the concrete proof point."
    )
    before = ve.compute_baseline_metrics(original)
    after = ve.compute_baseline_metrics(render)
    assert before["hedge_density"] == 0.0
    assert after["hedge_density"] > 0.0


def test_all_three_former_copies_now_agree_exactly():
    """
    The core bug this fix closes: three separate hand-copied word
    lists had already drifted out of sync. Confirms
    score_hedging_signature, compute_baseline_metrics, and
    deterministic_fixers._HEDGE_WORDS now all count identically,
    since they share one canonical pattern instead of three copies.
    """
    text = (
        "It seems apparently likely that we might curious whether it "
        "holds up, and presumably the numbers are quite off."
    )
    metrics = ve.compute_baseline_metrics(text)
    sentences = ve._extract_sentences(text)
    obs = ve.score_hedging_signature(sentences, text)
    fixer_count = len(df._HEDGE_WORDS.findall(text))

    metrics_count = round(metrics["hedge_density"] * len(text.split()) / 100)
    assert metrics_count == obs.data["hedge_count"] == fixer_count


def test_safe_to_delete_hedges_widened_to_match_new_adverbs():
    """The new single-word adverbs (presumably, apparently, allegedly,
    seemingly, supposedly) are the same safe-to-delete syntactic
    category as the original set — deleting one just tightens a claim,
    same as the originals."""
    for word in ["presumably", "apparently", "allegedly", "seemingly", "supposedly"]:
        assert df._SAFE_TO_DELETE_HEDGES.search(f"This is {word} unclear.")


def test_safe_to_delete_hedges_does_not_include_clause_level_hedges():
    """Clause-level hedges ('it seems', 'I wonder if') deliberately
    stay out of the safe-to-delete set — deleting them requires
    restructuring the sentence, not a clean word removal, same
    reasoning already applied to modal verbs in this module."""
    text = "I wonder if we should reconsider this."
    assert not df._SAFE_TO_DELETE_HEDGES.search(text)
