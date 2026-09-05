"""
api/telemetry/events.py — structured event emission (Section 5.5).

Fail-open by design, matching every other persistence write in this
codebase: telemetry must never be the reason a real user-facing
request fails. A dropped telemetry insert is a gap in the Week 6
decision-gate numbers (Full Spec Section 5.1), not an incident.
"""
from __future__ import annotations

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)

_TABLE = "telemetry_events"


def emit(
    *,
    installation_id: str | None,
    profile_id: str,
    surface: str,
    action: str,
    request_id: str,
    scoring_version: str | None = None,
    extension_version: str | None = None,
    result: str | None = None,
    fix_requested: bool = False,
    fix_accepted: bool | None = None,
    render_credit_consumed: bool = False,
    latency_ms: int | None = None,
    error_code: str | None = None,
    draft_length: int | None = None,
    composer_found: bool | None = None,
    panel_render_time_ms: int | None = None,
    fix_ambiguous_outcome: bool = False,
) -> None:
    client = get_supabase_client()
    if client is None:
        log.error("telemetry_emit_unavailable", reason="no_supabase_client")
        return
    try:
        client.table(_TABLE).insert({
            "installation_id": installation_id,
            "profile_id": profile_id,
            "surface": surface,
            "action": action,
            "request_id": request_id,
            "scoring_version": scoring_version,
            "extension_version": extension_version,
            "result": result,
            "fix_requested": fix_requested,
            "fix_accepted": fix_accepted,
            "render_credit_consumed": render_credit_consumed,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "draft_length": draft_length,
            "composer_found": composer_found,
            "panel_render_time_ms": panel_render_time_ms,
            "fix_ambiguous_outcome": fix_ambiguous_outcome,
        }).execute()
    except Exception:
        log.error("telemetry_emit_failed", exc_info=True)
