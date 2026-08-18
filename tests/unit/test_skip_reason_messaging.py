"""
Regression guard, same bug class as test_voice_match_close_verdict.py:
the evidence sentence and the numbers next to it telling two different
stories about the same render.

This time the culprit was SKIPPED. The verdict was originally built
for exactly one reason -- the input genuinely had nothing of that kind
of content to convert (no first-person content at all), and both the
evidence sentence ("nothing to convert in the original") and
build_voice_report's biggest_changes list (excluded entirely) were
correct for that case.

A second, genuinely different SKIPPED reason was added the same
session: the input has PLENTY of that content -- more than the
person's baseline, even -- and the residual drift can't be reduced
further without deleting real content (see ownership_miss_is_content_
driven in deterministic_fixers.py). Reusing the same message for both
was a real, live bug: "nothing to convert in the original" is actively
wrong when there's abundant content, and "Biggest changes: No
significant drift" is actively wrong when there's genuine, substantial
(if unavoidable) drift being silently hidden.

Fixed via a skip_reason field on the delta entry, defaulting to
"no_content" when unset so any caller not yet setting it explicitly
keeps the original behaviour unchanged.
"""
import voice_engine as ve


def test_no_content_skip_reason_keeps_original_message():
    """Regression: the original, correct message for the original,
    correct case must survive byte-for-byte."""
    delta = {
        "first_person_ratio": {"verdict": "SKIPPED", "skip_reason": "no_content", "pct_diff": 0.86, "delta": 0.1},
    }
    result = ve.voice_match_label(delta)
    assert "nothing to convert in the original" in result["evidence"]
    assert "cutting real content" not in result["evidence"]


def test_missing_skip_reason_defaults_to_no_content_message():
    """Backward compatibility: any caller not yet setting skip_reason
    (there was exactly one call site before this session added a
    second) must see identical behaviour to before this field existed."""
    delta = {
        "first_person_ratio": {"verdict": "SKIPPED", "pct_diff": 0.86, "delta": 0.1},
    }
    result = ve.voice_match_label(delta)
    assert "nothing to convert in the original" in result["evidence"]


def test_content_ceiling_skip_reason_gets_distinct_message():
    """The actual live bug this fixes: content_ceiling must NOT say
    'nothing to convert in the original' -- there's abundant content,
    that's the whole point."""
    delta = {
        "first_person_ratio": {"verdict": "SKIPPED", "skip_reason": "content_ceiling", "pct_diff": 0.37, "delta": 0.06},
    }
    result = ve.voice_match_label(delta)
    assert "nothing to convert in the original" not in result["evidence"]
    assert "cutting real content" in result["evidence"]
    assert "N/A on ownership" in result["evidence"]


def test_both_skip_reasons_can_appear_together_distinctly():
    """Two different SKIPPED dimensions, two different reasons, in the
    same render -- both must appear, each with its own correct message,
    not collapsed into one generic clause."""
    delta = {
        "first_person_ratio": {"verdict": "SKIPPED", "skip_reason": "content_ceiling", "pct_diff": 0.37, "delta": 0.06},
        "directive_ratio": {"verdict": "SKIPPED", "skip_reason": "no_content", "pct_diff": 0.9, "delta": 0.1},
    }
    result = ve.voice_match_label(delta)
    assert "nothing to convert in the original" in result["evidence"]
    assert "cutting real content" in result["evidence"]
    assert "ownership" in result["evidence"]
    assert "directness" in result["evidence"]


def test_biggest_changes_excludes_no_content_skipped():
    """Correct, unchanged behaviour: nothing to show, nothing shown."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "first_person_ratio": {"verdict": "SKIPPED", "skip_reason": "no_content", "pct_diff": 0.86, "delta": 0.1},
    }
    semantic = {"semantic_match": 98, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, "Low", "Low", {"clean": True})
    assert report["biggest_changes"] == []


def test_biggest_changes_includes_content_ceiling_skipped():
    """The actual live bug: 'Biggest changes: No significant drift'
    was shown to the person when there was genuine ~37% drift, purely
    because the verdict was SKIPPED. The number must be visible --
    the evidence sentence is what explains WHY it's not an error, not
    this list silently hiding it."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
        "first_person_ratio": {"verdict": "SKIPPED", "skip_reason": "content_ceiling", "pct_diff": 0.37, "delta": 0.06},
    }
    semantic = {"semantic_match": 98, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, "Low", "Low", {"clean": True})
    assert report["biggest_changes"] == ["ownership (first person) +37%"]


def test_biggest_changes_missing_skip_reason_defaults_to_excluded():
    """Backward compatibility for build_voice_report, same as above
    for voice_match_label -- an old-style SKIPPED entry with no
    skip_reason set must keep being excluded, not suddenly appear."""
    delta = {
        "first_person_ratio": {"verdict": "SKIPPED", "pct_diff": 0.86, "delta": 0.1},
    }
    semantic = {"semantic_match": 98, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, "Low", "Low", {"clean": True})
    assert report["biggest_changes"] == []


def test_hit_dimensions_still_excluded_regardless_of_skip_reason_logic():
    """Sanity check that the restructured exclusion logic didn't
    accidentally start including HIT dimensions."""
    delta = {
        "hedge_density": {"verdict": "HIT", "pct_diff": 0.05, "delta": 0.01},
    }
    semantic = {"semantic_match": 98, "dropped_entities": [], "attribution_swaps": []}
    report = ve.build_voice_report(delta, semantic, "Low", "Low", {"clean": True})
    assert report["biggest_changes"] == []
