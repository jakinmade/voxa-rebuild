"""
api/db/evidence_seals.py — persistence for the evidence_seals table
(Section 5.4). Computation lives in api/evidence/seal.py; this module
only inserts and reads.

Fail-open on write: a Voice Check result should never be blocked from
reaching the user because the seal couldn't be persisted — the receipt
returned to the client still carries its own seal_hash either way
(computed client-visibly in the response), only the durable row is
missing. Logged loudly since a missing row means that receipt can't
later be independently confirmed against Supabase, which matters more
here than for most other tables in this codebase.
"""
from __future__ import annotations

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)

_TABLE = "evidence_seals"


def create_seal(seal_payload: dict) -> dict | None:
    client = get_supabase_client()
    if client is None:
        log.error("evidence_seal_persist_unavailable", reason="no_supabase_client")
        return None
    try:
        result = client.table(_TABLE).insert(seal_payload).execute()
    except Exception:
        log.error("evidence_seal_persist_failed", exc_info=True)
        return None
    rows = result.data if result and result.data else []
    return rows[0] if rows else None
