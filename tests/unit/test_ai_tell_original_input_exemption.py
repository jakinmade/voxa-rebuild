"""
Tests for score_ai_tells' original_input_text exemption (18 Aug 2026).

Real bug, found live: several phrases in the AI-tell pattern lists —
"curious whether", "i suspect", "i would push back" — are also just
ordinary things a person might genuinely write. Two earlier sessions
"fixed" instances of this class of false positive by narrowing
individual regexes (excluding "curious if" but not "curious whether",
excluding possessive/determiner forms of "surface"). That approach
caps out the moment the SAME phrase is genuinely someone's voice: a
real render's own original input said "Curious whether your clients
have solved that" verbatim, and the render kept flagging it as an AI
tell even after correctly preserving it unedited. No regex narrowing
fixes that — only checking against what the person actually wrote
does.
"""
import voice_engine as ve


def test_curious_whether_flagged_without_original_input():
    """Baseline: confirms this phrase IS normally flagged (old
    behaviour, unchanged for callers that don't pass original_input_text)."""
    result = ve.score_ai_tells("Curious whether that works for you.")
    assert result["clean"] is False
    assert any("curious whether" in f.lower() for f in result["flagged"])


def test_curious_whether_exempted_when_genuinely_in_original():
    """The exact real-world case this fix was built for."""
    original = "Curious whether your clients have solved that, because the methodology is the easier half."
    render = "Curious whether your clients have solved that, because the methodology is the easier half."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is True
    assert result["flagged"] == []


def test_curious_whether_still_flagged_when_genuinely_fabricated():
    """The exemption must stay narrow — a phrase NOT in the original
    (the actual documented fabrication incident this pattern exists
    to catch) must still be flagged."""
    original = "Hi John, thanks for the update on the Meridian contract."
    render = "Curious whether that framing lands for you."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is False
    assert any("curious whether" in f.lower() for f in result["flagged"])


def test_i_suspect_and_i_would_push_back_exempted_when_genuine():
    """_ANALYTICAL_TELL_PHRASES also contains 'i suspect' and 'i would
    push back' unconditionally -- same bug class, different list."""
    original = (
        "So I suspect qualification is not a gate but a gate plus an expiry. "
        "Where I would push back slightly, or at least add friction."
    )
    render = "So I suspect qualification is not a gate. I would push back slightly on that."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is True


def test_i_suspect_still_flagged_when_not_in_original():
    original = "Hi John, thanks for the update."
    render = "So I suspect this is not quite right."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is False


def test_default_behaviour_unchanged_when_original_input_text_omitted():
    """Every existing caller not yet updated to pass this parameter
    must see byte-identical behaviour — the parameter defaults to ""
    which exempts nothing."""
    text = "Curious whether that works, and I suspect it does."
    with_default = ve.score_ai_tells(text)
    with_explicit_empty = ve.score_ai_tells(text, original_input_text="")
    assert with_default == with_explicit_empty
    assert with_default["clean"] is False


def test_em_dash_check_is_never_exempted_by_original_input():
    """Em dashes and spaced-hyphen substitutes enforce VOICOVA's own
    house style, not an AI-detection heuristic -- these must stay
    absolute even if the person's own original genuinely used an em
    dash themselves."""
    original = "This is my point \u2014 stated plainly, as I always write it."
    render = "This is my point \u2014 stated plainly."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is False
    assert result["em_dash_count"] == 1


def test_exemption_is_case_insensitive():
    original = "CURIOUS WHETHER your clients have solved that."
    render = "Curious whether your clients have solved that."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is True


def test_partial_word_in_original_does_not_falsely_exempt():
    """The exemption checks for the exact matched phrase, not loose
    substring containment of individual words scattered across the
    original -- 'curious' and 'whether' both appearing separately,
    nowhere near each other, must not exempt the fabricated
    combined phrase 'curious whether'."""
    original = "I am curious about the outcome. I wonder whether it will work."
    render = "Curious whether that framing lands for you."
    result = ve.score_ai_tells(render, original_input_text=original)
    assert result["clean"] is False


def test_analytical_register_only_checked_when_register_warrants_it():
    """Regression guard: the exemption logic must not change WHEN
    _ANALYTICAL_TELL_PHRASES is consulted, only WHETHER a hit within
    it gets exempted."""
    corporate_text = "We should leverage this synergy going forward."
    result = ve.score_ai_tells(corporate_text, original_input_text="")
    assert result["register"] == "corporate"


# ---------------------------------------------------------------------------
# calibration_text — added 4 Sept 2026, same false-positive class as
# original_input_text above, but for a stylistic device that's part of
# someone's genuine calibrated voice yet doesn't happen to appear in THIS
# specific render's input.
# ---------------------------------------------------------------------------

def test_verbatim_phrase_exempted_when_genuinely_in_calibration():
    """A phrase-list hit (not the fragment-emphasis pattern) exempted
    because it appears verbatim in the calibration corpus, even though
    it's absent from this specific render's input."""
    calibration = "I suspect this won't hold up under real load."
    render = "So I suspect this won't hold up."
    result = ve.score_ai_tells(render, calibration_text=calibration)
    assert result["clean"] is True


def test_fragment_emphasis_needs_pattern_level_exemption_not_phrase_level():
    """The real bug this fix exists for: a narrative-storyteller
    persona's calibration sample used a short declarative fragment for
    emphasis ('No stack trace, nothing.') and a rewrite using the SAME
    DEVICE on completely different words ('Not optional. Not a
    nice-to-have.') still got flagged, because a verbatim phrase-match
    exemption can never work here by construction - the two fragments
    share no literal text at all, only the same structural device.
    Confirms the fix checks whether the PATTERN itself matches
    anywhere in calibration_text, not whether the exact phrase does."""
    calibration = (
        "So picture this. It's 11pm, we're three days from launch, and the "
        "payment integration just silently stops working with zero error "
        "logs. No stack trace, nothing. Turned out to be a timezone mismatch."
    )
    render = (
        "Root cause was a config mismatch. Full end-to-end payment flows "
        "need to be a mandatory gate before any future launch. Not optional. "
        "Not a nice-to-have."
    )
    # Without calibration: correctly flagged (no evidence given).
    without = ve.score_ai_tells(render)
    assert without["clean"] is False
    assert any("not optional" in f.lower() for f in without["flagged"])
    # With calibration showing the same device on different words: exempted.
    with_calibration = ve.score_ai_tells(render, calibration_text=calibration)
    assert with_calibration["clean"] is True


def test_fragment_emphasis_still_flagged_without_calibration_evidence():
    """The exemption must stay narrow - a fragment-emphasis hit with
    no calibration evidence at all (the default, and every existing
    caller that hasn't been updated) must still be flagged exactly as
    before this fix existed."""
    render = "This is critical. Not optional. Move forward regardless."
    result = ve.score_ai_tells(render)
    assert result["clean"] is False


def test_calibration_pattern_match_requires_the_same_pattern_not_any_fragment():
    """The pattern-level exemption checks _FRAGMENT_EMPHASIS_PATTERN
    specifically - calibration text with unrelated content, no matter
    how long, must not accidentally exempt a real fragment-emphasis
    hit in the render."""
    calibration = "I write in complete, conventional sentences with no unusual emphasis devices at all."
    render = "This is critical. Not optional. Move forward regardless."
    result = ve.score_ai_tells(render, calibration_text=calibration)
    assert result["clean"] is False


def test_default_behaviour_unchanged_when_calibration_text_omitted():
    """Every existing caller not yet updated to pass this parameter
    must see byte-identical behaviour - defaults to "" which exempts
    nothing extra."""
    text = "This is critical. Not optional. Move forward regardless."
    with_default = ve.score_ai_tells(text)
    with_explicit_empty = ve.score_ai_tells(text, calibration_text="")
    assert with_default == with_explicit_empty
    assert with_default["clean"] is False


def test_i_think_that_exempted_when_calibration_shows_the_verb_family():
    """Real finding: a warm-hedging-manager persona's calibration
    sample opened 'I think, on balance, the draft is...' (the genuine
    device) and a rewrite using the same device with a different
    continuation ('I think that is what gives the initiative its
    value') still got flagged, because 'I think that' and 'I think, on
    balance' share the verb-opener but not the exact phrase a verbatim
    check requires. Same pattern-level exemption shape as the fragment-
    emphasis fix above, applied to the plausibility-shield family."""
    calibration = "I think, on balance, the draft is mostly there, though I wonder if the opening could land a little softer."
    render = "Stakeholder alignment might come more naturally through a data-driven methodology, and I think that is what gives the initiative its value."
    result = ve.score_ai_tells(render, calibration_text=calibration)
    assert result["clean"] is True


def test_i_think_that_still_flagged_without_calibration_evidence():
    render = "Stakeholder alignment might come more naturally through a data-driven methodology, and I think that is what gives the initiative its value."
    result = ve.score_ai_tells(render)
    assert result["clean"] is False
    assert any("i think that" in f.lower() for f in result["flagged"])


def test_i_think_that_still_flagged_with_unrelated_calibration():
    """The exemption must stay narrow - calibration text with no
    hedge-verb-opener device at all, no matter how long, must not
    accidentally exempt a real shield hit in the render."""
    calibration = "I write in complete, plain sentences with no unusual hedging devices at all."
    render = "Stakeholder alignment might come more naturally through a data-driven methodology, and I think that is what gives the initiative its value."
    result = ve.score_ai_tells(render, calibration_text=calibration)
    assert result["clean"] is False
