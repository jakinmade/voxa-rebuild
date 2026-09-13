"""
Regression test — 13 Sept 2026 real-render bug: preferred_function_words
was computed from calibration corpus alone and injected as a "use these"
instruction, causing a live render to change the user's actual "while"
to "whilst" (present only in an old calibration sample, absent from the
input actually being rewritten).
"""
import voice_engine as ve
from prompts import _build_voice_dna

CALIBRATION_WITH_WHILST = (
    "Whilst I was reviewing the numbers, I noticed the trend held steady "
    "across every quarter, and whilst that is reassuring, it is not the "
    "full story. " * 5
)

REAL_INPUT_WITH_WHILE = (
    "Scott, following up on the CLEARANCE test link from a while back. "
    "Curious if you got a chance to run it or if it fell off the desk "
    "with everything going on. Timing feels right off the back of your "
    "Workflow Agent Manager post. The scenario you described, dashboard "
    "green while an agent quietly does the wrong thing, is exactly what "
    "CLEARANCE is built to catch."
)


def test_function_patterns_never_recommend_word_absent_from_current_input():
    patterns = ve._extract_function_patterns(CALIBRATION_WITH_WHILST, current_text=REAL_INPUT_WITH_WHILE)
    assert 'whilst' not in patterns['preferred_function_words'], (
        f"'whilst' should not be recommended — it never appears in the "
        f"current input. Got: {patterns['preferred_function_words']}"
    )


def test_function_patterns_backward_compatible_with_no_current_text():
    # No current_text passed — must reproduce prior (corpus-only) behaviour
    patterns = ve._extract_function_patterns(CALIBRATION_WITH_WHILST)
    assert 'whilst' in patterns['preferred_function_words']


def test_voice_dna_prompt_does_not_instruct_whilst_when_input_says_while():
    observations = [{"dimension": "hedging_signature", "summary": "moderate hedging"}]
    voice_dna = _build_voice_dna(
        observations, CALIBRATION_WITH_WHILST, baseline=None, ai_score=0.0,
        current_input_text=REAL_INPUT_WITH_WHILE,
    )
    assert "'whilst'" not in voice_dna, (
        f"Voice DNA prompt should not instruct the model to use 'whilst' "
        f"when the current input only contains 'while'. Prompt:\n{voice_dna}"
    )
