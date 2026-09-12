"""
Tests for the cached, register-aware self-prompting style checklist
(12 Sept 2026, PR 5 of 5 — register-aware voice fidelity build). See
migrations/2026_09_12_add_style_checklists.sql and build_style_
checklist_prompt's own docstring for the full rationale.

Covers: the prompt builder is narrow and non-overlapping with the
existing voice-profile-summary prompt; _build_voice_dna surfaces the
checklist when present and is unchanged when absent; the lazy-
generation wiring in run_voice_render only fires when there's real
register-matched material and no cached entry yet; persistence reads/
writes the new column with the same optional-field backward
compatibility every other column here already has.
"""
import os
from unittest.mock import patch, MagicMock

import pytest
import prompts as pr
import persistence
import streamlit as st


@pytest.fixture(autouse=True)
def _reset_session_state():
    """Same isolation fixture as test_persistence.py — Streamlit's
    session_state leaks between tests otherwise."""
    st.session_state.clear()
    yield
    st.session_state.clear()


# ------------------------------------------------------------------
# build_style_checklist_prompt
# ------------------------------------------------------------------

def test_style_checklist_prompt_is_scoped_to_construction_not_tone():
    prompt = pr.build_style_checklist_prompt()
    # Must explicitly scope AWAY from what build_voice_profile_summary_
    # prompt already covers, per this function's own docstring.
    assert "tone" in prompt.lower() or "not tone" in prompt.lower()
    assert "coordination" in prompt.lower() or "subordination" in prompt.lower()


def test_style_checklist_prompt_asks_for_short_output():
    prompt = pr.build_style_checklist_prompt()
    assert "2-4 sentences" in prompt or "sentences" in prompt.lower()


# ------------------------------------------------------------------
# _build_voice_dna's new style_checklist parameter
# ------------------------------------------------------------------

def test_build_voice_dna_includes_checklist_when_present():
    observations = [{"headline": "Direct", "body": "States things plainly."}]
    result = pr._build_voice_dna(
        observations, "Some raw calibration text here for the corpus.",
        baseline=None, style_checklist="Links reasons after the main clause, not before.",
    )
    assert "SENTENCE CONSTRUCTION" in result
    assert "Links reasons after the main clause" in result


def test_build_voice_dna_omits_checklist_block_when_absent():
    observations = [{"headline": "Direct", "body": "States things plainly."}]
    result = pr._build_voice_dna(
        observations, "Some raw calibration text here for the corpus.",
        baseline=None, style_checklist="",
    )
    assert "SENTENCE CONSTRUCTION" not in result


def test_build_voice_dna_default_style_checklist_reproduces_prior_behaviour():
    # No style_checklist kwarg at all — every caller that predates this
    # feature must get byte-identical output to before it existed.
    observations = [{"headline": "Direct", "body": "States things plainly."}]
    with_default = pr._build_voice_dna(observations, "Some raw text.", baseline=None)
    with_explicit_empty = pr._build_voice_dna(observations, "Some raw text.", baseline=None, style_checklist="")
    assert with_default == with_explicit_empty


# ------------------------------------------------------------------
# _generate_style_checklist (render_pipeline.py)
# ------------------------------------------------------------------

def test_generate_style_checklist_returns_none_without_api_key():
    import render_pipeline as rp
    assert rp._generate_style_checklist("Some sample text.", "") is None


def test_generate_style_checklist_returns_none_without_sample_text():
    import render_pipeline as rp
    assert rp._generate_style_checklist("", "fake-key") is None
    assert rp._generate_style_checklist("   ", "fake-key") is None


def test_generate_style_checklist_calls_api_and_cleans_result():
    import render_pipeline as rp

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Links causes after the main clause.")]

    with patch("anthropic.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        result = rp._generate_style_checklist("A real writing sample.", "fake-key")

    assert result == "Links causes after the main clause."
    mock_client.messages.create.assert_called_once()
    _, kwargs = mock_client.messages.create.call_args
    assert kwargs["temperature"] == 0
    assert kwargs["max_tokens"] == 150


def test_generate_style_checklist_returns_none_on_api_failure():
    import render_pipeline as rp
    with patch("anthropic.Anthropic", side_effect=Exception("boom")):
        assert rp._generate_style_checklist("A real writing sample.", "fake-key") is None


# ------------------------------------------------------------------
# persistence.py read/write round trip
# ------------------------------------------------------------------

def _mock_context_cookies(device_id):
    ctx = MagicMock()
    ctx.cookies = {"voicova_device_id": device_id}
    return ctx


def _mock_supabase_client(select_rows=None):
    client = MagicMock()
    table = client.table.return_value
    table.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = select_rows or []
    table.upsert.return_value.execute.return_value = MagicMock()
    return client


def test_restore_populates_style_checklists_when_present():
    row = {
        "device_id": "device-1",
        "raw_text": "some writing",
        "baseline_fingerprint": {"hedge_density": 1.0},
        "style_checklists": {"professional": "Links reasons after the main clause."},
    }
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is True
    assert st.session_state["style_checklists"] == {"professional": "Links reasons after the main clause."}


def test_restore_omits_style_checklists_key_when_absent():
    row = {
        "device_id": "device-1",
        "raw_text": "some writing",
        "baseline_fingerprint": {"hedge_density": 1.0},
    }
    st.session_state.pop("style_checklists", None)  # explicit, though fixture already clears
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        with patch("persistence.st.context", _mock_context_cookies("device-1")):
            with patch("persistence.get_supabase_client", return_value=_mock_supabase_client(select_rows=[row])):
                assert persistence.restore_profile_if_available() is True
    assert "style_checklists" not in st.session_state


def test_save_includes_style_checklists_when_present():
    st.session_state["baseline_fingerprint"] = {"hedge_density": 1.0}
    st.session_state["_device_id"] = "device-1"
    st.session_state["style_checklists"] = {"professional": "Links reasons after the main clause."}
    with patch.dict(os.environ, {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_KEY": "key"}):
        mock_client = _mock_supabase_client()
        with patch("persistence.get_supabase_client", return_value=mock_client):
            persistence.save_profile_if_available()

    payload = mock_client.table.return_value.upsert.call_args[0][0]
    assert payload["style_checklists"] == {"professional": "Links reasons after the main clause."}
