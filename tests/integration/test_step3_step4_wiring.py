"""
End-to-end tests, via Streamlit's AppTest, for two pieces of
VOICOVA_Product_2.0_Consolidated.docx's Step 3 and Step 4:

- Step 3 (Section 9.1 / Section 11): the render-context field defaults
  to the last-used context rather than forcing a fresh choice every
  render.
- Step 4 (Section 9.4): write_render_history is called from
  _run_render's success path, with the actual render text/context/
  mode/scores from that render - not just that the module exists and
  is unit-tested in isolation (test_render_history.py already covers
  that; this file covers the wiring connecting it to app.py, the same
  gap test_app_render_pipeline.py's own docstring describes).

Same mocking approach as test_app_render_pipeline.py: only the
Anthropic API call and (for these tests) write_render_history itself
are mocked - zero cost, no real Supabase writes from tests.
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


def _seed_screen4(at: AppTest):
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
    at.session_state["_device_id"] = "test-device-1"


# ---------------------------------------------------------------------------
# Step 3 - context field defaults to last-used
# ---------------------------------------------------------------------------

def test_context_field_empty_on_first_ever_visit():
    # Nothing used before - must not seed anything that isn't there.
    at = AppTest.from_file(_APP_PATH)
    at.run()
    _seed_screen4(at)
    at.run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == ""


def test_context_field_prefills_with_last_used_context():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    _seed_screen4(at)
    # Simulate a previous render in this session having used a context.
    at.session_state["render_context_input"] = "LinkedIn post for a client"
    at.run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == "LinkedIn post for a client"


def test_context_field_remains_editable_after_prefill():
    at = AppTest.from_file(_APP_PATH)
    at.run()
    _seed_screen4(at)
    at.session_state["render_context_input"] = "LinkedIn post for a client"
    at.run()
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    context_input.set_value("A different context entirely").run()
    assert not at.exception
    context_input = next(t for t in at.text_input if t.key == "render_context_field")
    assert context_input.value == "A different context entirely"


# ---------------------------------------------------------------------------
# Step 4 - write_render_history called from _run_render's success path
#
# Patched at render_history.get_supabase_client rather than
# app.write_render_history: AppTest executes app.py through its own
# script-runner mechanism rather than a normal `import app`, so a
# patch on the app module's bound name is never seen by that
# execution path. render_history is imported by the normal Python
# import system as a dependency, so patching its own
# get_supabase_client (the same seam test_render_history.py's unit
# tests already patch) reaches the real call reliably.
# ---------------------------------------------------------------------------

def _mock_supabase_client():
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    table.insert.return_value.execute.return_value = MagicMock()
    select_result = MagicMock()
    select_result.data = []
    table.select.return_value.eq.return_value.order.return_value.execute.return_value = select_result
    return client, table


def test_write_render_history_called_on_successful_render():
    client, table = _mock_supabase_client()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch("render_history.get_supabase_client", return_value=client):
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.run()
            _seed_screen4(at)
            at.session_state["render_context_input"] = "A quick client email"
            at.run()
            assert not at.exception

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception, f"App raised during render: {at.exception}"

            table.insert.assert_called_once()
            payload = table.insert.call_args[0][0]
            assert payload["device_id"] == "test-device-1"
            assert payload["context"] == "A quick client email"
            assert payload["mode"] == "preserve"
            assert payload["input_text"] == "Please write a short note about the launch plan."
            assert payload["output_text"] == at.session_state["render_output"]
            # voice_match_tier (human-readable, e.g. "Strong match"),
            # not voice_match_badge (a CSS class name like
            # "badge-green") - caught and fixed 19 Aug 2026, this
            # assertion is what should have caught it the first time.
            assert payload["voice_match"] == at.session_state["voice_report"]["voice_match_tier"]
            assert not payload["voice_match"].startswith("badge-")
            assert isinstance(payload["content_lock_pass"], bool)


def test_write_render_history_content_lock_pass_matches_hard_fail_state():
    # content_lock_pass should be the inverse of content_integrity_hard_fail
    # for the same render, not an independently recomputed value.
    client, table = _mock_supabase_client()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls, \
             patch("render_history.get_supabase_client", return_value=client):
            mock_client = mock_anthropic_cls.return_value
            mock_client.messages.create.return_value = _fake_anthropic_response(FAKE_LLM_OUTPUT)

            at = AppTest.from_file(_APP_PATH)
            at.run()
            _seed_screen4(at)
            at.run()

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception

            hard_fail = at.session_state["voice_report"]["content_integrity_hard_fail"]
            payload = table.insert.call_args[0][0]
            assert payload["content_lock_pass"] == (not hard_fail)


def test_write_render_history_not_called_when_render_fails():
    # Daily cap reached -> _run_render returns False before generating
    # anything - History must not get a phantom entry for a render
    # that never happened.
    client, table = _mock_supabase_client()
    with patch("render_cap.check_and_reserve_render", return_value=(False, 40, 40)), \
         patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        _seed_screen4(at)
        at.run()

        at.text_area[0].input("Please write a short note about the launch plan.")
        at.button[0].click()
        at.run()

        table.insert.assert_not_called()
