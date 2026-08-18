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
