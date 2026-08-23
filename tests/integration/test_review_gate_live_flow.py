"""
Reproduces (or rules out) a live bug report: a person confirmed a
High-risk review gate (checked the box, clicked "Show my rewritten
text") and the rewritten text never appeared. Drives the REAL
screen_render() UI via AppTest - seeds a completed render forced to
High risk, then simulates the actual checkbox check and button click
a person would perform, exactly like test_app_render_pipeline.py's
existing pattern for driving Screen 4 without a real Anthropic call.
"""
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness

_APP_PATH = str(Path(__file__).resolve().parents[2] / "app.py")

FAKE_LLM_OUTPUT = (
    "I see it as the clearest way forward—we should leverage this "
    "approach across the team."
)

BASELINE_SAMPLE_1 = (
    "I think we should move fast on this. I want the team to focus on "
    "the core problem first, and then we can look at the edges."
)
BASELINE_SAMPLE_2 = (
    "I believe the data backs this up, and I think it is the right call "
    "for now. We need to move quickly and stay focused."
)


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _run_screen4_forced_high_risk():
    """Same seeded-baseline pattern as test_app_render_pipeline.py's
    _run_screen4_with_mocked_render, but forces the resulting report's
    risk to High afterward so the review gate actually engages -
    reproducing the reported scenario exactly, not a Low-risk render
    that would never show the gate at all."""
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.session_state["screen"] = 1  # skip landing screen (screen 0) in tests
            at.run()

            combined = BASELINE_SAMPLE_1 + " " + BASELINE_SAMPLE_2
            metrics_1 = compute_baseline_metrics(BASELINE_SAMPLE_1)
            metrics_2 = compute_baseline_metrics(BASELINE_SAMPLE_2)

            at.session_state["screen"] = 4
            at.session_state["raw_text"] = BASELINE_SAMPLE_1
            at.session_state["baseline_fingerprint"] = compute_baseline_metrics(combined)
            at.session_state["observations"] = analyse_writing(combined)
            at.session_state["sample_fitness"] = _score_sample_fitness(combined)
            at.session_state["fingerprint_samples"] = [metrics_1, metrics_2]
            at.session_state["fingerprint_sample_texts"] = [BASELINE_SAMPLE_1, BASELINE_SAMPLE_2]
            at.session_state["sample2_completions"] = ["", "", "", ""]

            at.run()
            assert not at.exception, f"App raised on Screen 4 load: {at.exception}"

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception, f"App raised during render: {at.exception}"

            # Force High risk regardless of what the fake output actually
            # scored, so this test reproduces the reported scenario on
            # every run rather than depending on the fake text happening
            # to trip a hard-fail.
            report = dict(at.session_state["voice_report"])
            report["risk"] = "High"
            at.session_state["voice_report"] = report
            at.session_state["risk_reason"] = "dropped_entity"

            at.run()
            assert not at.exception, f"App raised after forcing High risk: {at.exception}"

            return at


def _run_screen4_forced_high_risk_with_dropped_entity(dropped_entities):
    """Same as _run_screen4_forced_high_risk, but also sets
    report['dropped_entities'] explicitly - the field that's been
    computed and logged all along but was never actually rendered to
    the person. Reproduces the real incident: risk=High,
    risk_reason=dropped_entity, and a specific missing name."""
    at = _run_screen4_forced_high_risk()
    report = dict(at.session_state["voice_report"])
    report["dropped_entities"] = dropped_entities
    at.session_state["voice_report"] = report
    at.run()
    assert not at.exception, f"App raised after forcing dropped_entities: {at.exception}"
    return at


def test_dropped_entity_warning_shown_when_entities_missing():
    """The core gap this closes: report['dropped_entities'] was
    computed, logged to Supabase, and used to drive risk=High - but
    never actually shown to the person. Only attribution_swaps had a
    dedicated warning before this fix."""
    at = _run_screen4_forced_high_risk_with_dropped_entity(["Scott"])
    markdown_html = " ".join(m.value for m in at.markdown if m.value)
    assert "Missing from the rewrite" in markdown_html
    assert "Scott" in markdown_html


def test_dropped_entity_warning_names_multiple_entities():
    at = _run_screen4_forced_high_risk_with_dropped_entity(["Scott", "Matt", "43"])
    markdown_html = " ".join(m.value for m in at.markdown if m.value)
    assert "Scott, Matt, 43" in markdown_html or all(
        e in markdown_html for e in ["Scott", "Matt", "43"]
    )


def test_no_dropped_entity_warning_when_nothing_dropped():
    """Sanity check the warning isn't shown unconditionally - only
    when dropped_entities is actually non-empty."""
    at = _run_screen4_forced_high_risk_with_dropped_entity([])
    markdown_html = " ".join(m.value for m in at.markdown if m.value)
    assert "Missing from the rewrite" not in markdown_html


def test_dropped_entity_warning_appears_before_the_gate_is_confirmed():
    """Critical ordering check: this warning must be visible on the
    GATED (blocked) screen, before the person decides whether to
    click through - not only after, when it's too late to inform
    that decision."""
    at = _run_screen4_forced_high_risk_with_dropped_entity(["Scott"])
    # Confirm we're still in the gated state (output hidden).
    text_area_values = [t.value for t in at.text_area]
    assert FAKE_LLM_OUTPUT not in "".join(v or "" for v in text_area_values), (
        "Test setup issue: gate isn't actually engaged, this check "
        "wouldn't be testing what it claims to."
    )
    markdown_html = " ".join(m.value for m in at.markdown if m.value)
    assert "Missing from the rewrite" in markdown_html


def test_output_has_a_clear_heading_not_an_unlabeled_blob():
    """17 Aug 2026 layout fix: the rewritten text previously had no
    heading of its own - 'Your writing.' actually headed the report
    card above it, and the output text_area sat unlabeled below a
    'Try one more sample' panel. Confirms the new heading is present."""
    at = _run_screen4_forced_high_risk_with_dropped_entity([])
    gate_checkbox = next(c for c in at.checkbox if "sending this as mine" in c.label)
    gate_checkbox.set_value(True)
    at.run()
    confirm_button = next(b for b in at.button if "Show my rewritten text" in b.label)
    confirm_button.click()
    at.run()
    assert not at.exception, f"App raised after confirming the gate: {at.exception}"

    markdown_values = [m.value for m in at.markdown if m.value]
    assert any("Your rewritten text" in v for v in markdown_values), (
        f"Expected a heading directly above the output, got markdown blocks: "
        f"{markdown_values}"
    )


def test_try_one_more_sample_panel_now_appears_after_the_output():
    """17 Aug 2026 layout fix (JA feedback): the caveat/deepen panel
    used to sit BETWEEN the report warnings and the output text_area,
    ahead of the actual rewritten text - confirmed nonsensical
    ordering. Now must appear after the output in element order."""
    at = _run_screen4_forced_high_risk()
    at.session_state["dimension_stability"] = {
        "sample_count": 2, "stable_count": 1, "volatile_count": 3,
    }
    at.run()
    gate_checkbox = next(c for c in at.checkbox if "sending this as mine" in c.label)
    gate_checkbox.set_value(True)
    at.run()
    confirm_button = next(b for b in at.button if "Show my rewritten text" in b.label)
    confirm_button.click()
    at.run()
    assert not at.exception, f"App raised after confirming the gate: {at.exception}"

    # at.main.children reflects true document/render order across every
    # widget type (markdown, expander, etc), unlike the type-specific
    # lists (at.markdown, at.expander) which each only preserve order
    # within their own type. This is what actually proves the fix.
    ordered = list(at.main.children.values())
    heading_index = next(
        i for i, el in enumerate(ordered)
        if getattr(el, "value", None) and "Your rewritten text" in el.value
    )
    panel_index = next(
        i for i, el in enumerate(ordered)
        if getattr(el, "label", None) and "Try one more sample" in el.label
    )
    assert heading_index < panel_index, (
        f"Expected the output heading (index {heading_index}) to appear "
        f"before the 'Try one more sample' panel (index {panel_index}) - "
        f"the whole point of the layout fix was moving the panel after "
        f"the output, not before it."
    )


def test_gate_shows_when_risk_is_high_and_output_is_hidden():
    """Sanity check the gate actually engages before testing the
    confirm path - if this fails, the bug is upstream of the
    confirmation click entirely."""
    at = _run_screen4_forced_high_risk()

    checkbox_labels = [c.label for c in at.checkbox]
    assert any("sending this as mine" in lbl for lbl in checkbox_labels), (
        f"Expected the review-gate checkbox, got: {checkbox_labels}"
    )

    # The output text must NOT appear anywhere in a text_area yet -
    # the whole point of the gate.
    text_area_values = [t.value for t in at.text_area]
    assert FAKE_LLM_OUTPUT not in "".join(v or "" for v in text_area_values), (
        "Output text is visible before the gate was confirmed - "
        "gate is not actually hiding the text."
    )


def test_confirming_the_gate_reveals_the_output():
    """The actual reported bug: check the box, click the confirm
    button, and see whether the rewritten text appears afterward."""
    at = _run_screen4_forced_high_risk()

    gate_checkbox = next(
        c for c in at.checkbox if "sending this as mine" in c.label
    )
    gate_checkbox.set_value(True)
    at.run()
    assert not at.exception, f"App raised after checking the gate checkbox: {at.exception}"

    confirm_button = next(
        b for b in at.button if "Show my rewritten text" in b.label
    )
    assert not confirm_button.disabled, (
        "Confirm button still disabled after checking the checkbox - "
        "checkbox state isn't reaching the button's disabled= check."
    )
    confirm_button.click()
    at.run()
    assert not at.exception, f"App raised after confirming the gate: {at.exception}"

    # Compare against the REAL final output, not the raw fake LLM
    # string — the render pipeline's deterministic sweep/correction
    # pass legitimately rewords the raw model output (em-dash split,
    # "leverage" stripped, plausibility shield, etc.), same as
    # test_render_output_passed_through_real_guardrail_sweep already
    # verifies elsewhere. The bug under test is whether the *actual*
    # final render_output becomes visible, not whether the untouched
    # fake string does.
    actual_output = at.session_state["render_output"]
    text_area_values = [t.value for t in at.text_area]
    joined = "".join(v or "" for v in text_area_values)
    assert actual_output in joined, (
        "Output text still not visible after confirming the review gate - "
        "this reproduces the reported bug. Expected to find the real "
        f"render_output {actual_output!r} somewhere in the visible "
        f"text_area values, found: {text_area_values}"
    )


def test_confirming_the_gate_reveals_the_output_WITH_low_confidence_caveat_active():
    """Faithful reproduction of the reported scenario specifically:
    High risk AND Low confidence (the caveat / 'Try one more sample'
    panel) both active at once - the one combination the test above
    doesn't cover, and the one JA's actual report showed on screen
    alongside the gate. If this passes and the simpler test above
    also passes, the gate mechanism itself is confirmed sound under
    every combination reproducible outside the live Railway
    deployment, and the remaining explanations are environmental
    (stale deploy, a live-only API response shape) rather than a
    logic bug in this code path."""
    at = _run_screen4_forced_high_risk()

    # Force the exact second condition from JA's report: unstable
    # dimension_stability -> confidence_caveat() returns a string ->
    # _deepen_fingerprint_panel renders expanded, alongside the gate.
    at.session_state["dimension_stability"] = {
        "sample_count": 2, "stable_count": 1, "volatile_count": 3,
    }
    at.run()
    assert not at.exception, f"App raised with low-confidence caveat active: {at.exception}"

    # Confirm the caveat panel is genuinely present, not just assumed.
    expander_labels = [e.label for e in at.expander] if hasattr(at, "expander") else []
    assert any("Try one more sample" in lbl for lbl in expander_labels), (
        f"Expected the caveat-framed deepen panel to be showing, "
        f"got expanders: {expander_labels}"
    )

    gate_checkbox = next(
        c for c in at.checkbox if "sending this as mine" in c.label
    )
    gate_checkbox.set_value(True)
    at.run()
    assert not at.exception, (
        f"App raised after checking the gate checkbox with caveat panel "
        f"also on screen: {at.exception}"
    )

    confirm_button = next(
        b for b in at.button if "Show my rewritten text" in b.label
    )
    assert not confirm_button.disabled
    confirm_button.click()
    at.run()
    assert not at.exception, (
        f"App raised after confirming the gate with caveat panel also "
        f"on screen: {at.exception}"
    )

    actual_output = at.session_state["render_output"]
    text_area_values = [t.value for t in at.text_area]
    joined = "".join(v or "" for v in text_area_values)
    assert actual_output in joined, (
        "Output text still not visible after confirming the gate WITH "
        "the low-confidence caveat panel also active - this reproduces "
        f"the reported bug under the exact combination seen live. "
        f"text_area values found: {text_area_values}"
    )


def test_confirming_the_gate_with_a_real_succeeding_supabase_client():
    """Closes a real gap in every test above: all of them ran with no
    SUPABASE_URL/SUPABASE_SERVICE_KEY set, so log_review_confirmation()
    always took the fail-open 'client is None' path. Never actually
    tested the path where get_supabase_client() returns a real,
    successfully-connecting client and the insert genuinely succeeds -
    if something about handling a real client's return value (rather
    than short-circuiting on None) broke the reveal, nothing above
    would have caught it."""
    at = _run_screen4_forced_high_risk()

    mock_supabase_client = MagicMock()
    mock_supabase_client.table.return_value.insert.return_value.execute.return_value = (
        MagicMock(data=[{"id": "fake-uuid"}])
    )

    with patch("review_gate.get_supabase_client", return_value=mock_supabase_client):
        gate_checkbox = next(
            c for c in at.checkbox if "sending this as mine" in c.label
        )
        gate_checkbox.set_value(True)
        at.run()
        assert not at.exception

        confirm_button = next(
            b for b in at.button if "Show my rewritten text" in b.label
        )
        confirm_button.click()
        at.run()
        assert not at.exception, f"App raised with a real succeeding Supabase client: {at.exception}"

    mock_supabase_client.table.assert_called_with("review_confirmations")

    actual_output = at.session_state["render_output"]
    text_area_values = [t.value for t in at.text_area]
    joined = "".join(v or "" for v in text_area_values)
    assert actual_output in joined, (
        "Output text still not visible after confirming the gate with a "
        "REAL, successfully-connecting Supabase client (not the fail-"
        "open None path) - this is the specific combination none of "
        f"the earlier tests covered. text_area values found: {text_area_values}"
    )
