"""
firm_signal.extract_domain() and log_firm_signal() — the opt-in,
domain-only firm-clustering signal. Critical property under test:
the full email never reaches the logging function or gets returned
to any caller — only the lowercased domain, and only when it isn't
a personal-webmail provider.
"""
from unittest.mock import patch, MagicMock

import firm_signal
from scoring_rules import PERSONAL_EMAIL_DOMAINS


def test_extracts_domain_from_valid_work_email():
    assert firm_signal.extract_domain("john@akinmade.dev") == "akinmade.dev"


def test_lowercases_domain():
    assert firm_signal.extract_domain("John@AkinMade.DEV") == "akinmade.dev"


def test_strips_whitespace():
    assert firm_signal.extract_domain("  john@akinmade.dev  ") == "akinmade.dev"


def test_never_returns_the_local_part():
    result = firm_signal.extract_domain("john.akinmade@clearance-diagnostic.com")
    assert result == "clearance-diagnostic.com"
    assert "john" not in result
    assert "akinmade" not in result


def test_rejects_empty_string():
    assert firm_signal.extract_domain("") is None


def test_rejects_whitespace_only():
    assert firm_signal.extract_domain("   ") is None


def test_rejects_missing_at_sign():
    assert firm_signal.extract_domain("not-an-email.com") is None


def test_rejects_missing_dot_in_domain():
    assert firm_signal.extract_domain("john@localhost") is None


def test_rejects_multiple_at_signs():
    assert firm_signal.extract_domain("john@@akinmade.dev") is None


def test_excludes_every_configured_personal_domain():
    """Every domain in scoring_rules.PERSONAL_EMAIL_DOMAINS must be
    rejected — pinned as a loop over the live list so adding a new
    exclusion there is automatically covered, not something a new
    test has to remember to add."""
    for domain in PERSONAL_EMAIL_DOMAINS:
        assert firm_signal.extract_domain(f"someone@{domain}") is None


def test_gmail_specifically_rejected():
    """The single most common personal domain, called out explicitly
    since it's the whole reason this exclusion list exists."""
    assert firm_signal.extract_domain("advisor@gmail.com") is None


def test_subdomain_of_a_personal_provider_is_not_excluded():
    """Exact match only, per the module's own docstring — a firm
    running mail on a subdomain of a personal-sounding name is still
    a real, distinct domain and shouldn't be silently caught by the
    exclusion list."""
    assert firm_signal.extract_domain("john@mail.somefirm.com") == "mail.somefirm.com"


def test_no_client_configured_is_silent():
    with patch("firm_signal.get_supabase_client", return_value=None):
        result = firm_signal.log_firm_signal(
            domain="akinmade.dev", risk="High", risk_reason="dropped_entity",
            scoring_rules_version="1.3.0",
        )
        assert result is None


def test_insert_failure_is_caught_not_raised():
    mock_client = MagicMock()
    mock_client.table.return_value.insert.return_value.execute.side_effect = Exception("boom")
    with patch("firm_signal.get_supabase_client", return_value=mock_client):
        firm_signal.log_firm_signal(
            domain="akinmade.dev", risk="Medium", risk_reason="aggregate_band",
            scoring_rules_version="1.3.0",
        )


def test_successful_insert_uses_correct_table_and_payload_shape():
    mock_client = MagicMock()
    with patch("firm_signal.get_supabase_client", return_value=mock_client):
        firm_signal.log_firm_signal(
            domain="clearance-diagnostic.com", risk="High",
            risk_reason="dropped_entity", scoring_rules_version="1.3.0",
        )
    mock_client.table.assert_called_once_with("firm_signals")
    payload = mock_client.table.return_value.insert.call_args[0][0]
    assert payload["domain"] == "clearance-diagnostic.com"
    assert payload["risk"] == "High"
    assert payload["risk_reason"] == "dropped_entity"
    assert payload["scoring_rules_version"] == "1.3.0"
    # The whole point of this module — never an email, never identity.
    assert "email" not in payload
    assert "device_id" not in payload
    assert "local_part" not in payload
