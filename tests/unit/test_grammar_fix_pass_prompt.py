"""
Regression: 16 Aug 2026 live render. _grammar_fix_pass (a second,
separate LLM call scoped to grammar-only correction) had previously
been observed hallucinating a name in a salutation ("Hi Josh,." -
see the historical comment in prompts.py's _regex_sweep step 12,
which only patched the resulting punctuation artifact). It recurred
in a full form this session: the opening salutation name flipped
from "Scott" to "Josh" with no punctuation artifact left behind at
all, so nothing downstream had a mechanical hook to catch it. This
tests the system prompt itself, not the live model call (no API
access from unit tests) — confirming the instruction exists is the
testable half of this fix; score_semantic_drift/compute_risk's
dropped_entities hard-fail (see test_entity_drop_risk.py) is the
deterministic backstop for if the instruction doesn't hold.
"""
from prompts import _grammar_fix_pass


def _extract_system_prompt():
    """_grammar_fix_pass builds its system prompt as a local variable
    before making the API call - call it with a stub client that
    captures the system kwarg instead of hitting the network."""
    captured = {}

    class _StubMessages:
        def create(self, **kwargs):
            captured["system"] = kwargs.get("system", "")

            class _Resp:
                content = [type("Block", (), {"text": ""})()]
            return _Resp()

    class _StubClient:
        messages = _StubMessages()

    _grammar_fix_pass("Scott, following up.", _StubClient())
    return captured["system"]


def test_names_and_proper_nouns_explicitly_protected():
    system = _extract_system_prompt()
    assert "Names" in system or "names" in system
    assert "proper nouns" in system.lower()


def test_salutation_name_specifically_called_out():
    system = _extract_system_prompt()
    assert "salutation" in system.lower()
