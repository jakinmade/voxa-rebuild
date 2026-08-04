"""
Voicova — Communication Identity Platform
Streamlit App

"Voicova preserves who you are when you write."

Flow (per v4 frozen spec):
  Screen 1 — Paste your writing (no account, no friction)
  Screen 2 — Fingerprint reveal, as a checklist ("Your Voice")
  Screen 3 — Four sentence starters, typed live, paste blocked
  Screen 4 — Paste AI text, rewrite it in your voice, get the Voice Report,
             one opportunity to refine

Architecture: app.py is UI and routing only. voice_engine.py measures.
prompts.py builds prompt strings and cleans text. storage.py holds
session state. No module here reaches into packages/ — this rebuild is
fully self-contained.
"""

import os
import streamlit as st

from storage import init_state, go_to, reset_all, generate_receipt, export_profile
from voice_engine import (
    analyse_writing, _analyse_intro,
    compute_baseline_metrics, _merge_baseline,
    _score_sample_fitness, _fitness_gate,
    _score_ai_signal,
    score_semantic_drift, compute_confidence, compute_risk,
    score_render_delta, build_voice_report,
    uses_contractions, score_ai_tells,
)
from prompts import (
    _build_voice_dna, _build_system_prompt,
    _detect_mode, apply_intent_mode, _detect_locale,
    _apply_uk_english, _regex_sweep, _grammar_fix_pass,
    build_correction_prompt, merge_starter_evidence,
)
from components.paste_guard import paste_guard

# ---- Page config — must be first ----
st.set_page_config(
    page_title="Voicova - Communication Identity",
    page_icon="\U0001F535",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---- Styles ----
st.markdown("""
<style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    .block-container {
        max-width: 680px;
        padding-top: 3rem;
        padding-bottom: 3rem;
    }

    .tagline {
        font-size: 0.9rem;
        color: #888;
        letter-spacing: 0.05em;
        margin-bottom: 0.5rem;
    }

    .headline {
        font-size: 2rem;
        font-weight: 700;
        color: #1a1a2e;
        line-height: 1.2;
        margin-bottom: 0.3rem;
    }

    .sub {
        font-size: 1rem;
        color: #555;
        margin-bottom: 2rem;
    }

    /* Your Voice checklist — new in v3, replaces prose observation cards */
    .voice-check {
        display: flex;
        align-items: flex-start;
        gap: 0.6rem;
        padding: 0.55rem 0;
        border-bottom: 1px solid #f0f0f5;
    }
    .voice-check-mark {
        color: #2e8b57;
        font-weight: 700;
        font-size: 1rem;
        line-height: 1.5;
    }
    .voice-check-text {
        font-size: 0.95rem;
        color: #222;
        line-height: 1.5;
    }
    .voice-check-evidence {
        font-size: 0.82rem;
        color: #888;
        margin-top: 0.15rem;
    }

    .mode-label {
        font-size: 0.8rem;
        color: #888;
        margin-bottom: 0.5rem;
        font-weight: 500;
        letter-spacing: 0.05em;
    }

    .render-box {
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 1.5rem;
        font-size: 0.95rem;
        line-height: 1.7;
        color: #222;
        white-space: pre-wrap;
    }

    .receipt {
        background: #f0f4ff;
        border: 1px solid #c0d0ff;
        border-radius: 8px;
        padding: 1.2rem;
        font-size: 0.85rem;
        color: #334;
        line-height: 1.6;
    }
    .receipt-title {
        font-weight: 700;
        font-size: 0.9rem;
        color: #1a1a2e;
        margin-bottom: 0.5rem;
    }

    .microcopy {
        font-size: 0.8rem;
        color: #aaa;
        text-align: center;
        margin-top: 0.5rem;
    }

    .progress {
        text-align: center;
        margin-bottom: 2rem;
        color: #ccc;
        font-size: 1.2rem;
        letter-spacing: 0.3em;
    }
    .progress .active {
        color: #1a1a2e;
    }

    .divider {
        border: none;
        border-top: 1px solid #eee;
        margin: 2rem 0;
    }

    /* Voice Report — the differentiated output, new in v3/v4 */
    .voice-report {
        background: #f8f9ff;
        border: 1px solid #e4e6f5;
        border-radius: 10px;
        padding: 1.2rem 1.4rem;
        margin-bottom: 1.2rem;
    }
    .vr-grid {
        display: flex;
        gap: 1.4rem;
        flex-wrap: wrap;
        margin-bottom: 0.8rem;
    }
    .vr-stat {
        min-width: 100px;
    }
    .vr-stat-label {
        font-size: 0.72rem;
        color: #888;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        margin-bottom: 0.15rem;
    }
    .vr-stat-value {
        font-size: 1.4rem;
        font-weight: 700;
        color: #1a1a2e;
    }
    .badge {
        display: inline-block;
        padding: 0.15rem 0.6rem;
        border-radius: 999px;
        font-size: 0.85rem;
        font-weight: 700;
    }
    .badge-green { background: #e3f5ea; color: #1e7d46; }
    .badge-amber { background: #fdf2df; color: #a5690b; }
    .badge-red   { background: #fbe4e2; color: #b3382c; }
    .vr-changes {
        font-size: 0.85rem;
        color: #555;
        border-top: 1px solid #e4e6f5;
        padding-top: 0.6rem;
        margin-top: 0.2rem;
    }

    /* Refinement tags */
    .tag-hint {
        font-size: 0.85rem;
        color: #666;
        margin-bottom: 0.4rem;
    }
</style>
""", unsafe_allow_html=True)

init_state()


def progress_dots(current: int, total: int = 4):
    dots = ""
    for i in range(1, total + 1):
        if i == current:
            dots += f'<span class="active">\u25CF</span> '
        else:
            dots += "\u25CB "
    st.markdown(f'<div class="progress">{dots}</div>', unsafe_allow_html=True)


def _deepen_fingerprint_panel():
    """
    Visible from first use, not gated behind anything — per the v4 spec's
    decision (Section 6/10): fast path is the default, but anyone who
    wants a stronger baseline can reach it without being funnelled there.
    """
    with st.expander("Deepen your fingerprint"):
        st.markdown(
            '<div class="sub" style="margin-bottom:0.8rem;">'
            'Paste more of your own writing. Each sample strengthens the baseline '
            '— useful if you want a higher bar than the fast path gives you.'
            '</div>',
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
                st.success("Added. Your fingerprint just got stronger.")
            else:
                st.error("A bit more — at least a sentence or two.")


# ============================================================
# Screen 1 — Paste something you've written
# ============================================================

def screen_paste():
    progress_dots(1)

    st.markdown('<div class="tagline">VOICOVA</div>', unsafe_allow_html=True)
    st.markdown('<div class="headline">Voicova preserves who you are when you write.</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub">Paste anything you\'ve written. We\'ll show you what it reveals.</div>', unsafe_allow_html=True)

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
                f'<div class="microcopy" style="margin-top:0.5rem;color:#C8962E;">{nudge}</div>',
                unsafe_allow_html=True
            )
        elif tier == "gold":
            st.markdown(
                '<div class="microcopy" style="margin-top:0.5rem;color:#2e8b57;">Strong sample. Your fingerprint is ready.</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;">{words_so_far} words submitted. Paste more of your own writing.</div>',
                unsafe_allow_html=True
            )

    st.markdown('<div class="microcopy">No account needed. Nothing stored.</div>', unsafe_allow_html=True)


# ============================================================
# Screen 2 — Fingerprint reveal, as a checklist ("Your Voice")
# ============================================================

def screen_reveal():
    progress_dots(2)

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
    for obs in observations:
        quote_match = _re.search(r'"([^"]{10,})"', obs.get("body", ""))
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

STARTERS = [
    "Someone just sent over work that missed the brief entirely. Type your reply exactly as it comes to you, first draft, no editing...",
    "A friend just asked what you actually do for work. Answer them right now, in your own words...",
    "You've just decided something that affects someone else, and you have to tell them now. What do you say...",
    "Something someone just said is genuinely getting under your skin. Write down what you're thinking, unfiltered...",
]

SAMPLE2_MIN_WORDS = 40


def screen_sample2():
    progress_dots(3)

    st.markdown('<div class="headline">One more sample.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Finish these four starters, typed live. This proves the sample is genuinely '
        'yours right now, and strengthens your fingerprint. Don\'t think it through, don\'t edit - '
        'first version only.</div>',
        unsafe_allow_html=True
    )

    completions = st.session_state.sample2_completions
    for i, starter in enumerate(STARTERS):
        st.markdown(f'<div class="tag-hint" style="margin-top:0.8rem;">{starter}</div>', unsafe_allow_html=True)
        completions[i] = paste_guard(value=completions[i], key=f"starter_{i}")
    st.session_state.sample2_completions = completions

    total_words = sum(len(c.split()) for c in completions)
    st.markdown(
        f'<div class="microcopy" style="text-align:left;margin-top:0.4rem;">{total_words} / {SAMPLE2_MIN_WORDS} words</div>',
        unsafe_allow_html=True
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("\u2190 Back", use_container_width=True):
            go_to(2)
            st.rerun()
    with col2:
        if st.button("Continue \u2192", type="primary", use_container_width=True):
            combined = " ".join(c.strip() for c in completions if c.strip())
            if not combined:
                st.error("Complete at least one starter first.")
            elif total_words < SAMPLE2_MIN_WORDS:
                st.error(f"A little more — {SAMPLE2_MIN_WORDS} words total across the four, {total_words} so far.")
            else:
                intro_obs = _analyse_intro(combined)
                existing = st.session_state.observations
                existing_headlines = {o["headline"] for o in existing}
                for obs in intro_obs:
                    if obs["headline"] not in existing_headlines:
                        existing.append(obs)
                        existing_headlines.add(obs["headline"])
                existing.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
                st.session_state.observations = existing[:5]

                new_metrics = compute_baseline_metrics(combined)
                # Keep this unmerged - the correction pass needs the
                # starter-only baseline as a second, independent check,
                # separate from the blended one used for Voice Match.
                st.session_state.starter_baseline = new_metrics
                st.session_state.baseline_fingerprint = _merge_baseline(
                    st.session_state.get("baseline_fingerprint"), new_metrics
                )
                go_to(4)
                st.rerun()


# ============================================================
# Screen 4 — Render, Voice Report, one refinement
# ============================================================

def _run_render(input_text: str):
    """The actual generation pipeline. Kept as one function so the
    refinement re-render below can call the same path."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    try:
        api_key = st.secrets["ANTHROPIC_API_KEY"] or api_key
    except Exception:
        pass

    if not api_key:
        st.error("API key missing.")
        return

    import anthropic

    detected_mode = _detect_mode(input_text)
    st.session_state.intent_mode = detected_mode

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

    voice_dna = _build_voice_dna(observations, fingerprint_corpus or raw_text, baseline, ai_score)
    mode_instruction = apply_intent_mode(input_text, detected_mode)
    word_count_input = len(input_text.split())

    # Baseline-driven, not assumed: does this person's own writing use
    # contractions? Only strip them from the output if their own writing
    # doesn't have them either.
    keep_contractions = uses_contractions(fingerprint_corpus) if fingerprint_corpus else False

    system = _build_system_prompt(
        voice_dna=voice_dna, mode_instruction=mode_instruction,
        word_count_input=word_count_input, ai_score=ai_score, baseline=baseline,
    )

    client = anthropic.Anthropic(api_key=api_key)
    with st.spinner("Writing as you..."):
        response = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=4096,
            system=system, messages=[{"role": "user", "content": input_text}],
        )
        clean = response.content[0].text
        clean = _regex_sweep(clean, keep_contractions=keep_contractions)
        if st.session_state.get("locale", "uk") == "uk":
            clean = _apply_uk_english(clean)
        clean = _grammar_fix_pass(clean, client)
        clean = _regex_sweep(clean, keep_contractions=keep_contractions)

        if baseline:
            delta = score_render_delta(baseline, clean)
            semantic = score_semantic_drift(input_text, clean)

            # Second, independent check against the starter-only baseline
            # (unblended with sample1) - catches drift the blended average
            # can dilute below the correction threshold. Rule-based, no
            # extra API call: reuses score_render_delta, only widens what
            # feeds the one correction call that already fires conditionally.
            starter_baseline = st.session_state.get("starter_baseline")
            starter_delta = score_render_delta(starter_baseline, clean) if starter_baseline else None
            correction_delta = merge_starter_evidence(delta, starter_delta)

            correction_prompt = build_correction_prompt(correction_delta, semantic)
            if correction_prompt:
                try:
                    correction_response = client.messages.create(
                        model="claude-sonnet-4-6", max_tokens=4096,
                        system=correction_prompt, messages=[{"role": "user", "content": clean}],
                    )
                    corrected = correction_response.content[0].text
                    corrected = _regex_sweep(corrected, keep_contractions=keep_contractions)
                    if st.session_state.get("locale", "uk") == "uk":
                        corrected = _apply_uk_english(corrected)
                    clean = corrected
                    delta = score_render_delta(baseline, clean)
                    semantic = score_semantic_drift(input_text, clean)
                except Exception:
                    pass  # correction pass failed — keep the original render

            # Measured verification gate, not a trusted fix-and-hope step.
            # If anything survived the sweeps, run one more deterministic
            # pass (free, no API call) and re-measure. If it's still not
            # clean after that, say so honestly in the report rather than
            # shipping AI-tell contaminated text as if it were verified.
            ai_tells = score_ai_tells(clean)
            if not ai_tells["clean"]:
                clean = _regex_sweep(clean, keep_contractions=keep_contractions)
                ai_tells = score_ai_tells(clean)

            confidence = compute_confidence(
                st.session_state.get("sample_fitness"), baseline, len(observations)
            )
            risk = compute_risk(delta, semantic, ai_tells)
            st.session_state.render_delta = delta
            st.session_state.semantic_drift = semantic
            st.session_state.confidence = confidence
            st.session_state.risk = risk
            st.session_state.ai_tells = ai_tells
            st.session_state.voice_report = build_voice_report(delta, semantic, confidence, risk, ai_tells)
        else:
            st.session_state.render_delta = None
            st.session_state.voice_report = None

        st.session_state.render_output = clean


def screen_render():
    progress_dots(4)

    st.markdown('<div class="headline">Paste the text to restore.</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub">Paste AI-generated text here. Voicova rewrites it in your voice, using the fingerprint it just built.</div>', unsafe_allow_html=True)

    input_text = st.text_area(
        "input", value=st.session_state.get("render_input_text", ""),
        placeholder="Paste AI-generated text here — an email draft, a LinkedIn post, a proposal section...",
        height=220, label_visibility="collapsed",
    )

    if st.button("Write as me \u2192", type="primary", use_container_width=True):
        if not input_text or not input_text.strip():
            st.error("Paste some text first.")
        else:
            st.session_state.render_input_text = input_text
            st.session_state.render_output = ""
            st.session_state.refinement_used = False
            _run_render(input_text)
            st.rerun()

    output = st.session_state.get("render_output", "")
    if output:
        st.markdown("<hr class='divider'>", unsafe_allow_html=True)
        st.markdown('<div class="headline">Your writing.</div>', unsafe_allow_html=True)

        import hashlib
        output_key = "out_" + hashlib.md5(output[:50].encode()).hexdigest()[:8]
        st.text_area(
            label="output", value=output, height=350,
            label_visibility="collapsed", key=output_key,
        )
        st.markdown(
            '<div class="microcopy">The engine wrote as you. Not for you.</div>',
            unsafe_allow_html=True
        )

        report = st.session_state.get("voice_report")
        if report:
            badge_class = {"Low": "badge-green", "Medium": "badge-amber", "High": "badge-red"}
            conf_badge_class = {"High": "badge-green", "Medium": "badge-amber", "Low": "badge-red"}
            changes_html = (
                " \u00b7 ".join(report["biggest_changes"])
                if report["biggest_changes"] else "No significant drift from your baseline."
            )
            ai_tell_html = (
                '<span class="badge badge-green">Clean</span>'
                if report.get("ai_tell_clean", True)
                else f'<span class="badge badge-red">Flagged</span> — {"; ".join(report.get("ai_tell_flags", []))}'
            )
            st.markdown(f"""
            <div class="voice-report">
                <div class="vr-grid">
                    <div class="vr-stat">
                        <div class="vr-stat-label">Voice Match</div>
                        <div class="vr-stat-value">{report['voice_match']}%</div>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label">Semantic Match</div>
                        <div class="vr-stat-value">{report['semantic_match']}%</div>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label">Confidence</div>
                        <span class="badge {conf_badge_class.get(report['confidence'], 'badge-amber')}">{report['confidence']}</span>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label">Risk</div>
                        <span class="badge {badge_class.get(report['risk'], 'badge-amber')}">{report['risk']}</span>
                    </div>
                    <div class="vr-stat">
                        <div class="vr-stat-label">AI-tell check</div>
                        {ai_tell_html}
                    </div>
                </div>
                <div class="vr-changes">Biggest changes: {changes_html}</div>
            </div>
            """, unsafe_allow_html=True)

        if st.session_state.get("intent_mode") == "HELP_ME_UNDERSTAND":
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
        if not st.session_state.refinement_used:
            st.markdown("<hr class='divider'>", unsafe_allow_html=True)
            st.markdown('<div class="tag-hint">Not quite right? You get one refinement.</div>', unsafe_allow_html=True)
            tag_options = ["Too formal", "Too blunt", "Doesn't sound like me", "Too long", "Missing my directness"]
            chosen_tags = st.multiselect("What's off", tag_options, label_visibility="collapsed", key="refine_tags")
            freetext = st.text_area(
                "More detail (optional)", placeholder="Anything else specific...",
                height=80, key="refine_freetext",
            )
            if st.button("Refine \u2192", use_container_width=True):
                st.session_state.refinement_used = True
                st.session_state.refinement_tags = chosen_tags
                st.session_state.refinement_freetext = freetext
                refinement_note = ", ".join(chosen_tags)
                if freetext.strip():
                    refinement_note = f"{refinement_note}. {freetext.strip()}" if refinement_note else freetext.strip()
                refined_input = (
                    f"{st.session_state.render_input_text}\n\n"
                    f"[Refinement requested: {refinement_note}]"
                ) if refinement_note else st.session_state.render_input_text
                _run_render(refined_input)
                st.rerun()

        st.markdown("")
        col1, col2, col3 = st.columns([1, 1, 1])
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

    st.markdown(
        '<div class="microcopy" style="margin-top:2rem;">Voicova keeps your voice.</div>',
        unsafe_allow_html=True
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
else:
    go_to(1)
    st.rerun()
