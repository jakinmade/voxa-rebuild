"""
authenticity_report.py — exportable, tamper-evident proof that a
render's Voice Report is genuinely what VOICOVA measured, not a
number someone typed in afterward.

Why this exists: every AI-detection score on the market today scores
arbitrary text after the fact, with no visible reasoning — and that
pattern is already publicly discredited. A March 2026 case (writer
deBoer, detector Pangram, reported via a Substack/Blaze Media piece)
scored the same 300-word passage as 100% AI-written on its own and
100% human once embedded in the full 5,000-word essay. The take-away
quoted at the time: "an unexplained algorithmic score is not proof of
authorship." VOICOVA's report is structurally different in kind, not
just degree — it's a comparison against THIS person's own baseline,
built from writing collected before any AI touched the text, with the
dimension-level reasoning included, not just a headline number. That
claim only holds if the report itself can't be quietly edited after
export.

integrity_hash proves INTERNAL CONSISTENCY ONLY: that this exact
payload is what was issued, unedited, since export. It is NOT a
cryptographic signature and does NOT prove the report actually came
from VOICOVA (that needs a server-side signing key — explicitly out
of scope for this pass; flagged here rather than silently implied).
verify_authenticity_report() is what checks it.

Privacy: mirrors render_events.py's existing stance exactly — no
device_id, no raw text, nothing that could re-identify the person or
their writing. baseline_hash is a hash of the NUMERIC baseline
fingerprint values only, never the raw baseline text, so a third
party checking a report learns "this render matches a specific voice
baseline" without learning anything about what that baseline says.

Scope note: this module only packages and hashes data already
computed elsewhere (build_voice_report in voice_engine.py,
compute_baseline_metrics for the baseline dict). It adds no new
scoring logic and is never called from inside the render/prompt path
— it runs after a render is complete and confirmed, as a pure
reporting/export step. No LLM call, no change to render behaviour.
"""
import hashlib
import json


def compute_baseline_hash(baseline_fingerprint: dict | None) -> str:
    """Deterministic hash of the baseline's numeric fingerprint values
    only. Same baseline -> same hash, always -> lets two reports be
    checked against each other ("were these both scored against the
    same voice baseline?") without ever exposing the baseline itself.
    Empty/missing baseline returns "" rather than hashing an empty
    dict, so a report built without a real baseline is visibly
    incomplete rather than silently producing a hash that looks valid.
    """
    if not baseline_fingerprint:
        return ""
    canonical = json.dumps(baseline_fingerprint, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_payload_json(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


# Fields carried over from the existing Voice Report verbatim — kept
# as an explicit list (not **voice_report) so a future field added to
# build_voice_report() doesn't silently start appearing in exported
# authenticity reports without a deliberate decision to include it.
_VOICE_REPORT_FIELDS = (
    "voice_match_tier",
    "semantic_match",
    "confidence",
    "risk",
    "ai_tell_clean",
    "biggest_changes",
)


def build_authenticity_report(
    voice_report: dict,
    baseline_fingerprint: dict | None,
    render_id: str,
    created_at: str,
    scoring_rules_version: str,
) -> dict:
    """Assembles the exportable authenticity report.

    voice_report: the dict returned by build_voice_report() for this
    render — already computed, this function only selects and packages
    from it.
    baseline_fingerprint: st.session_state["baseline_fingerprint"] at
    render time — hashed, never included raw.
    render_id: caller-generated (uuid4 string), one per completed
    render, so a report can be uniquely referenced without needing
    the underlying text.
    created_at: ISO 8601 string, caller-supplied so this stays a pure
    function (no datetime.now() call buried in here) and is trivially
    testable.
    """
    payload = {
        "render_id": render_id,
        "created_at": created_at,
        "scoring_rules_version": scoring_rules_version,
        "baseline_hash": compute_baseline_hash(baseline_fingerprint),
    }
    for field in _VOICE_REPORT_FIELDS:
        payload[field] = (voice_report or {}).get(field)

    payload["integrity_hash"] = hashlib.sha256(
        _canonical_payload_json(payload).encode("utf-8")
    ).hexdigest()
    return payload


def verify_authenticity_report(report: dict) -> bool:
    """Recomputes integrity_hash over every field except itself and
    compares. True means the report is internally consistent — it
    hasn't been edited since VOICOVA issued it. False covers both
    tampering and a malformed/foreign report (missing hash, wrong
    shape) — deliberately not distinguished, since either way the
    honest answer is "can't be trusted as issued."

    Does NOT verify the report actually originated from VOICOVA —
    see module docstring for why that's an explicit, stated limit
    rather than an implied guarantee.
    """
    if not isinstance(report, dict) or "integrity_hash" not in report:
        return False
    claimed = report["integrity_hash"]
    payload = {k: v for k, v in report.items() if k != "integrity_hash"}
    recomputed = hashlib.sha256(
        _canonical_payload_json(payload).encode("utf-8")
    ).hexdigest()
    return recomputed == claimed


def export_authenticity_report_json(report: dict) -> str:
    """Pretty-printed JSON for a download button — human-readable, not
    just a hash blob, so anyone receiving this (a client, an editor,
    a platform) can read the actual scores without tooling."""
    return json.dumps(report, indent=2, sort_keys=True, default=str)


def export_authenticity_report_text(report: dict) -> str:
    """Formatted plain-text version, alongside the JSON export: the
    JSON exports are developer-facing — accurate, but not something a
    manager, client, or editor wants to open cold. Plain text over PDF
    deliberately — the actual use case ("show someone this render is
    genuine") is served by clean text pasteable into an email or Slack
    message; PDF generation would add a new dependency and layout work
    for marginal benefit over that. Field labels and order mirror the
    JSON export's _VOICE_REPORT_FIELDS plus the integrity fields, so
    the two exports never disagree about what they're reporting.

    UPDATE, 27 Aug 2026: the "marginal benefit" framing above was
    right for the free-tier consumer product this was built for, but
    doesn't hold for the agency/B2B pivot under consideration (see
    product history) — a ghostwriting or comms agency handing a
    client "here's the proof this was checked" needs something that
    reads as a deliverable, not a debug artifact. See
    export_authenticity_report_pdf below, added for that case
    specifically. This function and its two siblings above are
    unchanged; the PDF is a fourth option, not a replacement.
    """
    lines = [
        "VOICOVA: Authenticity Report",
        "=" * 32,
        "",
        f"Render ID:              {report.get('render_id', 'n/a')}",
        f"Created:                {report.get('created_at', 'n/a')}",
        "",
        f"Voice match:            {report.get('voice_match_tier', 'n/a')}",
        f"Semantic match:         {report.get('semantic_match', 'n/a')}",
        f"Confidence:             {report.get('confidence', 'n/a')}",
        f"Risk:                   {report.get('risk', 'n/a')}",
        f"AI-tell check:          {'Clean' if report.get('ai_tell_clean') else 'Flagged'}",
        "",
    ]
    biggest_changes = report.get("biggest_changes") or []
    if biggest_changes:
        lines.append("What changed:")
        for change in biggest_changes:
            lines.append(f"  - {change}")
        lines.append("")
    lines.extend([
        f"Scoring rules version:  {report.get('scoring_rules_version', 'n/a')}",
        f"Baseline hash:          {report.get('baseline_hash', 'n/a')}",
        f"Integrity hash:         {report.get('integrity_hash', 'n/a')}",
        "",
        "This report compares the render against this person's own",
        "writing baseline, built before any AI touched the text. Not",
        "a bare AI-detection score. The integrity hash confirms this",
        "exact report hasn't been edited since VOICOVA issued it; it",
        "does not independently prove the report came from VOICOVA",
        "(that needs server-side signing, out of scope for this report).",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------
# Branded PDF export (27 Aug 2026) — see the note on
# export_authenticity_report_text above for why this exists alongside
# it rather than replacing it. Colors are VOICOVA's actual design
# tokens (app.py's :root CSS block — ink/garnet/gold), not
# approximations, so a PDF next to a live screenshot reads as the
# same product. Fraunces/Inter (the app's actual typefaces) aren't
# available as PDF-embeddable fonts in this environment without
# bundling TTF files, which is a real gap worth closing later if this
# gets real usage — Helvetica/Helvetica-Bold stand in for now, chosen
# over Times for a cleaner, more editorial-adjacent look consistent
# with the product's actual aesthetic direction rather than a generic
# report-template serif.
# ---------------------------------------------------------------------

_PDF_INK = "#1C1B29"
_PDF_ACCENT = "#7A2632"
_PDF_GOLD = "#B08947"
_PDF_MUTED = "#7A7488"
_PDF_SUCCESS = "#3F6B3F"
_PDF_WARNING = "#96631E"
_PDF_DANGER = "#AE4530"
_PDF_BORDER = "#E4DBCC"
_PDF_SURFACE = "#F3EEE6"

# Per-field color maps, NOT one shared High/Medium/Low dict — the polarity
# genuinely differs by field and a shared map gets it backwards for two
# of these. Matches the live app's own badge classes exactly (app.py:
# Risk's badge_class dict, _MY_VOICE_CONFIDENCE_BADGE; voice_engine.py:
# voice_match_label's tier/badge pairs) rather than being independently
# invented here — confirmed against source, not assumed:
#   Risk:         Low=good(green)    High=bad(red)     — badge_class, app.py
#   Confidence:   Low=bad(red)       High=good(green)  — INVERTED from Risk,
#                 _MY_VOICE_CONFIDENCE_BADGE, app.py
#   Voice match:  tier is never "High/Medium/Low" at all — it's
#                 Strong/Good/Developing/Limited, voice_match_label(),
#                 voice_engine.py. A "High" test value here previously
#                 silently fell through to badge-red as an unmatched key,
#                 which happened to look plausible but was never a real
#                 value this field can take in production.
#   AI-tell:      Clean=good(green)  Flagged=bad(red)
_PDF_RISK_COLOR = {"Low": _PDF_SUCCESS, "Medium": _PDF_WARNING, "High": _PDF_DANGER}
_PDF_CONFIDENCE_COLOR = {"Low": _PDF_DANGER, "Medium": _PDF_WARNING, "High": _PDF_SUCCESS}
_PDF_VOICE_MATCH_COLOR = {
    "Strong": _PDF_SUCCESS, "Good": _PDF_SUCCESS,
    "Developing": _PDF_WARNING, "Limited": _PDF_DANGER,
}
_PDF_AI_TELL_COLOR = {"Clean": _PDF_SUCCESS, "Flagged": _PDF_DANGER}


def export_authenticity_report_pdf(report: dict) -> bytes:
    """Branded one-page PDF version of the same authenticity_report
    dict the JSON/text exports already serialise — no new scoring
    logic, no new data, purely a presentation layer for the agency/
    client-deliverable use case (see the note on
    export_authenticity_report_text). Returns raw PDF bytes, meant
    for a Streamlit st.download_button's data= argument directly,
    same calling convention as the other two export functions.
    """
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=22 * mm, bottomMargin=18 * mm,
        leftMargin=20 * mm, rightMargin=20 * mm,
        title="Voicova Authenticity Report",
    )

    styles = getSampleStyleSheet()
    tagline = ParagraphStyle(
        "Tagline", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=9, textColor=HexColor(_PDF_ACCENT), tracking=1,
        spaceAfter=2,
    )
    headline = ParagraphStyle(
        "Headline", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=22, textColor=HexColor(_PDF_INK), leading=26,
        spaceAfter=4, alignment=TA_LEFT,
    )
    sub = ParagraphStyle(
        "Sub", parent=styles["Normal"], fontName="Helvetica",
        fontSize=10, textColor=HexColor(_PDF_MUTED), leading=14,
        spaceAfter=14,
    )
    label_style = ParagraphStyle(
        "MetricLabel", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, textColor=HexColor(_PDF_MUTED),
    )
    section_head = ParagraphStyle(
        "SectionHead", parent=styles["Normal"], fontName="Helvetica-Bold",
        fontSize=9, textColor=HexColor(_PDF_MUTED), spaceBefore=16,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "Body", parent=styles["Normal"], fontName="Helvetica",
        fontSize=9.5, textColor=HexColor(_PDF_INK), leading=14,
    )
    mono_small = ParagraphStyle(
        "MonoSmall", parent=styles["Normal"], fontName="Courier",
        fontSize=7.5, textColor=HexColor(_PDF_MUTED), leading=11,
    )
    footer_style = ParagraphStyle(
        "Footer", parent=styles["Normal"], fontName="Helvetica",
        fontSize=8, textColor=HexColor(_PDF_MUTED), leading=12,
    )

    def _metric_value_style(color_hex):
        return ParagraphStyle(
            "MetricValue", parent=styles["Normal"], fontName="Helvetica-Bold",
            fontSize=15, textColor=HexColor(color_hex),
        )

    story = []
    story.append(Paragraph("VOICOVA", tagline))
    story.append(Paragraph("Authenticity Report", headline))
    story.append(Paragraph(
        "A comparison against this person's own writing baseline, built "
        "before any AI touched the text \u2014 not a bare AI-detection score.",
        sub,
    ))
    story.append(HRFlowable(width="100%", thickness=0.75, color=HexColor(_PDF_BORDER)))
    story.append(Spacer(1, 14))

    voice_match = report.get("voice_match_tier", "n/a")
    semantic_match = report.get("semantic_match", "n/a")
    confidence = report.get("confidence", "n/a")
    risk = report.get("risk", "n/a")
    ai_tell = "Clean" if report.get("ai_tell_clean") else "Flagged"

    metrics = [
        ("VOICE MATCH", str(voice_match), _PDF_VOICE_MATCH_COLOR.get(str(voice_match), _PDF_INK)),
        ("SEMANTIC MATCH", str(semantic_match), _PDF_INK),
        ("CONFIDENCE", str(confidence), _PDF_CONFIDENCE_COLOR.get(str(confidence), _PDF_INK)),
        ("RISK", str(risk), _PDF_RISK_COLOR.get(str(risk), _PDF_INK)),
        ("AI-TELL CHECK", ai_tell, _PDF_AI_TELL_COLOR.get(ai_tell, _PDF_INK)),
    ]
    cell_w = (doc.width) / len(metrics)
    label_row = [Paragraph(label, label_style) for label, _, _ in metrics]
    value_row = [Paragraph(value, _metric_value_style(color)) for _, value, color in metrics]
    metrics_table = Table([label_row, value_row], colWidths=[cell_w] * len(metrics))
    metrics_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor(_PDF_SURFACE)),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
        ("TOPPADDING", (0, 1), (-1, 1), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("LINEABOVE", (0, 0), (-1, 0), 3, HexColor(_PDF_ACCENT)),
    ]))
    story.append(metrics_table)

    biggest_changes = report.get("biggest_changes") or []
    if biggest_changes:
        story.append(Paragraph("WHAT CHANGED", section_head))
        for change in biggest_changes:
            story.append(Paragraph(f"&bull;&nbsp;&nbsp;{change}", body))
            story.append(Spacer(1, 3))

    story.append(Paragraph("RENDER", section_head))
    render_rows = [
        ["Render ID", str(report.get("render_id", "n/a"))],
        ["Created", str(report.get("created_at", "n/a"))],
        ["Scoring rules version", str(report.get("scoring_rules_version", "n/a"))],
    ]
    render_table = Table(render_rows, colWidths=[45 * mm, doc.width - 45 * mm])
    render_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
        ("FONTNAME", (1, 0), (1, -1), "Courier"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("TEXTCOLOR", (0, 0), (0, -1), HexColor(_PDF_MUTED)),
        ("TEXTCOLOR", (1, 0), (1, -1), HexColor(_PDF_INK)),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(render_table)

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor(_PDF_BORDER)))
    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "This report compares the render against this person's own writing "
        "baseline, built before any AI touched the text \u2014 not a bare "
        "AI-detection score. The integrity hash below confirms this exact "
        "report hasn't been edited since Voicova issued it; it does not "
        "independently prove the report came from Voicova, which needs "
        "server-side signing, out of scope for this report.",
        footer_style,
    ))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"Baseline hash: {report.get('baseline_hash', 'n/a')}<br/>"
        f"Integrity hash: {report.get('integrity_hash', 'n/a')}",
        mono_small,
    ))

    doc.build(story)
    return buf.getvalue()
