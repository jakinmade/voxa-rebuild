"""
api/evidence/seal.py — SHAA-lite evidence sealing (Engineering
Architecture Section 5.4, Full Spec Section 2.4).

Canonicalisation follows the exact pattern already live in CLEARANCE
(jakinmade/clearance-app, engine/CLEARANCE_Engine_Evaluator_V1_5.py):
json.dumps(payload, sort_keys=True, separators=(",", ":")), encoded to
UTF-8, then SHA-256. Reused deliberately rather than redesigned — this
is the established canonicalisation pattern across the AAE/CLEARANCE
evidence-sealing family, and two implementations of "the same idea"
producing different hashes for identical input would defeat the point
of sealing either one.

Deliberately NOT a hash chain (each seal stands alone) and NOT
append-only-ledger-backed — that heavier CLEARANCE/AAE-grade design
(hash-chained ledger, Governance/Decision/Verification planes) is
explicitly out of scope for this pilot (Full Spec Section 2.4) and is
the correct next step only once the regulated-communications track is
funded. prev_seal_hash exists in the schema (Section 5.4) but is never
populated by this module in V1.
"""
from __future__ import annotations

import hashlib
import json

# Bumped whenever score_draft_check's scoring logic changes in a way
# that would affect what a seal attests to — lets a later dispute be
# checked against the exact ruleset used (Section 5.4). This module
# doesn't try to derive it automatically from the engine; whoever
# changes the scoring logic is responsible for bumping this alongside
# that change, same discipline as CLEARANCE's engine_version field.
SCORING_VERSION = "voice-engine-2026-09-05"


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def seal(
    *,
    request_id: str,
    profile_id: str,
    action: str,
    input_text: str,
    result: dict,
    content_lock: dict | None = None,
) -> dict:
    """Builds and hashes the evidence receipt for one /api/check-draft
    or /api/fix call. Returns a dict matching the evidence_seals
    schema (Section 5.4), ready to insert — this function computes,
    the db layer persists, kept separate so this stays pure and
    testable without a live Supabase connection.

    action must be 'check' or 'fix' (the table's own check constraint
    enforces this too — this is a fail-fast at the call site, not a
    substitute for that constraint).

    content_lock: omit for 'check' — score_draft_check's own docstring
    is explicit that it deliberately skips content-lock/attribution
    checks, since those measure fidelity between an input and a
    REWRITTEN output, and a Voice Check has no rewrite to diff against
    (draft_text is the only text in play). Only 'fix' (a genuine
    rewrite) has a content-lock result to seal.
    """
    if action not in ("check", "fix"):
        raise ValueError(f"invalid action: {action!r}")
    if action == "check" and content_lock is not None:
        raise ValueError("check actions have no content_lock result to seal")
    if action == "fix" and content_lock is None:
        raise ValueError("fix actions must supply a content_lock result")

    input_hash = _sha256_hex(input_text.encode("utf-8"))

    # Output hash covers dimension scores, and Content Lock's result
    # only when one exists (Section 5.4) — the things this specific
    # action's result actually asserts. Deliberately not the full
    # result/content_lock dicts verbatim: those may carry fields
    # (timestamps, internal bookkeeping) that vary run-to-run without
    # the underlying determination changing, which would make the
    # seal fragile to things that were never part of the claim being
    # sealed.
    output_payload = {
        "verdict": result.get("verdict"),
        "tier": result.get("tier"),
        "evidence": result.get("evidence"),
        "ai_tells_clean": result.get("ai_tells_clean"),
        "ai_tells_flagged": result.get("ai_tells_flagged"),
        "burrows_delta_tier": (result.get("burrows_delta") or {}).get("tier"),
    }
    if content_lock is not None:
        output_payload["content_lock_pass"] = content_lock.get("pass")
        output_payload["content_lock_reason"] = content_lock.get("reason")
    output_hash = _sha256_hex(_canonical_json(output_payload))

    seal_payload = {
        "request_id": request_id,
        "profile_id": profile_id,
        "action": action,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "scoring_version": SCORING_VERSION,
    }
    seal_hash = _sha256_hex(_canonical_json(seal_payload))

    return {
        "request_id": request_id,
        "profile_id": profile_id,
        "action": action,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "scoring_version": SCORING_VERSION,
        "seal_hash": seal_hash,
        "prev_seal_hash": None,
    }
