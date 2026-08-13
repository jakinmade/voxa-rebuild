"""
Voxa — Deterministic Output Cleaning (Layer 3 support)

Referenced by engine.py's render() pipeline.
Code enforcement, not a prompt instruction — runs on every render
output regardless of what the LLM produced.

DELIBERATE, DOCUMENTED DUPLICATION — read before editing either side:
This used to be a self-contained 2-of-12-step subset, ported once and
never kept in sync with the live app's fuller sweep — an August 2026
audit found it had drifted to cover only em-dash removal and Claude
construction replacement, with zero AI-tell verification, while the
live product had grown to 12 steps including hedge stripping and
plausibility-shield removal. Any API consumer was silently getting
materially worse output than the Streamlit app.

Fixed by delegating to voxa_core.text_guardrail.sweep(), the canonical
implementation for the packages/ ecosystem — voxa_rendering already
depends on voxa-core (see pyproject.toml), so this adds no new
dependency edge. voxa_core.text_guardrail is itself a verbatim port of
root-level prompts.py's _regex_sweep, NOT automatically kept in sync
with it — see that module's docstring for the full duplication note
and why root (the live Railway deployment) isn't part of this
dependency graph.

Contraction handling: still not wired here, for the same accurate
reason the original author gave — this layer has no access to a user's
keep_contractions preference (nothing in voxa_core.entities carries
it), and guessing a default would silently override the user's actual
voice baseline rather than respect it. sweep() runs with its default
(contractions expanded) until a real preference signal is plumbed
through from the profile. If this package is ever wired to real
per-user contraction preference, that wiring belongs in engine.py,
which has profile access — this function should stay a thin delegate.
"""

from __future__ import annotations

from voxa_core.text_guardrail import sweep as _sweep


def clean_render_output(text: str) -> str:
    """
    Deterministic guardrail sweep — runs on every render output.
    Delegates entirely to voxa_core.text_guardrail.sweep(), the
    canonical implementation. See module docstring for why this is a
    thin wrapper rather than its own logic, and for the contraction-
    handling limitation carried forward from the original version.
    """
    if not text:
        return text
    return _sweep(text)
