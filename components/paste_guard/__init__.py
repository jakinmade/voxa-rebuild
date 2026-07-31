"""
paste_guard — a text input that hard-blocks paste and drag-drop text
insertion, reporting its value back to Streamlit like a native widget.

Needs a live browser smoke test before this is trusted in production —
see the note in index.html. Everything else in this rebuild is ported
from proven, working code; this component is new and unverified outside
the protocol spec.
"""

import os
import streamlit.components.v1 as components

_component_dir = os.path.dirname(os.path.abspath(__file__))

_paste_guard = components.declare_component("paste_guard", path=_component_dir)


def paste_guard(value: str = "", key: str | None = None) -> str:
    result = _paste_guard(value=value, key=key, default=value)
    return result or ""
