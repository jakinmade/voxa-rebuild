"""
paste_guard — a text input that hard-blocks paste and drag-drop text
insertion, reporting its value back to Streamlit like a native widget.

VERIFIED (August 2026): tested end-to-end against a real running
Streamlit instance with a headless browser (Playwright) — typing
round-trips correctly, real Ctrl+V paste is blocked, drag-drop insertion
is blocked. See index.html and tests/browser/test_paste_guard_live.py.
"""

import os
import streamlit.components.v1 as components

_component_dir = os.path.dirname(os.path.abspath(__file__))

_paste_guard = components.declare_component("paste_guard", path=_component_dir)


def paste_guard(value: str = "", key: str | None = None) -> str:
    result = _paste_guard(value=value, key=key, default=value)
    return result or ""
