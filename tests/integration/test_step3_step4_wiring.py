"""
End-to-end tests, via Streamlit's AppTest, for VOICOVA_Product_2.0_
Consolidated.docx's Steps 3, 4, and 5:

- Step 3 (Section 9.1 / Section 11): the render-context field defaults
  to the last-used context rather than forcing a fresh choice every
  render.
- Step 4 (Section 9.4), write half: write_render_history is called
  from _run_render's success path, with the actual render text/
  context/mode/scores from that render.
- Step 5 (Section 9.4): the History screen (screen 6) lists what
  Step 4 wrote and renders a before/after reopen view.

Same mocking approach as test_app_render_pipeline.py: only the
Anthropic API call and the Supabase client (via render_history.
get_supabase_client) are mocked - zero cost, no real Supabase writes
or reads from tests.
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


# ---------------------------------------------------------------------------
# Step 5 - History screen (screen 6): list view + before/after reopen
# ---------------------------------------------------------------------------

def _mock_supabase_client_with_history(rows):
    """Same shape as _mock_supabase_client, but the select().eq().
    order().limit().execute() chain get_render_history actually calls
    returns the given rows, instead of an empty list."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table
    select_result = MagicMock()
    select_result.data = rows
    table.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = select_result
    return client, table


def test_history_screen_shows_empty_state_with_no_renders():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception
        assert any("No renders yet" in info.value for info in at.info)


def test_history_screen_lists_past_renders():
    rows = [
        {
            "id": "row-1", "created_at": "2026-08-19T10:15:00Z",
            "context": "LinkedIn post", "mode": "preserve",
            "input_text": "Original draft one.", "output_text": "Rewritten draft one.",
            "voice_match": "Strong match", "content_lock_pass": True,
        },
        {
            "id": "row-2", "created_at": "2026-08-18T09:00:00Z",
            "context": "", "mode": "elevate",
            "input_text": "Original draft two.", "output_text": "Rewritten draft two.",
            "voice_match": "Close match", "content_lock_pass": False,
        },
    ]
    client, _ = _mock_supabase_client_with_history(rows)
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception

        expander_labels = [e.label for e in at.expander]
        assert any("LinkedIn post" in label and "Strong match" in label for label in expander_labels)
        assert any("No context set" in label and "Close match" in label for label in expander_labels)


def test_history_screen_reopen_shows_before_and_after_text():
    rows = [{
        "id": "row-1", "created_at": "2026-08-19T10:15:00Z",
        "context": "LinkedIn post", "mode": "preserve",
        "input_text": "Original draft one.", "output_text": "Rewritten draft one.",
        "voice_match": "Strong match", "content_lock_pass": True,
    }]
    client, _ = _mock_supabase_client_with_history(rows)
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        at.session_state["screen"] = 6
        at.session_state["_device_id"] = "test-device-1"
        at.run()
        assert not at.exception

        before_box = next(t for t in at.text_area if t.key == "history_before_row-1")
        after_box = next(t for t in at.text_area if t.key == "history_after_row-1")
        assert before_box.value == "Original draft one."
        assert after_box.value == "Rewritten draft one."
        assert before_box.disabled and after_box.disabled


def test_history_nav_buttons_present_from_write_and_my_voice():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        _seed_screen4(at)
        at.run()
        assert not at.exception
        assert any(b.key == "nav_to_history_from_write" for b in at.sidebar.button)

        at.session_state["screen"] = 5
        at.run()
        assert not at.exception
        assert any(b.key == "nav_to_history_from_my_voice" for b in at.sidebar.button)


def test_history_back_to_write_button_navigates():
    client, _ = _mock_supabase_client_with_history([])
    with patch("render_history.get_supabase_client", return_value=client):
        at = AppTest.from_file(_APP_PATH)
        at.run()
        _seed_screen4(at)  # sets baseline_fingerprint, needed for the sidebar nav to show
        at.session_state["screen"] = 6
        at.run()
        assert not at.exception

        back_button = next(b for b in at.sidebar.button if b.key == "nav_back_to_write_from_history")
        back_button.click().run()
        assert at.session_state["screen"] == 4
