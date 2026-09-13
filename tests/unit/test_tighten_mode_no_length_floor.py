"""
Regression test — 13 Sept 2026: rule 8 (the word-count floor) applied
unconditionally across every intent mode, including GET_IT_DONE, whose
own mode_instruction explicitly says "Tighten it. Remove anything that
doesn't earn its place." A floor requiring output >= input word count
structurally blocks any real cutting -- the model can't shrink text
while also being told not to fall short and not to pad. Real symptom:
a genuinely tightenable email came back nearly byte-identical to its
input. Fixed: GET_IT_DONE gets a rule 8 that explicitly permits
shortening; every other mode (which are expansive by design) keeps
the floor.
"""
from prompts import _build_system_prompt


def test_get_it_done_mode_has_no_word_count_floor():
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=200, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "at least 200 words" not in prompt
    assert "no minimum word count" in prompt.lower()


def test_other_modes_keep_the_word_count_floor():
    for mode in ["HELP_ME_UNDERSTAND", "WRITE_SOMETHING", "THINK_IT_THROUGH"]:
        prompt = _build_system_prompt(
            voice_dna="dummy", mode_instruction="x",
            word_count_input=200, ai_score=0.0, mode=mode,
        )
        assert "at least 200 words" in prompt, f"Expected floor preserved for {mode}"


def test_default_mode_is_get_it_done_and_gets_no_floor():
    # Callers not yet updated to pass mode explicitly should still get
    # the corrected behaviour, not silently revert to the old floor.
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=200, ai_score=0.0,
    )
    assert "at least 200 words" not in prompt


def test_get_it_done_rule_8_forbids_rewording_kept_material():
    """Real-render bug, same session: removing the length floor let
    the model reword a kept sentence's grammatical mood ('Curious if
    X' became 'Did you X?', dropping 'Curious' entirely) — Content
    Lock correctly flagged this as a dropped fact and an invented
    sentence, but the generation instruction itself needed tightening
    to forbid this class of change, not just rely on downstream
    detection."""
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=200, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "deletion only" in prompt.lower() or "DELETION ONLY" in prompt
    assert "may not reword" in prompt.lower() or "not reword, rephrase" in prompt.lower()


def test_get_it_done_mode_instruction_no_longer_says_rewrite():
    from prompts import apply_intent_mode
    instruction = apply_intent_mode("some text", "GET_IT_DONE")
    assert not instruction.startswith("Rewrite this text")
    assert "deletion only" in instruction.lower()
