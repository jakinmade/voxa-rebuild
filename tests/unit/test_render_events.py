"""
render_events.log_render_event() must never raise and must never
block a render - same fail-open contract persistence.py already has
tests for. These tests don't hit a real Supabase instance (none
configured in CI); they confirm the no-client and error paths are
both silent from the caller's perspective.
"""
from unittest.mock import patch, MagicMock

import render_events


def test_no_client_configured_is_silent():
    with patch("render_events.get_supabase_client", return_value=None):
        # Must not raise, must not return anything the caller needs.
        result = render_events.log_render_event(
            risk="High", risk_reason="dropped_entity", semantic_match=78,
            missed_dimensions=1, ai_tells_clean=True, is_refinement=False,
            scoring_rules_version="1.1.0",
        )
        assert result is None


def test_insert_failure_is_caught_not_raised():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = Exception("boom")
    with patch("render_events.get_supabase_client", return_value=mock_client):
        # Must not propagate the exception - table not existing yet
        # (before the one-time SQL setup) is the expected default state.
        render_events.log_render_event(
            risk="Low", risk_reason="clean", semantic_match=100,
            missed_dimensions=0, ai_tells_clean=True, is_refinement=False,
            scoring_rules_version="1.1.0",
        )


def test_successful_insert_uses_correct_table_and_payload_shape():
    mock_client = MagicMock()
    with patch("render_events.get_supabase_client", return_value=mock_client):
        render_events.log_render_event(
            risk="High", risk_reason="aggregate_band", semantic_match=60,
            missed_dimensions=3, ai_tells_clean=True, is_refinement=True,
            scoring_rules_version="1.1.0",
        )
    mock_client.table.assert_called_once_with("render_events")
    payload = mock_client.table.return_value.insert.call_args[0][0]
    assert payload["risk"] == "High"
    assert payload["risk_reason"] == "aggregate_band"
    assert payload["semantic_match"] == 60
    assert payload["missed_dimensions"] == 3
    assert payload["is_refinement"] is True
    assert payload["scoring_rules_version"] == "1.1.0"
    # No device_id, no text content - deliberately anonymous, per the
    # module's own docstring.
    assert "device_id" not in payload
    assert "text" not in payload
    assert "input_text" not in payload
