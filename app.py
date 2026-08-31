"""
Voicova — Communication Identity Platform
Streamlit App

"Voicova preserves who you are when you write."

Flow (per v4 frozen spec):
  Screen 1 — Paste your writing (no account, no friction)
  Screen 2 — Fingerprint reveal, as a checklist ("Your Voice")
  Screen 3 — Two required, register-contrasting sentence starters
             (plus two optional), typed live, paste blocked
  Screen 4 — Paste AI text, rewrite it in your voice, get the Voice Report,
             one opportunity to refine

Architecture: app.py is UI and routing only. voice_engine.py measures.
prompts.py builds prompt strings and cleans text. storage.py holds
session state. No module here reaches into packages/ — this rebuild is
fully self-contained.
"""

import html
import os
import re
import uuid
from datetime import datetime, timezone
import streamlit as st

from scoring_rules import scoring_rules_version
from render_events import log_render_event
from render_cap import check_and_reserve_render
from lifetime_cap import (
    check_and_reserve_lifetime_render,
    release_reserved_lifetime_render,
)
from render_history import write_render_history, get_render_history
from review_gate import requires_review, log_review_confirmation
from firm_signal import extract_domain, log_firm_signal
from storage import init_state, go_to, reset_all, generate_receipt, export_profile
from authenticity_report import build_authenticity_report, export_authenticity_report_json, export_authenticity_report_text, export_authenticity_report_pdf
from voice_dna_card import build_voice_dna_card_png
from voice_engine import (
    analyse_writing, _analyse_intro,
    compute_baseline_metrics, _merge_baseline,
    _score_sample_fitness, _fitness_gate,
    _score_ai_signal,
    score_semantic_drift, find_source_sentence, splice_dropped_sentence, highlight_flagged_phrases, compute_confidence, compute_risk, compute_risk_reason,
    has_content_integrity_hard_fail,
    score_render_delta, build_voice_report,
    uses_contractions, score_ai_tells, score_restructure_fidelity,
    compute_dimension_stability, compute_dimension_confidence, confidence_caveat,
    score_correction_evidence,
    compute_burrows_delta,
    compute_sentence_economy, compute_passive_voice,
    score_draft_check,
)
from prompts import (
    _build_voice_dna, _build_system_prompt,
    _detect_mode, apply_intent_mode, _detect_locale,
    _apply_uk_english, _regex_sweep, _grammar_fix_pass,
    build_correction_prompt, merge_starter_evidence,
    build_voice_profile_summary_prompt,
    CORRECTION_TOOL, response_looks_contaminated,
)
from components.paste_guard import paste_guard
from deterministic_fixers import (
    _fix_hedge_density, _fix_sentence_length_sd,
    _fix_first_person_ratio, _fix_first_person_over_ratio,
    _fix_directive_ratio, _fix_modal_hedge,
    _check_uncorrected_insertions, _fix_entity_casing,
    ownership_miss_is_content_driven, restore_fabricated_ownership_sentences,
)
from logging_config import get_logger
from persistence import restore_profile_if_available, save_profile_if_available, get_or_create_device_id, set_device_id_cookie
from stripe_subscription import (
    create_subscription_checkout,
    verify_and_record_subscription,
    request_subscription_restore,
    confirm_subscription_restore,
    create_billing_portal_session,
)
from lifetime_cap import get_lifetime_render_count, device_has_active_subscription

log = get_logger(__name__)

# ---- Page config — must be first ----
# Sidebar starts expanded for a known-returning user (the flag is set,
# once, the first time restore_profile_if_available() succeeds in this
# browser session — see the rerun immediately after that call below)
# and collapsed for a fresh/new visitor, matching the same
# onboarding-vs-account-holder split Notion and Gmail use for their own
# nav. On the very first script execution of a genuinely new tab we
# don't yet know which case this is — the device cookie hasn't
# round-tripped through its component yet — so this defaults to
# collapsed until that's resolved, same fail-open shape as the rest of
# persistence.py.
# ---- Page config — must be first ----
# Sidebar starts expanded for a known-returning user (the flag is set,
# once, the first time restore_profile_if_available() succeeds in this
# browser session — see the rerun immediately after that call below)
# and collapsed for a fresh/new visitor, matching the same
# onboarding-vs-account-holder split Notion and Gmail use for their own
# nav. On the very first script execution of a genuinely new tab we
# don't yet know which case this is — the device cookie hasn't
# round-tripped through its component yet — so this defaults to
# collapsed until that's resolved, same fail-open shape as the rest of
# persistence.py.
#
# Layout (27 Aug 2026, UI-quality pass): wide only for screen 4 (the
# write/render screen) — the one screen that's a working editor, not
# a linear onboarding step. Benchmarked against Grammarly/Wordtune/
# Sudowrite/Jasper, all of which put the editor and its output/
# analysis side by side rather than stacked in a narrow column; every
# other screen here (landing, paste, calibration, pricing) is
# correctly a centered intake flow and stays that way. Safe to read
# st.session_state.screen this early: init_state() (below) only sets
# the *default* of 0 the first time a brand-new session runs this
# script; on every later rerun within that session — including the
# one triggered by go_to(4) — session_state.screen already holds its
# real value by the time this line executes, since Streamlit persists
# session_state across reruns and re-executes the whole script from
# the top on each one.
_current_screen = st.session_state.get("screen", 0)
st.set_page_config(
    page_title="Voicova - Communication Identity",
    page_icon="\U0001F535",
    layout="wide" if _current_screen == 4 else "centered",
    initial_sidebar_state="expanded" if (
        st.session_state.get("_returning_user_sidebar")
        or st.session_state.get("_sidebar_unlocked")
    ) else "collapsed",
)

# ---- SEO / social meta ----
# Streamlit's static HTML shell always ships <title>Streamlit</title> with
# no meta description or OG tags; the real title only lands client-side
# after JS runs, and there's no description/OG tag at all otherwise. This
# is the single biggest thing standing between voicova.com and being
# indexed or looking right when shared/linked. Injected once per session
# via a component that reaches into the parent document's <head>.
if not st.session_state.get("_seo_meta_injected"):
    st.session_state["_seo_meta_injected"] = True
    st.components.v1.html(
        """
        <script>
        (function() {
            const doc = window.parent.document;
            const setMeta = (attr, key, content) => {
                let tag = doc.querySelector(`meta[${attr}="${key}"]`);
                if (!tag) {
                    tag = doc.createElement("meta");
                    tag.setAttribute(attr, key);
                    doc.head.appendChild(tag);
                }
                tag.setAttribute("content", content);
            };
            doc.title = "Voicova - Communication Identity";
            setMeta("name", "description",
                "Voicova preserves who you are when you write. Test any draft against your own voice fingerprint and fix what doesn't sound like you.");
            setMeta("property", "og:title", "Voicova - Communication Identity");
            setMeta("property", "og:description",
                "Voicova preserves who you are when you write.");
            setMeta("property", "og:type", "website");
            setMeta("property", "og:url", "https://voicova.com");
            setMeta("name", "twitter:card", "summary");
            let canon = doc.querySelector('link[rel="canonical"]');
            if (!canon) {
                canon = doc.createElement("link");
                canon.setAttribute("rel", "canonical");
                doc.head.appendChild(canon);
            }
            canon.setAttribute("href", "https://voicova.com");
        })();
        </script>
        """,
        height=0,
    )

# ---- Styles ----
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* ---------------------------------------------------------------
       Design tokens. One place to look, same discipline as
       scoring_rules.py — change a value here, not at each call site.
       Kept in sync with .streamlit/config.toml's [theme] block, which
       covers native widgets (buttons, checkboxes, focus rings) CSS
       alone can't reliably reach across Streamlit versions.

       Palette: ink/garnet/brass, grounded in the product's actual
       subject (authenticating genuine writing against a fingerprint,
       closer to forensic authentication than a generic SaaS
       dashboard). Serif display face used only for headlines, kept
       restrained elsewhere.
       --------------------------------------------------------------- */
    :root {
        --ink: #1C1B29;
        --body-text: #4A4658;
        --muted: #7A7488;
        --faint: #A79FB0;
        --canvas: #FBF9F6;
        --surface: #F3EEE6;
        --border: #E4DBCC;
        --accent: #7A2632;
        --accent-hover: #5E1D26;
        --accent-soft: #F4E1DE;
        --gold: #B08947;
        --gold-soft: #F3E9D6;
        --success: #3F6B3F;
        --success-soft: #E7EFDE;
        --warning: #96631E;
        --warning-soft: #F6EAD5;
        --danger: #AE4530;
        --danger-soft: #FAE5DC;
        --radius-sm: 6px;
        --radius-md: 10px;
        --radius-lg: 14px;
        /* Spacing scale, added 26 Aug 2026 UI-quality pass. Same
           rationale as the color tokens above: 41 separate inline
           style="margin-top:0.7rem" / "0.8rem" / "1.1rem" etc. calls
           were scattered across this file with no shared scale behind
           them, which is exactly the kind of drift a documented token
           system elsewhere in this file doesn't protect against — the
           colors were disciplined, the spacing wasn't. An 8px-based
           scale (4/8/12/16/24/32px) is the same convention Linear and
           Notion both use; not adopted here as a redesign, just made
           available so future spacing decisions pick a step on this
           scale instead of inventing a new rem value each time. Not
           yet back-filled onto every one of the 41 existing call
           sites in one pass — that's a real follow-up, deliberately
           scoped out here to keep this change low-risk and reviewable
           (see the UI/UX session notes for 26 Aug 2026 for the full
           list of sites still on ad-hoc values). */
        --space-1: 0.25rem;
        --space-2: 0.5rem;
        --space-3: 0.75rem;
        --space-4: 1rem;
        --space-5: 1.5rem;
        --space-6: 2rem;
        --font-display: 'Fraunces', Georgia, 'Times New Roman', serif;
        --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        --font-mono: 'IBM Plex Mono', 'SFMono-Regular', Menlo, Consolas, monospace;
    }

    html, body, [class*="css"] {
        font-family: var(--font-sans);
        color: var(--body-text);
    }

    .stApp {
        background: var(--canvas);
    }

    .block-container {
        max-width: 700px;
        padding-top: 3.5rem;
        padding-bottom: 4rem;
    }

    @media (max-width: 640px) {
        .block-container {
            padding-top: 2rem;
            padding-left: 1.25rem;
            padding-right: 1.25rem;
        }
    }

    /* Respect reduced-motion preferences — every transition below is
       decorative, not load-bearing, so it's safe to disable wholesale. */
    @media (prefers-reduced-motion: reduce) {
        * { transition: none !important; animation: none !important; }
    }

    /* Visible keyboard focus everywhere, not just the elements below
       that already get a custom ring — accessibility floor, not a
       per-component decision. */
    :focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }

    .tagline {
        font-family: var(--font-mono);
        font-size: 0.75rem;
        font-weight: 600;
        color: var(--accent);
        letter-spacing: 0.14em;
        text-transform: uppercase;
        margin-bottom: 0.9rem;
    }

    .headline {
        font-family: var(--font-display);
        font-optical-sizing: auto;
        font-size: 2.3rem;
        font-weight: 600;
        color: var(--ink);
        line-height: 1.12;
        letter-spacing: -0.01em;
        margin-bottom: 0.5rem;
    }

    .sub {
        font-size: 1.05rem;
        color: var(--muted);
        line-height: 1.55;
        margin-bottom: 2.25rem;
        max-width: 54ch;
    }

    /* "Your Voice" trait cards (shared by screen_reveal / Screen 2 and
       screen_my_voice / Screen 5 - same classes, one place to change
       both). Redesigned from a flat checklist into Grammarly-style
       insight cards per the 25 Aug 2026 UX audit: this is the
       product's payoff/reveal moment and its clearest differentiator
       (evidence-grounded traits, not a generic quiz output), so the
       trait name now carries real heading weight and the quoted
       evidence is visually subordinate to it, each in its own
       card rather than a plain bordered list row. CSS-only staggered
       fade/rise, animation-delay set per-card in Python. Respects the
       prefers-reduced-motion rule above (degrades to a static list). */
    @keyframes voice-check-in {
        from { opacity: 0; transform: translateY(8px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    .voice-check {
        display: flex;
        align-items: flex-start;
        gap: 0.85rem;
        padding: 1rem 1.1rem;
        margin-bottom: 0.65rem;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        opacity: 0;
        animation: voice-check-in 0.5s ease-out forwards;
    }
    .voice-check-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.6rem;
        height: 1.6rem;
        flex-shrink: 0;
        margin-top: 0.15rem;
        border-radius: 50%;
        background: var(--success-soft);
        color: var(--success);
        font-weight: 700;
        font-size: 0.85rem;
        line-height: 1;
    }
    .voice-check-text {
        font-family: var(--font-display);
        font-size: 1.12rem;
        font-weight: 600;
        color: var(--ink);
        line-height: 1.32;
        letter-spacing: -0.005em;
        /* Same fix as .callout-text below: sibling to a flex-shrink:0
           icon with no flex protection of its own = mid-word breaks
           under Streamlit's global CSS once squeezed. */
        flex: 1;
        min-width: 0;
        overflow-wrap: normal;
        word-break: normal;
    }
    .voice-check-evidence {
        font-size: 0.85rem;
        color: var(--muted);
        font-style: italic;
        margin-top: 0.45rem;
        padding-left: 0.7rem;
        border-left: 2px solid var(--gold-soft);
        line-height: 1.55;
    }

    /* Verdict headline - 31 Aug 2026 revamp. Leads results with a
       decision (PASS / REVIEW REQUIRED) rather than a metrics grid;
       the existing badges/detail rows render underneath as supporting
       evidence, unchanged. Reuses the existing success/warning tokens
       so it matches the rest of the design system exactly. */
    .verdict-banner {
        display: flex;
        align-items: center;
        gap: 0.9rem;
        padding: 1.15rem 1.3rem;
        margin-bottom: 1.1rem;
        border-radius: var(--radius-lg);
        opacity: 0;
        animation: voice-check-in 0.5s ease-out forwards;
    }
    .verdict-banner.pass {
        background: var(--success-soft);
        border: 1px solid var(--success);
    }
    .verdict-banner.review {
        background: var(--warning-soft);
        border: 1px solid var(--warning);
    }
    .verdict-banner-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 2.4rem;
        height: 2.4rem;
        flex-shrink: 0;
        border-radius: 50%;
        font-weight: 700;
        font-size: 1.2rem;
        line-height: 1;
    }
    .verdict-banner.pass .verdict-banner-mark {
        background: var(--success); color: var(--success-soft);
    }
    .verdict-banner.review .verdict-banner-mark {
        background: var(--warning); color: var(--warning-soft);
    }
    .verdict-banner-title {
        font-family: var(--font-display);
        font-size: 1.4rem;
        font-weight: 700;
        letter-spacing: -0.01em;
        line-height: 1.1;
    }
    .verdict-banner.pass .verdict-banner-title { color: var(--success); }
    .verdict-banner.review .verdict-banner-title { color: var(--warning); }
    .verdict-banner-sub {
        font-size: 0.9rem;
        color: var(--muted);
        margin-top: 0.15rem;
    }

    .mode-label {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        color: var(--accent);
        margin-bottom: 0.6rem;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
    }

    .render-box {
        background: var(--canvas);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        padding: 1.75rem;
        font-size: 0.97rem;
        line-height: 1.75;
        color: var(--ink);
        white-space: pre-wrap;
        box-shadow: 0 1px 2px rgba(28, 27, 41, 0.05), 0 4px 16px rgba(28, 27, 41, 0.05);
    }

    .receipt {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius-md);
        padding: 1.3rem 1.4rem;
        font-size: 0.85rem;
        color: var(--body-text);
        line-height: 1.7;
    }
    .receipt-title {
        font-weight: 700;
        font-size: 0.9rem;
        color: var(--ink);
        margin-bottom: 0.6rem;
    }
    .receipt strong {
        color: var(--ink);
        font-weight: 600;
    }

    .microcopy {
        font-size: 0.8rem;
        color: var(--faint);
        text-align: center;
        margin-top: 0.5rem;
    }

    /* 31 Aug 2026 polish pass: the landing page's "How it works"
       steps were reusing .microcopy — a class built for throwaway
       fine-print (e.g. the firm-signal disclaimer) — for the page's
       actual explainer content. Same font-size/color as a legal
       caption meant that content read as an afterthought. This gives
       it real hierarchy: a numbered mark that echoes the report's
       instrument-panel language, a proper headline weight for the
       step name, body-text (not faint) for the description. */
    .step-card {
        text-align: center;
        padding: 0 0.4rem;
    }
    .step-card .step-num {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.6rem;
        height: 1.6rem;
        margin-bottom: 0.5rem;
        border-radius: 50%;
        background: var(--accent-soft);
        color: var(--accent);
        font-family: var(--font-mono);
        font-size: 0.78rem;
        font-weight: 700;
    }
    .step-card .step-label {
        display: block;
        font-family: var(--font-display);
        font-size: 1rem;
        font-weight: 600;
        color: var(--ink);
        margin-bottom: 0.25rem;
    }
    .step-card .step-desc {
        font-size: 0.85rem;
        color: var(--body-text);
        line-height: 1.5;
    }

    .progress {
        display: flex;
        align-items: flex-end;
        justify-content: center;
        gap: 5px;
        margin-bottom: 2.5rem;
    }
    .progress .bar {
        display: inline-block;
        width: 3px;
        border-radius: 2px;
        background: var(--border);
        transition: background-color 0.2s ease, transform 0.2s ease;
    }
    .progress .bar.active {
        background: var(--accent);
        transform: scaleY(1.15);
    }

    /* Render-output seam (26 Aug 2026, UI-quality pass): the moment
       output appears after "Write as me" was styled identically to
       starting a fresh screen — full .divider hr (2.25rem margin) +
       full .headline (same 2.3rem serif used at the very top of the
       flow, e.g. "Paste the text to restore."). That reads as "new
       page", not "here's what came from what you just wrote" - the
       single most important transition in the product given the
       Lex/Grammarly benchmarking (26 Aug 2026 session): those
       products keep input and output in one continuous surface,
       never restart it. Full layout parity with a real canvas isn't
       possible in Streamlit (see that session's notes), but this
       specific seam is a safe, scoped fix within reach tonight -
       a lighter continuation marker instead of a second page-start. */
    .render-output-seam {
        margin-top: 1.75rem;
        padding-top: 1.1rem;
        border-top: 1px solid var(--border);
    }
    .render-output-seam .tagline {
        margin-bottom: 0.4rem;
    }

    .divider {
        border: none;
        border-top: 1px solid var(--border);
        margin: 2.25rem 0;
    }

    /* Voice Report — the differentiated output, new in v3/v4. Numerals
       in monospace throughout: this product's whole pitch is
       deterministic measurement, not model vibes, so the report reads
       like an instrument panel, not a chat bubble. */
    .voice-report {
        position: relative;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        padding: 1.4rem 1.5rem 1.2rem;
        margin-bottom: 1.4rem;
        overflow: hidden;
    }
    .voice-report::before {
        content: "";
        position: absolute;
        top: 0; left: 0; right: 0;
        height: 3px;
        background: var(--accent);
    }
    .vr-grid {
        display: flex;
        gap: 1.6rem;
        flex-wrap: wrap;
        margin-bottom: 1rem;
    }
    /* flex-shrink: 0 (29 Aug 2026 fix, confirmed live): without it,
       a narrow viewport could squeeze a .vr-stat block below the
       width its own badge value needed, and with no white-space:
       nowrap on .badge either, the browser would wrap the value text
       - including mid-word ("Developing" splitting across two
       lines). flex-wrap on .vr-grid above already exists specifically
       to push whole stat blocks to a new row when space runs out;
       this makes that the only thing that happens under pressure,
       rather than individual badges also being allowed to compress
       and wrap internally. Same defensive pattern this file already
       uses on .content-lock-mark for the same reason. */
    .vr-stat {
        min-width: 100px;
        flex-shrink: 0;
    }
    .vr-stat-label {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.25rem;
    }
    .vr-stat-value {
        font-family: var(--font-mono);
        font-size: 1.4rem;
        font-weight: 600;
        color: var(--ink);
        font-variant-numeric: tabular-nums;
    }
    .badge {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.2rem 0.7rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
        font-family: var(--font-mono);
        /* 29 Aug 2026 fix, confirmed live: badges (Voice consistency,
           Risk, AI-tell check) hold single short words or two-word
           phrases and are never meant to wrap - but without this,
           a badge squeezed narrower than its own text (e.g. inside
           a .vr-stat block under viewport pressure) would wrap that
           text, including mid-word ("Developing" -> "Develop" /
           "ing" across two lines inside the pill shape). See
           .vr-stat's own flex-shrink: 0 fix, same incident, the
           row-level companion to this. */
        white-space: nowrap;
    }
    .badge::before {
        content: "";
        width: 0.4rem;
        height: 0.4rem;
        border-radius: 50%;
        background: currentColor;
        flex-shrink: 0;
    }
    .badge-green { background: var(--success-soft); color: var(--success); }
    .badge-amber { background: var(--warning-soft); color: var(--warning); }
    .badge-red   { background: var(--danger-soft);  color: var(--danger); }
    .badge-icon {
        display: inline-flex;
        margin-right: 0.05rem;
    }

    /* ---------------------------------------------------------------
       Confidence signal-bars. Confidence never gates a render, only
       Risk and Content Lock do, so it deliberately avoids the red/
       amber/green alert-pill language used for Risk: a neutral
       filled-bar meter reads as "how much data," not "something's
       wrong," and stays legible without relying on color alone
       (per WCAG 1.4.1).
       --------------------------------------------------------------- */
    .signal-bars {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        font-family: var(--font-mono);
        font-size: 0.8rem;
        font-weight: 600;
        color: var(--ink);
    }
    .signal-bars-marks {
        display: inline-flex;
        align-items: flex-end;
        gap: 2px;
    }
    .signal-bar {
        display: inline-block;
        width: 4px;
        border-radius: 1px;
        background: var(--border);
    }
    .signal-bar.filled {
        background: var(--ink);
    }
    .what-changed-row {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-bottom: 0.9rem;
    }
    .what-changed-chip {
        font-family: var(--font-mono);
        font-size: 0.78rem;
        padding: 0.25rem 0.55rem;
        border-radius: 999px;
        background: var(--surface-2, var(--surface));
        color: var(--ink);
    }
    .what-changed-empty {
        font-size: 0.82rem;
        color: var(--muted);
        margin-bottom: 0.9rem;
    }
    .content-lock-banner {
        display: flex;
        flex-direction: column;
        gap: 0.15rem;
        padding: 0.7rem 0.9rem;
        border-radius: 0.5rem;
        margin-bottom: 0.9rem;
    }
    .content-lock-banner.pass {
        background: var(--success-soft);
        color: var(--success);
    }
    .content-lock-banner.fail {
        background: var(--danger-soft);
        color: var(--danger);
    }
    .content-lock-banner-title {
        font-weight: 600;
        font-size: 0.9rem;
    }
    .content-lock-banner-reason {
        font-size: 0.82rem;
        line-height: 1.4;
    }
    .content-lock-banner-note {
        font-size: 0.82rem;
        line-height: 1.4;
        margin-top: 0.35rem;
        padding-top: 0.35rem;
        border-top: 1px solid rgba(0, 0, 0, 0.08);
        color: var(--warning);
    }
    .content-lock {
        margin-top: 0.9rem;
        padding-top: 0.9rem;
        border-top: 1px solid var(--border);
    }
    .content-lock-title {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.5rem;
    }
    /* Per-row treatment matches the vr-stat badges above it on the
       same screen (same green/amber/red tokens, same pill shape) -
       previously this checklist was the one under-styled part of an
       otherwise color-and-icon-coded report card (25 Aug 2026 UX
       audit: Content Lock "visually the least trustworthy-looking
       thing in the product"). No new detection logic, no new colors -
       reusing .badge / --success / --danger / --warning exactly as
       the vr-grid above already does. */
    .content-lock-item {
        display: flex;
        align-items: flex-start;
        gap: 0.6rem;
        padding: 0.55rem 0.7rem;
        margin-bottom: 0.4rem;
        border-radius: var(--radius-sm);
        font-size: 0.85rem;
        line-height: 1.5;
    }
    .content-lock-item.pass {
        background: var(--success-soft);
        color: var(--success);
    }
    .content-lock-item.fail {
        background: var(--danger-soft);
        color: var(--danger);
    }
    .content-lock-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.15rem;
        height: 1.15rem;
        flex-shrink: 0;
        margin-top: 0.05rem;
        border-radius: 50%;
        background: rgba(255, 255, 255, 0.55);
        font-family: var(--font-mono);
        font-weight: 700;
        font-size: 0.7rem;
        line-height: 1;
    }
    .content-lock-item span:last-child {
        font-weight: 500;
        /* Same root-cause fix as .callout-text/.voice-check-text: text
           sibling of a flex-shrink:0 mark with no flex protection of
           its own gets squeezed and mid-word broken by Streamlit's
           global CSS. This is the Content Lock checklist box. */
        flex: 1;
        min-width: 0;
        overflow-wrap: normal;
        word-break: normal;
    }
    .voice-match-table {
        width: 100%;
        border-collapse: collapse;
        margin-top: 0.6rem;
        font-size: 0.82rem;
    }
    .voice-match-table th {
        text-align: left;
        font-family: var(--font-mono);
        font-size: 0.68rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-weight: 600;
        padding: 0.35rem 0.5rem;
        border-bottom: 1px solid var(--border);
    }
    .voice-match-table td {
        padding: 0.4rem 0.5rem;
        border-bottom: 1px solid var(--border);
        color: var(--ink);
        font-variant-numeric: tabular-nums;
    }
    .voice-match-table tr:last-child td {
        border-bottom: none;
    }
    .voice-match-table .vm-verdict {
        text-align: right;
    }
    .voice-match-explain {
        font-size: 0.78rem;
        color: var(--muted);
        margin-top: 0.4rem;
        line-height: 1.4;
    }
    .ai-tell-block {
        margin-top: 0.9rem;
        padding-top: 0.9rem;
        border-top: 1px solid var(--border);
    }
    .ai-tell-title {
        font-family: var(--font-mono);
        font-size: 0.7rem;
        color: var(--muted);
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin-bottom: 0.5rem;
    }
    .ai-tell-phrase-list {
        display: flex;
        flex-wrap: wrap;
        gap: 0.4rem;
        margin-bottom: 0.6rem;
    }
    .ai-tell-phrase {
        display: inline-flex;
        align-items: center;
        padding: 0.2rem 0.6rem;
        border-radius: 6px;
        background: var(--danger-soft);
        color: var(--danger);
        font-size: 0.8rem;
        font-family: var(--font-mono);
    }
    .vr-changes {
        font-size: 0.85rem;
        color: var(--body-text);
        border-top: 1px solid var(--border);
        padding-top: 0.7rem;
        margin-top: 0.3rem;
        line-height: 1.6;
    }

    /* Refinement tags */
    .tag-hint {
        font-size: 0.85rem;
        color: var(--muted);
        margin-bottom: 0.4rem;
    }

    /* ---------------------------------------------------------------
       Native Streamlit widgets — targeted via data-testid, which is
       Streamlit's own stable public contract for this (documented,
       version-checked against streamlit==1.61.1's compiled frontend
       via BaseButton.CD99a2NM.js / TextArea.BRlPhbKO.js etc. rather
       than assumed). Internal CSS class names churn between releases;
       data-testid is what Streamlit itself recommends for exactly
       this kind of customisation.
       --------------------------------------------------------------- */
    div[data-testid="stButton"] button,
    button[data-testid^="stBaseButton"] {
        border-radius: var(--radius-sm);
        font-family: var(--font-sans);
        font-weight: 600;
        transition: background-color 0.15s ease, border-color 0.15s ease, transform 0.1s ease;
    }
    button[data-testid^="stBaseButton"]:active {
        transform: scale(0.98);
    }
    button[data-testid="stBaseButton-primary"] {
        background: var(--accent);
        border-color: var(--accent);
    }
    button[data-testid="stBaseButton-primary"]:hover {
        background: var(--accent-hover);
        border-color: var(--accent-hover);
    }
    button[data-testid="stBaseButton-secondary"] {
        color: var(--ink);
        border-color: var(--border);
    }
    button[data-testid="stBaseButton-secondary"]:hover {
        border-color: var(--accent);
        color: var(--accent);
    }

    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextInput"] input {
        border-radius: var(--radius-sm);
        border-color: var(--border);
        font-family: var(--font-sans);
    }
    div[data-testid="stTextArea"] textarea:focus,
    div[data-testid="stTextInput"] input:focus {
        border-color: var(--accent);
        box-shadow: 0 0 0 1px var(--accent);
    }

    /* Native st.error/warning/success/info render with Streamlit's own
       fixed internal red/orange/green/blue palette — not exposed via
       [theme] or any stable per-kind CSS hook in this Streamlit
       version, so it can't be recolored to match the ink/garnet/gold
       system used everywhere else on the page. The border-radius line
       below is the only thing worth keeping for the odd native call
       still in flight; .callout (with render_alert() in Python) is
       the real replacement, built on tokens already defined above. */
    div[data-testid="stAlert"] {
        border-radius: var(--radius-sm);
    }

    .callout {
        display: flex;
        align-items: flex-start;
        gap: 0.7rem;
        padding: 0.85rem 1rem;
        margin: var(--space-4) 0;
        border-radius: var(--radius-sm);
        border: 1px solid transparent;
        font-size: 0.92rem;
        line-height: 1.55;
    }
    .callout-icon {
        flex-shrink: 0;
        width: 1.15rem;
        height: 1.15rem;
        margin-top: 0.1rem;
        font-family: var(--font-mono);
        font-size: 0.78rem;
        font-weight: 700;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 50%;
    }
    .callout-error {
        background: var(--danger-soft);
        border-color: color-mix(in srgb, var(--danger) 30%, transparent);
        color: var(--danger);
    }
    .callout-error .callout-icon { background: var(--danger); color: var(--canvas); }
    .callout-warning {
        background: var(--warning-soft);
        border-color: color-mix(in srgb, var(--warning) 30%, transparent);
        color: var(--warning);
    }
    .callout-warning .callout-icon { background: var(--warning); color: var(--canvas); }
    .callout-success {
        background: var(--success-soft);
        border-color: color-mix(in srgb, var(--success) 30%, transparent);
        color: var(--success);
    }
    .callout-success .callout-icon { background: var(--success); color: var(--canvas); }
    .callout-info {
        background: var(--gold-soft);
        border-color: color-mix(in srgb, var(--gold) 30%, transparent);
        color: var(--gold);
    }
    .callout-info .callout-icon { background: var(--gold); color: var(--canvas); }
    .callout-text {
        color: var(--ink);
        /* 31 Aug 2026 fix: same root cause already diagnosed for
           .badge and .vr-stat (29 Aug) — Streamlit's own global CSS
           force-breaks text mid-word once a flex child gets squeezed
           narrower than a single word. flex:1 + min-width:0 gives the
           text its fair share of the row instead of being crushed by
           .callout-icon; overflow-wrap/word-break reset to normal so
           it wraps at spaces only, never mid-word. */
        flex: 1;
        min-width: 0;
        overflow-wrap: normal;
        word-break: normal;
    }

    /* Spinner, brand-matched via Streamlit's stSpinner data-testid
       (same public-contract targeting convention as the button rules
       above). */
    div[data-testid="stSpinner"] {
        color: var(--ink);
        font-family: var(--font-sans);
    }
    div[data-testid="stSpinner"] > div > i {
        color: var(--accent) !important;
    }

    /* ---------------------------------------------------------------
       Persistent app-shell sidebar (Write / My Voice / Past renders).
       Added per the Step 4 UX research pass (26 Aug 2026): the prior
       fix only added a one-time text note when the shell first
       appeared. Research on Linear/Notion/Grammarly/Jasper/Superhuman
       converged on a different pattern - treat the shell as the
       permanent destination, narrate its arrival with motion rather
       than a jump-cut, teach it with exactly one anchored coachmark
       (not a tour - NN/g's "Instructional Overlays and Coach Marks"
       is explicit that stacked hints get dismissed faster, not read),
       and surface usage/upgrade persistently in the shell itself
       rather than only on the Write screen or the marketing site.
       Styles below implement that: an entrance animation for the
       shell's first appearance, a single-callout coachmark anchored
       under the My Voice nav item, a static (non-button) label for
       whichever page is current, and a usage chip with a low-balance
       state - all reusing existing tokens, no new palette.
       --------------------------------------------------------------- */
    @keyframes shell-dock-in {
        from { opacity: 0; transform: translateY(10px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    /* Applied to a div this file renders directly via st.markdown, not
       to Streamlit's own sidebar container - that container's class
       list isn't reachable from unsafe_allow_html without injecting a
       script to mutate it after render, which is fragile across
       Streamlit versions and isn't done here. The coachmark below
       carries its own fade-in instead, so the sidebar's arrival still
       reads as motion even without a wrapper animation on the
       sidebar element itself. */
    .shell-intro {
        animation: shell-dock-in 0.4s ease-out forwards;
    }
    .sidebar-nav-current {
        display: flex;
        align-items: center;
        gap: 0.4rem;
        font-size: 0.9rem;
        font-weight: 600;
        color: var(--accent);
        padding: 0.4rem 0;
    }
    .sidebar-nav-current::before {
        content: "";
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: var(--accent);
        flex-shrink: 0;
    }
    .sidebar-coachmark {
        position: relative;
        margin: 0.3rem 0 0.7rem;
        padding: 0.6rem 0.7rem;
        background: var(--ink);
        color: var(--canvas);
        border-radius: var(--radius-sm);
        font-size: 0.8rem;
        line-height: 1.45;
    }
    .sidebar-coachmark::before {
        content: "";
        position: absolute;
        top: -5px;
        left: 1.1rem;
        width: 9px;
        height: 9px;
        background: var(--ink);
        transform: rotate(45deg);
    }
    .sidebar-usage-chip {
        margin-top: 0.9rem;
        padding: 0.55rem 0.7rem;
        border-radius: var(--radius-sm);
        font-size: 0.78rem;
        line-height: 1.4;
        background: var(--surface);
        border: 1px solid var(--border);
        color: var(--muted);
    }
    .sidebar-usage-chip.low {
        background: var(--warning-soft);
        border-color: transparent;
        color: var(--warning);
        font-weight: 500;
    }
    .sidebar-usage-chip a, .sidebar-usage-chip-link {
        color: inherit;
        text-decoration: underline;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

init_state()

# Silent — restores a saved voice profile for this browser/device if one
# exists, so a returning visit skips straight to Screen 4 instead of
# redoing onboarding. No UI, no prompt; see persistence.py for the
# fail-open design (any absence or error just proceeds as fresh
# onboarding, exactly as it worked before this existed).
if restore_profile_if_available():
    st.session_state.screen = 4
    if not st.session_state.get("_returning_user_sidebar"):
        st.session_state["_returning_user_sidebar"] = True
        st.rerun()

# Checkout success/cancel handling - runs on every script pass, same
# top-level query-param pattern as AQE/CLEARANCE's own Stripe redirect
# handling. Only fires once per successful checkout: verify_and_record_
# subscription's own upsert makes a second verify of the same session_id
# harmless (idempotent), but query_params.clear() below avoids re-firing
# on every subsequent rerun of this same browser tab regardless.
if st.query_params.get("payment") == "success":
    _checkout_session_id = st.query_params.get("session_id")
    # 27 Aug 2026 hardening pass (live incident, production logs
    # confirmed the cause): _device_id_for_checkout used to be derived
    # from whatever local cookie/session_state happened to exist
    # BEFORE trusting Stripe - if the cookie write from before
    # checkout hadn't landed in the browser yet (a routine race for a
    # first-time subscriber, not an edge case), this device_id was
    # freshly minted and unrelated to Stripe's own record, so a
    # completely genuine payment got rejected as a "device mismatch."
    # verify_and_record_subscription now returns Stripe's own verified
    # device_id (or None) instead of taking one in and returning a
    # bare bool - set_device_id_cookie re-establishes THAT as this
    # browser's identity, regardless of what existed before.
    _verified_device_id = (
        verify_and_record_subscription(_checkout_session_id)
        if _checkout_session_id else None
    )
    if _verified_device_id:
        set_device_id_cookie(_verified_device_id)
        go_to(9)
        st.query_params.clear()
        st.rerun()
    else:
        st.session_state["subscription_confirm_failed"] = True
        st.query_params.clear()
elif st.query_params.get("payment") == "cancelled":
    st.query_params.clear()

# Restore-by-magic-link handling - same top-level query-param pattern
# as the payment=success handler above, one screen pass, then cleared
# so a page refresh doesn't re-consume an already-used token. Binds
# the subscription behind this token to WHATEVER device is currently
# viewing the link - correct behaviour, since the whole point is
# restoring access on a device that lost its own cookie.
if st.query_params.get("restore"):
    _restore_token = st.query_params.get("restore")
    _device_id_for_restore = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = _device_id_for_restore
    if confirm_subscription_restore(_restore_token, _device_id_for_restore):
        go_to(9)
        st.query_params.clear()
        st.rerun()
    else:
        st.session_state["restore_failed"] = True
        st.query_params.clear()


# ============================================================
# Shared pricing-tier content, used by both the standalone /pricing
# screen and the in-paywall comparison, so the two stay in sync.
# ============================================================

_PRICING_TIERS = (
    {
        "name": "Free",
        "price": "£0",
        "cadence": "",
        "features": [
            "15 renders, lifetime",
            "Full voice fingerprint + Content Lock",
            "AI-tell check on every render",
            "Unlimited draft checks, no render used",
            "No account, no card required",
        ],
    },
    {
        "name": "Monthly",
        "price": "£6.99",
        "cadence": "/month",
        "features": [
            "Unlimited renders",
            "Everything in Free",
            "Priority processing",
            "Cancel anytime",
        ],
    },
    {
        "name": "Annual",
        "price": "£49",
        "cadence": "/year",
        "features": [
            "Unlimited renders",
            "Everything in Monthly",
            "Works out at ~£4.08/month",
            "Save about 42% vs paying monthly",
        ],
    },
)


def _pricing_tiers_html(compact: bool = False) -> str:
    """compact=True drops the feature bullets to a single summary
    line per tier — used in the paywall, where the full /pricing
    layout would push the actual upgrade buttons below the fold.
    Full bullets are for the standalone /pricing screen."""
    cards = []
    for tier in _PRICING_TIERS:
        if compact:
            body = f'<div class="sub">{", ".join(tier["features"][:2])}</div>'
        else:
            body = "".join(f'<div class="microcopy">&#8226; {f}</div>' for f in tier["features"])
        cards.append(
            '<div style="border:0.5px solid var(--border);border-radius:10px;'
            'padding:14px 16px;flex:1;min-width:150px;">'
            f'<div class="tagline">{tier["name"]}</div>'
            f'<div class="headline" style="font-size:1.4rem;">{tier["price"]}'
            f'<span style="font-size:0.8rem;color:var(--muted);">{tier["cadence"]}</span>'
            '</div>'
            f'{body}'
            '</div>'
        )
    return f'<div style="display:flex;gap:12px;flex-wrap:wrap;margin:0.8rem 0;">{"".join(cards)}</div>'


def screen_landing():
    st.markdown(_voiceprint_svg(width=200, height=52), unsafe_allow_html=True)
    st.markdown('<div class="tagline" style="margin-top:0.7rem;">VOICOVA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-family:var(--font-mono);font-size:0.85rem;color:var(--muted);'
        'margin-top:-0.6rem;margin-bottom:1.4rem;letter-spacing:0.02em;">'
        'Your voice. Still yours.</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="headline">AI can write like you now. The question is whether it actually did.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Paste your draft. Voicova rewrites it so it sounds like you '
        'wrote it, not like a chatbot did. Or paste anything, from anywhere, and check '
        'whether it still sounds like you.</div>',
        unsafe_allow_html=True,
    )

    # How it works — four steps, no jargon. Cross-referenced against
    # Noren's own landing page (closest direct competitor) — their
    # structure is a single positioning line, a concrete before/after,
    # a trust/ownership note, one CTA. Same shape here. Fourth step
    # (Check) added 29 Aug 2026 alongside the headline/sub update -
    # Check a Draft was previously invisible on this page despite
    # being an elevated, second-in-nav feature inside the app itself.
    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    step_col1, step_col2, step_col3, step_col4 = st.columns(4)
    with step_col1:
        st.markdown(
            '<div class="step-card"><span class="step-num">1</span>'
            '<span class="step-label">Paste</span>'
            '<span class="step-desc">A few things you\'ve actually written. '
            'No account needed.</span></div>',
            unsafe_allow_html=True,
        )
    with step_col2:
        st.markdown(
            '<div class="step-card"><span class="step-num">2</span>'
            '<span class="step-label">Calibrate</span>'
            '<span class="step-desc">A couple of quick, typed sentences '
            'sharpen the fingerprint.</span></div>',
            unsafe_allow_html=True,
        )
    with step_col3:
        st.markdown(
            '<div class="step-card"><span class="step-num">3</span>'
            '<span class="step-label">Write</span>'
            '<span class="step-desc">Paste any AI draft. Get it back '
            'sounding like you.</span></div>',
            unsafe_allow_html=True,
        )
    with step_col4:
        st.markdown(
            '<div class="step-card"><span class="step-num">4</span>'
            '<span class="step-label">Check</span>'
            '<span class="step-desc">Paste anything, from anywhere. '
            'See if it still sounds like you.</span></div>',
            unsafe_allow_html=True,
        )

    # One concrete before/after, not an abstract feature list — same
    # reasoning as the step section above. Invented example text, not
    # drawn from any real user's writing.
    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    ex_col1, ex_col2 = st.columns(2)
    with ex_col1:
        st.markdown('<div class="sub">A generic AI draft</div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="white-space:pre-wrap;line-height:1.6;background:var(--surface);'
            'border:0.5px solid var(--border);border-radius:10px;padding:14px 16px;'
            'font-size:0.85rem;color:var(--body-text);">'
            'I wanted to reach out regarding the project timeline. I believe we should '
            'consider adjusting our approach moving forward to ensure optimal outcomes.'
            '</div>',
            unsafe_allow_html=True,
        )
    with ex_col2:
        st.markdown('<div class="sub">Rewritten in your voice</div>', unsafe_allow_html=True)
        st.markdown(
            '<div style="white-space:pre-wrap;line-height:1.6;background:var(--surface);'
            'border:0.5px solid var(--border);border-radius:10px;padding:14px 16px;'
            'font-size:0.85rem;color:var(--body-text);">'
            'Quick one on the timeline. I think we need to change tack here. Happy to '
            'talk it through whenever works.'
            '</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    st.markdown(
        '<div class="microcopy">No account or signup. Your profile is tied to a device '
        'cookie, not an email. Clear your cookies and it\'s gone. Per-device, too — your '
        'phone or another computer starts fresh with its own free renders. No selling, '
        'no sharing, no third-party analytics on what you write. 15 renders free, then '
        '£6.99/month or £49/year for unlimited.</div>',
        unsafe_allow_html=True,
    )

    st.markdown("")
    cta_col1, cta_col2 = st.columns([2, 1])
    with cta_col1:
        if st.button("Get started \u2192", type="primary", use_container_width=True):
            go_to(1)
            st.rerun()
    with cta_col2:
        if st.button("See pricing", use_container_width=True):
            go_to(7)
            st.rerun()


def _handle_checkout_plan_request() -> None:
    """Shared by screen_pricing() and the Step-4 paywall: if an Upgrade
    button on EITHER screen just set _checkout_plan_requested, create
    the Stripe Checkout Session and redirect. Pulled out as one
    function specifically because the pricing-page Upgrade buttons
    needed the exact same create-session-and-redirect behaviour the
    paywall already had, without a second copy of the meta-refresh /
    fallback-link logic to drift out of sync with the first.
    """
    _requested_plan = st.session_state.pop("_checkout_plan_requested", None)
    if not _requested_plan:
        return
    device_id_for_checkout = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id_for_checkout
    checkout_url = create_subscription_checkout(device_id_for_checkout, plan=_requested_plan)
    if checkout_url:
        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={_safe_html(checkout_url)}">',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="microcopy">Redirecting to secure checkout...</div>',
            unsafe_allow_html=True,
        )
        st.link_button("Continue to payment \u2192", checkout_url, use_container_width=True)
    else:
        render_alert("Couldn't start checkout. Please try again shortly.", "error")


def _handle_manage_subscription_request() -> None:
    """Same create-session-and-redirect shape as
    _handle_checkout_plan_request(), just for Stripe's Customer
    Portal instead of Checkout Session - one click ("Manage
    subscription") sets _manage_subscription_requested, this creates
    the portal session on the immediately following rerun and meta-
    refreshes to it. Cancel, plan change, card update, and invoices
    are all Stripe's own hosted portal UI from here - not a custom
    cancel flow VOICOVA builds and maintains itself, the same "reuse
    a proven pattern, don't invent one" approach as everything else
    in this module.
    """
    if not st.session_state.pop("_manage_subscription_requested", False):
        return
    device_id_for_portal = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id_for_portal
    portal_url = create_billing_portal_session(device_id_for_portal)
    if portal_url:
        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={_safe_html(portal_url)}">',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="microcopy">Redirecting to your subscription settings...</div>',
            unsafe_allow_html=True,
        )
        st.link_button("Manage subscription \u2192", portal_url, use_container_width=True)
    else:
        render_alert(
            "Couldn't open subscription settings. Please try again shortly.",
            "error",
        )


def _render_restore_access_expander(key_prefix: str = "") -> None:
    """Shared by screen_pricing() and the Step-4 paywall: the
    "Already subscribed? Restore access" recovery path for the
    device-cookie identity model's one real gap (see
    stripe_subscription.py's module docstring) - a real subscriber
    whose cookie is gone has no other way back in without this.
    Collapsed expander, not a prominent button, on both screens - this
    is for the rare returning-subscriber case, not competing for
    attention with the upgrade CTAs it sits below.

    key_prefix keeps Streamlit widget keys unique if this ever renders
    on two screens in the same session history (same reasoning as
    screen_pricing()'s own pricing_page_* button keys).
    """
    if st.session_state.get("restore_failed"):
        render_alert(
            "That link didn't work — it may have expired or already "
            "been used. Request a new one below.",
            "error",
        )
        st.session_state["restore_failed"] = False

    with st.expander("Already subscribed? Restore access"):
        st.markdown(
            '<div class="microcopy">Enter the email you subscribed with. '
            "If it matches an active subscription, we'll email you a link "
            'to restore access on this device.</div>',
            unsafe_allow_html=True,
        )
        _restore_email = st.text_input(
            label="Email",
            key=f"{key_prefix}restore_email_input",
            label_visibility="collapsed",
            placeholder="you@example.com",
        )
        if st.button("Send restore link", key=f"{key_prefix}send_restore_link"):
            if _restore_email and "@" in _restore_email:
                request_subscription_restore(_restore_email.strip())
                st.session_state["restore_requested"] = True
            else:
                render_alert("Enter a valid email address.", "error")
        # Always the SAME message whether or not a match was found -
        # request_subscription_restore() never reports back which case
        # it was (see its own docstring: telling an unauthenticated
        # caller "no subscription found" would let anyone probe which
        # emails belong to paying customers).
        if st.session_state.get("restore_requested"):
            render_alert(
                "If that email matches an active subscription, a restore "
                "link is on its way. It expires in 15 minutes.",
                "success",
            )
            st.session_state["restore_requested"] = False


def screen_pricing():
    st.markdown('<div class="tagline">VOICOVA</div>', unsafe_allow_html=True)
    st.markdown('<div class="headline">Pricing.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Try it free. No card required to start.</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_pricing_tiers_html(compact=False), unsafe_allow_html=True)
    st.markdown(
        '<div class="microcopy">Cancel anytime. Renders don\'t roll over month to month '
        'on the paid tiers. They\'re unlimited while your subscription is active.</div>',
        unsafe_allow_html=True,
    )
    st.markdown("")

    # Same upgrade pattern as the paywall (_checkout_plan_requested ->
    # rerun -> checkout URL), added here to close a real conversion
    # leak: this standalone screen previously had no way to actually
    # subscribe, only "<- Back" - a visitor curious about pricing
    # before spending any renders had no path to pay. Distinct keys
    # (pricing_page_*) so Streamlit doesn't collide with the paywall's
    # own upgrade_monthly/upgrade_annual buttons if both ever render
    # in the same session history.
    pay_col1, pay_col2 = st.columns(2)
    with pay_col1:
        if st.button("Upgrade: £6.99/month", key="pricing_page_upgrade_monthly", use_container_width=True):
            st.session_state["_checkout_plan_requested"] = "monthly"
            st.rerun()
    with pay_col2:
        if st.button("Upgrade: £49/year", key="pricing_page_upgrade_annual", use_container_width=True):
            st.session_state["_checkout_plan_requested"] = "annual"
            st.rerun()

    _handle_checkout_plan_request()

    st.markdown("")
    _render_restore_access_expander(key_prefix="pricing_page_")

    st.markdown("")
    if st.button("\u2190 Back", use_container_width=True):
        go_to(0 if not st.session_state.get("baseline_fingerprint") else 4)
        st.rerun()


def screen_confirmed():
    """Dedicated post-payment landing screen (screen 9). Replaces the
    old approach of dropping a small inline render_alert() on top of
    whatever screen the paywall happened to trigger checkout from -
    that banner was easy to miss even when the backend had correctly
    recorded the subscription. This is the same numbered-screen +
    go_to() pattern every other step in the product already uses, not
    a new navigation mechanism. Reached only after Stripe verification
    has already succeeded (app.py's payment=success and restore query
    param handlers both call go_to(9) only on a verified device_id) -
    this screen itself does no verification, it just confirms what
    already happened.
    """
    st.markdown('<div class="tagline">VOICOVA</div>', unsafe_allow_html=True)
    st.markdown('<div class="headline">You\'re subscribed.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Thanks for backing VOICOVA. Renders are unlimited while your '
        'subscription is active. Stripe has emailed you a receipt.</div>',
        unsafe_allow_html=True,
    )
    st.markdown("")
    if st.button("Start writing \u2192", key="confirmed_start_writing", use_container_width=True):
        go_to(4)
        st.rerun()


_PROGRESS_STEP_NAMES = ("Paste", "Your voice", "Calibrate", "Write")


def _voiceprint_svg(width: int = 220, height: int = 64, bar_width: int = 4, gap: int = 4) -> str:
    """The product's signature graphic: a voiceprint/waveform mark,
    echoing "fingerprint" and "voice" language already used
    throughout the product's own copy. Heights are a fixed pattern,
    not randomised, so this reads as a stable mark across the product
    rather than changing decoration.
    """
    heights_pattern = [0.35, 0.55, 0.8, 0.5, 1.0, 0.65, 0.4, 0.85, 0.6, 0.3, 0.75, 0.45]
    n = max(1, width // (bar_width + gap))
    bars = []
    x = 0
    for i in range(n):
        frac = heights_pattern[i % len(heights_pattern)]
        bar_h = max(3, int(height * frac))
        y = (height - bar_h) / 2
        color = "var(--gold)" if (i % 4 == 3) else "var(--accent)"
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_h}" '
            f'rx="{bar_width / 2:.1f}" fill="{color}" opacity="0.9"/>'
        )
        x += bar_width + gap
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
        f'xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Voicova voiceprint mark">'
        f'{"".join(bars)}</svg>'
    )


def progress_dots(current: int, total: int = 4):
    # Waveform-bar progress mark, echoing the landing hero's voiceprint
    # graphic in miniature rather than plain circles. Bar heights are
    # fixed per position, not randomised, so the shape stays stable.
    _bar_heights = (7, 11, 6, 10)
    bars = ""
    for i in range(1, total + 1):
        h = _bar_heights[(i - 1) % len(_bar_heights)]
        cls = "bar active" if i == current else "bar"
        bars += f'<span class="{cls}" style="height:{h}px;"></span>'
    step_name = _PROGRESS_STEP_NAMES[current - 1] if 1 <= current <= len(_PROGRESS_STEP_NAMES) else ""
    st.markdown(
        f'<div class="progress">{bars}'
        f'<span style="font-family:var(--font-mono);font-size:0.72rem;color:var(--faint);'
        f'letter-spacing:0.04em;margin-left:0.6rem;">'
        f'Step {current} of {total}{" \u00b7 " + step_name if step_name else ""}</span></div>',
        unsafe_allow_html=True,
    )


_CALLOUT_ICON = {"error": "!", "warning": "!", "success": "\u2713", "info": "i"}


def render_alert(message: str, kind: str = "info"):
    """
    Replaces native st.error/warning/success/info, which render with
    Streamlit's own fixed internal palette (red/orange/green/blue) —
    not reachable through [theme] or a stable per-kind CSS hook in this
    Streamlit version, so a native alert can't be recolored to match
    the ink/garnet/gold system used everywhere else on the page (see
    the .callout comment in the <style> block). This draws the same
    message on the existing --danger/--warning/--success/--gold tokens
    instead, same visual language as .voice-check and .receipt.

    kind: "error" | "warning" | "success" | "info"
    """
    icon = _CALLOUT_ICON.get(kind, "i")
    st.markdown(
        f'<div class="callout callout-{kind}">'
        f'<span class="callout-icon">{icon}</span>'
        f'<span class="callout-text">{_safe_html(message)}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _add_writing_sample_to_fingerprint(text: str, platform_format: str | None = None) -> None:
    """
    The complete "strengthen the baseline with one more genuine
    writing sample" sequence — extracted 29 Aug 2026 from inside
    _deepen_fingerprint_panel (below) so a second call site (Learn
    from my edit, screen_render) can reuse the exact same steps
    rather than a re-typed copy that could quietly drift out of sync.
    Pure extraction, not a rewrite - every line here previously lived
    directly inside the deepen panel's button handler, unchanged.

    Merges the sample into the blended baseline, the per-register
    stability check, cumulative word/doc counters, and the observed-
    traits list; if a Voice Report is already on screen, refreshes its
    Confidence badge and function-word Delta in place; persists the
    strengthened profile via save_profile_if_available() (a safe
    no-op if no baseline exists yet).

    platform_format (30 Aug 2026): optional, "social" | "email" | None.
    When given, ALSO merges this sample into a second, independent
    compounding baseline keyed by format —
    st.session_state.baseline_fingerprints_by_format[platform_format]
    — built with the same _merge_baseline logic as the blended
    baseline below, so a person's email voice and social voice
    compound separately instead of being flattened into one register.
    Purely additive: the existing blended baseline_fingerprint is
    still merged exactly as before regardless of this parameter, and
    every existing caller/reader of it is unaffected. Onboarding
    samples (Screen 1/3) don't have a platform_format and correctly
    pass None here — only Learn-from-edit samples, which know which
    register the render targeted, populate the per-format baseline.
    """
    new_metrics = compute_baseline_metrics(text)
    st.session_state.baseline_fingerprint = _merge_baseline(
        st.session_state.get("baseline_fingerprint"), new_metrics
    )
    if platform_format:
        by_format = st.session_state.get("baseline_fingerprints_by_format") or {}
        by_format[platform_format] = _merge_baseline(
            by_format.get(platform_format), new_metrics
        )
        st.session_state.baseline_fingerprints_by_format = by_format
    st.session_state.cumulative_words += len(text.split())
    st.session_state.cumulative_docs += 1
    extra_obs = analyse_writing(text)
    existing = st.session_state.observations
    existing_headlines = {o["headline"] for o in existing}
    for obs in extra_obs:
        if obs["headline"] not in existing_headlines:
            existing.append(obs)
            existing_headlines.add(obs["headline"])
    existing.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
    st.session_state.observations = existing[:5]

    samples = st.session_state.get("fingerprint_samples", [])
    samples.append(new_metrics)
    st.session_state.fingerprint_samples = samples
    st.session_state.dimension_stability = compute_dimension_stability(samples)

    sample_texts = st.session_state.get("fingerprint_sample_texts", [])
    sample_texts.append(text)
    st.session_state.fingerprint_sample_texts = sample_texts

    report = st.session_state.get("voice_report")
    if report:
        new_confidence = compute_confidence(
            st.session_state.get("sample_fitness"),
            st.session_state.get("baseline_fingerprint"),
            len(st.session_state.get("observations", [])),
            st.session_state.get("dimension_stability"),
        )
        report["confidence"] = new_confidence
        st.session_state.confidence = new_confidence

        render_output = st.session_state.get("render_output")
        if render_output:
            updated_sample_texts = st.session_state.get("fingerprint_sample_texts", [])
            new_burrows_delta = compute_burrows_delta(updated_sample_texts, render_output)
            st.session_state.function_word_delta = new_burrows_delta
            report["function_word_delta"] = new_burrows_delta.get("delta")
            report["function_word_delta_tier"] = new_burrows_delta.get("tier")
            report["function_word_biggest_divergences"] = new_burrows_delta.get("biggest_divergences", [])

        st.session_state.voice_report = report

    save_profile_if_available()


def _deepen_fingerprint_panel(show_caveat_framing: bool = False, expanded: bool = False):
    """
    Visible from first use, not gated behind anything — per the v4 spec's
    decision (Section 6/10): fast path is the default, but anyone who
    wants a stronger baseline can reach it without being funnelled there.

    show_caveat_framing=True is used on Screen 4, only when
    confidence_caveat() actually returned something - swaps the generic
    copy for language that matches why the panel is showing up there,
    and recomputes Confidence (and the caveat itself) immediately after
    a sample is added, since Screen 4 already has a Voice Report on
    screen that should reflect the new number without a re-render.
    """
    label = "Try one more sample" if show_caveat_framing else "Deepen your fingerprint"
    body = (
        "Paste one more piece of your own writing. If it reads closer to "
        "your other samples, this will lift the Confidence badge above. "
        "If it doesn't, that's a real result too. Some people's writing "
        "genuinely shifts more than others across situations, and no "
        "amount of extra samples changes that."
        if show_caveat_framing else
        "Paste more of your own writing. Each sample strengthens the baseline, "
        "useful if you want a higher bar than the fast path gives you."
    )
    with st.expander(label, expanded=(show_caveat_framing or expanded)):
        st.markdown(
            f'<div class="sub" style="margin-bottom:0.8rem;">{body}</div>',
            unsafe_allow_html=True,
        )
        extra = st.text_area(
            "deepen", placeholder="Paste another piece of your own writing...",
            height=120, label_visibility="collapsed", key="deepen_text",
        )
        if st.button("Add to my fingerprint", key="deepen_submit"):
            if extra and len(extra.split()) >= 10:
                _add_writing_sample_to_fingerprint(extra)

                # Same pattern already used for render_error: a bare
                # st.success() call here is wiped by the st.rerun()
                # immediately below before the browser ever paints it —
                # the person clicks Add, the page reruns, and nothing
                # visibly confirms anything happened, even though the
                # sample genuinely was added (confirmed directly: word
                # count, baseline, and dimension_stability all update
                # correctly). session_state survives the rerun; a bare
                # UI call does not.
                st.session_state.deepen_success_message = "Added. Your fingerprint just got stronger."
                st.rerun()
            else:
                render_alert("A bit more, at least a sentence or two.", "error")


# ============================================================
# Screen 1 — Paste something you've written
# ============================================================

def screen_paste():
    progress_dots(1)

    st.markdown('<div class="tagline">VOICOVA</div>', unsafe_allow_html=True)
    st.markdown(
        '<div style="font-family:var(--font-mono);font-size:0.85rem;color:var(--muted);'
        'margin-top:-0.6rem;margin-bottom:1.4rem;letter-spacing:0.02em;">'
        'Your voice. Still yours.</div>',
        unsafe_allow_html=True,
    )
    st.markdown('<div class="headline">AI can write well now. It just doesn\'t write like you.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Paste your draft. Voicova rewrites it so it sounds like you '
        'wrote it, not like a chatbot did.</div>',
        unsafe_allow_html=True,
    )

    # Upfront guidance, shown before the first submission rather than
    # only as a post-rejection error (25 Aug 2026 UX audit: "the
    # biggest first-impression tax in the entire flow" — every
    # comparable onboarding front-loads what good input looks like,
    # this one made a first-time user fail once to learn it). Reuses
    # the same wording the fitness gate's own nudge would give for
    # thin/generic input (voice_engine.py's _score_sample_fitness),
    # framed as guidance rather than a rejection — same substance,
    # earlier moment. Only shown pre-submission; once real fitness
    # feedback exists below, that takes over.
    if not st.session_state.get("cumulative_words"):
        st.markdown(
            '<div class="microcopy" style="margin-bottom:0.6rem;">'
            'Good input: an email you actually sent, a message to a colleague '
            'about a real project — names, specifics, your own words. Not a '
            'formal document or something written to sound professional.</div>',
            unsafe_allow_html=True,
        )

    text = st.text_area(
        label="Your writing",
        value=st.session_state.raw_text,
        placeholder="Paste an email, a message, a paragraph - anything you wrote...",
        height=220,
        label_visibility="collapsed",
    )

    # NOT a true live/per-keystroke count: st.text_area only commits
    # its value (and triggers a rerun) on blur or Ctrl+Enter - a
    # Streamlit platform constraint, not something fixable here without
    # swapping in a custom JS-backed component (flagged for the design
    # pass, 25 Aug 2026 UX audit). The count below is accurate as of
    # the last commit, not as of the last keystroke - the explicit
    # "updates when you pause or click away" line exists specifically
    # so a stuck-looking counter while actively typing reads as
    # expected behaviour, not a bug (the audit's actual finding: this
    # was previously unexplained and read as broken for a few seconds
    # at the very start of the product).
    _live_word_count = len(text.split()) if text and text.strip() else 0
    st.markdown(
        f'<div class="microcopy" style="margin-top:-0.6rem;">'
        f'{_live_word_count} words so far (updates when you pause typing '
        f'or click away) &middot; most fingerprints need '
        f'roughly 100&ndash;250 words of real writing, more specific and '
        f'personal than formal.</div>',
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("Show me my fingerprint \u2192", type="primary", use_container_width=True):
            if not text or not text.strip():
                render_alert("Paste something you wrote first.", "error")
            elif len(text.split()) < 10:
                render_alert("A bit more. At least a sentence or two.", "error")
            else:
                word_count = len(text.split())
                st.session_state.cumulative_words += word_count
                st.session_state.cumulative_docs += 1
                st.session_state.raw_text = text
                st.session_state.word_count = word_count
                st.session_state.locale = _detect_locale(text)

                new_metrics = compute_baseline_metrics(text)
                st.session_state.baseline_fingerprint = _merge_baseline(
                    st.session_state.get("baseline_fingerprint"), new_metrics
                )
                # Kept unmerged and separate from starter samples below -
                # this is the register-distinct sample list stability is
                # computed over. Reset here since Screen 1 can be re-pasted
                # (Back button), and a stale Screen 3 sample from a
                # previous pass shouldn't be compared against a new paste.
                st.session_state.fingerprint_samples = [new_metrics]
                # Raw text alongside the metrics — same reset-on-repaste
                # reasoning. Metrics alone can't feed compute_burrows_delta,
                # which needs the actual words to build a frequency profile.
                st.session_state.fingerprint_sample_texts = [text]

                fitness = _score_sample_fitness(text)
                st.session_state.sample_fitness = fitness
                words_so_far = st.session_state.cumulative_words
                gate = _fitness_gate(fitness, words_so_far, st.session_state.cumulative_docs)

                if gate["action"] == "fire":
                    with st.spinner("Reading your writing..."):
                        st.session_state.observations = analyse_writing(st.session_state.raw_text)
                    st.session_state.fingerprint_confidence = gate["confidence"]
                    st.session_state.fitness_nudge = None
                    go_to(2)
                    st.rerun()
                else:
                    st.session_state.fitness_nudge = gate.get("message")
                    # The gate correctly asking for more text was being
                    # read as "the button did nothing" (real UX finding,
                    # not a code bug — first click DOES score the sample,
                    # it just correctly declines to advance on a thin
                    # sample). A toast makes that outcome visible
                    # immediately, on top of the existing inline message
                    # below, rather than relying on the person to notice
                    # new text appear under the button after rerun.
                    if gate.get("message"):
                        st.toast(gate["message"], icon="\u270d\ufe0f")
                    st.rerun()

    fitness = st.session_state.get("sample_fitness")
    nudge = st.session_state.get("fitness_nudge")
    words_so_far = st.session_state.get("cumulative_words", 0)

    if words_so_far > 0 and fitness:
        tier = fitness.get("tier", "thin")
        if nudge:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;color:var(--warning);">{_safe_html(nudge)}</div>',
                unsafe_allow_html=True
            )
        elif tier == "gold":
            st.markdown(
                '<div class="microcopy" style="margin-top:0.5rem;color:var(--success);">Strong sample. Your fingerprint is ready.</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;">{words_so_far} words submitted. Paste more of your own writing.</div>',
                unsafe_allow_html=True
            )

    st.markdown(
        '<div class="microcopy">No account or signup. Your profile is tied to a device '
        'cookie, not an email — so it\'s there next time you\'re on this browser, gone if '
        'you clear cookies or switch devices.</div>',
        unsafe_allow_html=True
    )
    with st.expander("What we store, and why"):
        st.markdown(
            "- **Your writing sample and voice fingerprint**: so VOICOVA "
            "recognises your voice next time, without you re-onboarding.\n"
            "- **A summary of your voice profile**: used to write in your "
            "voice on future renders.\n"
            "- **Your render history** (last 50): so you can revisit past "
            "renders.\n\n"
            "This is tied to a device cookie, not an account or email. "
            "We don't know who you are unless you choose to subscribe. "
            "Clear your cookies and it's gone. No selling, no sharing, "
            "no third-party analytics on this data.\n\n"
            "It's also per-device: this browser only. On your phone or "
            "another computer, you'll go through onboarding again and get "
            "your own separate 15 free renders — nothing carries across."
        )

    # Plain link to /pricing, not a promotional push. This screen's
    # job is onboarding, not selling.
    if st.button("See pricing \u2192", key="pricing_link_screen1"):
        go_to(7)
        st.rerun()


# ============================================================
# Screen 2 — Fingerprint reveal, as a checklist ("Your Voice")
# ============================================================

def _show_deepen_success_if_pending():
    """Displays and clears the deepen-fingerprint success message left
    in session_state by _deepen_fingerprint_panel's submit handler.
    Called at the top of any screen that can host that panel — the
    message has to survive the st.rerun() the handler triggers right
    after setting it, and st.success() called before a rerun is wiped
    before the browser ever paints it. Session state survives the
    rerun; a bare UI call does not. Placed at the TOP of each caller
    deliberately, not inside the panel itself, because after adding a
    sample the caveat that gates the panel on Screen 4 may no longer
    fire (that's the whole point of adding the sample) — the message
    still needs to show even when the panel that produced it is gone.
    """
    if st.session_state.get("deepen_success_message"):
        render_alert(st.session_state.deepen_success_message, "success")
        st.session_state.deepen_success_message = None


def screen_reveal():
    progress_dots(2)
    _show_deepen_success_if_pending()

    st.markdown('<div class="headline">Your voice.</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sub">From {st.session_state.word_count} words of your writing. You typically:</div>',
        unsafe_allow_html=True
    )

    observations = st.session_state.observations
    if not observations:
        render_alert("Not enough signal. Paste more of your writing.", "warning")
        if st.button("\u2190 Try again"):
            go_to(1)
            st.rerun()
        return

    import re as _re
    for i, obs in enumerate(observations):
        quote_match = _re.search(r'"([^"]{10,})"', obs.get("body", ""))
        evidence_html = (
            f'<div class="voice-check-evidence">e.g. "{_safe_html(quote_match.group(1))}"</div>'
            if quote_match else ""
        )
        st.markdown(
            f'<div class="voice-check" style="animation-delay: {i * 0.12}s;">'
            f'<div class="voice-check-mark">\u2713</div>'
            f'<div><div class="voice-check-text">{_safe_html(obs["headline"])}</div>'
            f'{evidence_html}</div></div>',
            unsafe_allow_html=True,
        )

    # Calibration confidence (31 Aug 2026) — the reveal screen showed
    # observations with no indication of how solid each one is, so the
    # baseline got accepted on faith before Voice Drift ever checks
    # anything against it. Reuses the exact per-dimension table already
    # built for screen_my_voice (_render_dimension_confidence_table) —
    # no new scoring, just showing it one step earlier, at the point
    # the baseline is actually formed rather than only afterward on the
    # standing dashboard. A single first sample will mostly read "Not
    # enough data" / Low here — that's the correct, honest reading at
    # this stage, not a bug; it's exactly what "Add another sample"
    # below (the deepen panel) exists to improve.
    _render_dimension_confidence_table(
        observations, heading="How solid this baseline is so far:",
        show_flag_control=True,
    )

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    # Un-collapsed by default so this doesn't get missed the way a
    # collapsed expander with no visual weight would.
    _deepen_fingerprint_panel(expanded=True)
    st.markdown("")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("\u2190 Start over", use_container_width=True):
            reset_all()
            st.rerun()
    with col2:
        if st.button("Continue \u2192", type="primary", use_container_width=True):
            go_to(3)
            st.rerun()


# ============================================================
# Screen 3 — Four sentence starters, typed live, paste blocked
# ============================================================

def _safe_html(text: str) -> str:
    """Escape user- or model-derived text before it is interpolated into
    an f-string that gets rendered via st.markdown(..., unsafe_allow_html=True).
    Streamlit does not sanitise markdown/HTML itself - anything dynamic
    that reaches one of those calls unescaped is a stored/reflected HTML
    injection path. Call this on every dynamic value at the point of
    interpolation, not once upstream, so a future call site can't
    accidentally skip it."""
    if text is None:
        return ""
    return html.escape(str(text))


# Generic fallback scenarios - used when there's nothing usable in the
# Screen 1 paste to anchor to (too short, no sentence-shaped fragments).
# Kept under the old name so anything referencing app.STARTERS (e.g. the
# harness docstring) still resolves to something sensible.
STARTERS = [
    "Someone just sent over work that missed the brief entirely. Type your reply exactly as it comes to you, first draft, no editing...",
    "A friend just asked what you actually do for work. Answer them right now, in your own words...",
    "You've just decided something that affects someone else, and you have to tell them now. What do you say...",
    "Something someone just said is genuinely getting under your skin. Write down what you're thinking, unfiltered...",
]

# Anchored versions of the same four scenarios - {s} is a sentence pulled
# from the user's own Screen 1 paste. Continuation is a lower-effort task
# than invention, so anchoring to their own words instead of a free-floating
# scenario should raise both completion rate and how genuinely-voiced the
# sample is.
_ANCHOR_TEMPLATES = [
    'Picture someone pushing back hard on this line you wrote: "{s}" Type your reply exactly as it comes to you, first draft, no editing...',
    'A friend just read this line of yours, "{s}", and asked what you actually meant. Answer them right now, in your own words...',
    'Someone just asked you to justify this: "{s}" What do you say...',
    'Keep going from where you left off. "{s}" Write the next few sentences, unfiltered, first version only...',
]


def _extract_anchor_sentences(raw_text: str, n: int) -> list[str]:
    """Pull up to n sentence-shaped fragments from the user's own Screen 1
    paste. Crude split, good enough for anchoring a prompt - not meant to
    be grammatically precise. Returns fewer than n (or none) if raw_text
    doesn't have enough usable material; callers fall back to the generic
    scenario for any starter that doesn't get an anchor."""
    if not raw_text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+', raw_text.strip())
    usable = [s.strip() for s in sentences if 5 <= len(s.split()) <= 25]
    return usable[:n]


def _build_starters(raw_text: str) -> list[str]:
    """Screen 3 starters, anchored to the Screen 1 paste where possible,
    falling back to the generic scenario per-slot where it isn't."""
    anchors = _extract_anchor_sentences(raw_text, n=len(STARTERS))
    starters = []
    for i, generic in enumerate(STARTERS):
        if i < len(anchors):
            starters.append(_ANCHOR_TEMPLATES[i].format(s=anchors[i]))
        else:
            starters.append(generic)
    return starters


# Word floor for each required starter. Not the old 40-word combined
# floor across four boxes - see screen_sample2() for the gate logic.
SAMPLE2_REQUIRED_MIN_WORDS = 10

# Which two of the four starters are required, not optional. Chosen for
# maximum register contrast, not just "first two": index 0 is a
# professional, defensive-register scenario (replying to criticism of
# your work); index 3 is an unfiltered, emotional-register scenario
# (something getting under your skin). Research on register variation
# says idiolect signal separates from register noise by comparing across
# genuinely different registers, not by collecting more of the same one -
# so the two required starters are picked to be as far apart on that axis
# as the existing four scenarios allow. Indices 1 and 2 remain optional
# enrichment in the "Deepen your fingerprint" expander.
REQUIRED_STARTER_INDICES = (0, 3)


def screen_sample2():
    progress_dots(3)

    starters = _build_starters(st.session_state.get("raw_text", ""))

    st.markdown('<div class="headline">Two more samples.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Finish both starters, typed live. Deliberately different situations. '
        'That contrast is what lets us tell your real voice apart from just this one scenario. '
        'Don\'t think it through, don\'t edit. First version only. '
        '<strong>Paste is switched off on these two fields on purpose.</strong> '
        'Typing live is what makes the sample real.</div>',
        unsafe_allow_html=True
    )

    completions = st.session_state.sample2_completions
    required_word_counts = {}

    for position, idx in enumerate(REQUIRED_STARTER_INDICES, start=1):
        st.markdown(
            f'<div class="microcopy" style="text-align:left;margin-top:1.1rem;margin-bottom:0.1rem;'
            f'font-family:var(--font-mono);text-transform:uppercase;letter-spacing:0.05em;">'
            f'Prompt {position} of {len(REQUIRED_STARTER_INDICES)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(f'<div class="tag-hint" style="margin-top:0.3rem;">{_safe_html(starters[idx])}</div>', unsafe_allow_html=True)
        completions[idx] = paste_guard(value=completions[idx], key=f"starter_{idx}")
        wc = len(completions[idx].split())
        required_word_counts[idx] = wc
        # "X / N words" read like a fraction of a target (UX audit, 25
        # Aug 2026) when N is actually a minimum floor, not a target to
        # hit exactly - "at least N" states the actual rule. Also not
        # truly live per keystroke, same platform reason as the Paste
        # screen's counter (st.text_area only commits on blur/Ctrl+
        # Enter) - same clarifying note here.
        _met = wc >= SAMPLE2_REQUIRED_MIN_WORDS
        st.markdown(
            f'<div class="microcopy" style="text-align:left;margin-top:0.4rem;'
            f'{"color:var(--success);" if _met else ""}">'
            f'{wc} words (updates when you pause or click away) '
            f'&middot; at least {SAMPLE2_REQUIRED_MIN_WORDS} needed'
            f'{" &mdash; done" if _met else ""}</div>',
            unsafe_allow_html=True
        )

    st.markdown("")
    optional_indices = [i for i in range(len(starters)) if i not in REQUIRED_STARTER_INDICES]
    with st.expander("Deepen your fingerprint"):
        st.markdown(
            '<div class="sub" style="margin-bottom:0.8rem;">Optional. Each one you answer '
            'strengthens your fingerprint further.</div>',
            unsafe_allow_html=True
        )
        for i in optional_indices:
            st.markdown(f'<div class="tag-hint" style="margin-top:0.8rem;">{_safe_html(starters[i])}</div>', unsafe_allow_html=True)
            completions[i] = paste_guard(value=completions[i], key=f"starter_{i}")

    st.session_state.sample2_completions = completions

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("\u2190 Back", use_container_width=True):
            go_to(2)
            st.rerun()
    with col2:
        if st.button("Continue \u2192", type="primary", use_container_width=True):
            under_floor = [i for i in REQUIRED_STARTER_INDICES if required_word_counts[i] < SAMPLE2_REQUIRED_MIN_WORDS]
            if under_floor:
                render_alert(
                    f"A little more on both, at least {SAMPLE2_REQUIRED_MIN_WORDS} words each "
                    f"to continue.",
                    "error",
                )
            else:
                combined = " ".join(c.strip() for c in completions if c.strip())
                intro_obs = _analyse_intro(combined)
                existing = st.session_state.observations
                existing_headlines = {o["headline"] for o in existing}
                for obs in intro_obs:
                    if obs["headline"] not in existing_headlines:
                        existing.append(obs)
                        existing_headlines.add(obs["headline"])
                existing.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
                st.session_state.observations = existing[:5]

                # Score each required starter SEPARATELY, not combined -
                # combining them before measurement would throw away
                # exactly the register contrast the two-starter
                # requirement exists to capture. The combined text is
                # still fine for _analyse_intro above, which reads
                # narrative content, not the four numeric dimensions.
                required_metrics = [
                    compute_baseline_metrics(completions[i].strip())
                    for i in REQUIRED_STARTER_INDICES
                ]

                # Starter-only baseline (both required starters combined)
                # kept unmerged for the existing render-stage independent
                # check - unchanged from before.
                starter_combined = " ".join(
                    completions[i].strip() for i in REQUIRED_STARTER_INDICES
                )
                starter_baseline = compute_baseline_metrics(starter_combined)
                st.session_state.starter_baseline = starter_baseline

                samples = st.session_state.get("fingerprint_samples", [])
                samples.extend(required_metrics)
                st.session_state.fingerprint_samples = samples
                st.session_state.dimension_stability = compute_dimension_stability(samples)

                sample_texts = st.session_state.get("fingerprint_sample_texts", [])
                sample_texts.extend(
                    completions[i].strip() for i in REQUIRED_STARTER_INDICES
                )
                st.session_state.fingerprint_sample_texts = sample_texts

                for m in required_metrics:
                    st.session_state.baseline_fingerprint = _merge_baseline(
                        st.session_state.get("baseline_fingerprint"), m
                    )
                save_profile_if_available()
                # Unlocks the sidebar ("My Voice" / "Past renders") for
                # first-time completers, not just returning visitors.
                # Kept as a separate flag from _returning_user_sidebar,
                # which also drives screen copy and progress-dot
                # visibility and shouldn't change just because the
                # sidebar unlocks.
                st.session_state["_sidebar_unlocked"] = True
                go_to(4)
                st.rerun()


# ============================================================
# Screen 4 — Render, Voice Report, one refinement
# ============================================================

def _generate_voice_profile_summary(corpus_text: str) -> str | None:
    """
    One-time distillation call: condenses a person's raw writing corpus
    into a short natural-language profile of their distinctive habits.
    See build_voice_profile_summary_prompt's own docstring for the
    research basis — generating from a distilled profile measurably
    outperforms generating from raw context directly.

    Generated lazily on the FIRST render call after a baseline exists,
    not at Screen 3 completion — deliberately. An earlier version of
    this called it synchronously right before the Screen 3 -> 4
    transition, which would have added a real API round-trip's worth
    of latency to the exact "zero friction" onboarding flow this
    product has been built around. Generating on first render instead
    means onboarding completion stays instant; the one-time cost is
    paid at the point where the person is already waiting on an API
    call anyway (the render itself), not added as a new wait on top of
    a step that was previously instant. Cached in session_state
    (voice_profile_summary) from then on — subsequent renders and the
    deepen-fingerprint panel both check for an existing value before
    calling this again.

    Cost guardrail, per standing rule, checked before this was built:
    minimum viable max_tokens (200 — this only needs to hold 3-5
    sentences), no auto-retry on failure, cached rather than
    regenerated on every render.

    Returns None on any failure — this is a quality enhancement, not
    a required part of the pipeline. A render with no distilled
    profile falls back to exactly what already existed before this
    feature: anchor sentences and numeric targets alone.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"] or api_key
    except Exception:
        pass
    if not api_key or not corpus_text or not corpus_text.strip():
        return None

    import anthropic
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=200,
            system=build_voice_profile_summary_prompt(),
            messages=[{"role": "user", "content": corpus_text}],
        )
        summary = response.content[0].text.strip()
        # Same deterministic backstop the render output gets (em dashes,
        # corporate filler, verbose openers, Claude-isms) - the prompt
        # guardrails above are the first line of defence, this is the
        # second. keep_contractions=True: this is a synthesised
        # description, not a copy of the person's own words, so there's
        # no baseline to check contraction usage against either way.
        summary = _regex_sweep(summary, keep_contractions=True)
        log.info("voice_profile_summary_generated", summary_length=len(summary))
        return summary
    except Exception:
        log.error("voice_profile_summary_generation_failed", exc_info=True)
        return None


def _run_render(
    input_text: str, is_refinement: bool = False, render_context: str = "",
    render_mode: str = "preserve", platform_format: str | None = None,
) -> bool:
    """The actual generation pipeline. Kept as one function so the
    refinement re-render below can call the same path.

    render_context: optional, per-render audience/purpose text ("who's
    this for, what's it for") from the field above the paste box on
    Screen 4. Steers generation only — deliberately never touches the
    numeric baseline targets (hedge_density, ownership, etc.), which
    stay verifying against the person's own blended voice regardless
    of register. See the field's own comment in screen_render() for
    why these are kept as two separate signals.

    render_mode: "preserve" (default) or "elevate", from the toggle
    above the paste box on Screen 4. Passed straight through to
    build_correction_prompt's mode parameter — see that function's
    docstring for what elevate mode actually does (line-editing only:
    old-to-new sentence ordering and economy, never restructuring).

    platform_format: opt-in, one of "social" or "email" (or None,
    off), only offered in the UI once "elevate" is selected (see
    screen_render) — passed straight through to build_correction_
    prompt, which itself re-checks mode == "elevate" before honouring
    it, so this parameter can never trigger paragraph restructuring
    through the preserve path even if a future caller passes it
    incorrectly. Originally LinkedIn-only, then generalised once it
    became clear the underlying convention wasn't LinkedIn-specific
    (see build_correction_prompt's docstring). Same as render_context,
    this doesn't touch the baseline targets.

    Returns True on success, False on failure. Callers must check this
    before treating the render as having happened (e.g. before marking
    the one-time refinement as used) — previously that flag was set
    unconditionally before this ran, so a failed call still burned the
    user's one refinement. Failure is reported via
    st.session_state.render_error rather than a direct st.error() call
    here, because the caller immediately triggers st.rerun() afterwards,
    which would wipe an error shown before it — session_state survives
    the rerun, a bare st.error() call does not.

    is_refinement / render_last_attempt / render_last_is_refinement are
    recorded here (not by each caller separately) so the retry button
    shown alongside render_error always has an exact copy of what was
    actually sent, whichever path failed — the original paste or a
    refinement request — without duplicating that bookkeeping at every
    call site.
    """
    st.session_state.render_last_attempt = input_text
    st.session_state.render_last_is_refinement = is_refinement
    st.session_state.render_error = None
    # Reset unconditionally here, not just inside the platform_format
    # branch further down — otherwise a True from an earlier
    # platform-formatted render could leak into a later render that
    # never touches platform_format at all (e.g. a subsequent
    # preserve-mode render).
    st.session_state.restructure_declined = False
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"] or api_key
    except Exception:
        pass

    if not api_key:
        st.session_state.render_error = "API key missing."
        log.error("render_failed", reason="api_key_missing", is_refinement=is_refinement)
        return False

    # Order matters here (27 Aug 2026 hardening pass): entitlement is
    # checked BEFORE the daily spend cap is reserved, not after. Was
    # the other way round until this fix — check_and_reserve_render()
    # (render_cap.py) reserves the daily counter optimistically,
    # before any API call, by its own design ("so a render that fails
    # partway through still counts"). With the old order, a free user
    # who'd already used all 15 lifetime renders would still reserve a
    # daily-cap slot on every retry, even though the lifetime check
    # right after it would immediately block the render and no API
    # call would ever happen — someone repeatedly hitting the paywall
    # could exhaust the site-wide daily budget with zero completions.
    # Resolving the free-or-lifetime-or-refinement question first, and
    # only reserving daily spend once a render is actually going to be
    # attempted, closes that.
    #
    # Step 4 (Section 5.2 / Section 13): the 15-lifetime-render free
    # tier. Resolved here, once, not just inside this check - the same
    # device_id is reused for the render_history write later in this
    # function (see the success path below), rather than each call
    # site resolving its own copy.
    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id
    # Render accounting: "one user render = original generation + its
    # included refinement, one lifetime-counter decrement, not two."
    # Only the ORIGINAL generation reserves a lifetime render; a
    # refinement of that same render is included in the one already
    # spent, not a second draw against the person's 15.
    if is_refinement:
        lifetime_allowed, lifetime_used, lifetime_limit = True, 0, 0
    else:
        lifetime_allowed, lifetime_used, lifetime_limit = check_and_reserve_lifetime_render(device_id)
        if not lifetime_allowed:
            st.session_state.render_error = (
                "You've used all 15 free renders. Upgrade to keep writing as you."
            )
            st.session_state.render_paywall_hit = True
            log.error(
                "render_blocked", reason="lifetime_cap_reached",
                used=lifetime_used, limit=lifetime_limit, is_refinement=is_refinement,
            )
            return False
    st.session_state.render_paywall_hit = False

    allowed, used, limit = check_and_reserve_render()
    if not allowed:
        st.session_state.render_error = (
            "We've hit today's render limit while VOICOVA is in early testing. "
            "Please try again tomorrow."
        )
        log.error("render_blocked", reason="daily_cap_reached", used=used, limit=limit, is_refinement=is_refinement)
        return False

    import anthropic

    detected_mode = _detect_mode(input_text)
    st.session_state.intent_mode = detected_mode
    log.info(
        "render_start", input_words=len(input_text.split()),
        is_refinement=is_refinement, detected_mode=detected_mode,
    )

    raw_text = st.session_state.get("raw_text", "")
    user_uses_em_dashes = len(re.findall(r"[—–\u2014\u2013]", raw_text)) > 0 if raw_text else False
    ai_score = _score_ai_signal(input_text, user_uses_em_dashes=user_uses_em_dashes)
    observations = st.session_state.observations
    baseline = st.session_state.get("baseline_fingerprint")
    # Per-register baseline (30 Aug 2026): if this render targets a
    # specific platform_format and a compounding baseline for that
    # exact format has accumulated enough words to be "established"
    # (>=800 words, the same threshold _build_restoration_targets
    # already uses to distinguish provisional from established), use
    # that instead of the blended one -- a person's email voice and
    # social voice compound independently rather than being flattened
    # into one register. Falls back to the existing blended baseline
    # unchanged whenever there's no platform_format, no per-format
    # data yet, or it's still too thin to trust over the established
    # blended one -- so this only ever changes behaviour once real
    # per-register data exists, never regresses the existing path.
    if platform_format:
        by_format = st.session_state.get("baseline_fingerprints_by_format") or {}
        format_baseline = by_format.get(platform_format)
        if format_baseline and format_baseline.get("word_count", 0) >= 800:
            baseline = format_baseline

    # Full corpus for voice DNA extraction: screen 1 paste + the four
    # starter completions. The starters used to feed only the numeric
    # baseline and the contraction check - their sentences never reached
    # anchor-sentence, vocabulary-fingerprint, or function-pattern
    # extraction. Fixed: they're candid, unperformed writing and belong
    # in the same corpus that shapes the render.
    fingerprint_corpus = raw_text + " " + " ".join(st.session_state.get("sample2_completions", []))
    fingerprint_corpus = fingerprint_corpus.strip()

    # Lazy, cached distillation call — see _generate_voice_profile_summary's
    # docstring for why this happens here (first render) rather than at
    # Screen 3 completion (would add latency to what's meant to be an
    # instant onboarding step). Only fires once per baseline; a render
    # while it's still None just proceeds without it (fail-open, same
    # standard as everywhere else new this session — this is a quality
    # enhancement, never a blocker).
    if baseline and not st.session_state.get("voice_profile_summary"):
        summary = _generate_voice_profile_summary(fingerprint_corpus or raw_text)
        if summary:
            st.session_state.voice_profile_summary = summary
            save_profile_if_available()

    voice_dna = _build_voice_dna(observations, fingerprint_corpus or raw_text, baseline, ai_score)
    mode_instruction = apply_intent_mode(input_text, detected_mode)
    word_count_input = len(input_text.split())

    # Baseline-driven, not assumed: does this person's own writing use
    # contractions? Only strip them from the output if their own writing
    # doesn't have them either.
    keep_contractions = uses_contractions(fingerprint_corpus) if fingerprint_corpus else False

    # Same signal used to gate the Ownership/Directness restoration targets
    # and their correction-pass counterparts: does THIS input (not the
    # user's baseline corpus) have any first-person or directive content
    # of its own to convert? Computed once, reused at both gates each so
    # they can't drift apart on the same render.
    input_metrics_signal = compute_baseline_metrics(input_text)
    input_has_opinion_content = input_metrics_signal["first_person_ratio"] > 0
    input_has_directive_content = input_metrics_signal["directive_ratio"] > 0

    system = _build_system_prompt(
        voice_dna=voice_dna, mode_instruction=mode_instruction,
        word_count_input=word_count_input, ai_score=ai_score, baseline=baseline,
        input_text=input_text, render_context=render_context,
        voice_profile_summary=st.session_state.get("voice_profile_summary", ""),
        platform_format=platform_format,
        locale=st.session_state.get("locale", "uk"),
    )

    client = anthropic.Anthropic(api_key=api_key)
    try:
        with st.spinner("Writing as you..."):
            response = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=4096,
                system=system, messages=[{"role": "user", "content": input_text}],
            )
            clean = response.content[0].text
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
            if st.session_state.get("locale", "uk") == "uk":
                clean = _apply_uk_english(clean)
            clean = _grammar_fix_pass(clean, client, locale=st.session_state.get("locale", "uk"))
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
    except Exception:
        st.session_state.render_error = (
            "That didn't go through. Your text is safe, try again."
        )
        log.error("render_failed", reason="llm_call_exception", stage="initial_render", exc_info=True)
        # Release-on-failure: the lifetime cap reserved this render
        # optimistically, before this API call ran (see
        # check_and_reserve_lifetime_render's own docstring). If the
        # call itself failed, the person never got a render out of it
        # and shouldn't lose one of their 15 for VOICOVA's own API
        # failure. release_reserved_lifetime_render is self-contained
        # and safe to call unconditionally for an original render — it
        # no-ops for an active subscriber and fails open silently on
        # any Supabase error, same as the rest of that module.
        # Guarded on is_refinement: a refinement never reserved a
        # lifetime render in the first place, so releasing one on its
        # failure would wrongly hand back a slot from an earlier,
        # successful original render instead.
        if not is_refinement:
            release_reserved_lifetime_render(device_id)
        return False

    # Case-only entity drift — deterministic, runs before ANY scoring so
    # a defect that's mechanically fixable never reaches score_semantic_
    # drift's dropped_entities check (and therefore never trips compute_
    # risk's dropped_entities hard-fail, never gets sent to the LLM
    # correction pass, never appears to the user at all). Confirmed live:
    # a render kept a brand name's letters but not its casing ("CLEARANCE"
    # -> "Clearance") and that alone was enough to force a High risk
    # verdict on an otherwise clean render. See _fix_entity_casing's
    # docstring for why this is safe to apply unconditionally (whole-word,
    # case-only substitution, never touches word choice or count).
    clean, casing_restored, casing_still_dropped = _fix_entity_casing(clean, input_text)
    if casing_restored:
        log.info(
            "entity_casing_restored",
            restored=casing_restored,
            still_dropped=casing_still_dropped,
        )

    # Diff-preserving guard on the initial render pass — same check the
    # correction pass already runs (see _check_uncorrected_insertions's
    # docstring), applied here for the first time. Two LLM calls sit
    # upstream of this point (the voice-transformation render and the
    # grammar-fix pass) and neither had a diff against the true source
    # text before now — score_render_delta below is an aggregate band
    # check against the baseline fingerprint, not a diff against THIS
    # input, so a fabricated sentence or an invented hedge can land
    # inside a passing band and go unflagged. Diffing against input_text
    # (not an intermediate) catches both calls in one pass, and catches
    # it at the earliest point content exists to diff.
    initial_insertion_check = _check_uncorrected_insertions(input_text, clean)
    log.info(
        "initial_render_insertion_check",
        new_hedges=initial_insertion_check["new_hedges"],
        sentence_growth=initial_insertion_check["sentence_growth"],
        flagged=initial_insertion_check["flagged"],
        scoring_rules_version=scoring_rules_version(),
    )
    st.session_state.render_insertion_check = initial_insertion_check

    if baseline:
        delta = score_render_delta(baseline, clean)
        semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)

        # Second, independent check against the starter-only baseline
        # (unblended with sample1) - catches drift the blended average
        # can dilute below the correction threshold. Rule-based, no
        # extra API call: reuses score_render_delta, only widens what
        # feeds the one correction call that already fires conditionally.
        starter_baseline = st.session_state.get("starter_baseline")
        starter_delta = score_render_delta(starter_baseline, clean) if starter_baseline else None
        correction_delta = merge_starter_evidence(delta, starter_delta)

        # Deterministic fixer pass — runs before the LLM correction call,
        # not instead of it. Each fixer only fires on the one direction
        # it can safely handle (see deterministic_fixers.py); anything
        # it declines is left for build_correction_prompt() below, same
        # as before this pass existed. No API call, no meaning risk
        # beyond what each fixer's own docstring already accepts.
        if correction_delta.get("hedge_density", {}).get("verdict") == "MISSED":
            d = correction_delta["hedge_density"]
            clean, hedge_fixed = _fix_hedge_density(clean, d["baseline"], d["output"])
            clean, modal_fixed = _fix_modal_hedge(clean, d["baseline"], d["output"])
        else:
            hedge_fixed = modal_fixed = False
        if correction_delta.get("sentence_length_sd", {}).get("verdict") == "MISSED":
            d = correction_delta["sentence_length_sd"]
            clean, rhythm_fixed = _fix_sentence_length_sd(clean, d["baseline"], d["output"])
        else:
            rhythm_fixed = False
        if correction_delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            d = correction_delta["first_person_ratio"]
            clean, ownership_fixed = _fix_first_person_ratio(
                clean, d["baseline"], d["output"], input_has_opinion_content
            )
            # Companion fixer, opposite direction — see its own
            # docstring for why this gap existed. Each fixer declines
            # independently based on current vs target, so calling
            # both unconditionally is safe: at most one actually fires.
            clean, ownership_over_fixed = _fix_first_person_over_ratio(
                clean, d["baseline"], d["output"], input_text
            )
            # General, alignment-based fallback — runs after the
            # pattern-based fixer above, not instead of it, since it
            # needs the same sentence-alignment machinery but answers
            # a structurally different question (does this sentence
            # have a marker the original didn't have at all, regardless
            # of specific wording) rather than matching a known verb
            # pattern. Catches whatever the pattern fixer's enumerated
            # list doesn't — see restore_fabricated_ownership_sentences'
            # own docstring for why pattern enumeration alone can never
            # be complete for this failure class. Safe to always run:
            # it only ever touches a sentence where the aligned
            # original had zero first-person markers, so it can't
            # touch anything the fixer above already correctly left
            # alone.
            clean, ownership_restored = restore_fabricated_ownership_sentences(clean, input_text)
            ownership_fixed = ownership_fixed or ownership_over_fixed or ownership_restored
        else:
            ownership_fixed = False
        if correction_delta.get("directive_ratio", {}).get("verdict") == "MISSED":
            d = correction_delta["directive_ratio"]
            clean, directive_fixed = _fix_directive_ratio(
                clean, d["baseline"], d["output"], input_has_directive_content
            )
        else:
            directive_fixed = False
        log.info(
            "deterministic_fixers_applied",
            hedge_density=hedge_fixed, modal_hedge=modal_fixed,
            sentence_length_sd=rhythm_fixed, first_person_ratio=ownership_fixed,
            directive_ratio=directive_fixed,
        )

        # Re-score after the deterministic pass so the LLM correction
        # call — if still needed — only targets what genuinely survived
        # (residual modal hedges, noun-phrase subjects, non-imperative
        # wrappers, etc. — the directions each fixer declines on
        # purpose), not dimensions the deterministic pass already fixed.
        clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
        if st.session_state.get("locale", "uk") == "uk":
            clean = _apply_uk_english(clean)
        delta = score_render_delta(baseline, clean)
        semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)
        starter_delta = score_render_delta(starter_baseline, clean) if starter_baseline else None
        correction_delta = merge_starter_evidence(delta, starter_delta)

        # Only computed in elevate mode — preserve mode does none of
        # this extra work and build_correction_prompt's own params
        # default to None, so preserve-mode behaviour is byte-for-byte
        # what it was before these signals existed.
        sentence_economy = None
        passive_voice = None
        if render_mode == "elevate":
            sentence_economy = compute_sentence_economy(clean)
            passive_voice = compute_passive_voice(clean)

        correction_prompt = build_correction_prompt(
            correction_delta, semantic, input_has_opinion_content, input_has_directive_content,
            mode=render_mode, sentence_economy=sentence_economy, passive_voice=passive_voice,
            platform_format=platform_format,
            locale=st.session_state.get("locale", "uk"),
            user_uses_em_dashes=user_uses_em_dashes,
        )
        log.info(
            "correction_pass_decision",
            llm_correction_needed=bool(correction_prompt),
            missed_dimensions=[k for k, d in correction_delta.items() if d["verdict"] == "MISSED"],
        )
        # Seeded from the initial-render check above, not None — that
        # check already covers the voice-transformation and grammar-fix
        # calls; this block, if it runs, adds what the correction call
        # introduces on top. Merged rather than overwritten below so a
        # sentence fabricated at either stage still surfaces as risk —
        # whichever call invented it, compute_risk needs to see it.
        insertion_check = initial_insertion_check
        if correction_prompt:
            try:
                pre_llm_correction = clean
                corrected = None
                # Forced tool call, not a plain text completion — the
                # model returns corrected_text as a schema field, so
                # there's no free-text channel for it to narrate
                # reasoning into. One bounded retry underneath as a
                # safety net (response_looks_contaminated), then fail
                # closed to pre_llm_correction rather than ship a
                # response that failed the check twice. Flagged: not
                # verified against live model behaviour in this
                # session — no Anthropic key available here — needs a
                # real render on Railway to confirm the tool call
                # behaves as expected before this is trusted.
                for attempt in range(2):
                    with st.spinner("Refining..."):
                        correction_response = client.messages.create(
                            model="claude-sonnet-4-6", max_tokens=4096,
                            system=correction_prompt,
                            messages=[{"role": "user", "content": clean}],
                            tools=[CORRECTION_TOOL],
                            tool_choice={"type": "tool", "name": "return_correction"},
                        )
                    tool_use_block = next(
                        (b for b in correction_response.content if b.type == "tool_use"),
                        None,
                    )
                    if tool_use_block is None:
                        log.error(
                            "correction_pass_no_tool_use",
                            attempt=attempt,
                            stop_reason=correction_response.stop_reason,
                        )
                        continue
                    candidate = tool_use_block.input.get("corrected_text", "")
                    if candidate and not response_looks_contaminated(candidate):
                        corrected = candidate
                        break
                    log.error(
                        "correction_pass_contaminated_response",
                        attempt=attempt,
                        candidate_preview=candidate[:200],
                    )
                if corrected is None:
                    log.error("correction_pass_failed_both_attempts")
                    corrected = pre_llm_correction
                corrected = _regex_sweep(corrected, keep_contractions=keep_contractions, original_input_text=input_text)
                if st.session_state.get("locale", "uk") == "uk":
                    corrected = _apply_uk_english(corrected)
                clean = corrected

                # Word-level fidelity check, platform_format only:
                # verifies the model actually obeyed "rearrange, don't
                # rewrite" rather than trusting the instruction alone.
                # Confirmed necessary against a real render that
                # restructured two declarative sentences into a "When
                # X... When Y..." conditional, introducing "when" and
                # "occurs" — real rewriting, not rearrangement, despite
                # the explicit instruction against it. Fails CLOSED on
                # the restructuring specifically, not the whole render:
                # reverts to pre_llm_correction (already voice-correct,
                # ownership-fixed, just not platform-formatted) rather
                # than ship fabricated wording. Surfaced honestly to
                # the user below (restructure_declined), not silently
                # swapped. Applies to both platform_format targets
                # (social and email) equally — the check itself is
                # generic (any new word is a problem), it doesn't care
                # which instruction produced the restructuring.
                if platform_format in ("social", "email"):
                    fidelity = score_restructure_fidelity(pre_llm_correction, clean)
                    if not fidelity["clean"]:
                        log.error(
                            "platform_restructure_fidelity_failed",
                            platform_format=platform_format,
                            fabricated_words=fidelity["fabricated_words"],
                        )
                        clean = pre_llm_correction
                        st.session_state.restructure_declined = True

                # Catches collateral the LLM correction call introduced
                # as a side effect of fixing its target dimension — see
                # _check_uncorrected_insertions's docstring for why the
                # delta re-score two lines below can't catch this on its
                # own (aggregate band check, not a before/after diff).
                # Merged with the initial-render check rather than
                # replacing it, so growth/hedges from either stage carry
                # through to compute_risk below.
                correction_insertion_check = _check_uncorrected_insertions(pre_llm_correction, clean)
                insertion_check = {
                    "new_hedges": insertion_check["new_hedges"] + correction_insertion_check["new_hedges"],
                    "sentence_growth": insertion_check["sentence_growth"] + correction_insertion_check["sentence_growth"],
                    "flagged": insertion_check["flagged"] or correction_insertion_check["flagged"],
                }
                if correction_insertion_check["new_hedges"]:
                    # Same fixers already used earlier in this pass, same
                    # safe-deletion-only contract — no new correction
                    # logic, just running them again on what the LLM call
                    # added. Targets are irrelevant here (recompute from
                    # the diff itself: any new hedge is by definition over
                    # whatever the LLM was told to hold), so pass current
                    # count vs 0 to force the over-hedged branch.
                    new_hedge_count = len(correction_insertion_check["new_hedges"])
                    clean, _ = _fix_hedge_density(clean, 0, new_hedge_count)
                    clean, _ = _fix_modal_hedge(clean, 0, new_hedge_count)
                log.info(
                    "correction_pass_side_effect_caught",
                    new_hedges=correction_insertion_check["new_hedges"],
                    sentence_growth=correction_insertion_check["sentence_growth"],
                    flagged=correction_insertion_check["flagged"],
                )

                delta = score_render_delta(baseline, clean)
                semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)
            except Exception:
                log.error("correction_pass_llm_failed", stage="correction", exc_info=True)
                pass  # correction pass failed — keep the original render

        # Verify-and-retry gate, not instruct-and-trust: the LLM
        # correction call above is a request, not a guarantee — an
        # instruction can be partially followed or missed entirely,
        # which is exactly what re-scoring delta afterward is for. This
        # was previously the one place in the correction pass that
        # re-scored but never acted on the result — the ai_tells check
        # a few lines below already does this correctly (measure, and
        # if still not clean, run one more free deterministic pass
        # rather than reporting a result that didn't actually land).
        # Mirrors that same pattern here: bounded to one extra pass, no
        # additional API call, so this can't run away on cost. Each
        # fixer already independently checks its own dimension's
        # verdict and declines outright if it isn't MISSED or the
        # direction isn't its safe one, so this is safe to call
        # unconditionally rather than gating per-dimension twice.
        still_missed = [k for k, d in delta.items() if d["verdict"] == "MISSED"]
        if still_missed:
            if "hedge_density" in still_missed:
                d = delta["hedge_density"]
                clean, _ = _fix_hedge_density(clean, d["baseline"], d["output"])
                clean, _ = _fix_modal_hedge(clean, d["baseline"], d["output"])
            if "sentence_length_sd" in still_missed:
                d = delta["sentence_length_sd"]
                clean, _ = _fix_sentence_length_sd(clean, d["baseline"], d["output"])
            if "first_person_ratio" in still_missed:
                d = delta["first_person_ratio"]
                clean, _ = _fix_first_person_ratio(
                    clean, d["baseline"], d["output"], input_has_opinion_content
                )
                clean, _ = _fix_first_person_over_ratio(
                    clean, d["baseline"], d["output"], input_text
                )
                # See the initial-pass call site above for why this
                # general fallback runs unconditionally after the
                # pattern-based fixer, not instead of it.
                clean, _ = restore_fabricated_ownership_sentences(clean, input_text)
            if "directive_ratio" in still_missed:
                d = delta["directive_ratio"]
                clean, _ = _fix_directive_ratio(
                    clean, d["baseline"], d["output"], input_has_directive_content
                )
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
            if st.session_state.get("locale", "uk") == "uk":
                clean = _apply_uk_english(clean)
            delta = score_render_delta(baseline, clean)
            semantic = score_semantic_drift(input_text, clean, platform_format=platform_format)
            log.info(
                "post_correction_verify_pass",
                still_missed_before=still_missed,
                still_missed_after=[k for k, d in delta.items() if d["verdict"] == "MISSED"],
            )

        # Measured verification gate, not a trusted fix-and-hope step.
        # If anything survived the sweeps, run one more deterministic
        # pass (free, no API call) and re-measure. If it's still not
        # clean after that, say so honestly in the report rather than
        # shipping AI-tell contaminated text as if it were verified.
        # original_input_text=input_text: exempts phrases genuinely
        # present in the person's own input from being flagged as an
        # AI tell — see score_ai_tells' docstring for the real
        # false-positive this fixes ("curious whether", "i suspect",
        # "i would push back" all appeared verbatim in a real original
        # input and were flagged anyway).
        ai_tells = score_ai_tells(clean, original_input_text=input_text)
        if not ai_tells["clean"]:
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
            ai_tells = score_ai_tells(clean, original_input_text=input_text)

        # Downgrade MISSED -> SKIPPED for dimensions the input never
        # had content for in the first place (input_has_opinion_content
        # / input_has_directive_content, same signals that already gate
        # whether a correction is even attempted, see line ~1077). A
        # genuine 86% ownership "miss" on a product-pitch email with
        # zero first-person content in the original isn't a defect —
        # it's the correction pass correctly refusing to fabricate
        # first-person claims that would misrepresent authorship. Risk/
        # confidence and the report sentence should say so, not flag it
        # identically to an achievable target the system failed to hit.
        if not input_has_opinion_content and delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            delta["first_person_ratio"]["verdict"] = "SKIPPED"
            delta["first_person_ratio"]["skip_reason"] = "no_content"
        if not input_has_directive_content and delta.get("directive_ratio", {}).get("verdict") == "MISSED":
            delta["directive_ratio"]["verdict"] = "SKIPPED"
            delta["directive_ratio"]["skip_reason"] = "no_content"

        # Mirror case, OVER-owned direction: input DOES have opinion
        # content (so the block above didn't apply), but is more
        # opinion-dense than the person's baseline. Confirmed against a
        # real render: a 72% ownership drift on a genuinely opinionated
        # email dropped to ~37% after both fixer passes ran their full
        # course, and every remaining first-person sentence checked out
        # as the person's own genuine wording, not a defect — an
        # initially-proposed fix (restoring exact original wording for
        # the unfixable sentences) turned out to change nothing, since
        # first_person_ratio counts sentences, not words, and the
        # original wording was ALSO first-person in every case. See
        # ownership_miss_is_content_driven's docstring for the full
        # reasoning. Only checked once still MISSED after everything
        # else has already run, so this never short-circuits a genuine,
        # achievable fix the fixer just hasn't gotten to yet.
        #
        # skip_reason="content_ceiling" (distinct from "no_content"
        # above) — a real, live bug found the same session this was
        # tested: the report was reusing "nothing to convert in the
        # original" for THIS case too, which is actively wrong (this
        # input has abundant opinion content, that's the whole point —
        # it just can't be reduced further without deleting real
        # content). voice_match_label and build_voice_report both read
        # this field to produce distinct, accurate messaging per
        # reason instead of one generic SKIPPED explanation for two
        # very different situations.
        if delta.get("first_person_ratio", {}).get("verdict") == "MISSED":
            if ownership_miss_is_content_driven(clean, input_text):
                delta["first_person_ratio"]["verdict"] = "SKIPPED"
                delta["first_person_ratio"]["skip_reason"] = "content_ceiling"

        confidence = compute_confidence(
            st.session_state.get("sample_fitness"), baseline, len(observations),
            st.session_state.get("dimension_stability"),
        )
        risk = compute_risk(delta, semantic, ai_tells, insertion_check)
        risk_reason = compute_risk_reason(delta, semantic, ai_tells, insertion_check)
        # The ONLY thing that gates the rewritten text behind
        # review_gate.py's confirmation wall — see
        # has_content_integrity_hard_fail's docstring. risk above still
        # reflects style-drift severity too (informational badge), but
        # style drift alone must never block delivery.
        content_integrity_hard_fail = has_content_integrity_hard_fail(semantic, ai_tells, insertion_check)
        # Overwrite with the FINAL, merged insertion_check (initial
        # render + correction-pass side effects) — line ~1165 above
        # sets this to the INITIAL check only, before the correction
        # pass runs, and was never updated afterward. compute_risk
        # above already correctly uses the merged version; nothing
        # previously persisted it anywhere the UI could read it back,
        # so any check built on "did the correction pass add a
        # sentence" would have silently seen only half the picture.
        # Found while scoping Content Lock's "no sentences invented"
        # check — this gap existed before that feature, not
        # introduced by it.
        st.session_state.render_insertion_check = insertion_check
        # Persisted (not recomputed at click-time) for the same reason
        # as render_insertion_check above: the AI-Slop Firewall "Clean
        # it up" action needs the exact keep_contractions value THIS
        # render actually used, not a fresh recomputation that could
        # drift if the underlying baseline corpus changes between the
        # render and the click.
        st.session_state.render_keep_contractions = keep_contractions
        log.info(
            "render_complete", is_refinement=is_refinement,
            confidence=confidence.get("level") if isinstance(confidence, dict) else confidence,
            risk=risk.get("level") if isinstance(risk, dict) else risk,
            risk_reason=risk_reason,
            scoring_rules_version=scoring_rules_version(),
            ai_tells_clean=ai_tells["clean"],
            missed_dimensions=[k for k, d in delta.items() if d["verdict"] == "MISSED"],
        )
        # Correction-frequency instrumentation: all four inputs are
        # already in scope at this point in _run_render, computed
        # earlier in this same function - hedge_fixed/modal_fixed/
        # rhythm_fixed/ownership_fixed/directive_fixed from the
        # deterministic fixer pass (~line 1412), correction_prompt from
        # build_correction_prompt (~line 1488), content_integrity_
        # hard_fail just above. Checked in this order because a hard
        # fail is the most severe outcome regardless of what else
        # happened during the render.
        if content_integrity_hard_fail:
            correction_tier = "hard_fail"
        elif correction_prompt:
            correction_tier = "llm_correction"
        elif hedge_fixed or modal_fixed or rhythm_fixed or ownership_fixed or directive_fixed:
            correction_tier = "deterministic_only"
        else:
            correction_tier = "none"
        log_render_event(
            risk=risk.get("level") if isinstance(risk, dict) else risk,
            risk_reason=risk_reason,
            semantic_match=semantic.get("semantic_match") if semantic else None,
            missed_dimensions=sum(1 for d in delta.values() if d["verdict"] == "MISSED"),
            ai_tells_clean=ai_tells["clean"],
            is_refinement=is_refinement,
            scoring_rules_version=scoring_rules_version(),
            correction_tier=correction_tier,
        )

        # Second, independently-grounded voice-match signal alongside
        # the four-heuristic delta above — see compute_burrows_delta's
        # docstring for why function-word frequency distance is a
        # genuinely different measurement, not a restatement. Needs
        # 2+ raw baseline samples to compute a real reference
        # distribution; with fewer (most users who haven't gone
        # through the Screen 3 starters flow), it correctly reports
        # "Insufficient baseline samples" rather than guessing.
        baseline_texts = st.session_state.get("fingerprint_sample_texts", [])
        burrows_delta = compute_burrows_delta(baseline_texts, clean)

        st.session_state.render_delta = delta
        st.session_state.semantic_drift = semantic
        st.session_state.confidence = confidence
        st.session_state.risk = risk
        st.session_state.risk_reason = risk_reason
        st.session_state.ai_tells = ai_tells
        st.session_state.function_word_delta = burrows_delta
        st.session_state.voice_report = build_voice_report(
            delta, semantic, confidence, risk, ai_tells, burrows_delta,
            content_integrity_hard_fail=content_integrity_hard_fail,
        )
        # One id + timestamp per completed render — generated here
        # (not inside authenticity_report.py, which stays a pure
        # function) so the authenticity report built from this render
        # can be uniquely referenced without needing the render text
        # itself. Regenerated on every render/refinement, same as
        # voice_report above — never reused across renders.
        st.session_state.render_id = str(uuid.uuid4())
        st.session_state.render_completed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Section 9.4 / Step 4 (VOICOVA_Product_2.0_Consolidated.docx):
        # write path for the History screen. Called here, after the
        # render has fully succeeded and the voice report is built —
        # matches write_render_history's own docstring, which is
        # explicit that this must never run before success is known.
        # Fails open and silently inside write_render_history itself,
        # so no try/except needed at this call site. device_id already
        # resolved once, near the top of this function (see the
        # lifetime-cap check above) - reused here, not re-resolved.
        write_render_history(
            device_id=device_id,
            input_text=input_text,
            output_text=clean,
            context=render_context,
            mode=render_mode,
            # voice_match_tier, not voice_match_badge - the latter is a
            # CSS class name (e.g. "badge-green"), not a display label.
            # See voice_match_tier's use in screen_render's own HTML
            # (vm_tier) for the human-readable string this mirrors.
            voice_match=st.session_state.voice_report.get("voice_match_tier"),
            content_lock_pass=not content_integrity_hard_fail,
        )
    else:
        st.session_state.render_delta = None
        st.session_state.voice_report = None
        st.session_state.render_id = None
        st.session_state.render_completed_at = None
        st.session_state.render_insertion_check = None
        st.session_state.render_keep_contractions = None

    st.session_state.render_output = clean
    return True


def _build_what_changed_html(biggest_changes: list[str]) -> str:
    """
    Compact 'What changed' chip row — dimension name plus direction
    only (\u2191/\u2193), not the raw percentage. Reads biggest_changes,
    already computed by build_voice_report as "Label +NN%"/"Label
    -NN%" strings; this only reformats them for the leading summary
    position in the report card. The full percentage figures still
    appear in the Voice Match table further down for anyone who wants
    them — this is the "here's what you need to know" layer above it,
    same principle as the Content Lock banner.

    Falls back to a plain "no drift" line when biggest_changes is
    empty, same wording the old inline sentence used, so nothing reads
    as broken when a render matches baseline cleanly.
    """
    if not biggest_changes:
        return '<div class="what-changed-empty">No significant drift from your baseline.</div>'

    chips = []
    for change in biggest_changes[:3]:
        match = re.match(r"^(.*?)\s([+-])\d+%$", change)
        if not match:
            chips.append(f'<span class="what-changed-chip">{change}</span>')
            continue
        label, sign = match.group(1), match.group(2)
        arrow = "\u2191" if sign == "+" else "\u2193"
        chips.append(f'<span class="what-changed-chip">{label} {arrow}</span>')

    return '<div class="what-changed-row">' + "".join(chips) + '</div>'


def _sentence_growth_label(count: int) -> str:
    """Shared phrasing for both the banner and the full checklist, so
    they can never say this two different ways. Plain pluralisation
    instead of the "sentence(s)" construct that was here before. The
    reported count itself is still the raw sentence-count delta from
    _check_uncorrected_insertions - an attempt to attribute it to
    specific sentences was tried and reverted after it proved
    unreliable against heavily-paraphrased real text (see that
    function's own docstring)."""
    noun = "sentence" if count == 1 else "sentences"
    return f"Added {count} new {noun} not in the original"


def _confidence_signal_html(tier: str) -> str:
    """Neutral signal-strength meter for the Confidence badge. Three
    bars, filled left-to-right by tier, in
    plain ink — never red/amber/green, since Confidence never gates a
    render and shouldn't visually read as an alert the way Risk does.
    """
    fill_count = {"Low": 1, "Medium": 2, "High": 3}.get(tier, 1)
    bar_heights = (7, 11, 15)
    bars = "".join(
        f'<span class="signal-bar{" filled" if i < fill_count else ""}" '
        f'style="height:{bar_heights[i]}px;"></span>'
        for i in range(3)
    )
    return (
        f'<span class="signal-bars">'
        f'<span class="signal-bars-marks">{bars}</span>{tier}</span>'
    )


_RISK_ICON = {
    "Low": "",
    "Medium": '<svg class="badge-icon" width="11" height="11" viewBox="0 0 16 16" '
              'xmlns="http://www.w3.org/2000/svg"><path d="M8 1l7 13H1L8 1z" '
              'fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>'
              '<circle cx="8" cy="11.3" r="0.9" fill="currentColor"/></svg>',
    "High": '<svg class="badge-icon" width="11" height="11" viewBox="0 0 16 16" '
            'xmlns="http://www.w3.org/2000/svg"><path d="M8 1l7 13H1L8 1z" '
            'fill="currentColor" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>'
            '<rect x="7.3" y="5.5" width="1.4" height="4" fill="var(--canvas)"/>'
            '<circle cx="8" cy="11.3" r="0.9" fill="var(--canvas)"/></svg>',
}


def _build_content_lock_banner_html(report: dict, insertion_check: dict | None) -> str:
    """
    Leading summary state for Content Lock — 'CONTENT SAFE' or 'NEEDS
    YOUR EYES', shown at the top of the report card rather than the
    full four-row checklist buried at the bottom. Reads the exact same
    signals as _build_content_lock_html and has_content_integrity_
    hard_fail (dropped_entities, attribution_swaps, sentence_growth,
    new_hedges) — this adds no new detection, it's a second, higher-
    prominence view of data already computed. The full checklist below
    still renders for anyone who wants the row-by-row detail; this is
    the "here's what you need to know" layer above it, Content Lock as
    visible status rather than a buried diagnostic.

    Reasons list mirrors _build_content_lock_html's four checks so the
    banner's summary and the checklist below it can never disagree
    about what failed.

    lexical_fidelity_breaks is deliberately NOT one of the four
    "reasons" above and never flips this banner to fail — that would
    contradict the explicit decision (detect_lexical_fidelity_breaks'
    own docstring) that a watchlist hit is informational, not a
    content-integrity failure. It renders as its own amber note, in
    the SAME banner position either state lands in, so it's visible
    either way rather than silently swallowed - but it's styled and
    worded as a lower-severity notice, not folded into the red fail
    state or the checklist below it.
    """
    dropped = report.get("dropped_entities", [])
    swaps = report.get("attribution_swaps", [])
    lexical_breaks = report.get("lexical_fidelity_breaks", [])
    sentence_growth = (insertion_check or {}).get("sentence_growth", 0)
    new_hedges = (insertion_check or {}).get("new_hedges", [])

    reasons = []
    if dropped:
        reasons.append(f"Facts dropped: {_safe_html(', '.join(dropped))}")
    if swaps:
        reasons.append("Attribution may have changed. Check before sending.")
    if sentence_growth:
        reasons.append(_safe_html(_sentence_growth_label(sentence_growth)))
    if new_hedges:
        reasons.append(f"New hedging added: {_safe_html(', '.join(new_hedges))}")

    note_html = ""
    if lexical_breaks:
        note_lines = "".join(
            f'<div class="content-lock-banner-note">\u26a0 Worth a look: {_safe_html(b)}</div>'
            for b in lexical_breaks
        )
        note_html = note_lines

    if reasons:
        reason_html = "".join(f'<div class="content-lock-banner-reason">{r}</div>' for r in reasons)
        return (
            '<div class="content-lock-banner fail">'
            '<div class="content-lock-banner-title">\u26a0 Needs your eyes</div>'
            f'{reason_html}'
            f'{note_html}'
            '</div>'
        )

    return (
        '<div class="content-lock-banner pass">'
        '<div class="content-lock-banner-title">\u2713 Content safe</div>'
        '<div class="content-lock-banner-reason">Facts, attribution, and structure preserved.</div>'
        f'{note_html}'
        '</div>'
    )


def _build_content_lock_html(report: dict, insertion_check: dict | None) -> str:
    """
    Renders the Content Lock checklist — 'voice can change, meaning
    can't' made checkable rather than asserted. Every check here reads
    from data already computed and already tested elsewhere in this
    file; this function only formats it, it adds no new detection
    logic of its own.

    Four checks, not five. The original design brief (a market-
    landscape review) proposed five: names preserved,
    numbers preserved, attribution preserved, no new claims detected,
    no sentences invented. "No new claims detected" doesn't map
    cleanly onto any single measured signal — score_semantic_drift
    catches DROPPED content, not fabricated content in general (that's
    what sentence_growth and score_ai_tells cover, from different
    angles), so a checkbox literally labelled "no new claims" would be
    asserting more certainty than the underlying measurement supports.
    Rather than ship a plausible-sounding checkbox with weak backing,
    this uses new_hedges (also a real, tested signal — softened
    qualifiers the correction pass added as a side effect of fixing
    something else) under its own honest label instead. Four accurate
    checks beat five where one is aspirational.

    Names/numbers are reported together as "Facts preserved" rather
    than split into two rows: _entities_and_numbers (voice_engine.py)
    already tags both under one dropped_entities list without
    distinguishing type, so splitting them here would require guessing
    at a distinction the underlying data doesn't actually carry.
    """
    dropped = report.get("dropped_entities", [])
    swaps = report.get("attribution_swaps", [])
    sentence_growth = (insertion_check or {}).get("sentence_growth", 0)
    new_hedges = (insertion_check or {}).get("new_hedges", [])

    checks = [
        (not dropped, "Facts preserved",
         f"{len(dropped)} dropped: {_safe_html(', '.join(dropped))}" if dropped else None),
        (not swaps, "Attribution preserved",
         "Whose point this was may have changed. Check before sending." if swaps else None),
        (sentence_growth == 0, "No sentences invented",
         _safe_html(_sentence_growth_label(sentence_growth)) if sentence_growth else None),
        (not new_hedges, "No new hedging introduced",
         f"Added: {_safe_html(', '.join(new_hedges))}" if new_hedges else None),
    ]

    rows = []
    for passed, label, detail in checks:
        state = "pass" if passed else "fail"
        mark = "\u2713" if passed else "\u2717"
        detail_html = f": {detail}" if detail else ""
        rows.append(
            f'<div class="content-lock-item {state}">'
            f'<span class="content-lock-mark">{mark}</span>'
            f'<span>{label}{detail_html}</span>'
            f'</div>'
        )

    return (
        '<div class="content-lock">'
        '<div class="content-lock-title">Content Lock</div>'
        + "".join(rows) +
        '</div>'
    )


# Mirrors voice_engine.py's own _DIMENSION_LABELS exactly, for display
# consistency with the evidence sentence ("Held on hedging, ..."). Not
# imported directly — that dict is underscore-prefixed (module-private
# by convention) and this file already treats voice_engine as a
# measurement library, not something to reach into private internals
# of; a small duplicated mapping for four stable, rarely-changing
# dimension names is a fair trade against that boundary violation.
_VOICE_MATCH_LABELS = {
    "hedge_density": "Hedging",
    "sentence_length_sd": "Sentence rhythm",
    "first_person_ratio": "Ownership (first person)",
    "directive_ratio": "Directness",
}

_VOICE_MATCH_VERDICT_BADGE = {
    "HIT": "badge-green", "CLOSE": "badge-amber",
    "MISSED": "badge-red", "SKIPPED": "badge-amber",
}


def _format_voice_match_value(dimension: str, value: float) -> str:
    """
    Per-dimension formatting, not one blanket formatter — hedge_density
    is already expressed on a percentage-like scale by compute_baseline_
    metrics (e.g. 4.0 meaning 4%, confirmed against real baseline
    output), while first_person_ratio/directive_ratio are raw 0-1
    proportions of sentences, and sentence_length_sd is a word-count
    standard deviation with no percentage meaning at all. Using one
    format string for all four would either show hedge_density as
    "400%" or the two ratios as "0%" for anything under 1% — both
    wrong, found by actually checking real output values rather than
    assuming a shared scale.
    """
    if dimension == "sentence_length_sd":
        return f"{value:.1f}"
    if dimension == "hedge_density":
        return f"{value:.1f}%"
    return f"{value:.0%}"


def _build_voice_match_table_html(delta: dict) -> str:
    """
    The four measured dimensions, baseline vs. this render, side by
    side — the actual per-dimension evidence behind the single
    'Voice consistency: Good' badge shown above it. All data here is
    score_render_delta's own output; this function only formats it.

    Verdict shown as its own column with a distinct badge per state
    (HIT/CLOSE/MISSED/SKIPPED), not collapsed to a binary check mark —
    collapsing CLOSE and SKIPPED into the same visual as HIT or MISSED
    would silently undo two separate pieces of work this session:
    giving CLOSE its own evidence-sentence clause (so a middling
    result doesn't vanish from the prose), and giving SKIPPED a
    skip_reason so 'input never had this content' and 'input has more
    of this than baseline, unavoidably' don't get told as the same
    story. A four-state table keeps both distinctions visible here too.
    """
    order = ["hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio"]
    rows = []
    skip_notes = []
    for dim in order:
        d = delta.get(dim)
        if not d:
            continue
        label = _VOICE_MATCH_LABELS.get(dim, dim)
        baseline_display = _format_voice_match_value(dim, d["baseline"])
        output_display = _format_voice_match_value(dim, d["output"])
        verdict = d["verdict"]
        badge = _VOICE_MATCH_VERDICT_BADGE.get(verdict, "badge-amber")
        rows.append(
            f"<tr><td>{label}</td><td>{baseline_display}</td>"
            f"<td>{output_display}</td>"
            f'<td class="vm-verdict"><span class="badge {badge}">{verdict}</span></td></tr>'
        )
        if verdict == "SKIPPED":
            reason = d.get("skip_reason", "no_content")
            if reason == "content_ceiling":
                skip_notes.append(
                    f"{label}: your own writing here was already more opinionated than "
                    f"your baseline; further correction would mean cutting real content."
                )
            else:
                skip_notes.append(f"{label}: nothing to convert in the original.")

    if not rows:
        return ""

    table_html = (
        '<table class="voice-match-table">'
        '<thead><tr><th>Dimension</th><th>Your baseline</th>'
        '<th>This render</th><th class="vm-verdict">Result</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    explain_html = (
        f'<div class="voice-match-explain">{" ".join(skip_notes)}</div>' if skip_notes else ""
    )
    return table_html + explain_html


def _clean_ai_tells_and_rescore():
    """
    AI-Slop Firewall's 'Clean it up' action. Re-runs the same
    deterministic sweep already trusted elsewhere in the pipeline
    (_regex_sweep) on the current render output, then re-scores with
    score_ai_tells — no new removal logic here, this only re-triggers
    the existing mechanism on demand rather than automatically, so the
    person can see exactly what was flagged before it's changed.

    keep_contractions read from session_state (persisted at render
    time, see the comment at that persist point) rather than
    recomputed here, so this action always matches what the actual
    render used — recomputing fresh at click-time risks drifting from
    that if the underlying baseline corpus changes in between.

    Mutates session_state in place; caller is responsible for
    st.rerun() afterward.
    """
    current = st.session_state.get("render_output", "")
    if not current:
        return
    keep_contractions = st.session_state.get("render_keep_contractions", False)
    input_text = st.session_state.get("render_input_text", "")
    cleaned = _regex_sweep(current, keep_contractions=keep_contractions, original_input_text=input_text)
    new_ai_tells = score_ai_tells(cleaned, original_input_text=input_text)

    st.session_state.render_output = cleaned
    report = st.session_state.get("voice_report")
    if report:
        report = dict(report)
        report["ai_tell_clean"] = new_ai_tells["clean"]
        report["ai_tell_flags"] = new_ai_tells["flagged"]
        report["ai_tell_phrases"] = new_ai_tells["flagged_phrases"]
        st.session_state.voice_report = report


# ============================================================
# Shared app-shell sidebar — Write, My Voice, Past renders
# ============================================================

# key overrides preserve the exact button keys that predate this
# consolidation (nav_to_my_voice, nav_to_history_from_write,
# nav_back_to_write, nav_to_history_from_my_voice,
# nav_back_to_write_from_history) - covered by
# test_step3_step4_wiring.py. Only the History->My Voice link is new
# (History previously had no way back to My Voice at all - a real
# navigation gap, not a stylistic one) and gets a fresh key.
_SHELL_NAV_KEYS = {
    (4, 5): "nav_to_my_voice",
    (4, 6): "nav_to_history_from_write",
    (5, 4): "nav_back_to_write",
    (5, 6): "nav_to_history_from_my_voice",
    (6, 4): "nav_back_to_write_from_history",
    (6, 5): "nav_to_my_voice_from_history",
    # Screen 8 ("Check a draft", added 26 Aug 2026) added as a fourth
    # shell screen alongside the original three — same key-per-pair
    # pattern as above, one new key per direction.
    (4, 8): "nav_to_check_from_write",
    (5, 8): "nav_to_check_from_my_voice",
    (6, 8): "nav_to_check_from_history",
    (8, 4): "nav_back_to_write_from_check",
    (8, 5): "nav_to_my_voice_from_check",
    (8, 6): "nav_to_history_from_check",
}
_SHELL_SCREENS = [(4, "Write"), (8, "Check"), (5, "My Voice"), (6, "History")]


def _render_verdict_banner(verdict: str, sub: str = ""):
    """
    Shared verdict headline for both the Write render report and the
    Check-a-draft result - 31 Aug 2026 revamp. Leads with the decision
    (PASS / REVIEW REQUIRED); everything else on the screen remains
    exactly as before and now reads as supporting evidence underneath.
    Pure presentation - takes the already-computed verdict string, no
    new scoring logic.
    """
    is_pass = verdict == "PASS"
    cls = "pass" if is_pass else "review"
    title = "PASS \u2014 Safe to deliver" if is_pass else "REVIEW REQUIRED"
    mark = "\u2713" if is_pass else "!"
    sub_html = f'<div class="verdict-banner-sub">{_safe_html(sub)}</div>' if sub else ""
    st.markdown(
        f'<div class="verdict-banner {cls}">'
        f'<div class="verdict-banner-mark">{mark}</div>'
        f'<div><div class="verdict-banner-title">{title}</div>{sub_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _shell_sidebar(current_screen: int):
    """
    One shared sidebar for the three persistent app-shell screens,
    replacing three separately-maintained st.sidebar blocks that had
    already drifted (History had no link back to My Voice at all;
    only Write showed the free-render count). Per the Step 4 UX
    research pass (26 Aug 2026, benchmarked against Linear/Notion/
    Grammarly/Jasper/Superhuman): the shell is a permanent surface,
    not a one-screen afterthought, so nav/usage/upgrade need to be
    identical from wherever you enter it.

    Single coachmark, not a tour - NN/g's "Instructional Overlays and
    Coach Marks for Mobile Apps" is explicit that stacked hints get
    dismissed faster rather than read; this shows exactly one, tied
    to _step4_shell_intro_shown so it appears once per session
    regardless of which shell screen is reached first, then never
    again. Replaces the earlier plain-text note that lived only on
    the Write screen's main content.
    """
    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id

    with st.sidebar:
        _updated_raw = st.session_state.get("_voice_profile_updated_at")
        if _updated_raw:
            try:
                _updated_dt = datetime.fromisoformat(_updated_raw.replace("Z", "+00:00"))
                st.caption(f"Your voice \u00b7 updated {_updated_dt.strftime('%-d %b %Y')}")
            except Exception:
                st.caption("Your voice \u00b7 loaded")
        else:
            st.caption("Your voice \u00b7 loaded")

        # Persists until explicitly dismissed rather than auto-hiding
        # after one script pass - this app's own device-cookie/profile
        # persistence triggers an internal extra rerun on first landing
        # here (confirmed live: the auto-consuming version set-and-
        # cleared its own flag before the browser ever painted it, so
        # nobody ever actually saw it - a real bug, not just theory).
        # A real dismiss action is also just a better pattern on its
        # own terms: NN/g's coachmark guidance is that a hint gone
        # before it's read has defeated its own purpose, and Grammarly's
        # hotspots are interactive/dismissible too, not timed.
        show_coachmark = not st.session_state.get("_step4_shell_intro_shown")

        for screen_id, label in _SHELL_SCREENS:
            if screen_id == current_screen:
                st.markdown(
                    f'<div class="sidebar-nav-current">{label}</div>',
                    unsafe_allow_html=True,
                )
            else:
                key = _SHELL_NAV_KEYS[(current_screen, screen_id)]
                if st.button(f"{label} \u2192", key=key):
                    go_to(screen_id)
                    st.rerun()
            if screen_id == 5 and show_coachmark:
                st.markdown(
                    '<div class="sidebar-coachmark">Your voice fingerprint lives '
                    'here \u2014 reuse it anytime, no need to redo onboarding.</div>',
                    unsafe_allow_html=True,
                )
                if st.button("Got it", key=f"dismiss_coachmark_from_{current_screen}"):
                    st.session_state["_step4_shell_intro_shown"] = True
                    st.rerun()

        # Persistent usage/upgrade surface - previously only existed as
        # a line of text on the Write screen's main content, with no
        # equivalent on My Voice or History, and no in-app upgrade path
        # outside the paywall-hit error state. Notion's pattern (a
        # sidebar usage notification plus a durable Settings ->
        # Upgrade entry) is the model; the lifetime (not monthly) cap
        # makes the running count especially worth keeping visible.
        if not device_has_active_subscription(device_id):
            _used, _limit = get_lifetime_render_count(device_id)
            _remaining = max(_limit - _used, 0)
            chip_class = "sidebar-usage-chip low" if _remaining <= 3 else "sidebar-usage-chip"
            st.markdown(
                f'<div class="{chip_class}">{_remaining} of {_limit} free renders '
                f'left (lifetime, not monthly)</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "See plans \u2192" if _remaining > 3 else "Upgrade for unlimited \u2192",
                key=f"shell_upgrade_from_{current_screen}", use_container_width=True,
            ):
                go_to(7)
                st.rerun()
        else:
            # Frictionless cancel path for an active subscriber - same
            # sidebar real estate the free-tier upgrade chip occupies,
            # just the subscribed-state equivalent. One click into
            # Stripe's own Customer Portal, where cancelling is a
            # native, unassisted action - VOICOVA doesn't gate or
            # intercept it with its own confirmation flow.
            st.markdown(
                '<div class="sidebar-usage-chip">Subscribed \u2014 unlimited renders.</div>',
                unsafe_allow_html=True,
            )
            if st.button(
                "Manage subscription \u2192",
                key=f"shell_manage_subscription_from_{current_screen}", use_container_width=True,
            ):
                st.session_state["_manage_subscription_requested"] = True
                st.rerun()
            _handle_manage_subscription_request()


def screen_render():
    # Wide-layout cap, scoped to this screen only (set_page_config's
    # layout="wide" for screen 4 removes Streamlit's max-width entirely
    # — this reinstates a cap, just a wider one than the 700px
    # intake-flow column, sized for the two-pane editor+report split
    # below rather than single-column prose). Injected here rather
    # than the top-level <style> block so it only ever applies while
    # this function is actually rendering, no body-class JS needed.
    st.markdown(
        '<style>.block-container { max-width: 1180px !important; } '
        '@media (max-width: 900px) { .block-container { max-width: 700px !important; } }'
        '</style>',
        unsafe_allow_html=True,
    )
    if not st.session_state.get("_returning_user_sidebar"):
        progress_dots(4)
    _show_deepen_success_if_pending()

    if st.session_state.get("baseline_fingerprint") and (
        st.session_state.get("_returning_user_sidebar")
        or st.session_state.get("_sidebar_unlocked")
    ):
        _shell_sidebar(4)

    if st.session_state.get("_returning_user_sidebar"):
        st.markdown('<div class="headline">Write as me.</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub">Paste anything you want in your voice. Your fingerprint is loaded and ready.</div>', unsafe_allow_html=True)
    else:
        # shell-intro: brief docking-in motion the first time this
        # screen's content appears, narrating the layout change rather
        # than a jump-cut - the sidebar itself carries the coachmark
        # that explains what's new (_shell_sidebar above), so this is
        # motion only, no duplicate text note (25 Aug 2026 UX audit
        # flagged the abrupt shape change; the 26 Aug research pass
        # replaced the earlier plain-text fix with this pairing).
        st.markdown('<div class="shell-intro">', unsafe_allow_html=True)
        st.markdown('<div class="headline">Paste the text to restore.</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub">Paste AI-generated text here. Voicova rewrites it in your voice, using the fingerprint it just built.</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # Optional, skippable, per-render — not onboarding. Register/audience
    # is a genuinely separate axis from personal voice (the field's own
    # theory of style splits these into two distinct groups: registers/
    # genres people modulate deliberately by situation, versus the
    # unintentional style that comes from who someone is as a writer).
    # The baseline fingerprint only ever measures the second. This gives
    # the render a signal for the first, without touching the numeric
    # baseline targets at all — deliberately: those still verify against
    # the person's own blended voice, this only steers word choice and
    # formality at generation time. Same fast-path-by-default pattern as
    # the deepen-fingerprint panel: visible, not gated, easy to ignore.
    # Defaults to the last-used context rather than forcing a choice
    # every render.
    # Seeded into session_state BEFORE the widget is created, not
    # passed via value= below — Streamlit ignores a keyed widget's
    # value= after its first run, session_state is what actually
    # controls it from then on. Same pattern already used for
    # render_input_field's initial value elsewhere on this screen.
    # Still fully editable/clearable per render; this only changes
    # what's pre-filled, never forces the previous context to stick.
    if "render_context_field" not in st.session_state:
        st.session_state["render_context_field"] = st.session_state.get("render_context_input", "")

    render_context = st.text_input(
        "context",
        placeholder="Optional. Who's this for, and what's it for?",
        label_visibility="collapsed", key="render_context_field",
    )

    render_mode = st.radio(
        "mode",
        options=["preserve", "elevate"],
        format_func=lambda m: "Preserve: keep it close to as-is" if m == "preserve"
                                else "Elevate: tighten it, keep your voice",
        index=0,  # defaults to "preserve" — the existing behaviour, always
        horizontal=True,
        label_visibility="collapsed",
        key="render_mode_field",
    )

    # Only offered once elevate is selected, on purpose — platform
    # formatting restructures paragraphs, which is a materially
    # different, riskier operation than elevate's line-editing alone.
    # Making it a sub-choice of elevate rather than an independent
    # toggle keeps that distinction visible: line-edit first, then
    # optionally restructure, never restructure-only. See
    # build_correction_prompt's docstring for why this isn't just
    # folded into elevate mode itself.
    #
    # Originally LinkedIn-only; generalised once it was clear the
    # underlying convention (short paragraphs, hook-first) wasn't
    # LinkedIn-specific, and a second, genuinely different target
    # (email) was worth adding alongside it rather than stretching one
    # instruction to cover both. A selectbox rather than a second
    # checkbox, since these are mutually exclusive targets, not
    # independent toggles — a render is formatted for exactly one
    # destination or none.
    platform_format = None
    if render_mode == "elevate":
        platform_choice = st.selectbox(
            "platform format",
            options=["none", "social", "email"],
            format_func=lambda p: {
                "none": "No platform formatting",
                "social": "Social post: short paragraphs, hook first",
                "email": "Email: keep greeting and sign-off in place",
            }[p],
            index=0,
            label_visibility="collapsed",
            key="platform_format_field",
        )
        platform_format = None if platform_choice == "none" else platform_choice

    # Split-pane editor layout (27 Aug 2026, UI-quality pass),
    # benchmarked against Grammarly/Wordtune/Sudowrite/Jasper: input
    # and its result sit side by side instead of stacked in one
    # narrow column, so the Voice Report (this product's actual
    # differentiator) is visible without scrolling past the editor.
    # Every st.* call inside col_left/col_right below is completely
    # unchanged from before this pass — only re-indented one level
    # to sit inside the `with` block. See set_page_config's comment
    # for the matching layout="wide" gate on this screen only.
    col_left, col_right = st.columns([1, 1], gap="large")
    with col_left:
        input_text = st.text_area(
            "input", value=st.session_state.get("render_input_text", ""),
            placeholder="Paste AI-generated text here. An email draft, a LinkedIn post, a proposal section...",
            height=220, label_visibility="collapsed", key="render_input_field",
        )

        st.markdown(
            '<div class="microcopy" style="margin-bottom:0.5rem;">'
            'Next: a quick authenticity check on the rewrite, then your text.</div>',
            unsafe_allow_html=True,
        )
        render_in_progress = st.session_state.get("render_in_progress", False)

        def _start_render():
            # Guard INSIDE the callback, not just via the button's disabled=
            # prop: on_click callbacks run before Streamlit reruns and
            # re-sends the disabled state to the browser, closing the
            # round-trip window a fast double-click can land in (confirmed
            # real in the 25 Aug 2026 UX audit - a double-click burned two
            # of the 15 lifetime, non-renewing free renders on one
            # submission). The disabled= prop below still helps for a
            # slower second click after the first rerun completes; this
            # guard is what closes the fast-double-click race specifically.
            if st.session_state.get("render_in_progress"):
                return
            _input = st.session_state.get("render_input_field", "")
            if not _input or not _input.strip():
                st.session_state["render_missing_input"] = True
                return
            st.session_state["render_missing_input"] = False
            st.session_state.render_input_text = _input
            st.session_state.render_context_input = st.session_state.get("render_context_field", "")
            st.session_state.render_mode_input = st.session_state.get("render_mode_field", "preserve")
            st.session_state.platform_format_input = st.session_state.get("platform_format_field")
            st.session_state.render_output = ""
            st.session_state.refinement_used = False
            # Own request flag, not just render_in_progress (27 Aug 2026):
            # render_in_progress is a shared "something is rendering right
            # now" semaphore across all three render-triggering buttons
            # (this one, Try again, Refine) so they can't race each
            # other either - but the deferred trigger below needs to
            # know it was THIS button that asked, not just that some
            # render is pending, or it fires on every render_in_progress
            # transition regardless of which button caused it (confirmed
            # real: Refine's own fix below double-called _run_render
            # until this flag was added, because this block used to
            # check render_in_progress alone).
            st.session_state["_write_requested"] = True
            st.session_state.render_in_progress = True

        st.button(
            "Write as me \u2192", type="primary", use_container_width=True,
            disabled=render_in_progress, on_click=_start_render,
        )
        if st.session_state.get("render_missing_input"):
            render_alert("Paste some text first.", "error")
            st.session_state["render_missing_input"] = False

        if st.session_state.get("_write_requested"):
            st.session_state["_write_requested"] = False
            _run_render(
                st.session_state.get("render_input_text", ""),
                render_context=st.session_state.get("render_context_input", ""),
                render_mode=st.session_state.get("render_mode_input", "preserve"),
                platform_format=st.session_state.get("platform_format_input"),
            )
            st.session_state.render_in_progress = False
            st.rerun()

        if st.session_state.get("render_error"):
            render_alert(st.session_state.render_error, "error")
            if st.session_state.get("render_paywall_hit"):
                # Paywall, not a transient failure - "Try again" would just
                # hit the same cap again. Two plan buttons, same Session.
                # create → redirect → Session.retrieve pattern proven on
                # AQE/CLEARANCE (see stripe_subscription.py's docstring for
                # why this reuses that pattern rather than building new
                # Stripe surface for a subscription specifically).
                #
                # "Here's what you get" reuses the same _PRICING_TIERS
                # content as the standalone /pricing screen so the two
                # can't drift apart.
                st.markdown(_pricing_tiers_html(compact=True), unsafe_allow_html=True)

                pay_col1, pay_col2 = st.columns(2)
                with pay_col1:
                    if st.button("Upgrade: £6.99/month", key="upgrade_monthly", use_container_width=True):
                        st.session_state["_checkout_plan_requested"] = "monthly"
                        st.rerun()
                with pay_col2:
                    if st.button("Upgrade: £49/year", key="upgrade_annual", use_container_width=True):
                        st.session_state["_checkout_plan_requested"] = "annual"
                        st.rerun()

                # One click, not two: clicking "Upgrade" used to only
                # reveal a second "Continue to payment →" button that did
                # the actual navigating, with nothing informative shown in
                # between. Streamlit still needs a rerun to render the
                # checkout URL once Stripe returns it, so the click itself
                # can't literally navigate — but a meta-refresh
                # auto-redirects the browser the instant that URL exists,
                # with no second click needed. The manual link below is a
                # fallback only, for a browser that blocks the
                # auto-refresh, not a required second step.
                #
                # Shared with screen_pricing()'s own Upgrade buttons via
                # _handle_checkout_plan_request() - one create-session-and-
                # redirect implementation, not two copies to keep in sync.
                _handle_checkout_plan_request()

                # This IS the moment a returning subscriber who cleared
                # cookies is most likely to land - they hit the same free-
                # tier paywall as someone who never paid, since the app has
                # no way to know they're a subscriber without this. Shared
                # with screen_pricing()'s copy of the same expander.
                _render_restore_access_expander(key_prefix="paywall_")
            else:
                # Same double-click race the "Write as me" button was
                # fixed for (25 Aug 2026 UX audit) — this button was
                # missed at the time. _run_render was called directly
                # inside `if st.button(...)`, with no guard against two
                # fast clicks each landing before the first rerun could
                # disable the button, each triggering its own full
                # render and burning a lifetime render credit. Same
                # fix: guard set/checked INSIDE an on_click callback
                # (synchronous per click, unlike the disabled= prop
                # alone), actual render deferred to after the button so
                # it only ever runs once per genuine click. Reuses the
                # same render_in_progress flag "Write as me" uses, so
                # the two buttons can't race each other either.
                def _start_retry():
                    if st.session_state.get("render_in_progress"):
                        return
                    st.session_state["_retry_requested"] = True
                    st.session_state.render_in_progress = True

                st.button(
                    "Try again", key="retry_render",
                    disabled=st.session_state.get("render_in_progress", False),
                    on_click=_start_retry,
                )
                if st.session_state.get("_retry_requested"):
                    st.session_state["_retry_requested"] = False
                    last_attempt = st.session_state.get("render_last_attempt", input_text)
                    was_refinement = st.session_state.get("render_last_is_refinement", False)
                    if _run_render(
                        last_attempt, is_refinement=was_refinement,
                        render_context=st.session_state.get("render_context_input", ""),
                        render_mode=st.session_state.get("render_mode_input", "preserve"),
                        platform_format=st.session_state.get("platform_format_input"),
                    ) and was_refinement:
                        st.session_state.refinement_used = True
                    st.session_state.render_in_progress = False
                    st.rerun()

        if st.session_state.get("subscription_confirm_failed"):
            render_alert(
                "We couldn't confirm that payment. If you were charged, "
                "contact support and we'll sort it out.",
                "error",
            )
            st.session_state.subscription_confirm_failed = False

    with col_right:
        output = st.session_state.get("render_output", "")
        if output:
            st.markdown(
                '<div class="render-output-seam"><span class="tagline">Your writing</span></div>',
                unsafe_allow_html=True,
            )

            import hashlib
            output_key = "out_" + hashlib.md5(output[:50].encode()).hexdigest()[:8]

            # Review gate — see review_gate.py. Moved the report block ahead
            # of the text_area (previously rendered after) so the risk/
            # confidence/AI-tell badges are visible BEFORE any decision about
            # showing the text, for both the gated and ungated paths - the
            # ordering itself is part of what makes the gate meaningful,
            # not just the confirmation click.
            report = st.session_state.get("voice_report")
            risk_level = report.get("risk") if report else None
            hard_fail = report.get("content_integrity_hard_fail") if report else None
            gated = requires_review(hard_fail)
            confirm_flag_key = f"reviewed_{output_key}"
            already_confirmed = st.session_state.get(confirm_flag_key, False)
            show_output = (not gated) or already_confirmed

            if report:
                badge_class = {"Low": "badge-green", "Medium": "badge-amber", "High": "badge-red"}
                ai_tell_html = (
                    '<span class="badge badge-green">Clean</span>'
                    if report.get("ai_tell_clean", True)
                    else f'<span class="badge badge-red">Flagged</span>: {_safe_html("; ".join(report.get("ai_tell_flags", [])))}'
                )
                risk_tier = report.get("risk", "Low")
                risk_icon = _RISK_ICON.get(risk_tier, "")
                confidence_html = _confidence_signal_html(report.get("confidence", "Low"))
                vm_badge = report.get('voice_match_badge', 'badge-amber')
                vm_tier = report.get('voice_match_tier', 'Unrated')
                vm_evidence = _safe_html(report.get('voice_match_evidence', ''))
                content_lock_banner = _build_content_lock_banner_html(
                    report, st.session_state.get("render_insertion_check")
                )
                what_changed = _build_what_changed_html(report.get("biggest_changes", []))
                _metric_gloss = {
                    "consistency": "How closely this render matches how you actually write.",
                    "confidence": "How much of your writing we've seen so far - more samples, higher confidence.",
                    "risk": "How much this render may have drifted from what you actually meant.",
                    "ai_tell": "Whether wording that reads as AI-generated survived into the rewrite.",
                }
                _render_verdict_banner(
                    "REVIEW" if gated else "PASS",
                    sub=f"Voice consistency: {vm_tier}",
                )
                st.markdown(f"""
<div class="voice-report">
{content_lock_banner}
{what_changed}
<div class="vr-grid">
<div class="vr-stat">
<div class="vr-stat-label" title="{_metric_gloss['consistency']}">Voice consistency</div>
<span class="badge {vm_badge}">{vm_tier}</span>
</div>
<div class="vr-stat">
<div class="vr-stat-label" title="{_metric_gloss['confidence']}">Confidence</div>
{confidence_html}
</div>
<div class="vr-stat">
<div class="vr-stat-label" title="{_metric_gloss['risk']}">Risk</div>
<span class="badge {badge_class.get(report['risk'], 'badge-amber')}">{risk_icon}{report['risk']}</span>
</div>
<div class="vr-stat">
<div class="vr-stat-label" title="{_metric_gloss['ai_tell']}">AI-tell check</div>
{ai_tell_html}
</div>
</div>
<div class="vr-changes">{vm_evidence}</div>
<details style="margin-top:0.7rem;">
<summary style="cursor:pointer;font-family:var(--font-mono);font-size:0.7rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;">
Show the per-dimension breakdown
</summary>
<div style="margin-top:0.5rem;">
{_build_voice_match_table_html(st.session_state.get("render_delta") or {})}
</div>
</details>
{_build_content_lock_html(report, st.session_state.get("render_insertion_check"))}
</div>
    """, unsafe_allow_html=True)

                # Explicit, always-visible explainer for Confidence vs
                # Risk: these two badges can land as "Confidence: Low" +
                # "Risk: High", directly under a green "Content safe"
                # banner, and a hover title="" tooltip alone is invisible
                # on touch devices and easy to miss on desktop. Confidence
                # and Risk measure genuinely different things (sample size
                # vs meaning drift), and first reaction to seeing both look
                # bad at once is alarm, not clarity. One persistent line,
                # not another hover target, fixes that.
                st.markdown(
                    '<div class="microcopy" style="margin-top:-0.4rem;margin-bottom:0.6rem;">'
                    'Confidence reflects how much of your writing we\'ve seen so far, '
                    'not whether anything\'s wrong. Risk reflects how much this render '
                    'may have drifted from what you meant. They can move independently.'
                    '</div>',
                    unsafe_allow_html=True,
                )

                # AI-Slop Firewall — outside the raw-HTML block above,
                # since it needs a real st.button (Streamlit widgets can't
                # live inside an unsafe_allow_html string). ai_tell_phrases
                # comes from score_ai_tells' flagged_phrases field, the raw,
                # individual phrase list, not the
                # pre-joined "AI-typical phrasing found: X, Y, Z" prose
                # string ai_tell_flags carries; parsing that string on the
                # UI side would be fragile against any future wording
                # change to it.
                ai_tell_phrases = report.get("ai_tell_phrases", [])
                if ai_tell_phrases:
                    phrase_chips = "".join(
                        f'<span class="ai-tell-phrase">{_safe_html(p)}</span>' for p in ai_tell_phrases
                    )
                    st.markdown(
                        f'<div class="ai-tell-block">'
                        f'<div class="ai-tell-title">AI Tell Check: '
                        f'{len(ai_tell_phrases)} found</div>'
                        f'<div class="ai-tell-phrase-list">{phrase_chips}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    if st.button("Clean it up", key=f"clean_ai_tells_{output_key}"):
                        _clean_ai_tells_and_rescore()
                        st.rerun()

                swaps = report.get("attribution_swaps", [])
                if swaps:
                    st.markdown(
                        '<div class="microcopy" style="margin-top:0.5rem;color:var(--danger);">'
                        '\u26a0 Check who gets credit before sending. The rewrite may have swapped '
                        'whose point this was.</div>',
                        unsafe_allow_html=True
                    )

                dropped = report.get("dropped_entities", [])
                if dropped:
                    listed = _safe_html(", ".join(dropped))
                    source_sentence = find_source_sentence(
                        st.session_state.get("render_input_text", ""), dropped[0]
                    )
                    context_line = (
                        f' Original: "{_safe_html(source_sentence)}"' if source_sentence else ""
                    )
                    st.markdown(
                        f'<div class="microcopy" style="margin-top:0.5rem;color:var(--danger);">'
                        f'\u26a0 Missing from the rewrite: {listed}. This can mean the rewrite '
                        f'drifted into different content, not just a different style - read it '
                        f'in full before sending, don\'t just skim the changes above.{context_line}</div>',
                        unsafe_allow_html=True
                    )
                    if source_sentence and st.button(
                        "Restore this sentence", key=f"restore_sentence_{output_key}"
                    ):
                        st.session_state.render_output = splice_dropped_sentence(
                            output, source_sentence
                        )
                        st.rerun()

                # Amber, not red — this is a graceful decline, not a
                # content-integrity failure the person needs to hunt for.
                # The render still shipped, correctly, just without the
                # platform restructuring — because the restructuring
                # attempt introduced wording that couldn't be verified
                # against the pre-correction text and was discarded rather
                # than risked. See score_restructure_fidelity in
                # voice_engine.py for what specifically gets checked.
                if st.session_state.get("restructure_declined"):
                    st.markdown(
                        '<div class="microcopy" style="margin-top:0.5rem;color:var(--warning);">'
                        '\u26a0 Platform formatting was attempted but introduced wording that '
                        'could not be verified, so it was left out. This is your line-edited '
                        'version, not restructured for the platform. Voice and content are '
                        'still correct; only the platform formatting is missing.</div>',
                        unsafe_allow_html=True
                    )

            if show_output:
                st.markdown('<div class="tagline">Your rewritten text</div>', unsafe_allow_html=True)
                swaps_for_highlight = (report or {}).get("attribution_swaps", [])
                lexical_breaks_for_highlight = (report or {}).get("lexical_fidelity_breaks", [])
                has_flags = bool(swaps_for_highlight or lexical_breaks_for_highlight)
                if has_flags:
                    highlighted = highlight_flagged_phrases(
                        output, swaps_for_highlight, lexical_breaks_for_highlight
                    )
                    # Microcopy adapts to what's actually present - a
                    # render with only a lexical-fidelity note shouldn't
                    # tell the person to check "who said this" when
                    # nothing about attribution changed.
                    if swaps_for_highlight:
                        note = "Highlighted: credit may have swapped who said this. Hover to see what changed."
                    else:
                        note = "Highlighted: a word choice worth a second look. Hover to see why."
                    st.markdown(
                        f'<div style="white-space:pre-wrap;line-height:1.6;'
                        f'background:var(--canvas);border:0.5px solid var(--border);border-radius:10px;'
                        f'padding:14px 16px;margin-bottom:0.6rem;">{highlighted}</div>'
                        f'<div class="microcopy">{note}</div>',
                        unsafe_allow_html=True,
                    )
                # Double-render fix: this text_area used to fire
                # unconditionally, so any render with a flagged phrase
                # showed the same rewritten text twice — once highlighted
                # above, once plain here. The plain copy is only needed
                # when there's nothing highlighted to show it in place of;
                # when highlighted, the person still needs a copyable
                # plain-text version, so it now renders collapsed inside
                # an expander instead of a second full-height block.
                if has_flags:
                    with st.expander("Copy plain text"):
                        st.text_area(
                            label="output", value=output, height=350,
                            label_visibility="collapsed", key=output_key,
                        )
                else:
                    st.text_area(
                        label="output", value=output, height=350,
                        label_visibility="collapsed", key=output_key,
                    )

                # Character-length nudge, social platform format only.
                # Research-backed thresholds (checked, not guessed, 29
                # Aug 2026): LinkedIn's hard cap is 3,000 characters,
                # but the actual engagement sweet spot converged on
                # across every current source checked is 1,200-1,600 -
                # well short of the cap. Nothing in the render pipeline
                # targets or enforces a length for social-format
                # output, so this is a soft, informational nudge, not
                # a hard cutoff or truncation - the render itself is
                # never altered by this, only the person's own
                # decision about whether to trim before posting.
                if st.session_state.get("platform_format_input") == "social":
                    _char_count = len(output)
                    if _char_count > 3000:
                        _len_badge, _len_note = (
                            "badge-red",
                            "over LinkedIn's 3,000-character limit — it will be cut off",
                        )
                    elif _char_count > 1600:
                        _len_badge, _len_note = (
                            "badge-amber",
                            "longer than the 1,200–1,600 sweet spot for engagement",
                        )
                    elif _char_count < 1200:
                        _len_badge, _len_note = (
                            "badge-green",
                            "within LinkedIn's 3,000-character limit",
                        )
                    else:
                        _len_badge, _len_note = (
                            "badge-green",
                            "in the 1,200–1,600 sweet spot for engagement",
                        )
                    st.markdown(
                        f'<div class="microcopy" style="margin-top:0.3rem;">'
                        f'<span class="badge {_len_badge}">{_char_count:,} characters</span> '
                        f'{_len_note}</div>',
                        unsafe_allow_html=True,
                    )

                st.markdown(
                    '<div class="microcopy">Written as you. Not for you.</div>',
                    unsafe_allow_html=True
                )

                # Copy-to-clipboard: text is embedded via json.dumps rather
                # than read from the text_area's DOM node, since
                # Streamlit's own key-based re-render can detach a plain
                # <script> from that element between runs; embedding the
                # value directly is more robust than depending on DOM
                # lookup timing.
                _copy_btn_id = f"copybtn_{output_key}"
                _copy_source_id = f"copysrc_{output_key}"
                # Zero-indent, single-line HTML deliberately (23 Aug 2026
                # bug fix): a multi-line f-string here, indented to match
                # the surrounding Python code, gets treated as an indented
                # code block by Streamlit's markdown parser and rendered
                # as literal visible text instead of an actual button —
                # confirmed live, this exact block was the reported bug.
                #
                # Second, independent bug fixed at the same time: the
                # previous version embedded json.dumps(output) (a
                # double-quoted JSON string) directly inside a
                # double-quoted onclick="..." attribute. Any quote
                # character in the actual rendered text (apostrophes like
                # "it's", "doesn't" are near-certain in real output) broke
                # the attribute early and corrupted the whole element,
                # regardless of the indentation issue. Fixed properly, not
                # by picking a different quote character (json.dumps only
                # guarantees escaping ", not ', so single-quoting the
                # attribute would just move the same collision to the
                # first apostrophe instead): the text is written into a
                # hidden textarea via html.escape (escapes both " and '
                # for safe attribute/content embedding), and the button's
                # JS reads it back via .value, which the browser correctly
                # decodes from HTML entities to the original text. This is
                # the standard safe pattern for embedding arbitrary text
                # for JS to consume, not a one-off escaping hack.
                # Hardening (27 Aug 2026, live report: "copy text
                # appears not to be working"): navigator.clipboard.
                # writeText() can reject or simply not exist (older
                # browsers, some embedded/webview contexts, clipboard
                # permission denied) and the previous version had no
                # .catch() at all — a rejection meant the button did
                # nothing, with zero visible feedback that anything
                # had gone wrong. Now: try the modern API first: on
                # success OR on failure, fall back to the classic
                # select-and-execCommand('copy') approach (works in
                # far more contexts, including ones without Clipboard
                # API access at all); if that ALSO fails, show an
                # explicit "Copy failed" state rather than staying
                # silent — a failed copy the person can see and work
                # around (select the text manually) beats one they
                # can't tell happened at all. Same zero-embedded-
                # newline construction as before (many small
                # concatenated f-string pieces, not one multi-line
                # triple-quoted string) — see the indentation-bug note
                # above for why that distinction matters here.
                import html as _html
                st.markdown(
                    f'<textarea id="{_copy_source_id}" style="display:none">'
                    f'{_html.escape(output)}</textarea>'
                    f'<button id="{_copy_btn_id}" data-label="Copy text" '
                    f'onclick="(function(){{'
                    f'var t=document.getElementById(\'{_copy_source_id}\');'
                    f'var b=document.getElementById(\'{_copy_btn_id}\');'
                    f'var o=b.dataset.label;'
                    f'function ok(){{b.innerText=\'Copied\';'
                    f'setTimeout(function(){{b.innerText=o;}},1500);}}'
                    f'function fail(){{b.innerText=\'Copy failed \u2014 select text below\';'
                    f'setTimeout(function(){{b.innerText=o;}},2500);}}'
                    f'function fallback(){{try{{'
                    f'var d=t.style.display;t.style.display=\'block\';t.select();'
                    f'var s=document.execCommand(\'copy\');t.style.display=d;'
                    f'if(s){{ok();}}else{{fail();}}'
                    f'}}catch(e){{fail();}}}}'
                    f'if(navigator.clipboard&&navigator.clipboard.writeText){{'
                    f'navigator.clipboard.writeText(t.value).then(ok).catch(fallback);'
                    f'}}else{{fallback();}}'
                    f'}})()" '
                    f'style="font-family: var(--font-sans); font-size: 0.85rem; '
                    f'font-weight: 500; color: var(--accent); '
                    f'background: var(--accent-soft); border: 1px solid var(--border); '
                    f'border-radius: 8px; padding: 0.4rem 0.9rem; cursor: pointer; '
                    f'margin-bottom: 0.6rem;">Copy text</button>',
                    unsafe_allow_html=True,
                )

                # Learn from my edit — added 29 Aug 2026. If the person
                # edited the text inside the text_area above (Streamlit
                # auto-syncs its live value into st.session_state under
                # output_key, no extra wiring needed), their edited
                # version becomes a new fingerprint sample. Deliberately
                # only the user's own final, edited text — never the raw
                # AI output the render started from — per the same
                # evidence-not-instruction discipline as the Voice
                # Profile document above: the fingerprint only ever
                # grows from something the person actually wrote or
                # confirmed, never from an unverified AI guess.
                # Reuses _add_writing_sample_to_fingerprint (the exact
                # sequence _deepen_fingerprint_panel already used) - no
                # new merge logic. Hidden when the box is unedited;
                # "learning" from the untouched AI output would defeat
                # the whole point.
                _edited_output = st.session_state.get(output_key, output)
                if _edited_output.strip() and _edited_output.strip() != output.strip():
                    if st.button(
                        "Use my edit to strengthen my voice",
                        key=f"learn_from_edit_{output_key}", use_container_width=True,
                    ):
                        # Structured correction evidence (30 Aug 2026,
                        # voice-review item #1) — captures WHICH
                        # dimension the person corrected and in WHICH
                        # direction, alongside (not instead of) the
                        # existing blended-sample merge below. See
                        # score_correction_evidence's own docstring
                        # (voice_engine.py) for why this is a distinct
                        # signal from "a new sample was added."
                        evidence = score_correction_evidence(output, _edited_output)
                        if evidence:
                            log_entry = {
                                "evidence": evidence,
                                "platform_format": st.session_state.get("platform_format_input"),
                            }
                            history = st.session_state.get("correction_evidence", [])
                            history.append(log_entry)
                            st.session_state.correction_evidence = history
                        _add_writing_sample_to_fingerprint(
                            _edited_output,
                            platform_format=st.session_state.get("platform_format_input"),
                        )
                        render_alert(
                            "Added. Your edit now helps strengthen your voice baseline.",
                            "success",
                        )

                # Opt-in firm signal — offered once per session, only after
                # an actually-gated (Medium/High) render was confirmed.
                # Low-risk renders never see this at all: the offer only
                # makes sense right after someone has just demonstrated,
                # by confirming a review gate, that this is relevant to
                # them. See firm_signal.py for exactly what is and isn't
                # stored — domain only, never the email itself.
                if gated and not st.session_state.get("firm_signal_resolved", False):
                    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
                    st.markdown(
                        '<div class="microcopy">Optional: if others at your firm use '
                        'VOICOVA too, sharing your work email helps us show your firm '
                        'this is already in use. We store only the domain, never your '
                        'email address, never anything you write.</div>',
                        unsafe_allow_html=True,
                    )
                    col_a, col_b, col_c = st.columns([2, 1, 1])
                    with col_a:
                        work_email = st.text_input(
                            "work email", placeholder="you@yourfirm.com",
                            label_visibility="collapsed", key="firm_signal_email_input",
                        )
                    with col_b:
                        if st.button("Share", key="firm_signal_share_button", use_container_width=True):
                            domain = extract_domain(work_email)
                            if domain:
                                log_firm_signal(
                                    domain=domain, risk=risk_level,
                                    risk_reason=st.session_state.get("risk_reason", ""),
                                    scoring_rules_version=scoring_rules_version(),
                                )
                                st.session_state.firm_signal_resolved = True
                                st.rerun()
                            else:
                                render_alert(
                                    "That doesn't look like a work email, or it's a "
                                    "personal provider we don't count as a firm signal.",
                                    "warning",
                                )
                    with col_c:
                        if st.button("No thanks", key="firm_signal_dismiss_button", use_container_width=True):
                            st.session_state.firm_signal_resolved = True
                            st.rerun()
            else:
                _risk_reason_copy = {
                    "ai_tell": "This render still contains a phrase that reads like AI wrote it, not you.",
                    "attribution_swap": "This render may have shifted who said what.",
                    "dropped_entity": "This render dropped a name, date, or detail that was in your original text.",
                    "sentence_growth": "This render added content that wasn't in your original text.",
                    "aggregate_band": "This render drifted further from your voice than usual, across several measures.",
                }
                # Specific, not generic: per research on confirmation-copy
                # anti-patterns, NN/g and Intuit's own content design
                # guidelines both flag vague "I understand"/"are you sure"
                # acknowledgments as ineffective - people click through
                # boilerplate without reading it, and it erodes attention
                # for warnings that actually matter. risk_reason was
                # already computed and logged (compute_risk_reason,
                # review_gate.py) but never shown to the person it's about
                # - it just sat in analytics. Showing the actual specific
                # reason, and making the checkbox confirm that specific
                # thing, is the fix backed by that research, not just a
                # tone change.
                reason_text = _risk_reason_copy.get(
                    st.session_state.get("risk_reason", ""),
                    "This render needs a closer look before you send it.",
                )
                st.markdown(
                    f'<div class="microcopy" style="margin-top:0.5rem;color:var(--danger);">'
                    f'\u26a0 {_safe_html(reason_text)} Read the report above before sending.</div>',
                    unsafe_allow_html=True
                )
                confirmed_checkbox = st.checkbox(
                    "I've read it, and I'm sending this as mine.",
                    key=f"confirm_checkbox_{output_key}",
                )
                if st.button(
                    "Show my rewritten text \u2192", type="primary", use_container_width=True,
                    disabled=not confirmed_checkbox, key=f"confirm_button_{output_key}",
                ):
                    st.session_state[confirm_flag_key] = True
                    log_review_confirmation(
                        risk=risk_level,
                        risk_reason=st.session_state.get("risk_reason", ""),
                        semantic_match=report.get("semantic_match") if report else None,
                        scoring_rules_version=scoring_rules_version(),
                    )
                    st.rerun()

            # Positioned after the report+output/gate resolve rather than
            # wedged between the report warnings and the output text_area
            # - that ordering put an optional "improve your fingerprint"
            # upsell ahead of the actual rewritten text the person came
            # here for, which read as backwards. Runs regardless of
            # gated/show_output state (still relevant even if output is
            # hidden pending confirmation).
            if report:
                caveat = confidence_caveat(st.session_state.get("dimension_stability"))
                if caveat:
                    st.markdown(
                        f'<div class="microcopy" style="margin-top:0.5rem;">{_safe_html(caveat)}</div>',
                        unsafe_allow_html=True
                    )
                    _deepen_fingerprint_panel(show_caveat_framing=True)

            if show_output and st.session_state.get("intent_mode") == "HELP_ME_UNDERSTAND":
                st.markdown("<hr class='divider'>", unsafe_allow_html=True)
                receipt = generate_receipt(st.session_state.session_start, st.session_state.word_count)
                st.markdown(
                    f'<div class="receipt"><div class="receipt-title">Your render record</div>'
                    f'<div>{_safe_html(receipt["summary"])}</div><br>'
                    f'<div><strong>Session started:</strong> {_safe_html(receipt["session_started"])}</div>'
                    f'<div><strong>Words analysed:</strong> {_safe_html(receipt["words_analysed"])}</div>'
                    f'<div><strong>Rendered:</strong> {_safe_html(receipt["rendered_at"])}</div></div>',
                    unsafe_allow_html=True,
                )

            # Sample 3 — one refinement, per the v4 spec. Combo: tags + free text.
            # Gated on show_output too — refining text the person hasn't been
            # shown yet doesn't make sense, and would let someone route around
            # the confirmation by refining instead of confirming.
            if show_output and not st.session_state.refinement_used:
                st.markdown("<hr class='divider'>", unsafe_allow_html=True)
                st.markdown('<div class="tag-hint">Not quite right? You get one refinement.</div>', unsafe_allow_html=True)
                tag_options = ["Too formal", "Too blunt", "Doesn't sound like me", "Too long", "Missing my directness"]
                chosen_tags = st.multiselect("What's off", tag_options, label_visibility="collapsed", key="refine_tags")
                freetext = st.text_area(
                    "More detail (optional)", placeholder="Anything else specific...",
                    height=80, key="refine_freetext",
                )
                # Same double-click race fixed above for "Try again" —
                # see that comment. Guard captures and stores the
                # computed refinement request inside the on_click
                # callback itself (mirrors "Write as me"'s _start_render:
                # reads the widget values it needs from session_state
                # by key, since they're already committed there by the
                # time a callback runs), so the deferred block below
                # only ever has to read already-finalised values, not
                # recompute anything a second click could race on.
                def _start_refine():
                    if st.session_state.get("render_in_progress"):
                        return
                    _tags = st.session_state.get("refine_tags", [])
                    _freetext = st.session_state.get("refine_freetext", "")
                    _note = ", ".join(_tags)
                    if _freetext.strip():
                        _note = f"{_note}. {_freetext.strip()}" if _note else _freetext.strip()
                    st.session_state.refinement_tags = _tags
                    st.session_state.refinement_freetext = _freetext
                    st.session_state["_refine_input"] = (
                        f"{st.session_state.render_input_text}\n\n"
                        f"[Refinement requested: {_note}]"
                    ) if _note else st.session_state.render_input_text
                    st.session_state["_refine_requested"] = True
                    st.session_state.render_in_progress = True

                st.button(
                    "Refine \u2192", use_container_width=True,
                    disabled=st.session_state.get("render_in_progress", False),
                    on_click=_start_refine,
                )
                if st.session_state.get("_refine_requested"):
                    st.session_state["_refine_requested"] = False
                    if _run_render(
                        st.session_state.get("_refine_input", ""), is_refinement=True,
                        render_context=st.session_state.get("render_context_input", ""),
                        render_mode=st.session_state.get("render_mode_input", "preserve"),
                        platform_format=st.session_state.get("platform_format_input"),
                    ):
                        st.session_state.refinement_used = True
                    st.session_state.render_in_progress = False
                    st.rerun()

            st.markdown("")
            col1, col2, col3, col4, col5, col6 = st.columns([1, 1, 1, 1, 1, 1])
            with col1:
                if st.button("Write again", use_container_width=True):
                    st.session_state.render_input_text = ""
                    st.session_state.render_output = ""
                    st.session_state.refinement_used = False
                    st.rerun()
            with col2:
                if st.button("Start over", type="primary", use_container_width=True):
                    reset_all()
                    st.rerun()
            with col3:
                st.download_button(
                    "Export your profile",
                    data=export_profile(),
                    file_name="voicova-profile.json",
                    mime="application/json",
                    use_container_width=True,
                )
            with col4:
                # Proof this render matches your baseline — not a bare AI
                # score, a comparison against a fingerprint built before
                # this text existed. See authenticity_report.py's docstring
                # for why that distinction matters (the Pangram/deBoer
                # case). Only offered once a completed render + report
                # actually exist — same gating as the Export button above.
                if report and st.session_state.get("render_id"):
                    authenticity_report = build_authenticity_report(
                        report,
                        st.session_state.get("baseline_fingerprint"),
                        render_id=st.session_state["render_id"],
                        created_at=st.session_state["render_completed_at"],
                        scoring_rules_version=scoring_rules_version(),
                    )
                    st.download_button(
                        "Download the record",
                        data=export_authenticity_report_json(authenticity_report),
                        file_name="voicova-authenticity-report.json",
                        mime="application/json",
                        use_container_width=True,
                    )
            with col5:
                # Human-readable export: the JSON exports above are
                # developer-facing outputs; this is the "show my manager"
                # version, clean plain text, pasteable straight into an
                # email or Slack message, no tooling needed to read it.
                # Same gating as "Download the record" since it's built
                # from the same authenticity_report dict.
                if report and st.session_state.get("render_id"):
                    st.download_button(
                        "Download as text",
                        data=export_authenticity_report_text(authenticity_report),
                        file_name="voicova-authenticity-report.txt",
                        mime="text/plain",
                        use_container_width=True,
                    )
            with col6:
                # Branded one-pager (27 Aug 2026), for the agency/client-
                # deliverable use case — see authenticity_report.py's note
                # on export_authenticity_report_text for why plain text
                # alone wasn't judged enough for that case. Same gating,
                # same source dict as the two exports above; this is a
                # presentation layer only, no new data.
                if report and st.session_state.get("render_id"):
                    st.download_button(
                        "Download as PDF",
                        data=export_authenticity_report_pdf(authenticity_report),
                        file_name="voicova-authenticity-report.pdf",
                        mime="application/pdf",
                        use_container_width=True,
                    )

        st.markdown(
            '<div class="microcopy" style="margin-top:2rem;">Voicova keeps your voice.</div>',
            unsafe_allow_html=True
        )


# ============================================================
# Screen 5 — My Voice (Tier 1 v1: overall confidence only, no
# per-dimension breakdown — that's blocked on wiring voxa-profile/
# voxa-calibration's per-dimension confidence through to this app,
# out of scope for this pass. See VOICOVA_Product_2.0_Tier1_Spec.)
# ============================================================

_MY_VOICE_CONFIDENCE_BADGE = {"High": "badge-green", "Medium": "badge-amber", "Low": "badge-red"}

_STABILITY_VERDICT_LABEL = {
    "stable": "Stable", "volatile": "Varies by register", "insufficient_data": "Not enough data",
}
_STABILITY_VERDICT_BADGE = {
    "stable": "badge-green", "volatile": "badge-amber", "insufficient_data": "badge-amber",
}


def _render_dimension_confidence_table(
    observations: list[dict], heading: str, show_flag_control: bool = False
) -> bool:
    """
    Per-dimension Reading/Confidence table — extracted 31 Aug 2026 from
    screen_my_voice (30 Aug 2026 per-dimension confidence work) so
    screen_reveal (the onboarding calibration step) can show the same
    table before the baseline is ever relied on, not just afterward on
    the standing My Voice dashboard. Pure extraction: every line below
    previously lived only inside screen_my_voice, unchanged logic, same
    compute_dimension_confidence call, same .voice-match-table markup.
    No new detection, no new scoring — reads session-state values both
    screens already have by the time they render (see _strengthen_
    baseline_with_sample, called from screen_paste before screen_reveal
    is ever shown).

    Reads st.session_state.flagged_dimensions (31 Aug 2026, calibration
    flag) and passes it through to compute_dimension_confidence on
    every call site, so a flag made on the reveal screen demotes the
    Confidence badge consistently everywhere this table is shown, not
    only where it was flagged.

    show_flag_control (31 Aug 2026): when True, also renders the
    confirm/flag interaction below the table — "flag anything that
    doesn't sound like you" — and returns to session_state rather than
    hand-editing any value (see the reveal screen's docstring for why
    calibration stays a flag, not an edit). Only screen_reveal passes
    True; screen_my_voice's standing dashboard shows the resulting
    badges but doesn't re-solicit flags on every visit — the
    calibration moment is when the person is actively reading each
    dimension, not a returning check-in.

    Returns True if a table was rendered, False if there wasn't enough
    stability data yet (caller decides whether that's worth a fallback
    message).
    """
    stability = st.session_state.get("dimension_stability")
    if not stability or not stability.get("dimensions"):
        return False

    flagged = set(st.session_state.get("flagged_dimensions") or ())

    sample_count = stability.get("sample_count", 0)
    st.markdown(
        f'<div class="sub" style="margin-top:1.4rem;">{heading.format(sample_count=sample_count)}</div>',
        unsafe_allow_html=True,
    )
    dim_confidence = compute_dimension_confidence(
        st.session_state.get("sample_fitness"),
        st.session_state.get("baseline_fingerprint"),
        len(observations),
        stability,
        correction_evidence=st.session_state.get("correction_evidence"),
        flagged_dimensions=flagged,
    )
    rows = []
    for dim, verdict in stability["dimensions"].items():
        label = _VOICE_MATCH_LABELS.get(dim, dim)
        if dim in flagged:
            label += " \u2691"
        badge = _STABILITY_VERDICT_BADGE.get(verdict, "badge-amber")
        reading = _STABILITY_VERDICT_LABEL.get(verdict, verdict)
        conf = dim_confidence.get(dim, "Low")
        conf_badge = _MY_VOICE_CONFIDENCE_BADGE.get(conf, "badge-amber")
        rows.append(
            f"<tr><td>{label}</td>"
            f'<td class="vm-verdict"><span class="badge {badge}">{reading}</span></td>'
            f'<td class="vm-verdict"><span class="badge {conf_badge}">{conf}</span></td></tr>'
        )
    st.markdown(
        '<table class="voice-match-table">'
        '<thead><tr><th>Dimension</th><th class="vm-verdict">Reading</th>'
        '<th class="vm-verdict">Confidence</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="microcopy" style="margin-top:0.4rem;">"Stable" means this trait held '
        'steady across your different writing samples \u2014 likely genuine, not just what one '
        'situation pulled out of you.</div>',
        unsafe_allow_html=True,
    )

    if show_flag_control:
        _render_calibration_flag_control(stability["dimensions"], flagged)

    return True


def _render_calibration_flag_control(
    dimensions: dict[str, str], currently_flagged: set[str]
) -> None:
    """
    Confirm/flag interaction (31 Aug 2026) — lets a person say "this
    reading doesn't sound like me" for a specific dimension, without
    ever hand-editing the underlying number. Deliberately narrow: a
    flag only demotes that dimension's Confidence badge one tier
    (compute_dimension_confidence, voice_engine.py) and nudges toward
    "Deepen your fingerprint" below, which is the one legitimate way
    a reading actually changes — by giving the engine more genuine
    writing to measure, not by the person overriding the measurement
    directly. Keeps the "no LLM in the decision path, reproducible
    output" architecture intact: flagging changes how much a number is
    trusted, never the number itself.

    Session-scoped only for this pass — flagged_dimensions is not yet
    part of the Supabase persistence payload (persistence.py), so a
    flag survives the current session but not a fresh visit on a new
    device-cookie load. Extending persistence would mean a schema
    change on a live table; deliberately left out of this pass rather
    than risk that without it being asked for directly.
    """
    options = list(dimensions.keys())
    if not options:
        return

    st.markdown(
        '<div class="sub" style="margin-top:1.2rem;">'
        'Anything above not sound like you?</div>',
        unsafe_allow_html=True,
    )
    selected_labels = st.multiselect(
        "Flag dimensions",
        options=[_VOICE_MATCH_LABELS.get(d, d) for d in options],
        default=[_VOICE_MATCH_LABELS.get(d, d) for d in options if d in currently_flagged],
        label_visibility="collapsed",
        key="calibration_flag_select",
    )
    label_to_dim = {_VOICE_MATCH_LABELS.get(d, d): d for d in options}
    newly_flagged = {label_to_dim[label] for label in selected_labels}

    if newly_flagged != currently_flagged:
        st.session_state.flagged_dimensions = sorted(newly_flagged)
        st.rerun()


def build_voice_profile_markdown(
    observations: list[dict],
    confidence: str | None,
    baseline_fingerprint: dict | None,
    dimension_stability: dict | None,
    cumulative_words: int,
    cumulative_docs: int,
    updated_at: str | None,
    dimension_confidence: dict[str, str] | None = None,
) -> str:
    """
    Readable, exportable Voice Profile — reformats data already
    computed and already shown on screen_my_voice into a portable
    Markdown document, same underlying data export_profile() (JSON,
    storage.py) already exposes, no new extraction or detection
    logic. Deliberately not editable: the whole point of this
    document is that every line traces back to a measurement, not an
    instruction the person typed in - see the closing note in the
    document itself.

    Dimension labels reused from _VOICE_MATCH_LABELS (already defined
    above for the render-time Voice Report table) rather than a new
    mapping, so the same four dimension names read identically
    wherever they appear in the product.
    """
    lines = [
        "# Your Voice Profile",
        "",
        "*What VOICOVA has actually learned about how you write — a measured baseline, not an instruction.*",
        "",
    ]

    meta_lines = []
    if confidence:
        meta_lines.append(f"**Confidence:** {confidence}")
    if cumulative_words:
        doc_word = "document" if cumulative_docs == 1 else "documents"
        meta_lines.append(f"**Built from:** {cumulative_words} words across {cumulative_docs} {doc_word}")
    if updated_at:
        meta_lines.append(f"**Last updated:** {updated_at}")
    meta_lines.append(f"**Scoring rules version:** {scoring_rules_version()}")
    lines.extend(meta_lines)
    lines.append("")

    if observations:
        lines.append("## What's held steady across your writing")
        lines.append("")
        for obs in observations:
            quote_match = re.search(r'"([^"]{10,})"', obs.get("body", ""))
            lines.append(f"- **{obs['headline']}**")
            if quote_match:
                lines.append(f"  > \"{quote_match.group(1)}\"")
        lines.append("")

    if dimension_stability and dimension_stability.get("dimensions"):
        lines.append("## Stability across registers")
        lines.append("")
        lines.append(
            "How much each dimension held steady across your different writing samples, "
            "versus swinging with the situation:"
        )
        lines.append("")
        lines.append("| Dimension | Reading |" + (" Confidence |" if dimension_confidence else ""))
        lines.append("|---|---|" + ("---|" if dimension_confidence else ""))
        _stability_label = {
            "stable": "Stable — likely genuine",
            "volatile": "Varies by register",
            "insufficient_data": "Not enough samples yet",
        }
        for dim, verdict in dimension_stability["dimensions"].items():
            label = _VOICE_MATCH_LABELS.get(dim, dim)
            row = f"| {label} | {_stability_label.get(verdict, verdict)} |"
            if dimension_confidence:
                row += f" {dimension_confidence.get(dim, 'Low')} |"
            lines.append(row)
        lines.append("")

    if baseline_fingerprint:
        lines.append("## Baseline metrics")
        lines.append("")
        lines.append("| Dimension | Measured value |")
        lines.append("|---|---|")
        for dim, label in _VOICE_MATCH_LABELS.items():
            if dim in baseline_fingerprint:
                lines.append(f"| {label} | {baseline_fingerprint[dim]} |")
        lines.append("")

    lines.append("---")
    lines.append(
        "This document is a snapshot of a measured baseline, not a set of "
        "instructions — nothing here can be edited to change how VOICOVA "
        "renders. Every render is checked against this baseline afterward, "
        "not just written toward it; see the Voice Report on any render "
        "for that check."
    )

    return "\n".join(lines)


def screen_my_voice():
    """
    Standing voice dashboard, not a one-time onboarding artefact —
    answers "what does Voicova think I sound like right now."
    Reuses two things already computed elsewhere rather than adding
    new detection: st.session_state.observations (the fingerprint
    reveal built at onboarding, screen_reveal()) for "what Voicova has
    learned", and st.session_state.confidence (compute_confidence's
    output, already set after onboarding and after every render) for
    the overall confidence badge. No new backend, no new scoring.
    """
    if st.session_state.get("baseline_fingerprint"):
        _shell_sidebar(5)

    st.markdown('<div class="headline">Your voice.</div>', unsafe_allow_html=True)

    confidence = st.session_state.get("confidence")
    if confidence:
        badge_class = _MY_VOICE_CONFIDENCE_BADGE.get(confidence, "badge-amber")
        st.markdown(
            f'<div class="sub">Confidence: '
            f'<span class="badge {badge_class}">{confidence}</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="sub">Not established yet. Write a few renders to build confidence.</div>',
            unsafe_allow_html=True,
        )

    observations = st.session_state.get("observations", [])
    if not observations:
        render_alert("No voice profile yet. Paste some of your writing to get started.", "info")
        return

    st.markdown('<div class="sub" style="margin-top:1.2rem;">What Voicova has learned:</div>', unsafe_allow_html=True)
    for obs in observations:
        quote_match = re.search(r'"([^"]{10,})"', obs.get("body", ""))
        evidence_html = (
            f'<div class="voice-check-evidence">e.g. "{_safe_html(quote_match.group(1))}"</div>'
            if quote_match else ""
        )
        st.markdown(
            f'<div class="voice-check">'
            f'<div class="voice-check-mark">\u2713</div>'
            f'<div><div class="voice-check-text">{_safe_html(obs["headline"])}</div>'
            f'{evidence_html}</div></div>',
            unsafe_allow_html=True,
        )

    # Voice History (29 Aug 2026) — surfaces dimension_stability
    # directly on this screen, not just inside the downloadable Voice
    # Profile document above. Same data, already computed at
    # onboarding and after every "deepen"/"learn from my edit" sample
    # (compute_dimension_stability, voice_engine.py) - no new
    # detection. Reuses the exact .voice-match-table HTML/CSS shape
    # already built for the render-time function-word breakdown table
    # (screen_render's Voice Report), not a new table style.
    # Per-dimension confidence (30 Aug 2026, extracted into a shared
    # helper 31 Aug 2026 — see _render_dimension_confidence_table).
    _render_dimension_confidence_table(
        observations, heading="Stability across your last {sample_count} samples:"
    )

    # Shareable card (27 Aug 2026) — a low-effort, high-reach feature:
    # nothing in the product before this was shareable, and the data
    # here (top trait headlines + confidence) is exactly what's
    # already on screen above, just composed as an image instead of
    # app UI. Privacy: headlines only, never the evidence quotes shown
    # above (those are excerpts of the person's own writing) — see
    # voice_dna_card.py's module docstring for the full reasoning,
    # same stance authenticity_report.py already takes on the baseline.
    st.markdown('<div style="margin-top:1.4rem;"></div>', unsafe_allow_html=True)
    st.download_button(
        "Share your Voice DNA",
        data=build_voice_dna_card_png(observations, confidence),
        file_name="voicova-voice-dna.png",
        mime="image/png",
        use_container_width=True,
    )

    # Voice Profile (Markdown) — the readable, exportable document
    # version of everything shown on this screen, added 29 Aug 2026.
    # Same data already computed for this screen (observations,
    # confidence) plus baseline_fingerprint/dimension_stability
    # (already computed at onboarding/every render, just not
    # displayed here before now) - no new detection or extraction.
    st.download_button(
        "Download Voice Profile",
        data=build_voice_profile_markdown(
            observations=observations,
            confidence=confidence,
            baseline_fingerprint=st.session_state.get("baseline_fingerprint"),
            dimension_stability=st.session_state.get("dimension_stability"),
            dimension_confidence=compute_dimension_confidence(
                st.session_state.get("sample_fitness"),
                st.session_state.get("baseline_fingerprint"),
                len(observations),
                st.session_state.get("dimension_stability"),
                correction_evidence=st.session_state.get("correction_evidence"),
            ),
            cumulative_words=st.session_state.get("cumulative_words", 0),
            cumulative_docs=st.session_state.get("cumulative_docs", 0),
            updated_at=st.session_state.get("_voice_profile_updated_at"),
        ),
        file_name="voicova-voice-profile.md",
        mime="text/markdown",
        use_container_width=True,
    )


def screen_check_draft():
    """
    Check a draft against your voice — compare-only mode, added 26 Aug
    2026. Paste a draft you already have (from anywhere — VOICOVA,
    another tool, a human writer) and see whether it matches your
    saved voice fingerprint. No rewrite, no LLM call: this is a purely
    deterministic read against score_draft_check, same scoring
    machinery the Screen 4 Voice Report uses, just without generating
    anything. Does not touch the render cap — this isn't a render.

    Gated on baseline_fingerprint existing, same pattern as
    screen_my_voice: there's nothing to check a draft against until
    onboarding has produced a baseline.
    """
    if st.session_state.get("baseline_fingerprint"):
        _shell_sidebar(8)

    st.markdown('<div class="headline">Check anything.</div>', unsafe_allow_html=True)

    if not st.session_state.get("baseline_fingerprint"):
        st.markdown(
            '<div class="sub">No voice profile yet. Paste some of your writing on '
            'the Write screen first to build one.</div>',
            unsafe_allow_html=True,
        )
        return

    st.markdown(
        '<div class="sub">Paste a finished draft — yours, someone else\'s, '
        'AI-written, doesn\'t matter. See if it holds up against your voice, '
        'without rewriting it.</div>',
        unsafe_allow_html=True,
    )

    draft_text = st.text_area(
        "Draft to check", height=220, label_visibility="collapsed",
        placeholder="Paste the draft you want to check\u2026",
        key="check_draft_input",
    )

    if st.button("Check against my voice \u2192", type="primary", key="check_draft_submit"):
        if not draft_text or not draft_text.strip():
            st.session_state.check_draft_error = "Paste some text first."
            st.session_state.check_draft_result = None
        else:
            st.session_state.check_draft_error = None
            st.session_state.check_draft_result = score_draft_check(
                st.session_state.baseline_fingerprint,
                draft_text,
                baseline_texts=st.session_state.get("fingerprint_sample_texts", []),
            )

    if st.session_state.get("check_draft_error"):
        render_alert(st.session_state.check_draft_error, "warning")

    result = st.session_state.get("check_draft_result")
    if not result:
        return

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)

    # Calibration flag caveat (31 Aug 2026) — a soft caveat, not a
    # gate: deliberately does not block checking a draft even when the
    # baseline has flagged dimensions. Content Lock hard-fails because
    # a meaning change is a correctness problem; a flagged dimension is
    # a confidence problem, and refusing to show a result the person
    # asked for over reduced (not absent) confidence would be a worse
    # trade than just telling them plainly. Named generically ("some
    # readings"), not per-dimension — the badge on My Voice/reveal
    # already says which ones, this just points there.
    flagged = st.session_state.get("flagged_dimensions")
    if flagged:
        render_alert(
            "Heads up: you flagged some of your voice readings as not "
            "quite right, so this check leans on a softer baseline than "
            "usual. Add another sample on My Voice to firm it up.",
            "warning",
        )

    verdict = result["verdict"]
    _render_verdict_banner(verdict, sub=f'Voice match: {result["tier"]}')

    verdict_mark = "\u2713" if verdict == "PASS" else "!"
    st.markdown(
        f'<div class="voice-check">'
        f'<div class="voice-check-mark">{verdict_mark}</div>'
        f'<div><div class="voice-check-text">{_safe_html(result["evidence"])}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    ai_ok = result["ai_tells_clean"]
    ai_mark = "\u2713" if ai_ok else "!"
    ai_text = (
        "No generic AI phrasing detected." if ai_ok
        else "Generic AI phrasing detected: " + _safe_html(", ".join(result["ai_tells_flagged"]))
    )
    st.markdown(
        f'<div class="voice-check">'
        f'<div class="voice-check-mark">{ai_mark}</div>'
        f'<div><div class="voice-check-text">{ai_text}</div></div></div>',
        unsafe_allow_html=True,
    )

    burrows = result.get("burrows_delta") or {}
    burrows_tier = burrows.get("tier")
    if burrows_tier and "Insufficient" not in burrows_tier:
        burrows_mark = "\u2713" if burrows_tier == "Close" else "!"
        st.markdown(
            f'<div class="voice-check">'
            f'<div class="voice-check-mark">{burrows_mark}</div>'
            f'<div><div class="voice-check-text">Word-choice fingerprint: {burrows_tier} '
            f'to your baseline (function-word analysis, a second independent signal).'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )

    # Fix it — routes a REVIEW-verdict draft into the existing render
    # pipeline, added 29 Aug 2026. Deliberately does NOT auto-render:
    # sets render_input_text (the exact session_state key the Write
    # screen's paste box already reads to pre-fill itself - same
    # mechanism, no new wiring) and hands off to screen 4, where the
    # person clicks "Write as me" themselves, same as any other
    # render - a real render credit is only ever spent on an explicit
    # click, same rule as everywhere else in the product. No new
    # scoring, no new model call path: this is the exact same
    # _run_render the Write screen already uses.
    if verdict == "REVIEW":
        st.markdown("")
        if st.button("Fix it \u2192", type="primary", key="check_draft_fix_it", use_container_width=True):
            st.session_state["render_input_text"] = draft_text
            go_to(4)
            st.rerun()


def screen_history():
    """
    Section 9.4 (Step 5) - list of past renders, click to reopen.
    Reads render_history.get_render_history(device_id), which fails
    open to an empty list on any error - a blank History screen is a
    fine degraded state, not an error to surface to the person.

    Reopen is a simplified two-pane (before/after) view, not the full
    Voice Report from Screen 4 - deliberately, not a shortcut: the
    render_history table only ever stored input_text, output_text,
    context, mode, voice_match (the tier label) and content_lock_pass
    (see write_render_history's call site in _run_render). It never
    stored the full per-dimension delta, risk_reason, or AI-tell
    detail behind a completed render, so reconstructing the full
    three-pane diagnostic view here would mean fabricating numbers
    that were never actually persisted - the same discipline that
    kept the "Learned" field off Screen 4 (Section 8). Before/after
    plus voice match and Content Lock status is exactly what's
    honestly available.
    """
    if st.session_state.get("baseline_fingerprint"):
        _shell_sidebar(6)

    st.markdown('<div class="headline">Past renders.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Your last 50 renders on this device.</div>',
        unsafe_allow_html=True,
    )

    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id
    history = get_render_history(device_id)

    if not history:
        render_alert("No renders yet. Once you write something, it'll show up here.", "info")
        if st.button("Write your first one \u2192", key="history_empty_cta"):
            go_to(4)
            st.rerun()
        return

    for entry in history:
        created_at = entry.get("created_at", "")
        # Supabase returns full ISO timestamps; keep only date + time
        # to the minute for a scannable list, same trim style already
        # used for render_completed_at elsewhere in this file.
        display_date = created_at[:16].replace("T", " ") if created_at else "Unknown date"
        context_label = entry.get("context") or "No context set"
        voice_match = entry.get("voice_match") or "Unrated"
        content_lock_pass = entry.get("content_lock_pass")
        lock_badge = (
            '<span class="badge badge-green">Content Lock: passed</span>' if content_lock_pass
            else '<span class="badge badge-red">Content Lock: flagged</span>' if content_lock_pass is False
            else '<span class="badge badge-amber">Content Lock: unknown</span>'
        )

        with st.expander(f"{display_date} \u2014 {context_label} \u2014 {voice_match}"):
            st.markdown(lock_badge, unsafe_allow_html=True)
            col1, col2 = st.columns(2)
            with col1:
                st.markdown('<div class="sub" style="margin-top:0.6rem;">Before</div>', unsafe_allow_html=True)
                st.text_area(
                    "before", value=entry.get("input_text", ""), height=180,
                    label_visibility="collapsed", disabled=True,
                    key=f"history_before_{entry.get('id')}",
                )
            with col2:
                st.markdown('<div class="sub" style="margin-top:0.6rem;">After</div>', unsafe_allow_html=True)
                st.text_area(
                    "after", value=entry.get("output_text", ""), height=180,
                    label_visibility="collapsed", disabled=True,
                    key=f"history_after_{entry.get('id')}",
                )


# ============================================================
# Router
# ============================================================

screen = st.session_state.screen

if screen == 0:
    screen_landing()
elif screen == 1:
    screen_paste()
elif screen == 2:
    screen_reveal()
elif screen == 3:
    screen_sample2()
elif screen == 4:
    screen_render()
elif screen == 5:
    screen_my_voice()
elif screen == 6:
    screen_history()
elif screen == 7:
    screen_pricing()
elif screen == 8:
    screen_check_draft()
elif screen == 9:
    screen_confirmed()
else:
    go_to(1)
    st.rerun()
