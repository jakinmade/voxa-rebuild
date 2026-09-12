"""
Tests for _select_reference_statement (voice_engine.py, 12 Sept 2026) —
PR 3 of the register-aware voice fidelity build. Wires PR 1's storage
and PR 2's detector together: given a detected register and a profile's
reference_statements dict, picks which sample _build_voice_dna should
anchor against.
"""
import voice_engine as ve


def test_exact_register_match_wins():
    samples = {"professional": "prof sample", "casual": "casual sample"}
    assert ve._select_reference_statement(samples, "casual") == "casual sample"
    assert ve._select_reference_statement(samples, "professional") == "prof sample"


def test_falls_back_to_professional_when_no_exact_match():
    samples = {"professional": "prof sample"}
    assert ve._select_reference_statement(samples, "casual") == "prof sample"
    assert ve._select_reference_statement(samples, "email") == "prof sample"


def test_falls_back_to_any_populated_entry_when_no_professional():
    samples = {"casual": "casual sample"}
    assert ve._select_reference_statement(samples, "email") == "casual sample"


def test_falls_back_to_legacy_string_when_dict_empty():
    assert ve._select_reference_statement({}, "professional", "legacy sample") == "legacy sample"
    assert ve._select_reference_statement(None, "professional", "legacy sample") == "legacy sample"


def test_falls_back_to_legacy_string_when_dict_has_no_usable_entries():
    # Every value falsy/empty — same as an empty dict for selection
    # purposes.
    samples = {"professional": "", "casual": None}
    assert ve._select_reference_statement(samples, "casual", "legacy sample") == "legacy sample"


def test_returns_empty_string_when_nothing_available_at_all():
    assert ve._select_reference_statement({}, "professional", "") == ""
    assert ve._select_reference_statement(None, "casual", "") == ""


def test_exact_match_beats_legacy_fallback():
    # The new multi-register data should always be preferred over the
    # legacy single-value column when both exist.
    samples = {"casual": "new casual sample"}
    assert ve._select_reference_statement(samples, "casual", "old legacy sample") == "new casual sample"


def test_deterministic_same_inputs_same_output():
    samples = {"professional": "a", "casual": "b", "email": "c"}
    results = {ve._select_reference_statement(samples, "casual") for _ in range(10)}
    assert results == {"b"}
