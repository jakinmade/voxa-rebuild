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
from authenticity_report import build_authenticity_report, export_authenticity_report_json
from voice_engine import (
    analyse_writing, _analyse_intro,
    compute_baseline_metrics, _merge_baseline,
    _score_sample_fitness, _fitness_gate,
    _score_ai_signal,
    score_semantic_drift, find_source_sentence, splice_dropped_sentence, highlight_flagged_phrases, compute_confidence, compute_risk, compute_risk_reason,
    has_content_integrity_hard_fail,
    score_render_delta, build_voice_report,
    uses_contractions, score_ai_tells, score_restructure_fidelity,
    compute_dimension_stability, confidence_caveat,
    compute_burrows_delta,
    compute_sentence_economy, compute_passive_voice,
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
from persistence import restore_profile_if_available, save_profile_if_available, get_or_create_device_id
from stripe_subscription import create_subscription_checkout, verify_and_record_subscription

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
st.set_page_config(
    page_title="Voicova - Communication Identity",
    page_icon="\U0001F535",
    layout="centered",
    initial_sidebar_state="expanded" if st.session_state.get("_returning_user_sidebar") else "collapsed",
)

# ---- Styles ----
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500;600&display=swap');

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* ---------------------------------------------------------------
       Design tokens. One place to look, same discipline as
       scoring_rules.py — change a value here, not at each call site.
       Kept in sync with .streamlit/config.toml's [theme] block, which
       covers native widgets (buttons, checkboxes, focus rings) CSS
       alone can't reliably reach across Streamlit versions.
       --------------------------------------------------------------- */
    :root {
        --ink: #12172B;
        --body-text: #3C4257;
        --muted: #6B7280;
        --faint: #9AA1B1;
        --canvas: #FFFFFF;
        --surface: #F7F8FB;
        --border: #E4E7EE;
        --accent: #2B4C7E;
        --accent-hover: #1F3A5F;
        --accent-soft: #EAF0F9;
        --success: #1E7D46;
        --success-soft: #E3F5EA;
        --warning: #A5690B;
        --warning-soft: #FDF2DF;
        --danger: #B3382C;
        --danger-soft: #FBE4E2;
        --radius-sm: 6px;
        --radius-md: 10px;
        --radius-lg: 14px;
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
        font-size: 2.1rem;
        font-weight: 700;
        color: var(--ink);
        line-height: 1.15;
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

    /* Your Voice checklist — new in v3, replaces prose observation cards.
       Staggered entrance (22 Aug 2026): this screen is the payoff of
       the whole onboarding flow — the first time someone sees their
       own fingerprint reflected back. It was rendering all four cards
       simultaneously, instantly, same as any other list on the site.
       CSS-only staggered fade/rise, animation-delay set per-card by
       screen_reveal() in Python — no JS, no new dependency, respects
       the existing prefers-reduced-motion rule above (which disables
       all animation/transition wholesale, so this degrades to an
       instant static list for anyone who's asked for that). */
    @keyframes voice-check-in {
        from { opacity: 0; transform: translateY(8px); }
        to   { opacity: 1; transform: translateY(0); }
    }
    .voice-check {
        display: flex;
        align-items: flex-start;
        gap: 0.75rem;
        padding: 0.7rem 0;
        border-bottom: 1px solid var(--border);
        opacity: 0;
        animation: voice-check-in 0.5s ease-out forwards;
    }
    .voice-check-mark {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.4rem;
        height: 1.4rem;
        flex-shrink: 0;
        margin-top: 0.1rem;
        border-radius: 50%;
        background: var(--success-soft);
        color: var(--success);
        font-weight: 700;
        font-size: 0.8rem;
        line-height: 1;
    }
    .voice-check-text {
        font-size: 0.97rem;
        color: var(--ink);
        font-weight: 500;
        line-height: 1.5;
    }
    .voice-check-evidence {
        font-size: 0.83rem;
        color: var(--muted);
        font-style: italic;
        margin-top: 0.2rem;
        line-height: 1.5;
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
        box-shadow: 0 1px 2px rgba(18, 23, 43, 0.04), 0 4px 16px rgba(18, 23, 43, 0.04);
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

    .progress {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 0.5rem;
        margin-bottom: 2.5rem;
        color: var(--border);
        font-size: 0.9rem;
    }
    .progress .active {
        color: var(--accent);
        transform: scale(1.3);
        transition: transform 0.2s ease;
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
    .vr-stat {
        min-width: 100px;
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
        background: var(--surface-2, #f2f2f2);
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
    .content-lock-item {
        display: flex;
        align-items: baseline;
        gap: 0.5rem;
        font-size: 0.85rem;
        line-height: 1.5;
        margin-bottom: 0.3rem;
    }
    .content-lock-item.fail { color: var(--danger); }
    .content-lock-item.pass { color: var(--ink); }
    .content-lock-mark {
        font-family: var(--font-mono);
        font-weight: 600;
        flex-shrink: 0;
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

    div[data-testid="stAlert"] {
        border-radius: var(--radius-sm);
    }

    /* Spinner — brand-matched, 20 Aug 2026. Streamlit's default spin
       icon is unstyled grey, the one visual element in the render/
       onboarding flow that never picked up --accent or --font-sans
       like every other component above. Targets stSpinner's public
       data-testid (documented Streamlit contract, same targeting
       convention as the button rules above) plus the underlying
       <i> icon Streamlit renders the spin animation with. CSS-only,
       zero logic risk — a wrong selector here just means the rule
       doesn't apply, not a functional break, so this hasn't been
       verified against the compiled frontend bundle the way the
       button rules above explicitly were; confirm visually next time
       the app is open. */
    div[data-testid="stSpinner"] {
        color: var(--ink);
        font-family: var(--font-sans);
    }
    div[data-testid="stSpinner"] > div > i {
        color: var(--accent) !important;
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
    _device_id_for_checkout = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = _device_id_for_checkout
    if _checkout_session_id and verify_and_record_subscription(
        _checkout_session_id, _device_id_for_checkout
    ):
        st.session_state["subscription_just_confirmed"] = True
    else:
        st.session_state["subscription_confirm_failed"] = True
    st.query_params.clear()
elif st.query_params.get("payment") == "cancelled":
    st.query_params.clear()


_PROGRESS_STEP_NAMES = ("Paste", "Your voice", "Calibrate", "Write")


def progress_dots(current: int, total: int = 4):
    dots = ""
    for i in range(1, total + 1):
        if i == current:
            dots += '<span class="active">\u25CF</span> '
        else:
            dots += "\u25CB "
    step_name = _PROGRESS_STEP_NAMES[current - 1] if 1 <= current <= len(_PROGRESS_STEP_NAMES) else ""
    st.markdown(
        f'<div class="progress">{dots}'
        f'<span style="font-family:var(--font-mono);font-size:0.72rem;color:var(--faint);'
        f'letter-spacing:0.04em;margin-left:0.4rem;">'
        f'Step {current} of {total}{" \u00b7 " + step_name if step_name else ""}</span></div>',
        unsafe_allow_html=True,
    )


def _deepen_fingerprint_panel(show_caveat_framing: bool = False):
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
    with st.expander(label, expanded=show_caveat_framing):
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
                new_metrics = compute_baseline_metrics(extra)
                st.session_state.baseline_fingerprint = _merge_baseline(
                    st.session_state.get("baseline_fingerprint"), new_metrics
                )
                st.session_state.cumulative_words += len(extra.split())
                st.session_state.cumulative_docs += 1
                extra_obs = analyse_writing(extra)
                existing = st.session_state.observations
                existing_headlines = {o["headline"] for o in existing}
                for obs in extra_obs:
                    if obs["headline"] not in existing_headlines:
                        existing.append(obs)
                        existing_headlines.add(obs["headline"])
                existing.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
                st.session_state.observations = existing[:5]

                # Feed the stability check too - previously this panel
                # only touched the blended baseline, so a sample added
                # here could never actually move the Confidence badge
                # or resolve the caveat that sent someone here in the
                # first place.
                samples = st.session_state.get("fingerprint_samples", [])
                samples.append(new_metrics)
                st.session_state.fingerprint_samples = samples
                st.session_state.dimension_stability = compute_dimension_stability(samples)

                sample_texts = st.session_state.get("fingerprint_sample_texts", [])
                sample_texts.append(extra)
                st.session_state.fingerprint_sample_texts = sample_texts

                # If a Voice Report is already on screen (Screen 4),
                # refresh its Confidence badge in place - the rewritten
                # text itself doesn't change, only how much to trust the
                # baseline it was measured against.
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

                    # Same reasoning as the Confidence refresh above: the
                    # rewritten text on screen doesn't change, but the
                    # extra sample gives function-word Delta a better
                    # (or its first) reference distribution to score
                    # against. Re-run it in place rather than leaving a
                    # stale or "Insufficient baseline samples" reading on
                    # screen after the user just fixed exactly that.
                    render_output = st.session_state.get("render_output")
                    if render_output:
                        updated_sample_texts = st.session_state.get("fingerprint_sample_texts", [])
                        new_burrows_delta = compute_burrows_delta(updated_sample_texts, render_output)
                        st.session_state.function_word_delta = new_burrows_delta
                        report["function_word_delta"] = new_burrows_delta.get("delta")
                        report["function_word_delta_tier"] = new_burrows_delta.get("tier")
                        report["function_word_biggest_divergences"] = new_burrows_delta.get("biggest_divergences", [])

                    st.session_state.voice_report = report

                # Keeps the saved profile in sync with a strengthened
                # fingerprint. Safe to call even when no baseline exists
                # yet (e.g. reached from Screen 2, before Screen 3) —
                # save_profile_if_available() no-ops silently in that
                # case, same fail-open design as everywhere else in
                # persistence.py.
                save_profile_if_available()

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
                st.error("A bit more, at least a sentence or two.")


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
    st.markdown('<div class="sub">Paste your draft. Voicova puts your voice back in.</div>', unsafe_allow_html=True)

    text = st.text_area(
        label="Your writing",
        value=st.session_state.raw_text,
        placeholder="Paste an email, a message, a paragraph - anything you wrote...",
        height=220,
        label_visibility="collapsed",
    )

    col1, col2 = st.columns([3, 1])
    with col1:
        if st.button("Show me my fingerprint \u2192", type="primary", use_container_width=True):
            if not text or not text.strip():
                st.error("Paste something you wrote first.")
            elif len(text.split()) < 10:
                st.error("A bit more. At least a sentence or two.")
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
                    st.rerun()

    fitness = st.session_state.get("sample_fitness")
    nudge = st.session_state.get("fitness_nudge")
    words_so_far = st.session_state.get("cumulative_words", 0)

    if words_so_far > 0 and fitness:
        tier = fitness.get("tier", "thin")
        if nudge:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;color:#A5690B;">{nudge}</div>',
                unsafe_allow_html=True
            )
        elif tier == "gold":
            st.markdown(
                '<div class="microcopy" style="margin-top:0.5rem;color:#1E7D46;">Strong sample. Your fingerprint is ready.</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;">{words_so_far} words submitted. Paste more of your own writing.</div>',
                unsafe_allow_html=True
            )

    st.markdown(
        '<div class="microcopy">No account or signup. Your profile is saved securely so you don\'t have to rebuild it.</div>',
        unsafe_allow_html=True
    )
    with st.expander("What we store, and why"):
        st.markdown(
            "- **Your writing sample and voice fingerprint** — so VOICOVA "
            "recognises your voice next time, without you re-onboarding.\n"
            "- **A summary of your voice profile** — used to write in your "
            "voice on future renders.\n"
            "- **Your render history** (last 50) — so you can revisit past "
            "renders.\n\n"
            "This is tied to a device cookie, not an account or email — "
            "we don't know who you are unless you choose to subscribe. "
            "Clear your cookies and it's gone. No selling, no sharing, "
            "no third-party analytics on this data."
        )


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
        st.success(st.session_state.deepen_success_message)
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
        st.warning("Not enough signal. Paste more of your writing.")
        if st.button("\u2190 Try again"):
            go_to(1)
            st.rerun()
        return

    import re as _re
    for i, obs in enumerate(observations):
        quote_match = _re.search(r'"([^"]{10,})"', obs.get("body", ""))
        evidence_html = (
            f'<div class="voice-check-evidence">e.g. "{quote_match.group(1)}"</div>'
            if quote_match else ""
        )
        st.markdown(f"""
        <div class="voice-check" style="animation-delay: {i * 0.12}s;">
            <div class="voice-check-mark">\u2713</div>
            <div>
                <div class="voice-check-text">{obs['headline']}</div>
                {evidence_html}
            </div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    _deepen_fingerprint_panel()
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
        '<div class="sub">Finish both starters, typed live. Deliberately different situations - '
        'that contrast is what lets us tell your real voice apart from just this one scenario. '
        'Don\'t think it through, don\'t edit - first version only. '
        '<strong>Paste is switched off on these two fields on purpose</strong> — '
        'typing live is what makes the sample real.</div>',
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
        st.markdown(f'<div class="tag-hint" style="margin-top:0.3rem;">{starters[idx]}</div>', unsafe_allow_html=True)
        completions[idx] = paste_guard(value=completions[idx], key=f"starter_{idx}")
        wc = len(completions[idx].split())
        required_word_counts[idx] = wc
        st.markdown(
            f'<div class="microcopy" style="text-align:left;margin-top:0.4rem;">'
            f'{wc} / {SAMPLE2_REQUIRED_MIN_WORDS} words</div>',
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
            st.markdown(f'<div class="tag-hint" style="margin-top:0.8rem;">{starters[i]}</div>', unsafe_allow_html=True)
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
                st.error(
                    f"A little more on both, at least {SAMPLE2_REQUIRED_MIN_WORDS} words each "
                    f"to continue."
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
    incorrectly. Originally built LinkedIn-only (18 Aug 2026), then
    generalised the same session once it became clear the underlying
    convention wasn't LinkedIn-specific (see build_correction_prompt's
    docstring). Same as render_context, this doesn't touch the
    baseline targets.

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

    allowed, used, limit = check_and_reserve_render()
    if not allowed:
        st.session_state.render_error = (
            "We've hit today's render limit while VOICOVA is in early testing. "
            "Please try again tomorrow."
        )
        log.error("render_blocked", reason="daily_cap_reached", used=used, limit=limit, is_refinement=is_refinement)
        return False

    # Step 4 (Section 5.2 / Section 13): the 15-lifetime-render free
    # tier. Resolved here, once, not just inside this check - the same
    # device_id is reused for the render_history write later in this
    # function (see the success path below), rather than each call
    # site resolving its own copy.
    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id
    # Render accounting (Section 15.2 item 2, engineering review
    # response, resolved 21 Aug 2026): "one user render = original
    # generation + its included refinement, one lifetime-counter
    # decrement, not two". Only the ORIGINAL generation reserves a
    # lifetime render; a refinement of that same render is included in
    # the one already spent, not a second draw against the person's 15.
    # Confirmed as a real bug, not hypothetical, before this fix: the
    # reserve call fired unconditionally regardless of is_refinement,
    # so every refinement silently cost a second free render.
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

    import anthropic

    detected_mode = _detect_mode(input_text)
    st.session_state.intent_mode = detected_mode
    log.info(
        "render_start", input_words=len(input_text.split()),
        is_refinement=is_refinement, detected_mode=detected_mode,
    )

    ai_score = _score_ai_signal(input_text)
    observations = st.session_state.observations
    raw_text = st.session_state.get("raw_text", "")
    baseline = st.session_state.get("baseline_fingerprint")

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
            clean = _grammar_fix_pass(clean, client)
            clean = _regex_sweep(clean, keep_contractions=keep_contractions, original_input_text=input_text)
    except Exception:
        st.session_state.render_error = (
            "That didn't go through. Your text is safe, try again."
        )
        log.error("render_failed", reason="llm_call_exception", stage="initial_render", exc_info=True)
        # Release-on-failure (Section 15.2 item 5, 20 Aug 2026): the
        # lifetime cap reserved this render optimistically, before
        # this API call ran (see check_and_reserve_lifetime_render's
        # own docstring for why - same convention as render_cap.py).
        # If the call itself failed, the person never got a render out
        # of it and shouldn't lose one of their 15 for VOICOVA's own
        # API failure. release_reserved_lifetime_render is self-
        # contained and safe to call unconditionally for an original
        # render - it no-ops for an active subscriber (never
        # incremented in the first place) and fails open silently on
        # any Supabase error, same as everything else in that module.
        # Guarded on is_refinement here (21 Aug 2026, render-accounting
        # fix above): a refinement never reserved a lifetime render in
        # the first place, so releasing one on its failure would
        # wrongly hand back a slot from an earlier, successful original
        # render instead.
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
            # General, alignment-based fallback (18 Aug 2026) — runs
            # after the pattern-based fixer above, not instead of it,
            # since it needs the SAME sentence-alignment machinery but
            # answers a structurally different question (does this
            # sentence have a marker the original didn't have at all,
            # regardless of specific wording) rather than matching a
            # known verb pattern. Catches whatever the pattern fixer's
            # enumerated list doesn't — see restore_fabricated_
            # ownership_sentences' own docstring for why pattern
            # enumeration alone can never be complete for this failure
            # class. Safe to always run: it only ever touches a
            # sentence where the aligned original had zero first-person
            # markers, so it can't touch anything the fixer above
            # already correctly left alone.
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

                # Word-level fidelity check, platform_format only —
                # verifies the model actually obeyed "rearrange, don't
                # rewrite" rather than trusting the instruction alone.
                # Confirmed necessary live (18 Aug 2026): a real render
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
        # false-positive this fixes (18 Aug 2026: "curious whether",
        # "i suspect", "i would push back" all appeared verbatim in a
        # real original input and were flagged anyway).
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
        # opinion-dense than the person's baseline. Confirmed live (18
        # Aug 2026): a 72% ownership drift on a genuinely opinionated
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
        # review_gate.py's confirmation wall as of 19 Aug 2026 — see
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
        # check (18 Aug 2026) — this gap existed before that feature,
        # not introduced by it.
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
        # Correction-frequency instrumentation (Section 15.2 item 8,
        # 20 Aug 2026): all four inputs are already in scope at this
        # point in _run_render, computed earlier in this same
        # function - hedge_fixed/modal_fixed/rhythm_fixed/
        # ownership_fixed/directive_fixed from the deterministic
        # fixer pass (~line 1412), correction_prompt from
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
    same principle as the Content Lock banner (VOICOVA UX review, 19
    Aug 2026).

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
    instead of the "sentence(s)" construct that was here before -
    part of what was flagged as reading heavy (19 Aug 2026, F1 test
    render). The reported count itself is still the raw sentence-
    count delta from _check_uncorrected_insertions - an attempt to
    attribute it to specific sentences was tried and reverted the
    same session after it proved unreliable against heavily-
    paraphrased real text (see that function's own docstring)."""
    noun = "sentence" if count == 1 else "sentences"
    return f"Added {count} new {noun} not in the original"


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
    the "here's what you need to know" layer above it (VOICOVA UX
    review, 19 Aug 2026 — Content Lock as visible status, not buried
    diagnostic).

    Reasons list mirrors _build_content_lock_html's four checks so the
    banner's summary and the checklist below it can never disagree
    about what failed.

    lexical_fidelity_breaks (19 Aug 2026) is deliberately NOT one of
    the four "reasons" above and never flips this banner to fail —
    that would contradict the explicit decision (detect_lexical_
    fidelity_breaks' own docstring) that a watchlist hit is informational,
    not a content-integrity failure. Fixing the earlier gap where this
    signal was computed but shown nowhere: it now renders as its own
    amber note, in the SAME banner position either state lands in, so
    it's visible either way rather than silently swallowed - but it's
    styled and worded as a lower-severity notice, not folded into the
    red fail state or the checklist below it.
    """
    dropped = report.get("dropped_entities", [])
    swaps = report.get("attribution_swaps", [])
    lexical_breaks = report.get("lexical_fidelity_breaks", [])
    sentence_growth = (insertion_check or {}).get("sentence_growth", 0)
    new_hedges = (insertion_check or {}).get("new_hedges", [])

    reasons = []
    if dropped:
        reasons.append(f"Facts dropped: {', '.join(dropped)}")
    if swaps:
        reasons.append("Attribution may have changed — check before sending.")
    if sentence_growth:
        reasons.append(_sentence_growth_label(sentence_growth))
    if new_hedges:
        reasons.append(f"New hedging added: {', '.join(new_hedges)}")

    note_html = ""
    if lexical_breaks:
        note_lines = "".join(
            f'<div class="content-lock-banner-note">\u26a0 Worth a look: {b}</div>'
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
    landscape review, 18 Aug 2026) proposed five: names preserved,
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
         f"{len(dropped)} dropped: {', '.join(dropped)}" if dropped else None),
        (not swaps, "Attribution preserved",
         "Whose point this was may have changed — check before sending." if swaps else None),
        (sentence_growth == 0, "No sentences invented",
         _sentence_growth_label(sentence_growth) if sentence_growth else None),
        (not new_hedges, "No new hedging introduced",
         f"Added: {', '.join(new_hedges)}" if new_hedges else None),
    ]

    rows = []
    for passed, label, detail in checks:
        state = "pass" if passed else "fail"
        mark = "\u2713" if passed else "\u2717"
        detail_html = f" — {detail}" if detail else ""
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


def screen_render():
    if not st.session_state.get("_returning_user_sidebar"):
        progress_dots(4)
    _show_deepen_success_if_pending()

    device_id_for_ui = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id_for_ui

    if st.session_state.get("baseline_fingerprint"):
        with st.sidebar:
            _updated_raw = st.session_state.get("_voice_profile_updated_at")
            if _updated_raw:
                try:
                    _updated_dt = datetime.fromisoformat(_updated_raw.replace("Z", "+00:00"))
                    st.caption(f"Your voice · updated {_updated_dt.strftime('%-d %b %Y')}")
                except Exception:
                    st.caption("Your voice · loaded")
            else:
                st.caption("Your voice · loaded")
            if st.button("My Voice \u2192", key="nav_to_my_voice"):
                go_to(5)
                st.rerun()
            if st.button("Past renders \u2192", key="nav_to_history_from_write"):
                go_to(6)
                st.rerun()

    if st.session_state.get("_returning_user_sidebar"):
        st.markdown('<div class="headline">Write as me.</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub">Paste anything you want in your voice. Your fingerprint is loaded and ready.</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="headline">Paste the text to restore.</div>', unsafe_allow_html=True)
        st.markdown('<div class="sub">Paste AI-generated text here. Voicova rewrites it in your voice, using the fingerprint it just built.</div>', unsafe_allow_html=True)

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
    # Section 9.1 / Section 11 decision (19 Aug 2026): default to the
    # last-used context rather than forcing a choice every render.
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
    # Originally LinkedIn-only (18 Aug 2026); generalised the same
    # session once it was clear the underlying convention (short
    # paragraphs, hook-first) wasn't LinkedIn-specific, and a second,
    # genuinely different target (email) was worth adding alongside
    # it rather than stretching one instruction to cover both. A
    # selectbox rather than a second checkbox, since these are
    # mutually exclusive targets, not independent toggles — a render
    # is formatted for exactly one destination or none.
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
    if st.button(
        "Write as me \u2192", type="primary", use_container_width=True,
        disabled=render_in_progress,
    ):
        if not input_text or not input_text.strip():
            st.error("Paste some text first.")
        else:
            st.session_state.render_input_text = input_text
            st.session_state.render_context_input = render_context
            st.session_state.render_mode_input = render_mode
            st.session_state.platform_format_input = platform_format
            st.session_state.render_output = ""
            st.session_state.refinement_used = False
            st.session_state.render_in_progress = True
            st.rerun()

    if st.session_state.get("render_in_progress"):
        _run_render(
            st.session_state.get("render_input_text", ""),
            render_context=st.session_state.get("render_context_input", ""),
            render_mode=st.session_state.get("render_mode_input", "preserve"),
            platform_format=st.session_state.get("platform_format_input"),
        )
        st.session_state.render_in_progress = False
        st.rerun()

    if st.session_state.get("render_error"):
        st.error(st.session_state.render_error)
        if st.session_state.get("render_paywall_hit"):
            # Paywall, not a transient failure - "Try again" would just
            # hit the same cap again. Two plan buttons, same Session.
            # create → redirect → Session.retrieve pattern proven on
            # AQE/CLEARANCE (see stripe_subscription.py's docstring for
            # why this reuses that pattern rather than building new
            # Stripe surface for a subscription specifically).
            pay_col1, pay_col2 = st.columns(2)
            with pay_col1:
                if st.button("Upgrade — £6.99/month", key="upgrade_monthly", use_container_width=True):
                    checkout_url = create_subscription_checkout(device_id_for_ui, plan="monthly")
                    if checkout_url:
                        st.link_button("Continue to payment \u2192", checkout_url, use_container_width=True)
                    else:
                        st.error("Couldn't start checkout. Please try again shortly.")
            with pay_col2:
                if st.button("Upgrade — £49/year", key="upgrade_annual", use_container_width=True):
                    checkout_url = create_subscription_checkout(device_id_for_ui, plan="annual")
                    if checkout_url:
                        st.link_button("Continue to payment \u2192", checkout_url, use_container_width=True)
                    else:
                        st.error("Couldn't start checkout. Please try again shortly.")
        else:
            if st.button("Try again", key="retry_render"):
                last_attempt = st.session_state.get("render_last_attempt", input_text)
                was_refinement = st.session_state.get("render_last_is_refinement", False)
                if _run_render(
                    last_attempt, is_refinement=was_refinement,
                    render_context=st.session_state.get("render_context_input", ""),
                    render_mode=st.session_state.get("render_mode_input", "preserve"),
                    platform_format=st.session_state.get("platform_format_input"),
                ) and was_refinement:
                    st.session_state.refinement_used = True
                st.rerun()

    if st.session_state.get("subscription_just_confirmed"):
        st.success("You're subscribed. Thanks for backing VOICOVA — write away.")
        st.session_state.subscription_just_confirmed = False
    if st.session_state.get("subscription_confirm_failed"):
        st.error(
            "We couldn't confirm that payment. If you were charged, "
            "contact support and we'll sort it out."
        )
        st.session_state.subscription_confirm_failed = False

    output = st.session_state.get("render_output", "")
    if output:
        st.markdown("<hr class='divider'>", unsafe_allow_html=True)
        st.markdown('<div class="headline">Your writing.</div>', unsafe_allow_html=True)

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
            conf_badge_class = {"High": "badge-green", "Medium": "badge-amber", "Low": "badge-red"}
            ai_tell_html = (
                '<span class="badge badge-green">Clean</span>'
                if report.get("ai_tell_clean", True)
                else f'<span class="badge badge-red">Flagged</span>: {"; ".join(report.get("ai_tell_flags", []))}'
            )
            vm_badge = report.get('voice_match_badge', 'badge-amber')
            vm_tier = report.get('voice_match_tier', 'Unrated')
            vm_evidence = report.get('voice_match_evidence', '')
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
                        <span class="badge {conf_badge_class.get(report['confidence'], 'badge-amber')}">{report['confidence']}</span>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label" title="{_metric_gloss['risk']}">Risk</div>
                        <span class="badge {badge_class.get(report['risk'], 'badge-amber')}">{report['risk']}</span>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label" title="{_metric_gloss['ai_tell']}">AI-tell check</div>
                        {ai_tell_html}
                    </div>
                </div>
                <div class="vr-changes">{vm_evidence}</div>
                <details style="margin-top:0.7rem;">
                    <summary style="cursor:pointer;font-family:var(--font-mono);font-size:0.7rem;
                        color:var(--muted);text-transform:uppercase;letter-spacing:0.06em;">
                        Show the per-dimension breakdown
                    </summary>
                    <div style="margin-top:0.5rem;">
                        {_build_voice_match_table_html(st.session_state.get("render_delta") or {})}
                    </div>
                </details>
                {_build_content_lock_html(report, st.session_state.get("render_insertion_check"))}
            </div>
            """, unsafe_allow_html=True)

            # AI-Slop Firewall — outside the raw-HTML block above,
            # since it needs a real st.button (Streamlit widgets can't
            # live inside an unsafe_allow_html string). ai_tell_phrases
            # comes from score_ai_tells' flagged_phrases field (18 Aug
            # 2026) — the raw, individual phrase list, not the
            # pre-joined "AI-typical phrasing found: X, Y, Z" prose
            # string ai_tell_flags carries; parsing that string on the
            # UI side would be fragile against any future wording
            # change to it.
            ai_tell_phrases = report.get("ai_tell_phrases", [])
            if ai_tell_phrases:
                phrase_chips = "".join(
                    f'<span class="ai-tell-phrase">{p}</span>' for p in ai_tell_phrases
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
                    '<div class="microcopy" style="margin-top:0.5rem;color:#B3382C;">'
                    '\u26a0 Check who gets credit before sending. The rewrite may have swapped '
                    'whose point this was.</div>',
                    unsafe_allow_html=True
                )

            dropped = report.get("dropped_entities", [])
            if dropped:
                listed = ", ".join(dropped)
                source_sentence = find_source_sentence(
                    st.session_state.get("render_input_text", ""), dropped[0]
                )
                context_line = (
                    f' Original: "{source_sentence}"' if source_sentence else ""
                )
                st.markdown(
                    f'<div class="microcopy" style="margin-top:0.5rem;color:#B3382C;">'
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
                    '<div class="microcopy" style="margin-top:0.5rem;color:#8A6D1D;">'
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
                    f'background:#fff;border:0.5px solid #E4E7EE;border-radius:10px;'
                    f'padding:14px 16px;margin-bottom:0.6rem;">{highlighted}</div>'
                    f'<div class="microcopy">{note}</div>',
                    unsafe_allow_html=True,
                )
            # Double-render fix (22 Aug 2026): this text_area used to
            # fire unconditionally, so any render with a flagged phrase
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
            st.markdown(
                '<div class="microcopy">Written as you. Not for you.</div>',
                unsafe_allow_html=True
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
                            st.warning(
                                "That doesn't look like a work email, or it's a "
                                "personal provider we don't count as a firm signal."
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
            # Specific, not generic (22 Aug 2026, per friction audit +
            # research on confirmation-copy anti-patterns: NN/g and
            # Intuit's own content design guidelines both flag vague
            # "I understand"/"are you sure" acknowledgments as
            # ineffective - people click through boilerplate without
            # reading it, and it erodes attention for warnings that
            # actually matter. risk_reason was already computed and
            # logged (compute_risk_reason, review_gate.py) but never
            # shown to the person it's about - it just sat in
            # analytics. Showing the actual specific reason, and
            # making the checkbox confirm that specific thing, is the
            # fix backed by that research, not just a tone change.
            reason_text = _risk_reason_copy.get(
                st.session_state.get("risk_reason", ""),
                "This render needs a closer look before you send it.",
            )
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;color:#B3382C;">'
                f'\u26a0 {reason_text} Read the report above before sending.</div>',
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

        # Moved here (17 Aug 2026, JA feedback) from its previous spot
        # between the report warnings and the output text_area - that
        # ordering put an optional "improve your fingerprint" upsell
        # ahead of the actual rewritten text the person came here for,
        # which read as backwards. Runs regardless of gated/show_output
        # state (still relevant even if output is hidden pending
        # confirmation), just positioned after the report+output/gate
        # resolve rather than wedged in the middle of them.
        if report:
            caveat = confidence_caveat(st.session_state.get("dimension_stability"))
            if caveat:
                st.markdown(
                    f'<div class="microcopy" style="margin-top:0.5rem;">{caveat}</div>',
                    unsafe_allow_html=True
                )
                _deepen_fingerprint_panel(show_caveat_framing=True)

        if show_output and st.session_state.get("intent_mode") == "HELP_ME_UNDERSTAND":
            st.markdown("<hr class='divider'>", unsafe_allow_html=True)
            receipt = generate_receipt(st.session_state.session_start, st.session_state.word_count)
            st.markdown(f"""
            <div class="receipt">
                <div class="receipt-title">Your render record</div>
                <div>{receipt['summary']}</div>
                <br>
                <div><strong>Session started:</strong> {receipt['session_started']}</div>
                <div><strong>Words analysed:</strong> {receipt['words_analysed']}</div>
                <div><strong>Rendered:</strong> {receipt['rendered_at']}</div>
            </div>
            """, unsafe_allow_html=True)

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
            if st.button("Refine \u2192", use_container_width=True):
                st.session_state.refinement_tags = chosen_tags
                st.session_state.refinement_freetext = freetext
                refinement_note = ", ".join(chosen_tags)
                if freetext.strip():
                    refinement_note = f"{refinement_note}. {freetext.strip()}" if refinement_note else freetext.strip()
                refined_input = (
                    f"{st.session_state.render_input_text}\n\n"
                    f"[Refinement requested: {refinement_note}]"
                ) if refinement_note else st.session_state.render_input_text
                # Only mark the one-time refinement as used if it actually
                # succeeded — previously this flag was set unconditionally
                # before the call, so a failed render still burned the
                # user's one refinement with nothing to show for it.
                if _run_render(
                    refined_input, is_refinement=True,
                    render_context=st.session_state.get("render_context_input", ""),
                    render_mode=st.session_state.get("render_mode_input", "preserve"),
                    platform_format=st.session_state.get("platform_format_input"),
                ):
                    st.session_state.refinement_used = True
                st.rerun()

        st.markdown("")
        col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
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
        with st.sidebar:
            if st.button("\u2190 Back to Write", key="nav_back_to_write"):
                go_to(4)
                st.rerun()
            if st.button("Past renders \u2192", key="nav_to_history_from_my_voice"):
                go_to(6)
                st.rerun()

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
            '<div class="sub">Not established yet — write a few renders to build confidence.</div>',
            unsafe_allow_html=True,
        )

    observations = st.session_state.get("observations", [])
    if not observations:
        st.info("No voice profile yet. Paste some of your writing to get started.")
        return

    st.markdown('<div class="sub" style="margin-top:1.2rem;">What Voicova has learned:</div>', unsafe_allow_html=True)
    for obs in observations:
        quote_match = re.search(r'"([^"]{10,})"', obs.get("body", ""))
        evidence_html = (
            f'<div class="voice-check-evidence">e.g. "{quote_match.group(1)}"</div>'
            if quote_match else ""
        )
        st.markdown(f"""
        <div class="voice-check">
            <div class="voice-check-mark">\u2713</div>
            <div>
                <div class="voice-check-text">{obs['headline']}</div>
                {evidence_html}
            </div>
        </div>
        """, unsafe_allow_html=True)


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
        with st.sidebar:
            if st.button("\u2190 Back to Write", key="nav_back_to_write_from_history"):
                go_to(4)
                st.rerun()

    st.markdown('<div class="headline">History.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Your last 50 renders on this device.</div>',
        unsafe_allow_html=True,
    )

    device_id = st.session_state.get("_device_id") or get_or_create_device_id()
    st.session_state["_device_id"] = device_id
    history = get_render_history(device_id)

    if not history:
        st.info("No renders yet. Once you write something, it'll show up here.")
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

if screen == 1:
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
else:
    go_to(1)
    st.rerun()
