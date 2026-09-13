"""
Regression test — 13 Sept 2026 real-render finding: the grammar-fix
pass's category 5 (missing articles) explicitly named simple cases
("a, an, the before countable nouns") but a real render's genuine
missing-article error survived uncorrected: "The scenario you
described, dashboard green while an agent quietly does the wrong
thing, is exactly what CLEARANCE is built to catch." — "dashboard
green" has no determiner and doesn't parse as a noun phrase. Likely
cause: the pass (or the model interpreting it) read this as a
deliberate compressed idiom (like "traffic light red") rather than an
error, since DO NOT TOUCH items 2/4 protect genuine stylistic
compression. Category 5 now includes this exact case as a worked
example with an explicit test to distinguish the two: does the phrase
restate a noun just mentioned inside a larger sentence (needs the
article) vs. stand alone as its own exclamation (protected).

This test checks the system prompt text itself (the only thing
verifiable without an Anthropic API key in this sandbox) — it does
not call the live grammar-fix model, so it cannot confirm the actual
correction happens. See the function docstring for the full case.
"""
from prompts import _grammar_fix_pass
import inspect


def test_category_5_includes_the_real_dashboard_green_case():
    source = inspect.getsource(_grammar_fix_pass)
    assert "dashboard green" in source
    assert "traffic light red" in source or "all systems" in source


def test_category_5_still_protects_standalone_compressed_idioms():
    source = inspect.getsource(_grammar_fix_pass)
    # The distinguishing test (appositive-inside-a-sentence vs
    # standalone exclamation) must still be present, not just the
    # worked example, so standalone compressed idioms stay protected.
    assert "standing alone as its own exclamation" in source
