"""
LLM/engine boundary contract (30 Aug 2026, voice-review item #3).

The review's core architectural principle, already stated in this
repo's README: the deterministic engine decides, the LLM expresses.
Audited this session — every live client.messages.create() call site
was checked directly (not assumed) and confirmed to follow the same
pattern: a deterministic function decides WHAT needs to happen (voice
targets, whether a correction is needed, whether output passes), the
model is asked to phrase that within fixed constraints, and a
deterministic function VERIFIES the result afterward. No violation
found in any of the four current call sites.

This file turns that one-time audit into a standing, checked
constraint rather than leaving it as something someone has to re-audit
by hand next time. Each test below enumerates ONE known LLM call site
and asserts its specific guard is still textually present in the
source near it. This is deliberately narrow — it confirms the FOUR
call sites audited this session haven't quietly lost their guard, not
a general static-analysis proof that covers hypothetical future call
sites. A fifth call site added later needs its own test added here;
that omission is exactly the kind of gap this file exists to make
visible, since a PR that adds an LLM call with no corresponding test
here is a PR this file doesn't protect.

Reads the actual source files as text rather than importing and
exercising behaviour — the property under test is "is this guard
still written in the code", not "does it produce a particular output"
(that's what the rest of the test suite already covers per-function).
"""
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PY = (_REPO_ROOT / "app.py").read_text()
PROMPTS_PY = (_REPO_ROOT / "prompts.py").read_text()


def _lines_around(source: str, anchor: str, before: int = 5, after: int = 25) -> str:
    """Returns a window of lines around the first occurrence of anchor,
    for scoping a guard-presence check to near a specific call site
    rather than anywhere in the whole file (which could pass even if
    the guard moved somewhere unrelated)."""
    lines = source.splitlines()
    idx = next((i for i, l in enumerate(lines) if anchor in l), None)
    assert idx is not None, f"Anchor not found — call site may have moved or been removed: {anchor!r}"
    start = max(0, idx - before)
    end = min(len(lines), idx + after)
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# Call site 1: main render (_run_render, app.py) — plain-text completion.
# Guard: deterministic sweep + locale application immediately after, and
# (checked separately by the rest of the suite) score_render_delta /
# compute_risk verify the result before it's trusted.
# ---------------------------------------------------------------------------

def test_main_render_call_is_followed_by_deterministic_sweep():
    window = _lines_around(APP_PY, 'system=system, messages=[{"role": "user", "content": input_text}]')
    assert "_regex_sweep(" in window, (
        "Main render call site no longer followed by _regex_sweep — the "
        "deterministic cleanup pass may have been removed or moved out of "
        "range. Update this test's `after` window or investigate."
    )


# ---------------------------------------------------------------------------
# Call site 2: correction pass (_run_render, app.py) — schema-constrained,
# not plain text. Guard: forced tool_choice (no free-text channel to
# narrate reasoning into) AND response_looks_contaminated as a second,
# independent check on top of the schema constraint.
# ---------------------------------------------------------------------------

def test_correction_pass_is_schema_constrained_not_plain_text():
    window = _lines_around(APP_PY, "correction_response = client.messages.create(")
    assert 'tool_choice={"type": "tool", "name": "return_correction"}' in window, (
        "Correction pass no longer forces a tool call — this reopens the "
        "free-text channel the schema constraint exists to close."
    )


def test_correction_pass_result_is_independently_verified():
    window = _lines_around(APP_PY, "correction_response = client.messages.create(")
    assert "response_looks_contaminated(" in window, (
        "Correction pass no longer checked with response_looks_contaminated "
        "— the schema constraint alone was never meant to be the only guard "
        "(see that function's own docstring: 'not to be exhaustive on its own')."
    )


def test_correction_pass_fails_closed_not_open():
    window = _lines_around(APP_PY, "correction_response = client.messages.create(", after=35)
    assert "pre_llm_correction" in window, (
        "Correction pass's fail-closed fallback (pre_llm_correction) not "
        "found nearby — a twice-failed correction must fall back to the "
        "pre-correction text, not ship a response that failed the check."
    )


# ---------------------------------------------------------------------------
# Call site 3: grammar-fix pass (_grammar_fix_pass, prompts.py) — plain
# text, but tightly scoped ("Fix errors only. Never rewrite.") with an
# explicit DO NOT TOUCH list, not open-ended judgment.
# ---------------------------------------------------------------------------

def test_grammar_fix_pass_is_explicitly_scoped_to_errors_only():
    window = _lines_around(PROMPTS_PY, "def _grammar_fix_pass(", before=0, after=60)
    assert "Fix errors only" in window
    assert "Never rewrite" in window
    assert "DO NOT TOUCH" in window


# ---------------------------------------------------------------------------
# Call site 4: voice profile summary (app.py) — free-text by design (it's
# a natural-language description, not a decision), but still swept
# through the same deterministic backstop as render output.
# ---------------------------------------------------------------------------

def test_voice_profile_summary_is_swept_through_the_same_deterministic_backstop():
    window = _lines_around(APP_PY, "system=build_voice_profile_summary_prompt()")
    assert "_regex_sweep(" in window, (
        "Voice profile summary generation no longer swept through "
        "_regex_sweep — free-text generation still needs the same "
        "deterministic backstop render output gets."
    )


# ---------------------------------------------------------------------------
# Inventory check — if this ever fails, someone added a fifth
# client.messages.create() call site with no corresponding test above.
# Update the count AND add a test for the new call site in the same PR.
# ---------------------------------------------------------------------------

def test_known_llm_call_site_count_has_not_silently_grown():
    app_count = APP_PY.count("client.messages.create(")
    prompts_count = PROMPTS_PY.count("client.messages.create(")
    total = app_count + prompts_count
    assert total == 4, (
        f"Expected 4 known client.messages.create() call sites (main render, "
        f"correction pass, grammar-fix pass, voice profile summary), found "
        f"{total}. If this is a deliberate new call site, add a test for its "
        f"guard above and update this count in the same change — that's the "
        f"whole point of this file."
    )
