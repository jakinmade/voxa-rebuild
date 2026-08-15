"""
Tests for voice_engine._pick_anchor_sentences.

No dedicated test coverage existed for this function before this file —
worth fixing given it was just rewritten, not leaving a second
untested rewrite in the same repo.

Why the rewrite: the previous version scored sentences with a
hand-rolled heuristic — reward short declarative sentences, denial
phrasing, imperative verbs; unconditionally penalise hedges and
adjectives. That hardcodes an assumption about what "sounds like
someone" rather than measuring it against their own writing. A writer
whose actual baseline hedges frequently would have every one of their
most characteristic sentences penalised by the old heuristic —
precisely backwards, since the point of an anchor sentence is to be
representative of THIS person, not of some assumed ideal register.

The new version scores by average per-word typicality against the
corpus's own most-frequent-word (MFW) profile — the same machinery
compute_burrows_delta already uses, per its own docstring, as the
field's most robust style signal. test_hedge_heavy_corpus_favours_
hedged_anchors below is the direct regression guard for the failure
mode the old heuristic had no way to avoid.
"""
import voice_engine as ve


def test_empty_input_returns_empty():
    assert ve._pick_anchor_sentences([]) == []


def test_falls_back_gracefully_with_no_usable_profile():
    # Sentences with no alphabetic words at all -> no MFW profile
    # possible. Should degrade to returning the first few sentences
    # rather than raising or returning nothing.
    result = ve._pick_anchor_sentences(["123 456", "789"])
    assert result  # some fallback, not an exception


def test_selects_up_to_the_cap():
    corpus = (
        "I think we should move fast on this deal. I want the team to focus "
        "on the core problem first. I believe the data backs this up clearly. "
        "We need to stay focused on what matters here. I have asked David "
        "twice for the signed order form. This has been bothering me all "
        "week and I cannot let it go. I would rather raise this plainly now."
    )
    sentences = [s.strip() for s in corpus.split(".") if s.strip()]
    result = ve._pick_anchor_sentences(sentences, corpus_text=corpus)
    assert 1 <= len(result) <= ve._ANCHOR_SENTENCE_CAP


def test_hedge_heavy_corpus_favours_hedged_anchors():
    """
    The direct regression guard for the old heuristic's core failure
    mode: a writer whose own baseline genuinely hedges often should
    have their characteristic hedged sentences selected, not
    penalised out, since those hedge words are genuinely frequent
    (and therefore "typical") for this specific person.
    """
    corpus_sentences = [
        "I think this might possibly work if we are careful about it",
        "It could perhaps be the right approach for this particular case",
        "We should maybe consider this option going forward as well",
        "This seems like it could potentially be worth exploring further",
    ]
    corpus_text = " ".join(corpus_sentences)
    # A candidate pool that mixes hedge-heavy (in-voice) and blunt,
    # hedge-free (out-of-voice) sentences.
    candidates = corpus_sentences + [
        "This is definitely the plan and there is no doubt about it.",
        "We will absolutely proceed immediately without any delay.",
    ]
    result = ve._pick_anchor_sentences(candidates, corpus_text=corpus_text)

    # At least one selected anchor should come from the hedge-heavy,
    # in-voice pool, not exclusively from the blunt sentences the old
    # heuristic would have preferred (it rewarded absence of hedges).
    assert any(s in corpus_sentences for s in result), (
        f"Expected at least one hedge-heavy, in-voice sentence to be "
        f"selected as an anchor, got: {result}"
    )


def test_blunt_corpus_does_not_favour_hedges():
    """
    The mirror case: a writer whose baseline is genuinely blunt and
    hedge-free should NOT have hedge-heavy sentences selected just
    because they exist in the candidate pool — typicality is scored
    against THIS corpus, not a fixed preference either way.
    """
    corpus_sentences = [
        "This is wrong and we are fixing it today.",
        "Send the report now, do not wait for approval.",
        "The numbers do not add up and I have said so directly.",
        "We are done with this vendor, full stop.",
    ]
    corpus_text = " ".join(corpus_sentences)
    candidates = corpus_sentences + [
        "This might possibly perhaps somewhat work if we are careful.",
    ]
    result = ve._pick_anchor_sentences(candidates, corpus_text=corpus_text)

    hedge_sentence = "This might possibly perhaps somewhat work if we are careful."
    # The single heavily-hedged outlier, which shares almost no
    # vocabulary with this blunt corpus, should not be the top pick.
    assert result[0] != hedge_sentence


def test_length_variety_preserved():
    """The original design's length-variety property should survive
    the rewrite — anchors shouldn't all be clustered at the same
    length bucket when longer/shorter alternatives exist."""
    corpus = (
        "I ship fast. I ship fast every single time without exception here. "
        "I ship. I ship fast and clean. I ship fast and clean every time we "
        "release something new to production for our customers to use."
    )
    sentences = [s.strip() for s in corpus.split(".") if s.strip()]
    result = ve._pick_anchor_sentences(sentences, corpus_text=corpus)
    if len(result) >= 2:
        lengths = {len(s.split()) // 5 for s in result}
        assert len(lengths) >= 2, f"Expected length variety, got all same bucket: {result}"


def test_falls_back_to_sentence_pool_when_no_corpus_text_given():
    """corpus_text is optional — omitting it should still work,
    scoring against the sentence pool itself, not raise."""
    sentences = [
        "I always ship on time no matter what.",
        "The deadline is not negotiable and never has been.",
    ]
    result = ve._pick_anchor_sentences(sentences)
    assert result
