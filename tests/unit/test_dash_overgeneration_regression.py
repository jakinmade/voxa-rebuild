"""
Regression test — 13 Sept 2026, same session as the dash-preservation
fix but a distinct bug it exposed: fixing keep_dashes to trigger on a
single genuine in-input occurrence used one liberal instruction ("use
them naturally") for both that case and the real established-habit
case. The model read "use naturally" as license to convert unrelated
commas into new dashes too. Real case: one genuine dash in
"Scott — following up..." came back as six in the output. Also covers
score_ai_tells' matching fix: it previously hard-flagged every
surviving em dash unconditionally, even ones genuinely in the input.
"""
from prompts import _split_dashes_deterministic
from voice_engine import score_ai_tells


def test_split_dashes_keeps_first_n_converts_the_rest():
    text = "Scott — following up on this — and also this — and this."
    result = _split_dashes_deterministic(text, keep_dashes=True, max_dashes=1)
    assert result.count("—") == 1
    assert result.startswith("Scott — following up on this")


def test_split_dashes_unlimited_when_max_dashes_none():
    text = "Scott — following up — again — and again."
    result = _split_dashes_deterministic(text, keep_dashes=True, max_dashes=None)
    assert result == text


def test_split_dashes_zero_cap_converts_everything():
    text = "Scott — following up on this."
    result = _split_dashes_deterministic(text, keep_dashes=True, max_dashes=0)
    assert "—" not in result


def test_score_ai_tells_does_not_flag_dash_genuinely_in_input():
    original = "Scott — following up on the CLEARANCE test link."
    output = "Scott — following up on the CLEARANCE test link."
    result = score_ai_tells(output, original_input_text=original)
    assert result["em_dash_count"] == 0, "A dash genuinely in the original input must not be flagged"


def test_score_ai_tells_flags_only_excess_dashes():
    original = "Scott — following up on the CLEARANCE test link."
    # Model invented 5 extra dashes beyond the 1 genuine one.
    output = (
        "Scott — following up on the CLEARANCE test link. "
        "The scenario — dashboard green while an agent — quietly does "
        "the wrong thing — is exactly what CLEARANCE — is built to catch — like this."
    )
    result = score_ai_tells(output, original_input_text=original)
    assert result["em_dash_count"] == 5, f"Expected exactly 5 excess dashes flagged, got {result['em_dash_count']}"
