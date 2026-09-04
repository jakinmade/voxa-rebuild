"""
Regression: 16 Aug 2026 live render. A recipient's salutation name
flipped from "Scott" to "Josh" in the opening word of a real render
and still scored 96% semantic match / entity_preservation 100%.

Root cause was two separate gaps, both fixed here:

1. _entities_and_numbers' lookbehind (?<=[.!? ]) only matches a
   capitalised word preceded by a sentence boundary or a space -
   the very first word of the whole text has nothing before it, so
   it was structurally never extracted as an entity on either side.
   A salutation name is, by definition, that first word. Fixed by
   also matching start-of-string.

2. Even with the name correctly extracted as a dropped entity,
   compute_risk never checked dropped_entities at all - only
   attribution_swaps and sentence_growth were wired as hard fails,
   despite _entities_and_numbers' own docstring calling these "the
   facts a rewrite must not lose". A single dropped name could sit
   underneath an otherwise-strong aggregate semantic_match the same
   way one invented sentence used to hide inside score_render_delta.
   Fixed by adding dropped_entities as a third hard-fail category,
   same tier as attribution_swaps and sentence_growth.
"""
import voice_engine as ve


# ---------------------------------------------------------------------------
# _entities_and_numbers — first word of the text must be checkable
# ---------------------------------------------------------------------------

def test_first_word_of_text_extracted_as_entity():
    text = "Scott, following up on the test link from a while back."
    entities = ve._entities_and_numbers(text)
    assert "Scott" in entities


def test_first_word_still_excluded_if_common_sentence_opener():
    text = "The following up on the test link happened yesterday."
    entities = ve._entities_and_numbers(text)
    assert "The" not in entities


def test_mid_sentence_entities_unaffected_by_the_fix():
    """Confirms the start-of-string addition is additive, not a
    replacement for the existing [.!? ]-preceded matching."""
    text = "Scott — following up. Matt was pushing for this on Slack."
    entities = ve._entities_and_numbers(text)
    assert "Scott" in entities
    assert "Matt" in entities
    assert "Slack" in entities


def test_name_swap_now_visible_as_dropped_entity():
    original = "Scott, following up on the CLEARANCE test link from a while back."
    render = "Josh, following up on the CLEARANCE test link from a while back."
    result = ve.score_semantic_drift(original, render)
    assert "Scott" in result["dropped_entities"]


# ---------------------------------------------------------------------------
# Two more real findings, same session (4 Sept 2026 breadth benchmark),
# same underlying tension: _entities_and_numbers' exclusion-list approach
# can't be exhaustive (any capitalised word not on the list is a candidate
# entity), so ordinary sentence-initial common nouns and casual
# interjections keep surfacing as false positives one real example at a
# time. Each fix here is deliberately narrow and verified NOT to weaken
# the name-swap protection above, rather than a broader heuristic change
# that would trade real protection for less list maintenance.
# ---------------------------------------------------------------------------

def test_pluralised_common_noun_not_flagged_as_dropped():
    """'Users loved it.' is an ordinary common noun, capitalised only
    because it starts a sentence, not a proper noun. The rewrite
    correctly used singular 'user' mid-sentence - entirely normal
    English - but the exact whole-word match required 'users' itself
    to survive and flagged a real, meaningless false positive
    (semantic_match cratered to 9 on the actual live render)."""
    original = "The findings clearly demonstrate that the intervention worked. Users loved it."
    render = "The findings clearly demonstrate that the intervention worked, and user response was strongly positive."
    result = ve.score_semantic_drift(original, render)
    assert "Users" not in result["dropped_entities"]
    assert result["entity_preservation"] == 100


def test_morphology_tolerance_does_not_weaken_name_swap_detection():
    """Regression guard for the fix above: singular/plural tolerance
    must not make the Scott -> Josh detection any less strict - a
    genuinely different name is still a genuinely different name
    regardless of this change."""
    original = "Scott, following up on the CLEARANCE test link."
    render = "Josh, following up on the CLEARANCE test link."
    result = ve.score_semantic_drift(original, render)
    assert "Scott" in result["dropped_entities"]
    assert result["entity_preservation"] < 100


def test_casual_interjections_not_extracted_as_entities():
    """Real finding from the register-conversion fix: correctly
    removing casual filler ('OMG this proposal is honestly kind of a
    mess lol...Anyway thoughts?? Let's chat.') from a formal rewrite
    left the entity checker flagging the removed filler itself as
    dropped facts. None of these are proper nouns."""
    text = "OMG this proposal is honestly kind of a mess lol!! Anyway thoughts?? Let's chat."
    entities = ve._entities_and_numbers(text)
    assert "OMG" not in entities
    assert "Anyway" not in entities
    assert "Let" not in entities


# ---------------------------------------------------------------------------
# compute_risk — dropped entity is a hard fail, same tier as attribution
# swap and sentence_growth
# ---------------------------------------------------------------------------

def test_dropped_entity_forces_high_risk_even_with_high_semantic_match():
    delta = {}
    semantic = {"semantic_match": 96, "attribution_swaps": [], "dropped_entities": ["Scott"]}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "High"


def test_no_dropped_entity_no_forced_high_risk():
    delta = {}
    semantic = {"semantic_match": 96, "attribution_swaps": [], "dropped_entities": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": True})
    assert risk == "Low"


def test_dropped_entity_check_additive_to_existing_hard_fails():
    """Confirms this is a new, independent check - not a replacement
    for the attribution-swap or AI-tell hard fails."""
    delta = {}
    semantic = {"semantic_match": 100, "attribution_swaps": [], "dropped_entities": []}
    risk = ve.compute_risk(delta, semantic, ai_tells={"clean": False})
    assert risk == "High"
