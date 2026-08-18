"""
Tests for build_correction_prompt's platform_format parameter (18 Aug
2026) — the paragraph-restructuring feature, generalised the same
session from its original LinkedIn-only form once it became clear the
underlying convention (short paragraphs, hook-first) wasn't LinkedIn-
specific at all, and that a genuinely different target (email) was
worth building alongside it rather than stretching one instruction to
cover both. See build_correction_prompt's own docstring for the full
rationale on why this stays separate from elevate mode's line-editing.

What these tests actually guard, in order of how badly it would break
things if wrong:
1. Any platform_format value through mode="preserve" must be a hard
   no-op — checked defensively inside the function itself.
2. The deprecated linkedin_format=True alias must still work exactly
   as platform_format="social" would, so no existing caller breaks.
3. "social" and "email" must produce genuinely different instructions,
   not the same text with a different label — email must NOT get the
   hook-first/reorder-to-the-top permission social gets.
4. The contradiction-avoidance fix (inline exception on the old-to-new
   rule, relaxed closing framing) must hold for BOTH targets, not just
   the one it was originally built against.
"""
from prompts import build_correction_prompt


def _delta_with_one_miss():
    return {
        "hedge_density": {"verdict": "MISSED", "baseline": 0.8, "output": 0.1},
    }


def _empty_delta():
    return {}


# ------------------------------------------------------------------
# Safety: preserve mode must never be reachable via this parameter
# ------------------------------------------------------------------

def test_platform_format_is_ignored_in_preserve_mode():
    with_social = build_correction_prompt(
        _delta_with_one_miss(), mode="preserve", platform_format="social"
    )
    with_email = build_correction_prompt(
        _delta_with_one_miss(), mode="preserve", platform_format="email"
    )
    without = build_correction_prompt(_delta_with_one_miss(), mode="preserve")
    assert with_social == without
    assert with_email == without
    assert "PLATFORM FORMAT" not in with_social
    assert "PLATFORM FORMAT" not in with_email


def test_platform_format_ignored_in_preserve_mode_with_empty_delta():
    assert build_correction_prompt(_empty_delta(), mode="preserve", platform_format="social") is None
    assert build_correction_prompt(_empty_delta(), mode="preserve", platform_format="email") is None


def test_unrecognised_platform_format_value_is_a_no_op():
    """Same fail-safe pattern as an unrecognised mode string — an
    unexpected value must not accidentally activate restructuring."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="twitter-thread-v2"
    )
    assert "PLATFORM FORMAT" not in result


def test_none_platform_format_is_the_default_and_a_no_op():
    result = build_correction_prompt(_delta_with_one_miss(), mode="elevate", platform_format=None)
    no_arg = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    assert result == no_arg
    assert "PLATFORM FORMAT" not in result


# ------------------------------------------------------------------
# Backward compatibility: the deprecated linkedin_format alias
# ------------------------------------------------------------------

def test_linkedin_format_true_is_an_alias_for_social():
    via_alias = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    via_new_param = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert via_alias == via_new_param
    assert "PLATFORM FORMAT (social" in via_alias


def test_explicit_platform_format_wins_over_the_deprecated_alias():
    """If a caller somehow passes both, the new explicit parameter
    takes precedence rather than being silently overridden by the
    deprecated one."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate",
        linkedin_format=True, platform_format="email",
    )
    assert "PLATFORM FORMAT (email" in result
    assert "PLATFORM FORMAT (social" not in result


def test_linkedin_format_false_does_not_override_platform_format():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate",
        linkedin_format=False, platform_format="social",
    )
    assert "PLATFORM FORMAT (social" in result


# ------------------------------------------------------------------
# Default off — no change to existing elevate behaviour
# ------------------------------------------------------------------

def test_default_platform_format_is_none_and_elevate_is_unchanged():
    no_arg = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    assert "PLATFORM FORMAT" not in no_arg
    assert "Preserve everything else exactly." in no_arg
    assert "reordering and re-paragraphing" not in no_arg


# ------------------------------------------------------------------
# "social" target — short paragraphs, hook-first, reorder permitted
# ------------------------------------------------------------------

def test_social_adds_platform_instruction():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert result is not None
    assert "PLATFORM FORMAT (social" in result
    assert "short" in result.lower() and "paragraph" in result.lower()
    assert "hook" in result.lower()


def test_social_permits_reordering():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert "you may reorder" in result.lower()
    assert "never move sentences relative to each other" in result  # the general rule, still present
    assert "handled separately below under" in result  # the inline exception to it


def test_social_relaxes_the_closing_preservation_line():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert "reordering and re-paragraphing" in result
    assert "Preserve everything else exactly." not in result


def test_social_forbids_new_content():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert "does not already exist in the text" in result


def test_social_triggers_even_with_empty_delta():
    result = build_correction_prompt(_empty_delta(), mode="elevate", platform_format="social")
    assert result is not None
    assert "PLATFORM FORMAT (social" in result


def test_social_composes_with_existing_elevate_instructions():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="social"
    )
    assert "LINE EDIT (economy)" in result
    assert "PLATFORM FORMAT (social" in result


# ------------------------------------------------------------------
# "email" target — genuinely different: no hook-first, greeting/
# sign-off must stay in place
# ------------------------------------------------------------------

def test_email_adds_platform_instruction():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert result is not None
    assert "PLATFORM FORMAT (email" in result


def test_email_does_not_permit_hook_first_promotion():
    """The core difference from 'social' — this is the whole reason
    email needed its own instruction rather than reusing social's."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert "hook-first opening would" in result
    assert "do NOT" in result
    assert "opening hook" not in result  # social's specific phrasing must not leak in


def test_email_keeps_greeting_and_signoff_in_place():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert "greeting at the very start" in result
    assert "sign-off at the very end" in result


def test_email_still_permits_reordering_within_the_body():
    """Email isn't pure preserve-only either — it still gets the
    inline exception on the old-to-new rule and the relaxed closing
    framing, same mechanism as social, just with different scope."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert "handled separately below under" in result
    assert "Preserve everything else exactly." not in result


def test_email_forbids_new_content():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert "does not already exist in the text" in result


def test_email_triggers_even_with_empty_delta():
    result = build_correction_prompt(_empty_delta(), mode="elevate", platform_format="email")
    assert result is not None
    assert "PLATFORM FORMAT (email" in result


def test_social_and_email_are_mutually_exclusive_in_one_call():
    """Sanity check on the elif structure — a single call can never
    produce both instruction blocks at once. 'PLATFORM FORMAT' as a
    bare string legitimately appears more than once (the framing
    line, the old-to-new inline exception, and the heading itself) —
    checking the two distinct HEADINGS specifically, not a raw count."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", platform_format="email"
    )
    assert "PLATFORM FORMAT (social" not in result
    assert result.count("PLATFORM FORMAT (email") == 1


# ------------------------------------------------------------------
# Dropped-entity priority language, both targets
# ------------------------------------------------------------------

def test_dropped_entity_priority_language_present_for_social():
    delta = {}
    semantic = {"dropped_entities": ["John"], "attribution_swaps": []}
    result = build_correction_prompt(delta, semantic, mode="elevate", platform_format="social")
    assert "dropped specific facts from the original: John" in result
    assert "NOT optional and is not overridden by the platform-format instruction" in result


def test_dropped_entity_priority_language_present_for_email():
    delta = {}
    semantic = {"dropped_entities": ["John"], "attribution_swaps": []}
    result = build_correction_prompt(delta, semantic, mode="elevate", platform_format="email")
    assert "dropped specific facts from the original: John" in result
    assert "NOT optional and is not overridden by the platform-format instruction" in result


def test_dropped_entity_priority_language_absent_without_platform_format():
    delta = {}
    semantic = {"dropped_entities": ["John"], "attribution_swaps": []}
    result = build_correction_prompt(delta, semantic, mode="elevate", platform_format=None)
    assert "dropped specific facts from the original: John" in result
    assert "NOT optional and is not overridden by the platform-format instruction" not in result
