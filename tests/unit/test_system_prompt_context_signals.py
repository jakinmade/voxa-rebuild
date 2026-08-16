"""
Tests for prompts._build_system_prompt's two additions this session:
render_context (the optional per-render "who's this for, what's it
for" field) and voice_profile_summary (the distilled writer-habits
profile — see test_voice_profile_summary.py for the generation side).

No dedicated test file existed for _build_system_prompt before this —
worth fixing given two new parameters were just added to it, not
leaving them untested alongside everything else covered this session.
"""
from prompts import _build_system_prompt


def _base_kwargs(**overrides):
    kwargs = dict(
        voice_dna="THE STANDARD: sample sentences here.",
        mode_instruction="Rewrite this.",
        word_count_input=50,
        ai_score=0.1,
    )
    kwargs.update(overrides)
    return kwargs


# ------------------------------------------------------------------
# render_context
# ------------------------------------------------------------------

def test_render_context_injected_on_clean_input_path():
    prompt = _build_system_prompt(**_base_kwargs(
        ai_score=0.1, render_context="A cold outreach follow-up, keep it low-pressure"
    ))
    assert "CONTEXT FOR THIS PIECE" in prompt
    assert "cold outreach follow-up" in prompt


def test_render_context_injected_on_ai_contaminated_path():
    prompt = _build_system_prompt(**_base_kwargs(
        ai_score=0.5, render_context="A cold outreach follow-up, keep it low-pressure"
    ))
    assert "CONTEXT FOR THIS PIECE" in prompt
    assert "cold outreach follow-up" in prompt


def test_render_context_omitted_entirely_when_empty():
    prompt = _build_system_prompt(**_base_kwargs(render_context=""))
    assert "CONTEXT FOR THIS PIECE" not in prompt


def test_render_context_omitted_when_whitespace_only():
    prompt = _build_system_prompt(**_base_kwargs(render_context="   "))
    assert "CONTEXT FOR THIS PIECE" not in prompt


def test_render_context_omitted_by_default():
    """Backward-compatible: a caller not yet passing render_context at
    all must not have it appear as an empty/stray block."""
    prompt = _build_system_prompt(**_base_kwargs())
    assert "CONTEXT FOR THIS PIECE" not in prompt


def test_render_context_does_not_alter_the_numeric_baseline_targets():
    """The whole point of keeping this separate from voice_dna: audience
    context steers generation, it must never appear anywhere near or
    substitute for the actual baseline dict passed in."""
    baseline = {"hedge_density": 1.0, "sentence_length_sd": 5.0,
                "first_person_ratio": 0.5, "directive_ratio": 0.0, "word_count": 200}
    prompt_without = _build_system_prompt(**_base_kwargs(ai_score=0.5, baseline=baseline))
    prompt_with = _build_system_prompt(
        **_base_kwargs(ai_score=0.5, baseline=baseline, render_context="A LinkedIn post")
    )
    # The restoration/target block derived from baseline should be
    # identical in both — only the context block should differ.
    assert "hedge" in prompt_without.lower() or "1.0" in prompt_without
    assert ("hedge" in prompt_with.lower() or "1.0" in prompt_with) == (
        "hedge" in prompt_without.lower() or "1.0" in prompt_without
    )


# ------------------------------------------------------------------
# voice_profile_summary
# ------------------------------------------------------------------

def test_voice_profile_summary_injected_on_clean_input_path():
    prompt = _build_system_prompt(**_base_kwargs(
        ai_score=0.1, voice_profile_summary="Writes short, direct sentences. Rarely hedges."
    ))
    assert "WRITER'S DISTINCTIVE HABITS" in prompt
    assert "Writes short, direct sentences" in prompt


def test_voice_profile_summary_injected_on_ai_contaminated_path():
    prompt = _build_system_prompt(**_base_kwargs(
        ai_score=0.5, voice_profile_summary="Writes short, direct sentences. Rarely hedges."
    ))
    assert "WRITER'S DISTINCTIVE HABITS" in prompt
    assert "Writes short, direct sentences" in prompt


def test_voice_profile_summary_omitted_entirely_when_empty():
    prompt = _build_system_prompt(**_base_kwargs(voice_profile_summary=""))
    assert "WRITER'S DISTINCTIVE HABITS" not in prompt


def test_voice_profile_summary_omitted_by_default():
    """Backward-compatible: a render with no distilled profile yet
    (generation failed, or hasn't happened for this baseline) must
    proceed exactly as it did before this feature existed."""
    prompt = _build_system_prompt(**_base_kwargs())
    assert "WRITER'S DISTINCTIVE HABITS" not in prompt


def test_voice_profile_summary_and_render_context_coexist():
    """Both new signals can be present in the same render without
    interfering with each other."""
    prompt = _build_system_prompt(**_base_kwargs(
        ai_score=0.1,
        render_context="A LinkedIn post announcing a product launch",
        voice_profile_summary="Opens with the concrete problem before context.",
    ))
    assert "CONTEXT FOR THIS PIECE" in prompt
    assert "WRITER'S DISTINCTIVE HABITS" in prompt
    assert "LinkedIn post" in prompt
    assert "Opens with the concrete problem" in prompt


# ------------------------------------------------------------------
# Lexical fidelity (clean human input path)
#
# Root-caused against a real render: clean-human input ("Curious if
# you got a chance to run it") came back with gratuitous synonym
# substitution ("Curious whether...") that _check_uncorrected_insertions
# correctly flagged as a new hedge — the detection layer was already
# working (see test_deterministic_fixers.py), but nothing upstream in
# the render prompt itself discouraged the substitution in the first
# place, so every render risked tripping its own insertion check.
# This is the missing instruction, clean-path only: the AI-contaminated
# path is intentionally a full rewrite ("keep the ideas, destroy the
# words") and must NOT get this constraint.
# ------------------------------------------------------------------

def test_lexical_fidelity_instruction_present_on_clean_input_path():
    prompt = _build_system_prompt(**_base_kwargs(ai_score=0.1))
    assert "LEXICAL FIDELITY" in prompt
    assert "not a paraphrase task" in prompt


def test_lexical_fidelity_instruction_absent_on_ai_contaminated_path():
    """The AI-contaminated path's whole job is full rewrite - stripping
    AI tells and replacing the words wholesale. A lexical-preservation
    instruction there would directly contradict "Keep the ideas.
    Destroy the words." and must not be injected on this path."""
    prompt = _build_system_prompt(**_base_kwargs(ai_score=0.5))
    assert "LEXICAL FIDELITY" not in prompt
