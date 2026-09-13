"""
Regression test — 13 Sept 2026, later in the same session as the
keep_dashes preservation fix (98e2432) and over-generation fix
(52689e3): explicit user statement, "we don't use em dashes",
overrides the whole preserve-if-present mechanism built earlier that
same night. An em dash appearing in the current input (e.g. from
autocorrect/smart punctuation) is not to be treated as this person's
voice. keep_dashes must always resolve False regardless of input or
calibration evidence, and the pre-generation instruction must always
say not to introduce/to convert any.
"""
from prompts import _build_voice_dna, _split_dashes_deterministic


def test_voice_dna_always_instructs_against_em_dashes_even_with_dash_in_current_input():
    calibration = "Some ordinary writing with no dashes at all in it whatsoever here."
    current_input = "Scott — following up on the CLEARANCE test link from a while back."
    observations = [{"dimension": "hedging_signature", "summary": "moderate hedging"}]
    voice_dna = _build_voice_dna(observations, calibration, baseline=None, ai_score=0.0, current_input_text=current_input)
    assert "no em dashes in their writing" in voice_dna
    assert "uses em dashes" not in voice_dna


def test_voice_dna_always_instructs_against_em_dashes_even_with_established_habit():
    # Even a calibration corpus with 2+ genuine historical dashes must
    # not flip this back to permissive, per the explicit override.
    calibration = "I went to the store — it was closed. Later — much later — I went back."
    current_input = "Following up on the report."
    observations = [{"dimension": "hedging_signature", "summary": "moderate hedging"}]
    voice_dna = _build_voice_dna(observations, calibration, baseline=None, ai_score=0.0, current_input_text=current_input)
    assert "no em dashes in their writing" in voice_dna


def test_split_dashes_with_keep_dashes_false_and_max_dashes_zero_strips_everything():
    text = "Scott — following up on this — and this too."
    result = _split_dashes_deterministic(text, keep_dashes=False, max_dashes=0)
    assert "—" not in result
