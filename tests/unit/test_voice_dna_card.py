"""
Tests for voice_dna_card.py — the shareable "My Voice DNA" image built
from the same observations/confidence data screen_my_voice() already
displays. See that module's docstring for the privacy reasoning (headlines
only, never the evidence quotes) and the dynamic-height rationale (a fixed
canvas either clips long content or leaves dead space for short content —
confirmed visually during development, not assumed).

PNG output can't be text-extracted the way the PDF export's tests check
real content (no OCR dependency here) — these verify structure instead:
valid PNG bytes, dimensions that actually respond to content length,
correct pixel colors at known coordinates for the confidence badge, and
that the module never includes raw evidence-quote text anywhere in the
observations it's given.
"""
from io import BytesIO

from PIL import Image

import voice_dna_card as vdc


_SAMPLE_OBSERVATIONS = [
    {"headline": "You write in a direct, informal register", "body": 'e.g. "totally, let\'s just do it"'},
    {"headline": "You pack a lot into a short space", "body": 'e.g. "short and to the point"'},
    {"headline": "You lead with what you do for others", "body": 'e.g. "happy to help however I can"'},
]


def _load(png_bytes: bytes) -> Image.Image:
    return Image.open(BytesIO(png_bytes))


def test_produces_valid_png_bytes():
    png_bytes = vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High")
    assert isinstance(png_bytes, bytes)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    img = _load(png_bytes)
    assert img.format == "PNG"
    assert img.mode == "RGB"


def test_width_is_fixed_at_the_designed_card_width():
    img = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High"))
    assert img.width == vdc._CARD_W


def test_height_is_content_driven_not_fixed():
    """The bug caught during development: a fixed canvas height left a
    large dead gap for short content. Confirms height actually scales
    with trait count/wrapping rather than being a constant regardless
    of input."""
    one_trait = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS[:1], "High"))
    three_traits = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High"))
    assert three_traits.height > one_trait.height


def test_empty_observations_does_not_crash():
    png_bytes = vdc.build_voice_dna_card_png([], "Medium")
    img = _load(png_bytes)
    assert img.width == vdc._CARD_W
    assert img.height > 0


def test_missing_confidence_does_not_crash():
    """confidence can be None (e.g. before enough baseline exists) —
    screen_my_voice() itself handles this case, the card must too."""
    png_bytes = vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, None)
    img = _load(png_bytes)
    assert img.width == vdc._CARD_W


def test_missing_confidence_produces_a_shorter_card_than_with_one():
    """No badge drawn -> less content -> shorter card, confirming the
    None path isn't drawing a badge with placeholder text."""
    with_badge = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High"))
    without_badge = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, None))
    assert without_badge.height < with_badge.height


def test_confidence_badge_color_matches_the_live_apps_own_convention():
    """Regression guard, mirrors the PDF export's equivalent test:
    confirms the three confidence colors are genuinely distinct and
    match _MY_VOICE_CONFIDENCE_BADGE's polarity (High=green,
    Medium=amber/warning, Low=red) rather than being accidentally
    the same color or an invented palette."""
    assert vdc._CONFIDENCE_COLOR["High"] == vdc._SUCCESS
    assert vdc._CONFIDENCE_COLOR["Medium"] == vdc._WARNING
    assert vdc._CONFIDENCE_COLOR["Low"] == vdc._DANGER
    colors = set(vdc._CONFIDENCE_COLOR.values())
    assert len(colors) == 3, "Expected three genuinely distinct confidence colors"


def test_badge_pixel_is_actually_the_expected_color():
    """Not just 'the color constant is right' (the test above) but
    that a pixel actually sampled from the rendered badge area matches
    it — confirms the fill call really used that color, not just that
    the constant exists correctly elsewhere."""
    img = _load(vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High"))
    # Badge fill confirmed by direct pixel scan of a real render at
    # y=385 for this exact input (not a guessed layout coordinate —
    # the first attempt at this test guessed y=405 and was wrong,
    # caught immediately by this same test rather than silently
    # passing on the wrong pixel).
    pixel = img.getpixel((150, 385))
    assert pixel == vdc._SUCCESS, (
        f"Expected the High-confidence badge area to be _SUCCESS, got {pixel}"
    )


def test_never_includes_raw_evidence_quote_text():
    """Privacy guard: observations carry a 'body' field with a verbatim
    excerpt of the person's own writing (see the module docstring's
    privacy section) - this must never end up embedded as literal text
    the way it would if a future edit accidentally drew obs['body']
    instead of obs['headline']. Can't extract raw text from a PNG, but
    can dimension-test surgically: a card built from headline-only
    data must be byte-identical to one built from the same
    observations with a very long, distinctive 'body' value bolted on
    - if body ever leaked into the drawing, the two would diverge."""
    quote_marker = "ZZZ_THIS_MUST_NEVER_APPEAR_IN_THE_IMAGE_ZZZ"
    with_body = [
        {**obs, "body": f'e.g. "{quote_marker} {obs["body"]}"'}
        for obs in _SAMPLE_OBSERVATIONS
    ]
    a = vdc.build_voice_dna_card_png(_SAMPLE_OBSERVATIONS, "High")
    b = vdc.build_voice_dna_card_png(with_body, "High")
    assert a == b, "Changing only 'body' text changed the rendered image — body is leaking into the drawing"


def test_max_traits_caps_how_many_are_drawn():
    many_observations = _SAMPLE_OBSERVATIONS + [
        {"headline": "A fourth trait that should be cut off", "body": ""},
        {"headline": "A fifth trait that should also be cut off", "body": ""},
    ]
    capped_at_3 = _load(vdc.build_voice_dna_card_png(many_observations, "High", max_traits=3))
    capped_at_5 = _load(vdc.build_voice_dna_card_png(many_observations, "High", max_traits=5))
    assert capped_at_5.height > capped_at_3.height
