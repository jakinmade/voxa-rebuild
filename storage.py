"""
storage.py — session state.

Thin wrapper around st.session_state. Kept as its own module deliberately:
when real persistence is built (currently out of scope — see v4 spec
Section 11, Railway is disk-based JSON wiped on redeploy), this is the
one file that changes. Nothing else should need to know where state
actually lives.
"""

import json
import streamlit as st
from datetime import datetime


def init_state():
    defaults = {
        "screen": 1,
        "raw_text": "",
        "observations": [],
        "intent_mode": "GET_IT_DONE",
        "render_output": "",
        "render_input_text": "",
        "session_start": datetime.now().strftime("%d %B %Y, %H:%M"),
        "word_count": 0,
        "locale": "uk",
        "cumulative_words": 0,
        "cumulative_docs": 0,
        "baseline_fingerprint": None,
        "render_delta": None,
        "sample_fitness": None,

        # New in v4 — sample 2, sentence-starter completions
        "sample2_completions": ["", "", "", ""],

        # New in v4 — Voice Report fields (Section 5/7 of the spec)
        "semantic_drift": None,
        "confidence": None,
        "risk": None,
        "voice_report": None,

        # New in v4 — sample 3, one refinement after the rewrite
        "refinement_used": False,
        "refinement_tags": [],
        "refinement_freetext": "",

        # New in v4 — deepen fingerprint, visible from first use, not gated
        "deepen_open": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def go_to(screen: int):
    st.session_state.screen = screen


def reset_all():
    """Full reset — start over. Clears every key, re-initialises defaults."""
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    init_state()


def generate_receipt(session_start: str, word_count: int) -> dict:
    """Plain English render receipt. No legal claims. No guarantees."""
    return {
        "rendered_at": datetime.now().strftime("%d %B %Y, %H:%M"),
        "session_started": session_start,
        "words_analysed": word_count,
        "identity_preserved": True,
        "calibration_occurred": False,
        "summary": (
            "This render used your personal voice profile, built from your own writing in this session. "
            "The engine wrote as you. Not for you. "
            "No changes were made to your voice profile during this render. "
            "No calibration data was recorded."
        ),
    }


def export_profile() -> str:
    """
    Portable voice profile — plain JSON, owned by the user.
    Nothing here lives only on Voicova's servers; this is the file
    a person keeps regardless of what happens to the session.
    """
    profile = {
        "exported_at": datetime.now().strftime("%d %B %Y, %H:%M"),
        "session_started": st.session_state.get("session_start"),
        "locale": st.session_state.get("locale"),
        "words_analysed": st.session_state.get("cumulative_words", 0),
        "documents_analysed": st.session_state.get("cumulative_docs", 0),
        "fingerprint": st.session_state.get("observations", []),
        "baseline": st.session_state.get("baseline_fingerprint"),
    }
    return json.dumps(profile, indent=2, default=str)
