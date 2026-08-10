"""
Tests for dev_tools/harness.py's Sample 2 gate logic.

Confirms run_fingerprint_stage() mirrors app.py's screen_sample2() gate
exactly: sample2_completions[0] AND sample2_completions[3] are both
required, each at SAMPLE2_REQUIRED_MIN_WORDS words - picked for register
contrast (defensive-professional vs unfiltered-emotional), not just "the
first one". Indices 1 and 2 remain optional enrichment, same as the live
app's "Deepen your fingerprint" expander.

This matters because the harness exists specifically so results here are
results the real product would produce. A harness run that let a persona
through when the live app would block Screen 3's Continue button (or
blocked one the live app would accept) would silently report numbers the
live app could never actually produce - the exact drift this suite is
here to catch.
"""
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEV_TOOLS = str(_REPO_ROOT / "dev_tools")
if _DEV_TOOLS not in sys.path:
    sys.path.insert(0, _DEV_TOOLS)

import harness  # noqa: E402


def _persona(sample1=None, sample2_completions=None):
    return {
        "persona_name": "test",
        "sample1_text": sample1 or (
            "This is a perfectly ordinary sample of writing that easily "
            "clears the ten word floor for sample one."
        ),
        "sample2_completions": (
            sample2_completions if sample2_completions is not None else []
        ),
        "render_input": "Some AI-ish text to rewrite.",
    }


# ---------------------------------------------------------------------------
# Sanity check - sample1_text floor is unchanged by this fix
# ---------------------------------------------------------------------------

def test_sample1_below_floor_errors():
    result = harness.run_fingerprint_stage(_persona(sample1="Too short."))
    assert "error" in result
    assert "sample1_text" in result["error"]


# ---------------------------------------------------------------------------
# Gate: sample2_completions[0] and [3] required, SAMPLE2_REQUIRED_MIN_WORDS
# floor each
# ---------------------------------------------------------------------------

_OK4 = [
    "this is the first required starter and it clears the floor easily",
    "a second optional starter with a handful of words in it",
    "a third optional one, also filled in for good measure here",
    "and a fourth, rounding out the full set of four answers",
]


def test_missing_sample2_completions_key_errors():
    persona = _persona()
    del persona["sample2_completions"]
    result = harness.run_fingerprint_stage(persona)
    assert "error" in result
    assert "sample2_completions" in result["error"]


def test_empty_sample2_completions_list_errors():
    result = harness.run_fingerprint_stage(_persona(sample2_completions=[]))
    assert "error" in result
    assert "sample2_completions" in result["error"]


def test_first_starter_empty_string_errors():
    completions = list(_OK4)
    completions[0] = ""
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result


def test_fourth_starter_empty_string_errors():
    completions = list(_OK4)
    completions[3] = ""
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result


def test_first_starter_whitespace_only_errors():
    completions = list(_OK4)
    completions[0] = "   \n\t  "
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result


def test_first_starter_under_floor_errors():
    under = "one two three four five six seven eight nine"  # 9 words
    assert len(under.split()) == harness.SAMPLE2_REQUIRED_MIN_WORDS - 1
    completions = list(_OK4)
    completions[0] = under
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result
    assert str(harness.SAMPLE2_REQUIRED_MIN_WORDS) in result["error"]


def test_fourth_starter_under_floor_errors():
    under = "one two three four five six seven eight nine"  # 9 words
    completions = list(_OK4)
    completions[3] = under
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result


def test_both_required_starters_exactly_at_floor_passes():
    exactly = "one two three four five six seven eight nine ten"  # 10 words
    assert len(exactly.split()) == harness.SAMPLE2_REQUIRED_MIN_WORDS
    completions = ["", "", "", ""]
    completions[0] = exactly
    completions[3] = exactly
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" not in result
    assert result["sample2_met_floor"] is True
    assert result["sample2_required_word_count"][0] == harness.SAMPLE2_REQUIRED_MIN_WORDS
    assert result["sample2_required_word_count"][3] == harness.SAMPLE2_REQUIRED_MIN_WORDS


def test_only_required_starters_filled_passes_with_no_optional_completions():
    completions = ["", "", "", ""]
    completions[0] = "this single starter easily clears the required word floor on its own merit"
    completions[3] = "and this fourth starter also clears the required word floor on its own"
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" not in result
    assert result["sample2_met_floor"] is True
    expected = len(completions[0].split()) + len(completions[3].split())
    assert result["sample2_word_count"] == expected
    # Gate passed -> sample2 signal must be merged into the baseline,
    # same as app.py's unconditional merge once Continue is clickable.
    assert result["starter_baseline"] is not None
    # Two register-distinct samples (screen 1 + both required starters
    # scored separately) means stability is actually computable.
    assert result["dimension_stability"]["sample_count"] == 3


def test_all_four_starters_filled_combines_word_count():
    result = harness.run_fingerprint_stage(_persona(sample2_completions=_OK4))
    assert "error" not in result
    expected_combined = sum(len(c.split()) for c in _OK4)
    assert result["sample2_word_count"] == expected_combined


def test_optional_starters_with_only_whitespace_excluded_from_combined_count():
    completions = list(_OK4)
    completions[1] = "   "
    completions[2] = "\n\t"
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" not in result
    assert result["sample2_word_count"] == len(completions[0].split()) + len(completions[3].split())


def test_second_starter_alone_does_not_satisfy_the_gate():
    # Only completions[1] is filled - completions[0] and [3], the
    # required pair, are empty. The live app's Continue button stays
    # disabled in this exact shape, since it checks both required boxes.
    result = harness.run_fingerprint_stage(_persona(sample2_completions=[
        "",
        "a full and proper answer to the second optional starter here",
        "",
        "",
    ]))
    assert "error" in result


def test_first_starter_alone_without_fourth_does_not_satisfy_the_gate():
    completions = ["", "", "", ""]
    completions[0] = "this single starter easily clears the required word floor on its own merit"
    result = harness.run_fingerprint_stage(_persona(sample2_completions=completions))
    assert "error" in result


# ---------------------------------------------------------------------------
# run_persona() wrapping
# ---------------------------------------------------------------------------

def test_run_persona_reports_error_status_when_gate_fails():
    persona = _persona(sample2_completions=["too short", "", "", "too short"])
    result = harness.run_persona(persona, dry_run=True, api_key=None)
    assert result["status"] == "error"
    assert "error" in result["fingerprint_stage"]


def test_run_persona_dry_run_completes_when_gate_passes():
    completions = ["", "", "", ""]
    completions[0] = "this required starter alone clears the ten word floor by itself"
    completions[3] = "and this fourth required starter also clears the ten word floor"
    persona = _persona(sample2_completions=completions)
    result = harness.run_persona(persona, dry_run=True, api_key=None)
    assert result["status"] == "dry_run_complete"


# ---------------------------------------------------------------------------
# Real fixtures - every checked-in persona must still clear the new gate.
# Guards against a persona file drifting stale the same way harness.py
# itself just did.
# ---------------------------------------------------------------------------

_PERSONAS_DIR = _REPO_ROOT / "dev_tools" / "personas"
_PERSONA_FILES = sorted(_PERSONAS_DIR.glob("*.json"))


def test_persona_fixtures_exist():
    # If this fails, the glob above is pointed at the wrong place and
    # every parametrized test below is silently not running at all.
    assert len(_PERSONA_FILES) >= 1


@pytest.mark.parametrize("persona_path", _PERSONA_FILES, ids=lambda p: p.stem)
def test_all_checked_in_personas_clear_the_new_gate(persona_path):
    persona = json.loads(persona_path.read_text())
    result = harness.run_fingerprint_stage(persona)
    assert "error" not in result, (
        f"{persona_path.name} fails the new Screen 3 gate: {result.get('error')}"
    )
    assert result["sample2_met_floor"] is True
