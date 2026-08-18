"""
authenticity_report.py — exportable, tamper-evident proof that a
render's Voice Report is genuinely what VOICOVA measured, not a
number someone typed in afterward.

Why this exists: every AI-detection score on the market today scores
arbitrary text after the fact, with no visible reasoning — and that
pattern is already publicly discredited. A March 2026 case (writer
deBoer, detector Pangram, reported via a Substack/Blaze Media piece)
scored the same 300-word passage as 100% AI-written on its own and
100% human once embedded in the full 5,000-word essay. The take-away
quoted at the time: "an unexplained algorithmic score is not proof of
authorship." VOICOVA's report is structurally different in kind, not
just degree — it's a comparison against THIS person's own baseline,
built from writing collected before any AI touched the text, with the
dimension-level reasoning included, not just a headline number. That
claim only holds if the report itself can't be quietly edited after
export.

integrity_hash proves INTERNAL CONSISTENCY ONLY: that this exact
payload is what was issued, unedited, since export. It is NOT a
cryptographic signature and does NOT prove the report actually came
from VOICOVA (that needs a server-side signing key — explicitly out
of scope for this pass; flagged here rather than silently implied).
verify_authenticity_report() is what checks it.

Privacy: mirrors render_events.py's existing stance exactly — no
device_id, no raw text, nothing that could re-identify the person or
their writing. baseline_hash is a hash of the NUMERIC baseline
fingerprint values only, never the raw baseline text, so a third
party checking a report learns "this render matches a specific voice
baseline" without learning anything about what that baseline says.

Scope note: this module only packages and hashes data already
computed elsewhere (build_voice_report in voice_engine.py,
compute_baseline_metrics for the baseline dict). It adds no new
scoring logic and is never called from inside the render/prompt path
— it runs after a render is complete and confirmed, as a pure
reporting/export step. No LLM call, no change to render behaviour.
"""
import hashlib
import json


def compute_baseline_hash(baseline_fingerprint: dict | None) -> str:
    """Deterministic hash of the baseline's numeric fingerprint values
    only. Same baseline -> same hash, always -> lets two reports be
    checked against each other ("were these both scored against the
    same voice baseline?") without ever exposing the baseline itself.
    Empty/missing baseline returns "" rather than hashing an empty
    dict, so a report built without a real baseline is visibly
    incomplete rather than silently producing a hash that looks valid.
    """
    if not baseline_fingerprint:
        return ""
    canonical = json.dumps(baseline_fingerprint, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_payload_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


# Fields carried over from the existing Voice Report verbatim — kept
# as an explicit list (not **voice_report) so a future field added to
# build_voice_report() doesn't silently start appearing in exported
# authenticity reports without a deliberate decision to include it.
_VOICE_REPORT_FIELDS = (
    "voice_match_tier",
    "semantic_match",
    "confidence",
    "risk",
    "ai_tell_clean",
    "biggest_changes",
)


def build_authenticity_report(
    voice_report: dict,
    baseline_fingerprint: dict | None,
    render_id: str,
    created_at: str,
    scoring_rules_version: str,
) -> dict:
    """Assembles the exportable authenticity report.

    voice_report: the dict returned by build_voice_report() for this
    render — already computed, this function only selects and packages
    from it.
    baseline_fingerprint: st.session_state["baseline_fingerprint"] at
    render time — hashed, never included raw.
    render_id: caller-generated (uuid4 string), one per completed
    render, so a report can be uniquely referenced without needing
    the underlying text.
    created_at: ISO 8601 string, caller-supplied so this stays a pure
    function (no datetime.now() call buried in here) and is trivially
    testable.
    """
    payload = {
        "render_id": render_id,
        "created_at": created_at,
        "scoring_rules_version": scoring_rules_version,
        "baseline_hash": compute_baseline_hash(baseline_fingerprint),
    }
    for field in _VOICE_REPORT_FIELDS:
        payload[field] = (voice_report or {}).get(field)

    payload["integrity_hash"] = hashlib.sha256(
        _canonical_payload_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def verify_authenticity_report(report: dict) -> bool:
    """Recomputes integrity_hash over every field except itself and
    compares. True means the report is internally consistent — it
    hasn't been edited since VOICOVA issued it. False covers both
    tampering and a malformed/foreign report (missing hash, wrong
    shape) — deliberately not distinguished, since either way the
    honest answer is "can't be trusted as issued."

    Does NOT verify the report actually originated from VOICOVA —
    see module docstring for why that's an explicit, stated limit
    rather than an implied guarantee.
    """
    if not isinstance(report, dict) or "integrity_hash" not in report:
        return False
    claimed = report["integrity_hash"]
    payload = {k: v for k, v in report.items() if k != "integrity_hash"}
    recomputed = hashlib.sha256(
        _canonical_payload_json(payload).encode("utf-8")
    ).hexdigest()
    return recomputed == claimed


def export_authenticity_report_json(report: dict) -> str:
    """Pretty-printed JSON for a download button — human-readable, not
    just a hash blob, so anyone receiving this (a client, an editor,
    a platform) can read the actual scores without tooling."""
    return json.dumps(report, indent=2, sort_keys=True, default=str)
