"""
tests/unit/test_evidence_seal.py — direct coverage of
api/evidence/seal.py's seal(), proving the fix for independent
architecture review finding #4: the seal must actually bind to the
match percentage and per-dimension scores a user is shown, not just
the verdict/tier/evidence summary around them.

seal() is a pure function (no Supabase, no engine) — these tests call
it directly with hand-built result dicts rather than running a full
render, which is the deliberate, minimal way to prove a hashing
function's own behaviour without dragging in everything upstream of it.
"""
from __future__ import annotations

from api.evidence import seal as evidence


def _base_result(**overrides):
    result = {
        "verdict": "PASS",
        "tier": "Strong match",
        "evidence": "Matches your usual hedging and sentence rhythm.",
        "ai_tells_clean": True,
        "ai_tells_flagged": [],
        "burrows_delta": {"tier": "close", "delta": 0.02, "biggest_divergences": []},
        "match_pct": 87,
        "dimension_scores": {
            "hedge_density": {"label": "Hedging", "baseline": 1.0, "output": 0.9, "verdict": "HIT"},
        },
    }
    result.update(overrides)
    return result


def test_seal_changes_when_the_displayed_match_percentage_changes():
    # This is the exact gap the review flagged: two results that
    # differ ONLY in the number shown to the user must not produce
    # the same seal — otherwise the seal isn't actually attesting to
    # what's on screen.
    low = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(match_pct=42),
    )
    high = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(match_pct=87),
    )
    assert low["output_hash"] != high["output_hash"]
    assert low["seal_hash"] != high["seal_hash"]


def test_seal_changes_when_a_dimension_score_changes():
    baseline = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(),
    )
    changed = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft",
        result=_base_result(dimension_scores={
            "hedge_density": {"label": "Hedging", "baseline": 1.0, "output": 0.3, "verdict": "MISSED"},
        }),
    )
    assert baseline["output_hash"] != changed["output_hash"]
    assert baseline["seal_hash"] != changed["seal_hash"]


def test_seal_is_deterministic_for_identical_input():
    first = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(),
    )
    second = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(),
    )
    assert first["output_hash"] == second["output_hash"]
    assert first["seal_hash"] == second["seal_hash"]


def test_seal_unaffected_by_fields_outside_the_sealed_claim():
    # A result dict can carry extra keys (e.g. from a future engine
    # change) that aren't part of what this seal attests to — the
    # hash must only move when a field output_payload actually reads
    # changes, matching seal.py's own comment on why it's not the
    # full dict verbatim.
    first = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(),
    )
    second = evidence.seal(
        request_id="r1", profile_id="p1", action="check",
        input_text="some draft", result=_base_result(some_future_field="irrelevant"),
    )
    assert first["output_hash"] == second["output_hash"]


def test_seal_for_fix_action_includes_content_lock_in_the_hash():
    passed = evidence.seal(
        request_id="r1", profile_id="p1", action="fix",
        input_text="some draft", result=_base_result(),
        content_lock={"pass": True, "reason": None},
    )
    failed = evidence.seal(
        request_id="r1", profile_id="p1", action="fix",
        input_text="some draft", result=_base_result(),
        content_lock={"pass": False, "reason": "Facts dropped"},
    )
    assert passed["output_hash"] != failed["output_hash"]


def test_check_action_rejects_a_content_lock_argument():
    # score_draft_check has no rewrite to diff against — see seal.py's
    # own docstring on why 'check' must never carry a content_lock
    # result. Existing validation, re-confirmed still enforced after
    # this change.
    import pytest
    with pytest.raises(ValueError):
        evidence.seal(
            request_id="r1", profile_id="p1", action="check",
            input_text="some draft", result=_base_result(),
            content_lock={"pass": True, "reason": None},
        )
