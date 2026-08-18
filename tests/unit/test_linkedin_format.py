"""
Tests for the linkedin_format flag on build_correction_prompt (18 Aug
2026) — the paragraph-restructuring feature deliberately kept SEPARATE
from elevate mode's line-editing, not folded into it. See
build_correction_prompt's own docstring for the full rationale: it's
the one instruction set in this function permitted to move sentences
relative to each other, which is exactly what elevate mode's own
tests (test_elevate_mode.py) guarantee it will never do on its own.

What these tests actually guard, in order of how badly it would break
things if wrong:
1. linkedin_format=True through mode="preserve" must be a hard no-op —
   the flag is UI-gated (only shown once elevate is selected), but the
   function itself must not trust that; a future call site passing it
   incorrectly must not be able to trigger paragraph restructuring
   through the preserve path.
2. linkedin_format=False (the default) must be byte-for-byte identical
   to the pre-existing elevate behaviour — this feature must not
   change anything for the many existing elevate-mode tests/behaviour
   unless explicitly opted into.
3. When active, the reorder/re-paragraph permission must actually
   reach the prompt, AND the closing "preserve everything else
   exactly" framing must be relaxed enough not to contradict it —
   the exact bug caught and fixed before this shipped (see the
   linkedin_active branch in build_correction_prompt).
"""
from prompts import build_correction_prompt


def _delta_with_one_miss():
    return {
        "hedge_density": {"verdict": "MISSED", "baseline": 0.8, "output": 0.1},
    }


def _empty_delta():
    return {}


# ------------------------------------------------------------------
# Safety: preserve mode must never be reachable via this flag
# ------------------------------------------------------------------

def test_linkedin_format_is_ignored_in_preserve_mode_with_a_miss():
    """The core safety guarantee: passing linkedin_format=True through
    mode='preserve' must not add restructuring instructions, and must
    not even change the closing framing — a defensive re-check inside
    the function, not just a UI-level gate."""
    with_flag = build_correction_prompt(
        _delta_with_one_miss(), mode="preserve", linkedin_format=True
    )
    without_flag = build_correction_prompt(
        _delta_with_one_miss(), mode="preserve", linkedin_format=False
    )
    assert with_flag == without_flag
    assert "PLATFORM FORMAT" not in with_flag
    assert "reorder" not in with_flag.lower()


def test_linkedin_format_is_ignored_in_preserve_mode_with_empty_delta():
    """Preserve mode with nothing missed returns None regardless of
    the flag — must not become the one way to force a correction call
    out of preserve mode."""
    assert build_correction_prompt(_empty_delta(), mode="preserve", linkedin_format=True) is None


def test_unknown_mode_with_linkedin_format_still_fails_safe():
    """Same fail-safe as unknown mode already gets for elevate itself
    (test_unknown_mode_value_fails_safe_to_preserve in the sibling
    file) — an unrecognised mode string must not let linkedin_format
    sneak restructuring in either."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="typo-elevate", linkedin_format=True
    )
    assert result is not None
    assert "PLATFORM FORMAT" not in result


# ------------------------------------------------------------------
# Default off — no change to existing elevate behaviour
# ------------------------------------------------------------------

def test_default_linkedin_format_is_false_and_elevate_is_unchanged():
    """No caller passing linkedin_format at all (every pre-existing
    call site) must produce byte-identical output to explicitly
    passing False — this feature must be fully opt-in."""
    no_arg = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    explicit_false = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=False
    )
    assert no_arg == explicit_false
    assert "PLATFORM FORMAT" not in no_arg


def test_elevate_mode_closing_framing_unchanged_when_flag_is_off():
    """The exact preservation-line wording that existed before this
    feature must survive untouched when linkedin_format is off — this
    is the regression guard for the fix to the contradiction bug
    (elevate's original 'preserve everything else exactly' framing
    would have directly contradicted the new reorder permission if
    the two branches weren't kept genuinely separate)."""
    result = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    assert "Preserve everything else exactly." in result
    assert "reordering and re-paragraphing" not in result


# ------------------------------------------------------------------
# Active: elevate + linkedin_format=True
# ------------------------------------------------------------------

def test_linkedin_format_adds_platform_instruction_in_elevate_mode():
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    assert result is not None
    assert "PLATFORM FORMAT" in result
    assert "short" in result.lower() and "paragraph" in result.lower()
    assert "hook" in result.lower()


def test_linkedin_format_triggers_even_with_empty_delta():
    """Same shape as elevate's own unconditional trigger (test_elevate_
    mode_returns_prompt_even_on_empty_delta) — restructuring should be
    offered even when no voice-dimension target was missed."""
    result = build_correction_prompt(_empty_delta(), mode="elevate", linkedin_format=True)
    assert result is not None
    assert "PLATFORM FORMAT" in result


def test_linkedin_format_composes_with_existing_elevate_instructions():
    """Layered on top of, not instead of — elevate's own line-edit
    instructions (old-to-new ordering, economy) must still be present
    alongside the new platform-format one."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    assert "old-to-new" in result.lower() or "LINE EDIT (old-to-new" in result
    assert "LINE EDIT (economy)" in result
    assert "PLATFORM FORMAT" in result


def test_linkedin_format_relaxes_the_closing_preservation_line():
    """The fix for the contradiction this feature could have shipped
    with: when active, the closing framing must explicitly carve out
    the restructuring permission rather than blanket-forbidding all
    change, which would directly contradict the PLATFORM FORMAT
    instruction telling the model to reorder and re-paragraph."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    assert "reordering and re-paragraphing" in result
    assert "Preserve everything else exactly." not in result


def test_linkedin_format_instruction_forbids_new_content():
    """The permission to restructure must be paired with an explicit
    content-fabrication guard — reordering is allowed, inventing new
    sentences is not."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    assert "may not cut" in result.lower() or "may NOT cut" in result
    assert "does not already exist in the text" in result


def test_linkedin_format_explicitly_permits_reordering_unlike_base_elevate():
    """The one deliberate exception in this whole function: base
    elevate forbids moving sentences relative to each other
    (see test_elevate_mode_does_not_authorise_sentence_reordering in
    the sibling file); linkedin_format is the one instruction
    explicitly allowed to do exactly that. Both must be true at once
    in the combined prompt — the general rule and its one stated
    exception, not a silent contradiction."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    # The general rule (from elevate's own line-edit instruction) is
    # still present, unmodified...
    assert "never move sentences relative to each other" in result
    # ...alongside its one explicit, separately-scoped exception.
    assert "you may reorder" in result.lower()


def test_old_to_new_instruction_carries_an_inline_exception_when_linkedin_active():
    """Caught during independent review, not by the tests above:
    instruction #2 says 'never move sentences relative to each other'
    and instruction #6 (PLATFORM FORMAT) says the opposite -- a model
    reading the numbered list would have to reconcile that using only
    the opening paragraph's blanket caveat, several sentences away.
    Fixed by attaching the exception directly to the rule it modifies,
    not just to the framing above the list. This test guards that the
    inline exception is actually present, not just the framing one
    (already covered by test_linkedin_format_relaxes_the_closing_
    preservation_line above)."""
    result = build_correction_prompt(
        _delta_with_one_miss(), mode="elevate", linkedin_format=True
    )
    assert "handled separately below under" in result
    assert "PLATFORM FORMAT" in result
    # The exception must sit on the SAME instruction as the rule it
    # modifies, not floating disconnected — checked structurally by
    # confirming both fragments occur within one contiguous stretch
    # of text (no other numbered instruction boundary between them).
    idx_rule = result.index("never move sentences relative to each other")
    idx_exception = result.index("handled separately below under")
    idx_next_item = result.index("LINE EDIT (economy)")
    assert idx_rule < idx_exception < idx_next_item


def test_old_to_new_instruction_has_no_inline_exception_when_linkedin_inactive():
    """Companion regression guard: base elevate mode (no linkedin_
    format) must NOT carry the inline exception clause -- it only
    makes sense in the context of a PLATFORM FORMAT instruction that,
    without the flag, was never added."""
    result = build_correction_prompt(_delta_with_one_miss(), mode="elevate")
    assert "handled separately below under" not in result
    assert "PLATFORM FORMAT" not in result


def test_dropped_entity_restoration_has_priority_language_when_linkedin_active():
    """Real bug, found live: a render dropped 'Hi John,' entirely under
    linkedin_format restructuring, despite the dropped-entity
    restoration instruction already being present in the same
    correction call. The model prioritised platform convention over
    an explicit restore instruction. Fixed by making both instructions
    reference each other explicitly rather than trusting numbered-list
    order to imply priority."""
    delta = {}
    semantic = {"dropped_entities": ["John"], "attribution_swaps": []}
    result = build_correction_prompt(delta, semantic, mode="elevate", linkedin_format=True)
    assert result is not None
    assert "dropped specific facts from the original: John" in result
    assert "NOT optional and is not overridden by the platform-format instruction" in result
    assert "restructuring is never a reason to drop it again" in result


def test_dropped_entity_restoration_has_no_priority_language_without_linkedin():
    """Companion regression guard: the priority cross-reference only
    makes sense when a PLATFORM FORMAT instruction actually exists --
    must not appear (referencing a non-existent instruction) when
    linkedin_format is off."""
    delta = {}
    semantic = {"dropped_entities": ["John"], "attribution_swaps": []}
    result = build_correction_prompt(delta, semantic, mode="elevate", linkedin_format=False)
    assert result is not None
    assert "dropped specific facts from the original: John" in result
    assert "NOT optional and is not overridden by the platform-format instruction" not in result
