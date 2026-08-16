"""
review_gate.requires_review() and log_review_confirmation() — the
business rule deciding which risk verdicts require explicit human
confirmation before the rewritten text is shown, and the anonymous,
fail-open logging of that confirmation once given.

Mirrors tests/unit/test_render_events.py's mocking pattern exactly,
since log_review_confirmation() follows the same fail-open contract.
"""
from unittest.mock import patch, MagicMock

import review_gate
from scoring_rules import REVIEW_REQUIRED_RISK_LEVELS


def test_low_risk_does_not_require_review():
    assert review_gate.requires_review("Low") is False


def test_medium_risk_requires_review():
    assert review_gate.requires_review("Medium") is True


def test_high_risk_requires_review():
    assert review_gate.requires_review("High") is True


def test_none_risk_does_not_require_review():
    """No baseline yet / no report computed — nothing to have missed
    against, so gating here would be friction with no signal behind
    it."""
    assert review_gate.requires_review(None) is False


def test_requires_review_matches_scoring_rules_constant():
    """Pinned so a future change to REVIEW_REQUIRED_RISK_LEVELS is a
    deliberate, changelogged edit in scoring_rules.py rather than a
    silent behavior change discovered here."""
    for level in ("Low", "Medium", "High"):
        assert review_gate.requires_review(level) == (level in REVIEW_REQUIRED_RISK_LEVELS)


def test_no_client_configured_is_silent():
    with patch("review_gate.get_supabase_client", return_value=None):
        result = review_gate.log_review_confirmation(
            risk="High", risk_reason="dropped_entity", semantic_match=78,
            scoring_rules_version="1.2.0",
        )
        assert result is None


def test_insert_failure_is_caught_not_raised():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = Exception("boom")
    with patch("review_gate.get_supabase_client", return_value=mock_client):
        # Must not propagate — table not created yet is the expected
        # default state until the SQL in review_gate.py's docstring is
        # run once, same as render_events.py.
        review_gate.log_review_confirmation(
            risk="Medium", risk_reason="aggregate_band", semantic_match=80,
            scoring_rules_version="1.2.0",
        )


def test_successful_insert_uses_correct_table_and_payload_shape():
    mock_client = MagicMock()
    with patch("review_gate.get_supabase_client", return_value=mock_client):
        review_gate.log_review_confirmation(
            risk="High", risk_reason="dropped_entity", semantic_match=70,
            scoring_rules_version="1.2.0",
        )
    mock_client.table.assert_called_once_with("review_confirmations")
    payload = mock_client.table.return_value.insert.call_args[0][0]
    assert payload["risk"] == "High"
    assert payload["risk_reason"] == "dropped_entity"
    assert payload["semantic_match"] == 70
    assert payload["scoring_rules_version"] == "1.2.0"
    # Deliberately anonymous — no device_id, no identity, no text
    # content, matching render_events.py's contract exactly.
    assert "device_id" not in payload
    assert "text" not in payload
    assert "input_text" not in payload
    assert "email" not in payload
