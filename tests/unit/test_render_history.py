"""
Tests for render_history.py — the History screen's write/read path
(Section 9.4). Covers the write-then-trim-to-50 retention behaviour
and the same fail-open contract as the rest of the persistence layer.
"""
from unittest.mock import MagicMock, patch

import render_history


def _mock_client_for_write(existing_rows=None):
    """existing_rows: list of {'id': ...} dicts returned by the
    post-write retention check, most-recent-first (as the real query
    orders them)."""
    client = MagicMock()
    table = MagicMock()
    client.table.return_value = table

    table.insert.return_value.execute.return_value = MagicMock()

    select_result = MagicMock()
    select_result.data = existing_rows or []
    table.select.return_value.eq.return_value.order.return_value.execute.return_value = select_result

    table.delete.return_value.eq.return_value.execute.return_value = MagicMock()

    return client, table


# ------------------------------------------------------------------
# Fail-open behaviour
# ------------------------------------------------------------------

def test_write_does_nothing_when_supabase_not_configured():
    with patch("render_history.get_supabase_client", return_value=None):
        # Must not raise.
        render_history.write_render_history("device-1", "input", "output")


def test_write_swallows_insert_failure():
    client = MagicMock()
    client.table.return_value.insert.return_value.execute.side_effect = Exception("boom")
    with patch("render_history.get_supabase_client", return_value=client):
        # Must not raise, must not propagate.
        render_history.write_render_history("device-1", "input", "output")


def test_read_returns_empty_list_when_supabase_not_configured():
    with patch("render_history.get_supabase_client", return_value=None):
        result = render_history.get_render_history("device-1")
    assert result == []


def test_read_swallows_failure_and_returns_empty_list():
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.side_effect = Exception("boom")
    with patch("render_history.get_supabase_client", return_value=client):
        result = render_history.get_render_history("device-1")
    assert result == []


# ------------------------------------------------------------------
# Normal write path
# ------------------------------------------------------------------

def test_write_inserts_expected_payload():
    client, table = _mock_client_for_write(existing_rows=[{"id": "1"}])
    with patch("render_history.get_supabase_client", return_value=client):
        render_history.write_render_history(
            device_id="device-1",
            input_text="original text",
            output_text="rewritten text",
            context="linkedin",
            mode="preserve",
            voice_match="Strong",
            content_lock_pass=True,
        )
    table.insert.assert_called_once()
    payload = table.insert.call_args[0][0]
    assert payload["device_id"] == "device-1"
    assert payload["input_text"] == "original text"
    assert payload["output_text"] == "rewritten text"
    assert payload["context"] == "linkedin"
    assert payload["mode"] == "preserve"
    assert payload["voice_match"] == "Strong"
    assert payload["content_lock_pass"] is True


def test_write_defaults_context_and_mode():
    client, table = _mock_client_for_write(existing_rows=[])
    with patch("render_history.get_supabase_client", return_value=client):
        render_history.write_render_history("device-1", "in", "out")
    payload = table.insert.call_args[0][0]
    assert payload["context"] == ""
    assert payload["mode"] == "preserve"
    assert payload["voice_match"] is None
    assert payload["content_lock_pass"] is None


# ------------------------------------------------------------------
# Retention trim — the 50-render cap (Section 9.4 decision)
# ------------------------------------------------------------------

def test_trim_does_nothing_under_the_limit():
    rows = [{"id": str(i)} for i in range(30)]
    client, table = _mock_client_for_write(existing_rows=rows)
    with patch("render_history.get_supabase_client", return_value=client):
        render_history.write_render_history("device-1", "in", "out")
    table.delete.assert_not_called()


def test_trim_deletes_rows_past_the_retention_limit():
    # 55 rows, most-recent-first — the trim should delete the oldest 5.
    rows = [{"id": str(i)} for i in range(55)]
    client, table = _mock_client_for_write(existing_rows=rows)
    with patch("render_history.get_supabase_client", return_value=client):
        render_history.write_render_history("device-1", "in", "out")
    assert table.delete.call_count == 5
    # Confirm the deleted ids are exactly the tail past index 50.
    eq_calls = [c.args for c in table.delete.return_value.eq.call_args_list]
    deleted_ids = {args[1] for args in eq_calls}
    expected_ids = {str(i) for i in range(50, 55)}
    assert deleted_ids == expected_ids


def test_trim_swallowed_failure_does_not_break_write():
    client, table = _mock_client_for_write(existing_rows=[{"id": "1"}])
    table.select.return_value.eq.return_value.order.return_value.execute.side_effect = Exception("boom")
    with patch("render_history.get_supabase_client", return_value=client):
        # The trim's own exception is inside the same try/except as
        # the insert in write_render_history, so this must not raise.
        render_history.write_render_history("device-1", "in", "out")
    table.insert.assert_called_once()


# ------------------------------------------------------------------
# Normal read path
# ------------------------------------------------------------------

def test_read_returns_rows_in_order_returned():
    client = MagicMock()
    result = MagicMock()
    result.data = [{"id": "2"}, {"id": "1"}]
    client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = result
    with patch("render_history.get_supabase_client", return_value=client):
        rows = render_history.get_render_history("device-1")
    assert rows == [{"id": "2"}, {"id": "1"}]


def test_read_respects_limit_parameter():
    client = MagicMock()
    result = MagicMock()
    result.data = []
    client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = result
    with patch("render_history.get_supabase_client", return_value=client):
        render_history.get_render_history("device-1", limit=10)
    client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.assert_called_with(10)
