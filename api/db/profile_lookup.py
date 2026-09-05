"""
api/db/profile_lookup.py — pure voice_profiles fetch, no Streamlit.

persistence.py's restore_profile_if_available() does this same query but
writes straight into st.session_state, which doesn't exist in the API
process. This is the same query and the same field set (see that
function's docstring), returned as a plain dict instead. Deliberately
NOT a rewrite of the scoring/fingerprint logic — voice_profiles is
"unchanged" per Engineering Architecture Section 5.3; this module only
reads it.

Fail-open contract matches the rest of the persistence layer: returns
None on any absence or failure, never raises. Route-level code decides
what "no profile" means for its own response (Section 4.6 error shape).
"""
from __future__ import annotations

from supabase_client import get_supabase_client
from logging_config import get_logger

log = get_logger(__name__)

_TABLE = "voice_profiles"


def get_profile_bundle(profile_id: str) -> dict | None:
    """Returns the same field set persistence.restore_profile_if_available
    restores into session_state, as a plain dict, or None if no usable
    profile exists for this id. profile_id here is the existing
    voice_profiles.device_id value — see Engineering Architecture
    Section 4.3 on why the extension's identifier and this one are
    deliberately different values, resolved fresh on every call.
    """
    client = get_supabase_client()
    if client is None:
        log.error("profile_lookup_unavailable", reason="no_supabase_client")
        return None

    try:
        result = (
            client.table(_TABLE)
            .select("*")
            .eq("device_id", profile_id)
            .limit(1)
            .execute()
        )
    except Exception:
        log.error("profile_lookup_query_failed", exc_info=True)
        return None

    rows = result.data if result and result.data else []
    if not rows:
        return None

    row = rows[0]
    if not row.get("baseline_fingerprint"):
        # Row exists but has no usable baseline — same "treat as absent"
        # rule persistence.py applies, for the same reason: nothing to
        # check a draft against.
        return None

    raw_text = row.get("raw_text") or ""
    sample2_completions = row.get("sample2_completions") or ["", "", "", ""]

    # Reconstructs the same corpus app.py builds up interactively during
    # onboarding as st.session_state.fingerprint_sample_texts (Screen 1
    # paste, then each Screen 3 starter appended as it's typed) — see
    # that key's call sites in app.py. There's no persisted column with
    # this exact shape; voice_profiles stores the two ingredients
    # (raw_text, sample2_completions) separately, so this is where they
    # get recombined for score_draft_check's baseline_texts parameter.
    baseline_texts = [t for t in [raw_text, *sample2_completions] if t]

    return {
        "profile_id": profile_id,
        "raw_text": raw_text,
        "sample2_completions": sample2_completions,
        "baseline_texts": baseline_texts,
        "baseline_fingerprint": row.get("baseline_fingerprint"),
        "starter_baseline": row.get("starter_baseline"),
        "baseline_fingerprints_by_format": row.get("baseline_fingerprints_by_format"),
        "correction_evidence": row.get("correction_evidence"),
        "flagged_dimensions": row.get("flagged_dimensions"),
        "voice_profile_summary": row.get("voice_profile_summary"),
        "updated_at": row.get("updated_at"),
    }
