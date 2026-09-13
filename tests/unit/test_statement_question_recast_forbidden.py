"""
Regression test — 13 Sept 2026: rule 8 (aec67cf) explicitly permitted
"turn a statement into a question or vice versa" as legitimate FORM
restructuring. That is precisely the transformation that turned
"Curious if you got a chance to run it..." into "Did you get a chance
to run it...?" on a real render — twice, on two separate real
renders, both times correctly flagged by Content Lock as a dropped
fact ("Curious"). The instruction itself was telling the model to do
the exact thing Content Lock was built to catch. Fixed: explicitly
forbid this one class of restructuring (statement<->question recast)
while keeping every other form of restructuring (split/join/reorder/
blunter pivot/fragment) permitted, in all three places this
permission previously appeared (GET_IT_DONE mode instruction, rule 8,
and the LEXICAL FIDELITY block's cross-reference).

Also: rule 7 strengthened after a real render merged two adjacent
paragraphs into one (no deterministic check exists yet for this —
flagged as a good follow-up, not built tonight).
"""
from prompts import _build_system_prompt, apply_intent_mode


def test_mode_instruction_forbids_statement_question_recast():
    instruction = apply_intent_mode("some text", "GET_IT_DONE")
    assert "do not recast a statement as a question" in instruction.lower()


def test_rule_8_forbids_statement_question_recast():
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "never recast a statement as a question" in prompt.lower()
    # The old permissive phrasing must be gone from rule 8 and the mode instruction
    assert "turn a statement into a" not in prompt.lower()


def test_lexical_fidelity_no_longer_lists_statement_question_as_permitted():
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "recasting a statement as a" not in prompt.lower() or "never recasting a statement" in prompt.lower() or "rule 8's exception" in prompt.lower()


def test_rule_7_explicitly_forbids_merging_paragraphs():
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "do not merge two paragraphs into one" in prompt.lower()


def test_other_restructuring_still_permitted():
    # Make sure the fix didn't overcorrect and ban restructuring
    # entirely — split/join/reorder/fragment must still be there.
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "split one into two" in prompt.lower() or "split a sentence" in prompt.lower()
    assert "blunter pivot" in prompt.lower()
    assert "deliberate fragment" in prompt.lower()
