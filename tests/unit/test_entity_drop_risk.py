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


# ---------------------------------------------------------------------------
# _entities_and_numbers — common correspondence-opener words must not be
# false-flagged as dropped entities. Confirmed live 31 Aug 2026: the
# start-of-string fix above (correctly) made the regex catch a
# capitalised first word, but the lookbehind (?<=[.!? ]) also matches
# after any plain space, so it was never really "sentence-initial
# only" — it flags any capitalised word not in the exclusion set,
# anywhere in the text. A real render opening "Please write a short
# note..." had "Please" flagged as a dropped proper noun once the
# rewrite paraphrased past it, alongside "Thanks" and "Looking" in
# similar constructions.
# ---------------------------------------------------------------------------

def test_common_correspondence_openers_not_treated_as_entities():
    text = (
        "Please write a short note. Thanks for reading. Looking forward "
        "to it. Following up on the CLEARANCE link."
    )
    entities = ve._entities_and_numbers(text)
    assert "Please" not in entities
    assert "Thanks" not in entities
    assert "Looking" not in entities
    assert "Following" not in entities


def test_correspondence_opener_paraphrased_away_is_not_a_hard_fail():
    """The exact shape of the live incident: a message opens with a
    common imperative word, the rewrite paraphrases it away entirely
    (not just relocates or lowercases it) — must not trip
    dropped_entities the way a genuinely dropped name or number would."""
    original = "Please write a short note about the launch plan."
    render = "Here's a short note about the launch plan."
    result = ve.score_semantic_drift(original, render)
    assert "Please" not in result["dropped_entities"]


def test_genuine_name_still_caught_alongside_correspondence_openers():
    """The broadened exclusion set must not weaken the original
    salutation-name fix it sits next to — a real dropped/swapped name
    in the same kind of message still needs to be caught."""
    original = "Please pass this along to Scott before Friday."
    render = "Please pass this along to Josh before Friday."
    result = ve.score_semantic_drift(original, render)
    assert "Scott" in result["dropped_entities"]
    assert "Please" not in result["dropped_entities"]

