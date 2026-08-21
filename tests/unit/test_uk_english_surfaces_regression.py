"""
Regression: 21 Aug 2026 live render. "It surfaces when someone finally
asks who decided this was suitable" was rewritten to "It brings up
when someone finally asks..." - "brings up" is transitive and needs
an object, so used intransitively it breaks grammar. This had already
been flagged once (19 Aug, voice_engine.py's LEXICAL_FIDELITY_WATCHLIST)
and a 12-category grammar-fix-pass expansion was shipped to catch it -
but the pass runs on Haiku and did not reliably apply its own named
example. Root cause traced one level further back: _apply_uk_english's
blind (r"\bsurfaces\b", "brings up") substitution was the actual
source, firing unconditionally regardless of whether "surfaces" was
used transitively or intransitively. Removed at source rather than
patched downstream a second time - this test guards against it
reappearing.
"""
from prompts import _apply_uk_english


def test_intransitive_surfaces_left_unchanged():
    text = "It surfaces when someone finally asks who decided this."
    result = _apply_uk_english(text)
    assert "brings up" not in result
    assert "surfaces" in result


def test_transitive_surfaces_left_unchanged_too():
    # Even the grammatically-safe case is left alone now - the
    # substitution was never a strong enough AI-tell to justify the
    # risk, per the 21 Aug decision recorded in prompts.py.
    text = "The report surfaces three key findings."
    result = _apply_uk_english(text)
    assert "brings up" not in result
    assert "surfaces" in result
