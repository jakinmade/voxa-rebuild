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


class TestExpandedGrammarCategories:
    """
    Regression: 19 Aug 2026. The original 6-category whitelist meant
    any grammar error outside those 6 was structurally invisible to
    this pass, regardless of the model's own competence — confirmed
    against a real render ("It brings up when someone finally asks",
    a transitive verb used with no object) matching none of the 6.
    Expanded to 12 named categories plus a catch-all. Tests the
    prompt's own text, same pattern as the rest of this file (no live
    API access from unit tests) — confirms each new category is
    actually present, not just that the change compiles.
    """

    def test_subject_verb_agreement_present(self):
        system = _extract_system_prompt()
        assert "subject-verb agreement" in system.lower()

    def test_verb_valency_present_with_the_actual_confirmed_example(self):
        system = _extract_system_prompt()
        assert "valency" in system.lower()
        # The exact confirmed-live example must survive verbatim in
        # the prompt, not just a paraphrase of the concept — this is
        # the specific error that motivated the whole change.
        assert "brings up when someone finally asks" in system.lower()

    def test_pronoun_reference_present(self):
        system = _extract_system_prompt()
        assert "pronoun" in system.lower()

    def test_run_on_sentences_present(self):
        system = _extract_system_prompt()
        assert "run-on" in system.lower() or "comma splice" in system.lower()

    def test_dangling_modifiers_present(self):
        system = _extract_system_prompt()
        assert "dangling" in system.lower() or "misplaced modifier" in system.lower()

    def test_tense_inconsistency_present(self):
        system = _extract_system_prompt()
        assert "tense" in system.lower()

    def test_catch_all_present_but_scoped_to_clear_cut_errors(self):
        system = _extract_system_prompt()
        assert "clear-cut grammar error" in system.lower()
        # The catch-all must still exclude matters of taste/style, not
        # just widen scope with no boundary at all.
        assert "not merely informal" in system.lower() or "not a matter of taste" in system.lower() or "matter of taste" in system.lower()

    def test_original_six_categories_all_still_present(self):
        """The expansion must be additive, not a replacement — every
        pre-existing category stays exactly as it was."""
        system = _extract_system_prompt()
        for phrase in [
            "adverb/adjective confusion",
            "missing prepositions",
            "loose gerund constructions",
            "open-ended lists",
            "missing articles",
            "dropped words",
        ]:
            assert phrase in system.lower(), f"missing original category: {phrase}"

    def test_do_not_touch_list_completely_unchanged(self):
        """The expansion only ever widens what's fixable, never what's
        off-limits — every DO NOT TOUCH protection must still be
        present and unweakened."""
        system = _extract_system_prompt()
        assert "collective nouns" in system.lower()
        assert "england are" in system.lower()
        assert "sentence fragments used deliberately" in system.lower()
        assert "register, tone, or voice" in system.lower()
        assert "uk spellings" in system.lower() or "do not americanise" in system.lower()
        assert "never substitute" in system.lower()

    def test_haiku_model_and_call_shape_unchanged(self):
        """This is a prompt-content change only — model, max_tokens
        sizing logic, and single-call shape must be untouched."""
        captured = {}

        class _StubMessages:
            def create(self, **kwargs):
                captured.update(kwargs)
                class _Resp:
                    content = [type("Block", (), {"text": ""})()]
                return _Resp()

        class _StubClient:
            messages = _StubMessages()

        _grammar_fix_pass("Some test text here for sizing.", _StubClient())
        assert captured["model"] == "claude-haiku-4-5-20251001"
        assert "max_tokens" in captured
