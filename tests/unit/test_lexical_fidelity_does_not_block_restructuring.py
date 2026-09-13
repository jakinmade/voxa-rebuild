"""
Regression test — 13 Sept 2026: rule 8 was updated (aec67cf) to permit
genuine sentence restructuring ("form is free, content is fixed"), but
a separate, earlier, more strongly-worded instruction block —
LEXICAL FIDELITY in the RENDERING INSTRUCTIONS section — was never
updated and directly contradicted it: "Only change a word or sentence
structure when it is actually needed... Most of the input's original
phrasing should survive into the output unchanged." That block sits
earlier in the prompt and is labelled "critical", plausibly carrying
more real weight than a numbered rule further down — a likely
explanation for why restructuring barely happened even after rule 8
was loosened. Fixed: lexical fidelity now explicitly scoped to WORD
CHOICE only (don't swap synonyms), with sentence structure explicitly
carved out as not what it protects, pointing to rule 8.
"""
from prompts import _build_system_prompt


def test_lexical_fidelity_scoped_to_word_choice_not_structure():
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "word-level only" in prompt.lower() or "WORD-LEVEL only" in prompt
    assert "not what lexical fidelity protects" in prompt.lower() or "explicitly not what" in prompt.lower()
    # The old blanket instruction must be gone
    assert "most of the input's original phrasing should survive into the output unchanged" not in prompt.lower()


def test_lexical_fidelity_still_protects_word_choice():
    # Don't lose the legitimate part of the original instruction —
    # word-level synonym substitution should still be discouraged.
    prompt = _build_system_prompt(
        voice_dna="dummy", mode_instruction="Tighten it.",
        word_count_input=100, ai_score=0.0, mode="GET_IT_DONE",
    )
    assert "do not substitute synonyms for variety" in prompt.lower()
