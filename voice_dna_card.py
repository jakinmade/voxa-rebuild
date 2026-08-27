"""
voice_dna_card.py — a shareable, brand-styled image summarising someone's
Voice DNA (their top fingerprint traits + confidence), meant to be posted
somewhere like LinkedIn (27 Aug 2026, low-effort/high-reach feature: reuses
data VOICOVA already computes at calibration time, nothing new to score).

Deliberately reuses observations (voice_engine.py's analyse_writing output)
and confidence exactly as screen_my_voice() already displays them — no new
detection, no new scoring, a presentation layer only, same principle as
authenticity_report.py's PDF export.

PRIVACY, matching authenticity_report.py's own stated stance exactly: an
observation's headline ("You write in a formal register") is a short,
qualitative, VOICOVA-generated description — safe to share publicly. Its
evidence quote (the verbatim snippet from the person's own writing that the
headline was drawn from) is NOT included here, deliberately, the same way
authenticity_report.py hashes the baseline rather than exposing it raw. A
person sharing this card is sharing what VOICOVA concluded about their
writing, never a quoted fragment of the writing itself.

Fonts are bundled in assets/fonts/ (DejaVu — Bitstream Vera License, freely
redistributable) rather than relying on the deploy environment happening to
have them installed system-wide. DejaVu Serif/Sans/Mono stand in for
Fraunces/Inter/IBM Plex Mono (the app's actual typefaces, not
PNG-embeddable without bundling those specific TTFs, a closed-license
Google Fonts distribution — worth revisiting if this feature gets real
usage, same caveat as authenticity_report.py's PDF export).
"""
import os
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

_FONT_DIR = os.path.join(os.path.dirname(__file__), "assets", "fonts")

_INK = (28, 27, 41)
_ACCENT = (122, 38, 50)
_GOLD = (176, 137, 71)
_MUTED = (122, 116, 136)
_CANVAS = (251, 249, 246)
_SURFACE = (243, 238, 230)
_BORDER = (228, 219, 204)
_SUCCESS = (63, 107, 63)
_WARNING = (150, 99, 30)
_DANGER = (174, 69, 48)

_CONFIDENCE_COLOR = {"High": _SUCCESS, "Medium": _WARNING, "Low": _DANGER}

_CARD_W = 1200
_MAX_CARD_H = 2200  # generous scratch canvas; real output is cropped to fit


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Greedy word-wrap against actual rendered text width, not a
    fixed character-count guess — different words/fonts render at
    different widths, a char-count wrap either overflows or wastes
    space depending on the text."""
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def build_voice_dna_card_png(
    observations: list[dict],
    confidence: str | None,
    max_traits: int = 3,
) -> bytes:
    """Renders the shareable card. observations: the same list
    screen_my_voice() already reads from session_state (already
    sorted by signal strength — this takes the top max_traits as-is,
    no re-sorting). confidence: "High" | "Medium" | "Low" | None.
    Returns PNG bytes, meant for a Streamlit st.download_button's
    data= argument directly, same convention as
    authenticity_report.py's export functions.

    Height is content-driven, not fixed: 1 trait and 3 wrapped-onto-
    two-lines traits are very different heights, and a fixed canvas
    either clips long content or leaves a large dead gap before the
    footer for short content — confirmed visually before this was
    fixed (a 3-short-trait card on a fixed 1500px canvas left roughly
    640px of empty space above the footer). Draws onto a generously
    tall scratch canvas first, tracks the actual y position after the
    last trait card, then composes the real output at that height
    plus a fixed footer block, so proportions stay consistent
    regardless of how many traits or how much their headlines wrap.
    """
    scratch = Image.new("RGB", (_CARD_W, _MAX_CARD_H), _CANVAS)
    draw = ImageDraw.Draw(scratch)
    margin = 90

    # Top accent bar — same visual signature as the app's own
    # .voice-report::before rule (app.py CSS) and the PDF export's
    # LINEABOVE rule: a 3px garnet line is this product's consistent
    # "this is a Voicova artifact" mark across every surface.
    draw.rectangle([0, 0, _CARD_W, 10], fill=_ACCENT)

    y = 110
    tagline_font = _font("DejaVuSansMono-Bold.ttf", 26)
    draw.text((margin, y), "VOICOVA", font=tagline_font, fill=_ACCENT)
    y += 60

    headline_font = _font("DejaVuSerif-Bold.ttf", 64)
    draw.text((margin, y), "My Voice DNA", font=headline_font, fill=_INK)
    y += 100

    sub_font = _font("DejaVuSans.ttf", 30)
    sub_lines = _wrap_text(
        draw,
        "What Voicova has learned about how I actually write \u2014 "
        "measured against my own baseline, not a generic style quiz.",
        sub_font, _CARD_W - 2 * margin,
    )
    for line in sub_lines:
        draw.text((margin, y), line, font=sub_font, fill=_MUTED)
        y += 42
    y += 30

    # Confidence badge — a filled pill, same colour convention as
    # _MY_VOICE_CONFIDENCE_BADGE in app.py (High=green, Medium=amber,
    # Low=red), not re-derived independently.
    if confidence:
        badge_font = _font("DejaVuSansMono-Bold.ttf", 24)
        badge_color = _CONFIDENCE_COLOR.get(confidence, _WARNING)
        label = f"CONFIDENCE: {confidence.upper()}"
        text_w = draw.textlength(label, font=badge_font)
        pad_x, pad_y = 28, 16
        pill_w, pill_h = text_w + pad_x * 2, 30 + pad_y * 2
        draw.rounded_rectangle(
            [margin, y, margin + pill_w, y + pill_h],
            radius=pill_h // 2, fill=badge_color,
        )
        draw.text(
            (margin + pad_x, y + pad_y - 2), label,
            font=badge_font, fill=_CANVAS,
        )
        y += pill_h + 50
    else:
        y += 20

    # Trait cards — same visual language as .voice-check in the live
    # app (a checkmark medallion + headline), reused here rather than
    # inventing a new treatment, so a screenshot of the real app and
    # this shareable card read as the same product.
    trait_font = _font("DejaVuSerif-Bold.ttf", 34)
    mark_font = _font("DejaVuSans-Bold.ttf", 28)
    traits = (observations or [])[:max_traits]
    card_w = _CARD_W - 2 * margin
    for obs in traits:
        headline = obs.get("headline", "")
        lines = _wrap_text(draw, headline, trait_font, card_w - 110)
        line_h = 46
        card_h = 40 + len(lines) * line_h + 30

        draw.rounded_rectangle(
            [margin, y, margin + card_w, y + card_h],
            radius=14, fill=_SURFACE, outline=_BORDER, width=2,
        )
        mark_cx, mark_cy = margin + 60, y + card_h // 2
        draw.ellipse(
            [mark_cx - 26, mark_cy - 26, mark_cx + 26, mark_cy + 26],
            fill=(231, 239, 222),
        )
        draw.text(
            (mark_cx - 11, mark_cy - 18), "\u2713",
            font=mark_font, fill=_SUCCESS,
        )
        text_y = y + 35
        for line in lines:
            draw.text((margin + 110, text_y), line, font=trait_font, fill=_INK)
            text_y += line_h

        y += card_h + 26

    # Content ends here (y). Compose the real, correctly-sized output:
    # crop the scratch canvas down to the actual content, then append
    # a fixed-height footer block — never a dead gap, never clipped.
    content_bottom = y + 20
    footer_block_h = 150
    final_h = content_bottom + footer_block_h
    final_img = scratch.crop((0, 0, _CARD_W, final_h))
    final_draw = ImageDraw.Draw(final_img)

    footer_y = content_bottom + 20
    final_draw.line(
        [(margin, footer_y), (_CARD_W - margin, footer_y)],
        fill=_BORDER, width=2,
    )
    footer_font = _font("DejaVuSansMono.ttf", 24)
    final_draw.text(
        (margin, footer_y + 30), "voicova.com",
        font=footer_font, fill=_MUTED,
    )
    footer_note_font = _font("DejaVuSans.ttf", 22)
    final_draw.text(
        (margin, footer_y + 68),
        "AI-generated text, rewritten to sound like you \u2014 not the other way round.",
        font=footer_note_font, fill=_MUTED,
    )

    buf = BytesIO()
    final_img.save(buf, format="PNG")
    return buf.getvalue()
