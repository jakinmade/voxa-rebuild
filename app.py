"""
Voxa — User Testing App
Three screens. One goal: the moment the user says "that's me."

Screen 1: Paste your writing → fingerprint reveal
Screen 2: Paste AI text → rewrite in your voice
Screen 3: Feedback → steer → recalibrate

Wired directly to voxa-rebuild packages. No backend dependency.
"""

import asyncio
import os
import sys

import streamlit as st

# ── Path setup — allows running from any directory ────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_REBUILD = os.path.join(_HERE, "voxa-rebuild")
for pkg in ["voxa-core", "voxa-humanisation", "voxa-profile",
            "voxa-rendering", "voxa-calibration", "voxa-governance", "voxa-api"]:
    _pkg_path = os.path.join(_REBUILD, "packages", pkg, "src")
    if _pkg_path not in sys.path:
        sys.path.insert(0, _pkg_path)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Voxa",
    page_icon="◈",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ── Styles ────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@400;500&family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
    background: #0D0D0D;
    color: #E8E4DC;
}

#MainMenu, footer, header { visibility: hidden; }
.block-container {
    padding-top: 3rem;
    padding-bottom: 4rem;
    max-width: 760px;
}

/* Wordmark */
.voxa-mark {
    font-family: 'DM Serif Display', serif;
    font-size: 2.6rem;
    color: #E8E4DC;
    letter-spacing: -0.03em;
    line-height: 1;
    margin-bottom: 0.2rem;
}
.voxa-sub {
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: #555;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    margin-bottom: 3rem;
}

/* Step indicator */
.step-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 2.5rem;
}
.step-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #2A2A2A;
    border: 1px solid #333;
    transition: all 0.3s;
}
.step-dot.active {
    background: #C4A882;
    border-color: #C4A882;
    box-shadow: 0 0 8px rgba(196, 168, 130, 0.4);
}
.step-dot.done {
    background: #3D5A3D;
    border-color: #3D5A3D;
}
.step-line {
    height: 1px;
    width: 32px;
    background: #2A2A2A;
}

/* Section labels */
.section-label {
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    color: #555;
    margin-bottom: 0.6rem;
}

/* Headings */
.screen-heading {
    font-family: 'DM Serif Display', serif;
    font-size: 1.8rem;
    color: #E8E4DC;
    letter-spacing: -0.02em;
    line-height: 1.2;
    margin-bottom: 0.5rem;
}
.screen-sub {
    font-size: 0.88rem;
    color: #777;
    line-height: 1.6;
    margin-bottom: 2rem;
}

/* Text areas */
.stTextArea textarea {
    background: #141414 !important;
    border: 1px solid #2A2A2A !important;
    border-radius: 6px !important;
    color: #E8E4DC !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.9rem !important;
    line-height: 1.7 !important;
    padding: 1rem !important;
    resize: vertical !important;
}
.stTextArea textarea:focus {
    border-color: #C4A882 !important;
    box-shadow: 0 0 0 1px rgba(196, 168, 130, 0.2) !important;
}
.stTextInput input {
    background: #141414 !important;
    border: 1px solid #2A2A2A !important;
    border-radius: 6px !important;
    color: #E8E4DC !important;
    font-family: 'DM Mono', monospace !important;
    font-size: 0.82rem !important;
}
.stTextInput input:focus {
    border-color: #C4A882 !important;
}

/* Primary button */
.stButton > button {
    background: #C4A882 !important;
    color: #0D0D0D !important;
    border: none !important;
    border-radius: 4px !important;
    font-family: 'DM Sans', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    padding: 0.65rem 1.8rem !important;
    letter-spacing: 0.02em !important;
    transition: all 0.2s !important;
}
.stButton > button:hover {
    background: #D4BA96 !important;
    transform: translateY(-1px) !important;
}

/* Ghost button variant */
.ghost-btn > button {
    background: transparent !important;
    color: #777 !important;
    border: 1px solid #2A2A2A !important;
    font-weight: 400 !important;
}
.ghost-btn > button:hover {
    color: #E8E4DC !important;
    border-color: #555 !important;
    transform: none !important;
}

/* Observation cards */
.obs-card {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-radius: 8px;
    padding: 1.4rem 1.6rem;
    margin-bottom: 1rem;
    position: relative;
    transition: border-color 0.2s;
}
.obs-card:hover {
    border-color: #3A3A3A;
}
.obs-headline {
    font-family: 'DM Serif Display', serif;
    font-size: 1.15rem;
    color: #C4A882;
    margin-bottom: 0.5rem;
    letter-spacing: -0.01em;
}
.obs-body {
    font-size: 0.88rem;
    color: #AAA;
    line-height: 1.7;
}
.obs-signal {
    position: absolute;
    top: 1.2rem;
    right: 1.4rem;
    font-family: 'DM Mono', monospace;
    font-size: 0.65rem;
    color: #3A3A3A;
    letter-spacing: 0.1em;
}

/* Rewrite panel */
.rewrite-box {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-radius: 8px;
    padding: 1.6rem;
    font-size: 0.9rem;
    line-height: 1.8;
    color: #E8E4DC;
    white-space: pre-wrap;
    word-break: break-word;
    margin-bottom: 1.5rem;
}
.original-box {
    background: #0D0D0D;
    border: 1px solid #1E1E1E;
    border-radius: 8px;
    padding: 1.2rem 1.4rem;
    font-size: 0.85rem;
    line-height: 1.7;
    color: #555;
    white-space: pre-wrap;
    word-break: break-word;
    margin-bottom: 1.5rem;
}

/* Rule pills */
.rule-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    margin-bottom: 1.5rem;
}
.rule-pill {
    background: #1A1A1A;
    border: 1px solid #2A2A2A;
    border-radius: 20px;
    padding: 0.2rem 0.75rem;
    font-family: 'DM Mono', monospace;
    font-size: 0.68rem;
    color: #666;
    letter-spacing: 0.05em;
}

/* Feedback buttons */
.fb-positive > button {
    background: #1E2E1E !important;
    color: #6DB86D !important;
    border: 1px solid #2A3D2A !important;
}
.fb-positive > button:hover {
    background: #243524 !important;
    transform: none !important;
}
.fb-negative > button {
    background: #2E1E1E !important;
    color: #B86D6D !important;
    border: 1px solid #3D2A2A !important;
}
.fb-negative > button:hover {
    background: #352424 !important;
    transform: none !important;
}

/* Divider */
.voxa-rule {
    border: none;
    border-top: 1px solid #1E1E1E;
    margin: 2rem 0;
}

/* Alert / info box */
.info-box {
    background: #141414;
    border: 1px solid #2A2A2A;
    border-left: 3px solid #C4A882;
    border-radius: 4px;
    padding: 0.9rem 1.2rem;
    font-size: 0.83rem;
    color: #888;
    line-height: 1.6;
    margin-bottom: 1.5rem;
}
.error-box {
    background: #1A1010;
    border: 1px solid #3D2020;
    border-left: 3px solid #B86D6D;
    border-radius: 4px;
    padding: 0.9rem 1.2rem;
    font-size: 0.83rem;
    color: #B86D6D;
    line-height: 1.6;
    margin-bottom: 1.5rem;
}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run_async(coro):
    """Run async coroutine from sync Streamlit context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _set_api_key(key: str):
    os.environ["ANTHROPIC_API_KEY"] = key


def _step_indicator(current: int):
    """Renders 3-step progress dots."""
    dots = []
    for i in range(1, 4):
        if i < current:
            cls = "step-dot done"
        elif i == current:
            cls = "step-dot active"
        else:
            cls = "step-dot"
        dots.append(f'<div class="{cls}"></div>')
        if i < 3:
            dots.append('<div class="step-line"></div>')
    st.markdown(f'<div class="step-row">{"".join(dots)}</div>', unsafe_allow_html=True)


def _rule_pills(top_rules: list[dict]):
    if not top_rules:
        return
    pills = "".join(
        f'<span class="rule-pill">{r["dimension"].replace("_"," ")} · {r["value"]}</span>'
        for r in top_rules
    )
    st.markdown(f'<div class="rule-row">{pills}</div>', unsafe_allow_html=True)


# ── Session state ─────────────────────────────────────────────────────────────

for key, default in [
    ("screen", 1),
    ("api_key", ""),
    ("own_text", ""),
    ("session_id", None),
    ("profile", None),
    ("fingerprint_obs", None),     # list of {id, headline, body}
    ("top_rules", []),
    ("ai_text", ""),
    ("rewrite", ""),
    ("feedback_given", False),
    ("steer_text", ""),
    ("recalibrated", False),
    ("rewrite_v2", ""),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ── Header ────────────────────────────────────────────────────────────────────

st.markdown('<div class="voxa-mark">Voxa</div>', unsafe_allow_html=True)
st.markdown('<div class="voxa-sub">Voice Identity Engine</div>', unsafe_allow_html=True)

# API key (always visible at top — unobtrusive)
api_col, _ = st.columns([2, 3])
with api_col:
    key_input = st.text_input(
        "Anthropic API key",
        value=st.session_state.api_key,
        type="password",
        placeholder="sk-ant-...",
        label_visibility="collapsed",
    )
    if key_input != st.session_state.api_key:
        st.session_state.api_key = key_input
        _set_api_key(key_input)
    if st.session_state.api_key:
        _set_api_key(st.session_state.api_key)

if not st.session_state.api_key:
    st.markdown('<div class="info-box">Enter your Anthropic API key above to begin.</div>', unsafe_allow_html=True)
    st.stop()

st.markdown('<hr class="voxa-rule">', unsafe_allow_html=True)

# ── Screen 1 — Paste your writing → fingerprint reveal ───────────────────────

if st.session_state.screen == 1:
    _step_indicator(1)

    st.markdown('<div class="section-label">Step 1 of 3</div>', unsafe_allow_html=True)
    st.markdown('<div class="screen-heading">Paste something you wrote.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="screen-sub">An email. A proposal section. A message to a colleague. '
        'One page is enough. The engine reads it and shows you what makes your writing yours.</div>',
        unsafe_allow_html=True
    )

    own_text = st.text_area(
        "Your writing",
        value=st.session_state.own_text,
        height=280,
        placeholder="Paste your own writing here — not AI-generated, not heavily edited. The less polished, the better the signal.",
        label_visibility="collapsed",
    )

    word_count = len(own_text.split()) if own_text.strip() else 0
    if word_count > 0:
        colour = "#6DB86D" if word_count >= 150 else "#C4A882" if word_count >= 60 else "#B86D6D"
        st.markdown(
            f'<div style="font-family:\'DM Mono\',monospace;font-size:0.68rem;color:{colour};'
            f'margin-bottom:1rem;">{word_count} words'
            f'{"  ·  good" if word_count >= 150 else "  ·  more gives better signal" if word_count >= 60 else "  ·  aim for 150+ words"}'
            f'</div>',
            unsafe_allow_html=True
        )

    if st.button("Read my writing →", use_container_width=False):
        if not own_text.strip():
            st.markdown('<div class="error-box">Paste some of your writing first.</div>', unsafe_allow_html=True)
        elif word_count < 40:
            st.markdown('<div class="error-box">Too short for a reliable fingerprint. Add more.</div>', unsafe_allow_html=True)
        else:
            st.session_state.own_text = own_text
            with st.spinner("Reading your writing..."):
                try:
                    from voxa_api.onboarding import process_anonymous_paste
                    from voxa_rendering.fingerprint import generate_fingerprint_narrative

                    # Build profile
                    result = process_anonymous_paste(own_text)
                    st.session_state.session_id = result.session_id
                    st.session_state.top_rules = result.top_rules

                    # Retrieve profile from session store for render step
                    from voxa_api.onboarding import _anonymous_sessions
                    session = _anonymous_sessions.get(result.session_id)
                    if session:
                        st.session_state.profile = session.profile

                    # Generate fingerprint narrative (the mirror moment)
                    narrative = _run_async(
                        generate_fingerprint_narrative(own_text, session.profile if session else None)
                    )
                    st.session_state.fingerprint_obs = narrative.observations
                    st.session_state.screen = 2
                    st.rerun()

                except Exception as e:
                    st.markdown(f'<div class="error-box">Engine error: {e}</div>', unsafe_allow_html=True)


# ── Screen 2 — Fingerprint reveal + paste AI text → rewrite ──────────────────

elif st.session_state.screen == 2:
    _step_indicator(2)

    # ── Fingerprint reveal
    st.markdown('<div class="section-label">Your voice fingerprint</div>', unsafe_allow_html=True)
    st.markdown('<div class="screen-heading">Here is what makes your writing yours.</div>', unsafe_allow_html=True)

    obs = st.session_state.fingerprint_obs or []
    for i, o in enumerate(obs):
        signal_pct = ""
        st.markdown(
            f'<div class="obs-card">'
            f'<div class="obs-headline">{o.get("headline", "")}</div>'
            f'<div class="obs-body">{o.get("body", "")}</div>'
            f'</div>',
            unsafe_allow_html=True
        )

    _rule_pills(st.session_state.top_rules)

    st.markdown('<hr class="voxa-rule">', unsafe_allow_html=True)

    # ── Rewrite section
    st.markdown('<div class="section-label">Step 2 of 3</div>', unsafe_allow_html=True)
    st.markdown('<div class="screen-heading">Now paste something AI wrote.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="screen-sub">A draft email, a proposal section, a LinkedIn post — '
        'anything AI generated that you would normally edit into your voice. '
        'Voxa will rewrite it using your fingerprint.</div>',
        unsafe_allow_html=True
    )

    ai_text = st.text_area(
        "AI-generated text",
        value=st.session_state.ai_text,
        height=220,
        placeholder="Paste AI-generated text here...",
        label_visibility="collapsed",
    )

    col1, col2 = st.columns([3, 2])
    with col1:
        run_rewrite = st.button("Apply my voice →", use_container_width=True)
    with col2:
        st.markdown('<div class="ghost-btn">', unsafe_allow_html=True)
        if st.button("← Start over", use_container_width=True):
            for key in ["screen", "own_text", "session_id", "profile", "fingerprint_obs",
                        "top_rules", "ai_text", "rewrite", "feedback_given",
                        "steer_text", "recalibrated", "rewrite_v2"]:
                st.session_state[key] = 1 if key == "screen" else ([] if key == "top_rules" else ("" if isinstance(st.session_state[key], str) else (False if isinstance(st.session_state[key], bool) else None)))
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    if run_rewrite:
        if not ai_text.strip():
            st.markdown('<div class="error-box">Paste some AI-generated text first.</div>', unsafe_allow_html=True)
        else:
            st.session_state.ai_text = ai_text
            profile = st.session_state.profile

            # Rebuild profile from session store — more reliable than stored Pydantic object
            from voxa_api.onboarding import _anonymous_sessions
            session_id = st.session_state.session_id
            live_session = _anonymous_sessions.get(session_id) if session_id else None
            live_profile = live_session.profile if live_session else profile

            if not live_profile:
                st.markdown('<div class="error-box">Profile not found. Please start over.</div>', unsafe_allow_html=True)
            else:
                with st.spinner("Applying your voice..."):
                    try:
                        from voxa_rendering.engine import render
                        from uuid import uuid4

                        output = _run_async(
                            render(
                                input_text=ai_text,
                                profile=live_profile,
                                session_id=uuid4(),
                                context="user_testing",
                            )
                        )

                        if output and output.output_text:
                            st.session_state.rewrite = output.output_text
                            st.session_state.screen = 3
                            st.rerun()
                        else:
                            # Boundary check failed or empty — passthrough to screen 3 anyway
                            # so tester sees something and can give feedback
                            st.session_state.rewrite = ai_text
                            st.session_state.screen = 3
                            st.rerun()

                    except Exception as e:
                        st.markdown(f'<div class="error-box">Render error: {e}</div>', unsafe_allow_html=True)


# ── Screen 3 — Result + feedback + recalibrate ───────────────────────────────

elif st.session_state.screen == 3:
    _step_indicator(3)

    st.markdown('<div class="section-label">Your voice applied</div>', unsafe_allow_html=True)
    st.markdown('<div class="screen-heading">Does this sound like you?</div>', unsafe_allow_html=True)

    # Show rewrite (v2 if recalibrated, else v1)
    display_text = st.session_state.rewrite_v2 or st.session_state.rewrite
    st.markdown(f'<div class="rewrite-box">{display_text}</div>', unsafe_allow_html=True)

    # Original for comparison
    with st.expander("See original AI text"):
        st.markdown(f'<div class="original-box">{st.session_state.ai_text}</div>', unsafe_allow_html=True)

    _rule_pills(st.session_state.top_rules)

    st.markdown('<hr class="voxa-rule">', unsafe_allow_html=True)

    # ── Feedback
    if not st.session_state.feedback_given:
        st.markdown('<div class="section-label">Step 3 of 3 — Feedback</div>', unsafe_allow_html=True)

        fb_col1, fb_col2 = st.columns(2)
        with fb_col1:
            st.markdown('<div class="fb-positive">', unsafe_allow_html=True)
            if st.button("✓  Yes — that sounds like me", use_container_width=True):
                st.session_state.feedback_given = True
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)
        with fb_col2:
            st.markdown('<div class="fb-negative">', unsafe_allow_html=True)
            if st.button("✗  Not quite — let me steer it", use_container_width=True):
                st.session_state.feedback_given = "negative"
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.feedback_given == True:
        # Positive — done
        st.markdown(
            '<div class="info-box" style="border-left-color:#6DB86D;">'
            '✓ Noted. That signal will strengthen your fingerprint over time.'
            '</div>',
            unsafe_allow_html=True
        )
        st.markdown('<div class="ghost-btn">', unsafe_allow_html=True)
        if st.button("← Test another text", use_container_width=False):
            st.session_state.screen = 2
            st.session_state.ai_text = ""
            st.session_state.rewrite = ""
            st.session_state.rewrite_v2 = ""
            st.session_state.feedback_given = False
            st.session_state.recalibrated = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

    elif st.session_state.feedback_given == "negative":
        # Negative — show steer input
        if not st.session_state.recalibrated:
            st.markdown(
                '<div class="section-label" style="margin-top:1rem;">Tell the engine what to change</div>',
                unsafe_allow_html=True
            )
            st.markdown(
                '<div class="screen-sub" style="margin-bottom:1rem;">'
                'Be specific. "More direct." "Shorter sentences." "Remove the warmth — I don\'t write like that." '
                '"The opening is wrong — I lead with the problem, not the context."'
                '</div>',
                unsafe_allow_html=True
            )

            steer = st.text_area(
                "Your steer",
                value=st.session_state.steer_text,
                height=100,
                placeholder="What specifically is wrong with this rewrite?",
                label_visibility="collapsed",
            )

            if st.button("Recalibrate and rewrite →", use_container_width=False):
                if not steer.strip():
                    st.markdown('<div class="error-box">Tell the engine what to change.</div>', unsafe_allow_html=True)
                else:
                    st.session_state.steer_text = steer
                    profile = st.session_state.profile

                    with st.spinner("Recalibrating..."):
                        try:
                            from voxa_calibration.engine import classify_edit
                            from voxa_rendering.engine import render
                            from voxa_profile.builder import merge_profile, build_profile
                            from voxa_humanisation.engine import humanise
                            from voxa_core.enums import SourceType
                            from uuid import uuid4

                            # Classify the steer — is it a voice signal?
                            edit_class = classify_edit(
                                original=st.session_state.rewrite,
                                edited=steer,
                                user_instruction=steer,
                            )

                            # Re-humanise the steer as an explicit preference statement
                            steer_as_preference = f"I prefer: {steer}. My writing style is: {steer}."
                            humanised_steer = humanise(
                                text=steer_as_preference,
                                user_id=profile.user_id,
                                source_type=SourceType.EDIT,
                            )

                            # Merge steer signal into profile
                            if humanised_steer.facts:
                                merge_profile(profile, humanised_steer)

                            # Re-render with updated profile
                            output = _run_async(
                                render(
                                    input_text=st.session_state.ai_text,
                                    profile=profile,
                                    session_id=uuid4(),
                                    context="recalibration",
                                )
                            )

                            if output and output.output_text:
                                st.session_state.rewrite_v2 = output.output_text
                                st.session_state.recalibrated = True
                                st.rerun()
                            else:
                                st.markdown('<div class="error-box">Recalibration render failed. Try again.</div>', unsafe_allow_html=True)

                        except Exception as e:
                            st.markdown(f'<div class="error-box">Recalibration error: {e}</div>', unsafe_allow_html=True)

        else:
            # Recalibrated — show updated rewrite and ask again
            st.markdown(
                '<div class="info-box" style="border-left-color:#C4A882;">'
                f'Recalibrated using your steer: <em>"{st.session_state.steer_text}"</em>'
                '</div>',
                unsafe_allow_html=True
            )

            fb2_col1, fb2_col2 = st.columns(2)
            with fb2_col1:
                st.markdown('<div class="fb-positive">', unsafe_allow_html=True)
                if st.button("✓  Better — that's more me", use_container_width=True):
                    st.session_state.feedback_given = True
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
            with fb2_col2:
                st.markdown('<div class="fb-negative">', unsafe_allow_html=True)
                if st.button("Still not right — start over", use_container_width=True):
                    for key in ["screen", "own_text", "session_id", "profile", "fingerprint_obs",
                                "top_rules", "ai_text", "rewrite", "feedback_given",
                                "steer_text", "recalibrated", "rewrite_v2"]:
                        st.session_state[key] = 1 if key == "screen" else ([] if key == "top_rules" else ("" if isinstance(st.session_state.get(key), str) else (False if isinstance(st.session_state.get(key), bool) else None)))
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

    # Footer nav
    st.markdown('<hr class="voxa-rule">', unsafe_allow_html=True)
    nav_col1, nav_col2 = st.columns(2)
    with nav_col1:
        st.markdown('<div class="ghost-btn">', unsafe_allow_html=True)
        if st.button("← Try different AI text", use_container_width=True):
            st.session_state.screen = 2
            st.session_state.ai_text = ""
            st.session_state.rewrite = ""
            st.session_state.rewrite_v2 = ""
            st.session_state.feedback_given = False
            st.session_state.recalibrated = False
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    with nav_col2:
        st.markdown('<div class="ghost-btn">', unsafe_allow_html=True)
        if st.button("Start completely fresh", use_container_width=True):
            for key in ["screen", "own_text", "session_id", "profile", "fingerprint_obs",
                        "top_rules", "ai_text", "rewrite", "feedback_given",
                        "steer_text", "recalibrated", "rewrite_v2"]:
                st.session_state[key] = 1 if key == "screen" else ([] if key == "top_rules" else ("" if isinstance(st.session_state.get(key), str) else (False if isinstance(st.session_state.get(key), bool) else None)))
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown(
    '<div style="margin-top:3rem;font-family:\'DM Mono\',monospace;font-size:0.62rem;'
    'color:#333;letter-spacing:0.1em;">VOXA · AKINMADE.CO.UK · USER TESTING BUILD</div>',
    unsafe_allow_html=True
)
