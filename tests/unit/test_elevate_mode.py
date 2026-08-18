"""
Tests for build_correction_prompt's mode parameter (18 Aug 2026,
Preserve/Elevate groundwork; wired to the render_mode radio + the
linkedin_format checkbox on Screen 4 as of this session; that checkbox
was later generalised into the platform_format selectbox — see
test_platform_format.py).

Two things this guards specifically:
1. mode defaults to "preserve" and that default must be byte-for-byte
   the old behaviour — no new caller passes mode explicitly yet, so a
   regression here would silently change every existing render.
2. mode="elevate" adds line-editing instructions and can trigger a
   correction pass even with an empty delta (no MISSED dimensions),
   which is new: previously an empty delta always meant no correction
   call at all.
"""
from prompts import build_correction_prompt


def _empty_delta():
    return {}


def _delta_with_one_miss():
    return {
        "hedge_density": {"verdict": "MISSED", "baseline": 0.8, "output": 0.1},
    }


def test_default_mode_is_preserve_and_unchanged():
    # No mode argument at all — matches every existing call site.
    prompt_no_arg = build_correction_prompt(_delta_with_one_miss())
    prompt_explicit_preserve = build_correction_prompt(
        _delta_with_one_miss(), mode="preserve"
    )
    assert prompt_no_arg == prompt_explicit_preserve


def test_preserve_mode_returns_none_on_empty_delta():
    assert build_correction_prompt(_empty_delta()) is None
    assert build_correction_prompt(_empty_delta(), mode="preserve") is None


def test_elevate_mode_returns_prompt_even_on_empty_delta():
    prompt = build_correction_prompt(_empty_delta(), mode="elevate")
    assert prompt is not None
    assert "line edit" in prompt.lower()


def test_elevate_mode_includes_old_to_new_instruction():
    prompt = build_correction_prompt(_empty_delta(), mode="elevate")
    assert "old-to-new" in prompt.lower() or "old to new" in prompt.lower()


def test_elevate_mode_does_not_authorise_sentence_reordering():
    prompt = build_correction_prompt(_empty_delta(), mode="elevate")
    assert "never move sentences relative to each other" in prompt.lower()


def test_unknown_mode_value_fails_safe_to_preserve():
    # A typo like mode="elevated" must not silently apply elevate
    # instructions — only the exact string "elevate" should.
    assert build_correction_prompt(_empty_delta(), mode="elevated") is None
    assert build_correction_prompt(_empty_delta(), mode="") is None


def test_elevate_mode_still_includes_normal_voice_corrections():
    # Elevate mode adds to correction_instructions, it doesn't replace
    # the existing voice-dimension corrections.
    prompt = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    assert prompt is not None
    assert "hedge density" in prompt.lower()
    assert "line edit" in prompt.lower()


def test_elevate_mode_high_grade_level_adds_economy_instruction():
    prompt = build_correction_prompt(
        _empty_delta(), mode="elevate",
        sentence_economy={"grade_level": 18.2, "avg_sentence_length": 25.0,
                           "avg_syllables_per_word": 2.1},
    )
    assert prompt is not None
    assert "18.2" in prompt
    assert "college level" in prompt.lower()


def test_elevate_mode_low_grade_level_does_not_add_economy_instruction():
    prompt = build_correction_prompt(
        _empty_delta(), mode="elevate",
        sentence_economy={"grade_level": 6.0, "avg_sentence_length": 8.0,
                           "avg_syllables_per_word": 1.3},
    )
    assert prompt is not None
    assert "college level" not in prompt.lower()


def test_elevate_mode_high_passive_ratio_adds_active_voice_instruction():
    prompt = build_correction_prompt(
        _empty_delta(), mode="elevate",
        passive_voice={"passive_count": 4, "passive_sentence_ratio": 0.5},
    )
    assert prompt is not None
    assert "50%" in prompt
    assert "active voice" in prompt.lower()


def test_elevate_mode_low_passive_ratio_does_not_add_active_voice_instruction():
    prompt = build_correction_prompt(
        _empty_delta(), mode="elevate",
        passive_voice={"passive_count": 1, "passive_sentence_ratio": 0.1},
    )
    assert prompt is not None
    assert "active voice" not in prompt.lower()


def test_preserve_mode_ignores_sentence_economy_and_passive_voice():
    # These params must have zero effect outside elevate mode, even
    # when both would clearly cross the elevate thresholds.
    prompt = build_correction_prompt(
        _empty_delta(), mode="preserve",
        sentence_economy={"grade_level": 20.0, "avg_sentence_length": 30.0,
                           "avg_syllables_per_word": 2.5},
        passive_voice={"passive_count": 10, "passive_sentence_ratio": 0.9},
    )
    assert prompt is None


def test_elevate_mode_handles_none_signals_gracefully():
    # Matches the real call pattern when sentence_economy returns None
    # (e.g. short text) — must not raise, must still return the base
    # elevate instructions.
    prompt = build_correction_prompt(
        _empty_delta(), mode="elevate",
        sentence_economy=None, passive_voice=None,
    )
    assert prompt is not None
    assert "line edit" in prompt.lower()
