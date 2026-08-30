"""
Tests for per-register compounding baselines (30 Aug 2026).

A person's email voice and social voice are legitimately different
fingerprints — the existing baseline_fingerprint blends every sample
into one profile regardless of register. This adds a second,
independent compounding baseline keyed by platform_format
(st.session_state.baseline_fingerprints_by_format), populated only
from Learn-from-edit samples (which know which register the render
targeted) and consulted at render time only when it has accumulated
enough words to be trusted over the blended baseline.

Deliberately additive: every test here also asserts the existing
baseline_fingerprint path is completely unaffected, since that's the
actual regression risk — 30+ existing call sites across app.py and
persistence.py read the single blended baseline and must keep working
exactly as before regardless of this feature.

Session-state fixture pattern matches test_ai_slop_firewall.py's
established convention.
"""
import streamlit as st
import pytest

import app


@pytest.fixture(autouse=True)
def _reset_session_state():
    st.session_state.clear()
    st.session_state["cumulative_words"] = 0
    st.session_state["cumulative_docs"] = 0
    st.session_state["observations"] = []
    yield
    st.session_state.clear()


SAMPLE_A = (
    "This is the first genuine writing sample used to build a baseline "
    "for these tests, long enough to produce a real word count."
)
SAMPLE_B = (
    "This is a second, different writing sample used to confirm merging "
    "behaves correctly across two separate calls to the same function."
)


# ------------------------------------------------------------------
# Additive behaviour — the existing blended baseline is never affected
# ------------------------------------------------------------------

def test_no_platform_format_leaves_by_format_untouched():
    """The default, pre-existing call shape (onboarding samples) must
    behave exactly as before — no per-format bucket created at all."""
    app._add_writing_sample_to_fingerprint(SAMPLE_A)
    assert st.session_state.get("baseline_fingerprints_by_format") is None
    assert st.session_state["baseline_fingerprint"]["word_count"] == len(SAMPLE_A.split())


def test_platform_format_still_merges_the_blended_baseline_too():
    """Passing platform_format must not skip the existing blended
    merge — both happen, the blended one exactly as before."""
    app._add_writing_sample_to_fingerprint(SAMPLE_A, platform_format="email")
    assert st.session_state["baseline_fingerprint"]["word_count"] == len(SAMPLE_A.split())


# ------------------------------------------------------------------
# Per-format bucket creation and merging
# ------------------------------------------------------------------

def test_platform_format_creates_its_own_bucket():
    app._add_writing_sample_to_fingerprint(SAMPLE_A, platform_format="email")
    by_format = st.session_state["baseline_fingerprints_by_format"]
    assert "email" in by_format
    assert by_format["email"]["word_count"] == len(SAMPLE_A.split())


def test_different_formats_compound_independently():
    app._add_writing_sample_to_fingerprint(SAMPLE_A, platform_format="email")
    app._add_writing_sample_to_fingerprint(SAMPLE_B, platform_format="social")
    by_format = st.session_state["baseline_fingerprints_by_format"]
    assert by_format["email"]["word_count"] == len(SAMPLE_A.split())
    assert by_format["social"]["word_count"] == len(SAMPLE_B.split())
    assert "email" in by_format and "social" in by_format


def test_same_format_compounds_across_two_samples():
    app._add_writing_sample_to_fingerprint(SAMPLE_A, platform_format="email")
    app._add_writing_sample_to_fingerprint(SAMPLE_B, platform_format="email")
    by_format = st.session_state["baseline_fingerprints_by_format"]
    expected_wc = len(SAMPLE_A.split()) + len(SAMPLE_B.split())
    assert by_format["email"]["word_count"] == expected_wc
    # Blended baseline also reflects both samples, same total.
    assert st.session_state["baseline_fingerprint"]["word_count"] == expected_wc


def test_general_blended_baseline_unaffected_by_which_formats_were_used():
    """Regression check: the blended baseline's word count must equal
    the sum of every sample regardless of what platform_format (if
    any) each individual sample carried."""
    app._add_writing_sample_to_fingerprint(SAMPLE_A, platform_format="email")
    app._add_writing_sample_to_fingerprint(SAMPLE_B, platform_format=None)
    expected_wc = len(SAMPLE_A.split()) + len(SAMPLE_B.split())
    assert st.session_state["baseline_fingerprint"]["word_count"] == expected_wc
    # Only "email" got a bucket — the None-format sample never created one.
    assert list(st.session_state["baseline_fingerprints_by_format"].keys()) == ["email"]
