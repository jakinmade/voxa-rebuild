"""
review_gate.requires_review() and log_review_confirmation() — the
business rule deciding which renders require explicit human
confirmation before the rewritten text is shown, and the anonymous,
fail-open logging of that confirmation once given.

Mirrors tests/unit/test_render_events.py's mocking pattern exactly,
since log_review_confirmation() follows the same fail-open contract.

19 Aug 2026: requires_review()'s contract changed from a risk-level
string ("Low"/"Medium"/"High") to a plain bool — whether
voice_engine.has_content_integrity_hard_fail() fired. See
review_gate.py's module docstring for why (style drift alone was
gating nearly every render). log_review_confirmation()'s own contract
is unchanged — it still logs the risk string for the aggregate
question of what fraction of gated renders get confirmed.
"""
from unittest.mock import patch, MagicMock

import review_gate


def test_no_hard_fail_does_not_require_review():
    assert review_gate.requires_review(False) is False


def test_hard_fail_requires_review():
    assert review_gate.requires_review(True) is True


def test_none_does_not_require_review():
    """No report computed yet — nothing to have failed against, so
    gating here would be friction with no signal behind it."""
    assert review_gate.requires_review(None) is False


def test_style_drift_alone_never_gates():
    """The actual bug this change fixes: Risk going Medium purely
    from missing one style dimension must never gate the output on
    its own. requires_review only ever looks at the hard_fail bool,
    never at a risk level string, so there's no way for a Risk badge
    of 'Medium' or even 'High' driven by missed dimensions/semantic
    match alone to reach this function and gate anything - only
    has_content_integrity_hard_fail's own True/False decides it."""
    assert review_gate.requires_review(False) is False


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
