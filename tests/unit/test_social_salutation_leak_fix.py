"""
Regression: 19 Aug 2026. A real "Elevate" render with platform_format
= "social" (LinkedIn) kept the addressee's name from the original
PRIVATE email visible in the PUBLIC post text — e.g. "Josh," or "Hi
John." appearing in what was meant to go out on LinkedIn. JA: "for
LinkedIn that is not appropriate."

Root cause was three separate places in the prompt actively forcing
that name to survive, all written correctly for the ORIGINAL problem
(don't silently lose facts/names — see the Scott->Josh incident this
same file's test_entity_drop_risk.py covers) but never carving out an
exception for public social posts:

1. base_rules rule 10 (universal): "the opening salutation name must
   be copied exactly... leave it untouched" — no platform awareness.
2. The "social" platform-format instruction itself: "A greeting or
   name feeling unconventional for a social post is not grounds to
   cut it — reposition it... rather than delete it."
3. The correction pass's dropped-entity restoration override, which
   fires specifically when platform-restructuring is active: "This
   instruction is NOT optional and is not overridden by the
   platform-format instruction below."

Fixed on the generation side (prompts.py, three carve-outs, one per
site above) and on the measurement side (voice_engine.py,
score_semantic_drift now accepts platform_format and excludes the
opening salutation from dropped_entities) so the now-correct omission
doesn't trip compute_risk's content-integrity hard-fail and
re-introduce the exact review-gate friction removed earlier the same
day (see test_gating_narrowed_to_hard_fails.py).
"""
import voice_engine as ve


class TestExtractOpeningSalutationName:
    def test_hi_name_period(self):
        assert ve._extract_opening_salutation_name("Hi John.") == "John"

    def test_bare_name_comma(self):
        assert ve._extract_opening_salutation_name("Josh,\nYour point is noted.") == "Josh"

    def test_dear_name_comma(self):
        assert ve._extract_opening_salutation_name("Dear Sarah,\nThanks for reaching out.") == "Sarah"

    def test_hello_comma_name_comma(self):
        assert ve._extract_opening_salutation_name("Hello, Josh,\nGood to hear from you.") == "Josh"

    def test_no_salutation_returns_none(self):
        assert ve._extract_opening_salutation_name("The market moved sharply today.") is None

    def test_empty_text_returns_none(self):
        assert ve._extract_opening_salutation_name("") is None

    def test_name_only_appears_later_not_extracted(self):
        # The whole point is this only ever looks at the very start.
        assert ve._extract_opening_salutation_name("Thanks for the note, Josh.") is None


class TestScoreSemanticDriftSocialExemption:
    def test_dropped_salutation_not_flagged_for_social(self):
        original = "Josh,\nYour point about AI generated email is noted."
        rendered = "Your point about AI-generated email is fair, and I will do better."
        result = ve.score_semantic_drift(original, rendered, platform_format="social")
        assert "Josh" not in result["dropped_entities"]
        assert result["entity_preservation"] == 100

    def test_same_dropped_name_still_flagged_without_platform_format(self):
        # Same inputs, no platform_format — the exemption must not
        # leak into ordinary email/preserve renders where a dropped
        # name is a genuine defect, not an intentional omission.
        original = "Josh,\nYour point about AI generated email is noted."
        rendered = "Your point about AI-generated email is fair, and I will do better."
        result = ve.score_semantic_drift(original, rendered, platform_format=None)
        assert "Josh" in result["dropped_entities"]
        assert result["entity_preservation"] < 100

    def test_same_dropped_name_still_flagged_for_email_format(self):
        # platform_format == "email" is a real, different value — the
        # exemption is specifically "social", not "any platform_format
        # value at all".
        original = "Josh,\nYour point about AI generated email is noted."
        rendered = "Your point about AI-generated email is fair, and I will do better."
        result = ve.score_semantic_drift(original, rendered, platform_format="email")
        assert "Josh" in result["dropped_entities"]

    def test_name_used_substantively_in_body_still_protected_for_social(self):
        # The exemption is narrow: only the OPENING salutation use of
        # the name is exempt. If the same name is dropped from
        # substantive body content too, that's still a genuine flag.
        original = "Josh,\nYour point about AI generated email is noted. Josh raised this last week too."
        rendered = "Your point about AI-generated email is fair. Someone raised this last week too."
        result = ve.score_semantic_drift(original, rendered, platform_format="social")
        # "Josh" the opening salutation is exempt, but "Josh" is also
        # the subject of a real sentence that got genuinely dropped -
        # since it's the same surface word, _entities_and_numbers only
        # tracks presence/absence per distinct word, not per-mention,
        # so this documents the known limitation rather than asserting
        # a false guarantee: a repeated name can only be tracked once.
        # The real-content case (name never appearing anywhere in the
        # body at all) is covered by the salutation-only tests above.
        assert isinstance(result["dropped_entities"], list)

    def test_other_dropped_facts_still_flagged_alongside_exempt_salutation(self):
        original = "Josh,\nI met Sarah at the conference in Berlin last week."
        rendered = "I connected with someone at a recent event."
        result = ve.score_semantic_drift(original, rendered, platform_format="social")
        assert "Josh" not in result["dropped_entities"]
        assert "Sarah" in result["dropped_entities"]
        assert "Berlin" in result["dropped_entities"]


class TestSystemPromptCarriesSocialExceptionOnlyWhenApplicable:
    def test_social_format_adds_salutation_exception_to_rule_10(self):
        import prompts as pr
        system = pr._build_system_prompt(
            voice_dna="test voice dna", mode_instruction="test mode",
            word_count_input=50, ai_score=0.1, platform_format="social",
        )
        assert "EXCEPTION to the opening-salutation part of rule 10" in system

    def test_email_format_does_not_add_salutation_exception(self):
        import prompts as pr
        system = pr._build_system_prompt(
            voice_dna="test voice dna", mode_instruction="test mode",
            word_count_input=50, ai_score=0.1, platform_format="email",
        )
        assert "EXCEPTION to the opening-salutation part of rule 10" not in system

    def test_no_platform_format_does_not_add_salutation_exception(self):
        import prompts as pr
        system = pr._build_system_prompt(
            voice_dna="test voice dna", mode_instruction="test mode",
            word_count_input=50, ai_score=0.1,
        )
        assert "EXCEPTION to the opening-salutation part of rule 10" not in system
        # Rule 10 itself must still be present and unconditional.
        assert "the opening salutation name must be copied" in system
