"""
Tests for build_correction_prompt's closing ABSOLUTE RULES block.

Previously this only banned em dashes and specified UK English -
much thinner than the main render's full base_rules (10 items,
including verbose openers, filler transitions, corporate filler).
Since the correction pass fires on most renders (see
correction_pass_decision in the logs) and edits text that's already
been through the main render's guardrails + the regex sweep, a thin
rule set here meant correction edits could quietly reintroduce
exactly the AI tells everything upstream had just removed.
"""
from prompts import build_correction_prompt


def _delta_with_one_miss():
    return {
        "hedge_density": {"verdict": "MISSED", "baseline": 0.8, "output": 0.1},
    }


def test_correction_prompt_bans_verbose_openers():
    prompt = build_correction_prompt(_delta_with_one_miss())
    assert prompt is not None
    assert "verbose opener" in prompt.lower()


def test_correction_prompt_bans_filler_transitions():
    prompt = build_correction_prompt(_delta_with_one_miss())
    assert "filler transition" in prompt.lower()


def test_correction_prompt_bans_corporate_filler():
    prompt = build_correction_prompt(_delta_with_one_miss())
    lowered = prompt.lower()
    assert "leverage" in lowered
    assert "corporate filler" in lowered


def test_correction_prompt_still_bans_em_dashes_and_uk_english():
    """The original two rules must survive the expansion, not just
    the new ones added alongside them."""
    prompt = build_correction_prompt(_delta_with_one_miss())
    assert "no em dashes" in prompt.lower()
    assert "uk english" in prompt.lower()


def test_correction_prompt_rules_apply_to_new_wording_not_just_the_input():
    """The rules must be framed as applying to whatever the model
    introduces while correcting, not just describing the input text -
    otherwise a model could read them as already-satisfied and ignore
    them for its own edits."""
    prompt = build_correction_prompt(_delta_with_one_miss())
    lowered = prompt.lower()
    assert "reintroduce" in lowered or "new wording" in lowered or "you introduce" in lowered


def test_correction_prompt_bans_fabrication():
    """Added 5 Sept 2026: this correction pass is itself a place
    fabrication can be reintroduced as a side effect of paraphrasing
    while fixing an unrelated dimension - confirmed live the same day
    (see PR history) that it reintroduced an invented CI/CD directive
    after a dedicated fabrication-fix pass had already cleared one.
    The general correction prompt needs its own explicit guard, not
    just the two render-path prompts."""
    prompt = build_correction_prompt(_delta_with_one_miss())
    lowered = prompt.lower()
    assert "do not invent specifics" in lowered
    assert "fabricat" in lowered
