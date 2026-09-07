"""
Tests for the Screen 3 calibration anchor-template fix (7 Sept 2026).

Root cause, confirmed against a real live user's calibration data: the
anchored version of starter index 3 ("Something someone just said is
genuinely getting under your skin...") used to read "Keep going from
where you left off." — a literal continuation instruction. Since
_extract_anchor_sentences() picks anchor sentences purely mechanically
(no regard for tone), a dry/descriptive anchor sentence forced a
dry/descriptive continuation, which cannot produce the emotional
reaction this required starter exists to elicit. That completion then
got folded into the person's baseline as if it were genuine unfiltered
emotional-register writing, pulling first_person_ratio down on a small
calibration sample.

These tests pin down two things: the fix is actually in place (no
"keep going" continuation framing, the emotional trigger phrase
survives), and templates 0-2 — which never had this problem, since
they frame the anchor as something an external party is doing WITH it
rather than asking the person to extend it — are unchanged.
"""
import re
import sys

sys.path.insert(0, "/home/claude/voicova")
import app


def test_anchor_template_3_no_longer_asks_for_continuation():
    rendered = app._ANCHOR_TEMPLATES[3].format(s="Some dry, descriptive sentence.")
    lowered = rendered.lower()
    assert "keep going" not in lowered
    assert "where you left off" not in lowered


def test_anchor_template_3_preserves_the_emotional_trigger():
    rendered = app._ANCHOR_TEMPLATES[3].format(s="Some dry, descriptive sentence.")
    assert "getting under your skin" in rendered


def test_anchor_template_3_frames_anchor_as_external_reaction_not_self_continuation():
    # Same shape as templates 0-2: someone else does something WITH the
    # anchor line (quotes it back, pushes back on it, asks about it) -
    # never "continue this line yourself".
    rendered = app._ANCHOR_TEMPLATES[3].format(s="Some dry, descriptive sentence.")
    assert re.search(r"someone just", rendered, re.I)


def test_anchor_templates_0_through_2_unchanged():
    expected = [
        'Picture someone pushing back hard on this line you wrote: "{s}" Type your reply exactly as it comes to you, first draft, no editing...',
        'A friend just read this line of yours, "{s}", and asked what you actually meant. Answer them right now, in your own words...',
        'Someone just asked you to justify this: "{s}" What do you say...',
    ]
    assert app._ANCHOR_TEMPLATES[:3] == expected


def test_build_starters_still_falls_back_to_generic_when_no_anchor_available():
    # No raw_text -> no anchors -> every slot falls back to the plain
    # STARTERS scenario, unchanged behaviour.
    starters = app._build_starters("")
    assert starters == app.STARTERS


def test_build_starters_anchors_slot_3_with_new_template_when_material_exists():
    raw_text = (
        "Clearance URL contains detailed information about the product offering. "
        "In addition, it details the methodology that underpins Clearance in full. "
        "A sample report showcases the details a typical report contains today. "
        "The system runs correctly for eighteen months without any real incident."
    )
    starters = app._build_starters(raw_text)
    assert "getting under your skin" in starters[3]
    assert "keep going" not in starters[3].lower()
