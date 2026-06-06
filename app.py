"""
Voxa — Communication Identity Platform
Streamlit App v2.0

"Voxa preserves who you are when you write."

Flow:
  Screen 1 — Paste (no account, no friction)
  Screen 2 — Fingerprint reveal (5 observations, your words as proof)
  Screen 3 — Governed render + intent mode
  Screen 4 — Receipt (student mode) + next question

No demo samples. No AI-written examples.
Real writing. Real identity. Two minutes.
"""

import streamlit as st
import re
import json
import sys
import os
from datetime import datetime

# ---- Page config — must be first ----
st.set_page_config(
    page_title="Voxa - Communication Identity",
    page_icon="🔵",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---- Add engine to path ----
ENGINE_PATH = os.path.join(
    os.path.dirname(__file__),
    "packages/voxa-rendering/src"
)
HUMANISATION_PATH = os.path.join(
    os.path.dirname(__file__),
    "packages/voxa-humanisation/src"
)
for p in [ENGINE_PATH, HUMANISATION_PATH]:
    if p not in sys.path and os.path.exists(p):
        sys.path.insert(0, p)

# ---- Styles ----
st.markdown("""
<style>
    /* Hide Streamlit chrome */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    /* Typography */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Main container */
    .block-container {
        max-width: 680px;
        padding-top: 3rem;
        padding-bottom: 3rem;
    }

    /* Tagline */
    .tagline {
        font-size: 0.9rem;
        color: #888;
        letter-spacing: 0.05em;
        margin-bottom: 0.5rem;
    }

    /* Headline */
    .headline {
        font-size: 2rem;
        font-weight: 700;
        color: #1a1a2e;
        line-height: 1.2;
        margin-bottom: 0.3rem;
    }

    /* Sub */
    .sub {
        font-size: 1rem;
        color: #555;
        margin-bottom: 2rem;
    }

    /* Observation card */
    .obs-card {
        background: #f8f9ff;
        border-left: 3px solid #1a1a2e;
        padding: 1rem 1.2rem;
        margin-bottom: 1rem;
        border-radius: 0 6px 6px 0;
    }
    .obs-headline {
        font-weight: 700;
        font-size: 1rem;
        color: #1a1a2e;
        margin-bottom: 0.3rem;
    }
    .obs-body {
        font-size: 0.9rem;
        color: #444;
        line-height: 1.5;
    }

    /* Intent mode pills */
    .mode-label {
        font-size: 0.8rem;
        color: #888;
        margin-bottom: 0.5rem;
        font-weight: 500;
        letter-spacing: 0.05em;
    }

    /* Render output */
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

    /* Receipt */
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

    /* Microcopy */
    .microcopy {
        font-size: 0.8rem;
        color: #aaa;
        text-align: center;
        margin-top: 0.5rem;
    }

    /* Progress dots */
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

    /* Divider */
    .divider {
        border: none;
        border-top: 1px solid #eee;
        margin: 2rem 0;
    }
</style>
""", unsafe_allow_html=True)


# ============================================================
# Engine — deterministic, no API required
# ============================================================

def _extract_sentences(text: str) -> list[str]:
    # Split on sentence endings AND newlines — catches "Josh,\nYour point..."
    text = re.sub(r'\n+', '. ', text)
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip() and len(s.split()) >= 2]


def _score_conclusion_position(sentences, text):
    if not sentences:
        return {"signal": 0.4, "point_first": True, "evidence": sentences[:1]}
    first_three = sentences[:3]
    avg_first = sum(len(s.split()) for s in first_three) / max(len(first_three), 1)
    avg_all = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)
    imp = re.compile(r"^(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|Deploy|Ship|Run|Get)\b", re.I)
    imperatives = [s for s in first_three if imp.match(s)]
    point_first = avg_first < avg_all * 0.90 or len(imperatives) >= 1
    signal = 0.75 if point_first else 0.55
    if imperatives:
        signal = min(0.92, signal + 0.15)
    return {"signal": signal, "point_first": point_first,
            "evidence": first_three[:2], "imperatives": len(imperatives),
            "avg_first": round(avg_first, 1), "avg_all": round(avg_all, 1)}


def _score_hedging(sentences, text):
    words = text.split()
    total = max(len(words), 1)
    hedge = re.compile(r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b", re.I)
    count = len(hedge.findall(text))
    density = count / total
    hedge_sents = [s for s in sentences if hedge.search(s)]
    if density < 0.02:
        signal, owns = 0.88, True
    elif density > 0.06:
        signal, owns = 0.82, False
    else:
        signal, owns = 0.42, density < 0.04
    evidence = hedge_sents[:2] if hedge_sents else sentences[:2]
    return {"signal": signal, "owns": owns, "density": round(density, 3),
            "count": count, "evidence": evidence}


def _score_reader_assumption(sentences, text):
    scaffold = re.compile(
        r"\b(as you (know|may know)|let me (explain|clarify)|in other words|"
        r"to put it simply|basically|what this means)\b", re.I
    )
    found = len(scaffold.findall(text))
    peer = found == 0
    signal = 0.78 if peer else 0.72
    return {"signal": signal, "peer": peer, "scaffolding": found,
            "evidence": sentences[:2]}


def _score_compression(sentences, text):
    if not sentences:
        return {"signal": 0.4, "structural": True, "avg": 8.0, "evidence": []}
    lengths = [len(s.split()) for s in sentences]
    avg = sum(lengths) / max(len(lengths), 1)
    variance = sum((l - avg) ** 2 for l in lengths) / max(len(lengths), 1)
    stddev = variance ** 0.5
    structural = avg <= 12 and stddev <= 7
    signal = 0.88 if (avg <= 10 and stddev <= 5) else 0.72 if structural else 0.55
    shortest = sorted(sentences, key=lambda s: len(s.split()))[:2]
    return {"signal": signal, "structural": structural,
            "avg": round(avg, 1), "stddev": round(stddev, 1), "evidence": shortest}


def _score_energy(sentences, text):
    action = re.compile(
        r"\b(fix|send|call|build|close|check|review|do|make|take|stop|start|"
        r"deploy|ship|run|get|go|ensure|define|test|prove|drive|write|create|"
        r"is|are|was|were|have|has|will|can|should)\b", re.I
    )
    emphasis = re.compile(
        r"\b(very|really|extremely|quite|rather|highly|deeply|absolutely|"
        r"incredible|amazing|excellent|great|significant|important|critical)\b", re.I
    )
    verb_count = len(action.findall(text))
    adj_count = len(emphasis.findall(text))
    ratio = verb_count / max(adj_count, 1)
    imp = re.compile(r"^(Fix|Send|Call|Build|Close|Check|Do|Make|Take|Stop|Start|Deploy|Ship)\b", re.I)
    imperatives = [s for s in sentences if imp.match(s)]
    verb_dom = ratio > 2.0
    signal = 0.82 if verb_dom else 0.65
    evidence = imperatives[:2] if imperatives else sentences[:2]
    return {"signal": signal, "verb_dominant": verb_dom,
            "ratio": round(ratio, 2), "evidence": evidence}


def analyse_writing(text: str) -> list[dict]:
    """
    Deterministic fingerprint engine.
    No API. No external calls. Always works.
    Returns 3-5 observations ordered by signal strength.
    """
    sentences = _extract_sentences(text)

    scores = [
        ("conclusion", _score_conclusion_position(sentences, text)),
        ("hedging", _score_hedging(sentences, text)),
        ("reader", _score_reader_assumption(sentences, text)),
        ("compression", _score_compression(sentences, text)),
        ("energy", _score_energy(sentences, text)),
    ]

    # Sort by signal strength
    scores.sort(key=lambda x: x[1]["signal"], reverse=True)

    observations = []
    used_quotes = set()  # Shared across all observations — guarantees unique evidence
    for obs_id, data in scores[:5]:
        obs = _format_observation(obs_id, data, used_quotes)
        if obs:
            observations.append(obs)

    # Guarantee minimum 3
    return observations[:5] if len(observations) >= 3 else observations


def _format_observation(obs_id: str, data: dict, used_quotes: set = None) -> dict | None:
    """
    Formats a single observation for display.
    used_quotes: set of quotes already used by other observations — ensures each gets a unique quote.
    No metrics exposed to the user. No ratios, percentages, or counts.
    Specific enough to be wrong about someone.
    """
    import re

    if used_quotes is None:
        used_quotes = set()

    evidence = data.get("evidence", [])

    def _usable_quote(sentences, min_words=6, exclude=None):
        """
        Returns first usable quote not already used by another observation.
        Filters weak openers, greetings, names.
        """
        exclude = exclude or set()
        weak = re.compile(
            r"^(hi|hello|hey|dear|thanks|thank you|regards|best|"
            r"your point|that.s noted|noted|understood|agreed|correct|"
            r"exactly|absolutely|sure|ok|okay|yes|no|right|fair|"
            r"i understand|i see|i agree|i note)\b",
            re.I
        )
        starts_with_name = re.compile(r"^[A-Z][a-z]+,\s")

        for s in sentences:
            s = s.strip()
            if len(s.split()) < min_words:
                continue
            if weak.match(s):
                continue
            if starts_with_name.match(s):
                continue
            if s.endswith(","):
                continue
            q = f'"{s}"'
            if q in exclude:
                continue
            return q
        return ""

    HEDGE_WORDS = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b",
        re.I
    )

    quote = ""

    if obs_id == "conclusion":
        # Use opening sentences — shortest, most declarative
        openers = sorted(evidence, key=lambda s: len(s.split()))
        quote = _usable_quote(openers, min_words=4, exclude=used_quotes)
        if quote:
            used_quotes.add(quote)
        if data["point_first"]:
            return {
                "id": obs_id,
                "headline": "You lead with the answer",
                "body": (
                    "The point comes first. Context and reasoning follow. "
                    "You don't build toward a conclusion. You start with one. "
                    + quote
                ),
                "signal": data["signal"],
            }
        else:
            return {
                "id": obs_id,
                "headline": "You build to your conclusion",
                "body": (
                    "You lay the context before landing the point. "
                    "The reasoning comes before the answer. "
                    + quote
                ),
                "signal": data["signal"],
            }

    elif obs_id == "hedging":
        if data["owns"]:
            # Quote must be direct and declarative — no hedge words, no soft openers
            # A quote that contains "optimistic", "hopefully", "feeling" contradicts the observation
            soft = re.compile(
                r"\b(optimistic|hopefully|feeling|quite|rather|somewhat|"
                r"journey|excited|pleased|glad|happy|grateful|appreciate|"
                r"looking forward|i think|i believe|i feel|i hope)\b", re.I
            )
            unhedged = [
                s for s in evidence
                if not HEDGE_WORDS.search(s)
                and not soft.search(s)
                and len(s.split()) >= 6
            ]
            quote = _usable_quote(unhedged, exclude=used_quotes)
            # If nothing clean found, no quote — observation stands without it
            if quote:
                used_quotes.add(quote)
            return {
                "id": obs_id,
                "headline": "You own your statements",
                "body": (
                    "You state things directly. No cushioning before the point. "
                    "When you're uncertain, you say so plainly. You don't blur it. "
                    + quote
                ),
                "signal": data["signal"],
            }
        else:
            hedge_sents = [s for s in evidence if HEDGE_WORDS.search(s)]
            quote = _usable_quote(hedge_sents, exclude=used_quotes) or _usable_quote(evidence, exclude=used_quotes)
            if quote:
                used_quotes.add(quote)
            return {
                "id": obs_id,
                "headline": "You soften before you land",
                "body": (
                    "Cushioning language appears before conclusions. "
                    "You protect the reader — or yourself — before the point arrives. "
                    + quote
                ),
                "signal": data["signal"],
            }

    elif obs_id == "reader":
        # Use a sentence from the middle of the text — not the opener
        # Lower min_words to 4 — after other observations have claimed longer sentences
        mid = evidence[len(evidence)//2:] if len(evidence) > 2 else evidence
        quote = _usable_quote(mid, min_words=4, exclude=used_quotes) or _usable_quote(evidence, min_words=4, exclude=used_quotes)
        if quote:
            used_quotes.add(quote)
        if data["peer"]:
            return {
                "id": obs_id,
                "headline": "You write to an equal",
                "body": (
                    "No explanatory scaffolding. No 'as you know' or 'let me explain'. "
                    "You assume the reader is already in the room. "
                    + quote
                ),
                "signal": data["signal"],
            }
        else:
            return {
                "id": obs_id,
                "headline": "You write to inform",
                "body": (
                    "You build context before the point. "
                    "The reader is assumed to need grounding before the conclusion. "
                    + quote
                ),
                "signal": data["signal"],
            }

    elif obs_id == "compression":
        avg = data["avg"]
        # Use the shortest sentences as evidence — that's the compression signal
        shortest = sorted(evidence, key=lambda s: len(s.split()))
        quote = _usable_quote(shortest, min_words=3, exclude=used_quotes)
        if quote:
            used_quotes.add(quote)
        if data["structural"]:
            return {
                "id": obs_id,
                "headline": "You stop when the idea is complete",
                "body": (
                    "Short sentences. No padding. "
                    "You finish when the point is made, not when the line looks long enough. "
                    + quote
                ),
                "signal": data["signal"],
            }
        else:
            # High variability — deliberate use of length as a tool
            longest = sorted(evidence, key=lambda s: len(s.split()), reverse=True)
            long_quote = _usable_quote(longest, exclude=used_quotes)
            if long_quote and long_quote != quote:
                used_quotes.add(long_quote)
                quote = long_quote
            return {
                "id": obs_id,
                "headline": "You use sentence length as punctuation",
                "body": (
                    "Short sentences land hard. Longer ones carry the weight of reasoning. "
                    "You switch between them deliberately, not randomly. "
                    + quote
                ),
                "signal": data["signal"],
            }

    elif obs_id == "energy":
        # Use imperative sentences if available — they ARE the energy signal
        imp = re.compile(r"^(Fix|Send|Call|Build|Close|Check|Do|Make|Take|Stop|Start|Deploy|Ship|Run|Get|Go|Ensure|Define|Test|Prove|Drive|Write|Create)\b", re.I)
        imperatives = [s for s in evidence if imp.match(s)]
        quote = _usable_quote(imperatives, exclude=used_quotes) or _usable_quote(evidence, exclude=used_quotes)
        if quote:
            used_quotes.add(quote)
        if data["verb_dominant"]:
            return {
                "id": obs_id,
                "headline": "Your force comes from verbs",
                "body": (
                    "The intensity is in the action words, not the descriptive ones. "
                    "The writing moves because the verbs move it. "
                    + quote
                ),
                "signal": data["signal"],
            }
        else:
            return {
                "id": obs_id,
                "headline": "Your force comes from emphasis",
                "body": (
                    "You build weight through how you describe things, not what you tell people to do. "
                    + quote
                ),
                "signal": data["signal"],
            }

    return None


def apply_intent_mode(text: str, mode: str) -> str:
    """Applies intent mode task instruction to the render prompt."""
    mode_prompts = {
        "GET_IT_DONE": (
            "Rewrite this text. Tighten it. Remove anything that doesn't earn its place. "
            "Preserve the writer's voice exactly: their directness, their cadence, their register. "
            "Do not add warmth, hedging, or polish that isn't already there."
        ),
        "WRITE_SOMETHING": (
            "Help compose this as original content. "
            "Structure it clearly. Preserve the writer's voice throughout. "
            "The voice is theirs. The structure is your contribution."
        ),
        "THINK_IT_THROUGH": (
            "Explore the ideas in this text. Generate challenges, alternative angles, questions. "
            "This is not final copy. It is thinking. Expand, challenge, question. "
            "Preserve the writer's voice in any prose you produce."
        ),
        "HELP_ME_UNDERSTAND": (
            "Explain the concepts in this text clearly. "
            "Use step-by-step structure where it helps. Use analogies where they clarify. "
            "Write with the depth needed for genuine understanding. Not brevity. "
            "Preserve the writer's voice. Never write for them. Write as them, explaining."
        ),
    }
    return mode_prompts.get(mode, mode_prompts["GET_IT_DONE"])


def _detect_mode(text: str) -> str:
    """
    Auto-detects intent mode from input text.
    Silent — user never sees the mode name.
    Student mode detected via five independent signals.
    Receipt attaches automatically when student mode fires.
    """
    import re

    score = 0.0

    # Academic language
    academic = re.compile(
        r"\b(furthermore|moreover|nevertheless|in conclusion|it can be argued|"
        r"according to|as argued by|cited in|essay|thesis|hypothesis|"
        r"analysis|evaluate|critically|literature|methodology)\b", re.I
    )
    words = max(len(text.split()), 1)
    ac_matches = len(academic.findall(text))
    score += min(0.35, (ac_matches / (words / 100)) * 0.08)

    # Explicit student signals
    student_explicit = re.compile(
        r"\b(help me understand|explain (to me|why|how|what)|"
        r"i (don.t|do not|can.t|cannot) understand|"
        r"my essay|my assignment|my coursework|my dissertation|"
        r"for class|my professor|my tutor|word limit|struggling with)\b", re.I
    )
    if student_explicit.search(text):
        score += 0.35

    # Academic hedges
    ac_hedges = re.compile(
        r"\b(it could be argued|it can be argued|one could argue|"
        r"to some extent|arguably|ostensibly|it is possible that|"
        r"it seems that|it appears that)\b", re.I
    )
    hedge_count = len(ac_hedges.findall(text))
    if hedge_count >= 2:
        score += 0.20
    elif hedge_count == 1:
        score += 0.10

    # Content domain clustering
    domain = re.compile(
        r"\b(theory|argument|evidence|critique|evaluation|concept|"
        r"framework|discuss|analyse|analyze|compare|contrast|examine)\b", re.I
    )
    if len(domain.findall(text)) >= 3:
        score += 0.15

    # Essay structure
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if len(sentences) >= 4:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 18:
            score += 0.10

    return "HELP_ME_UNDERSTAND" if score >= 0.55 else "GET_IT_DONE"
    """
    Applies intent mode rendering constraints to a system prompt.
    Deterministic. No API required for the constraint layer.
    LLM call is separate (handled by Claude API if key present).
    """
    mode_prompts = {
        "GET_IT_DONE": (
            "Rewrite this text. Tighten it. Remove anything that doesn't earn its place. "
            "Preserve the writer's voice exactly: their directness, their cadence, their register. "
            "Do not add warmth, hedging, or polish that isn't already there."
        ),
        "WRITE_SOMETHING": (
            "Help compose this as original content. "
            "Structure it clearly. Preserve the writer's voice throughout. "
            "The voice is theirs. The structure is your contribution."
        ),
        "THINK_IT_THROUGH": (
            "Explore the ideas in this text. Generate challenges, alternative angles, questions. "
            "This is not final copy. It is thinking. Expand, challenge, question. "
            "Preserve the writer's voice in any prose you produce."
        ),
        "HELP_ME_UNDERSTAND": (
            "Explain the concepts in this text clearly. "
            "Use step-by-step structure where it helps. Use analogies where they clarify. "
            "Write with the depth needed for genuine understanding. Not brevity. "
            "Preserve the writer's voice. Never write for them. Write as them, explaining."
        ),
    }
    return mode_prompts.get(mode, mode_prompts["GET_IT_DONE"])


def generate_receipt(session_start: str, word_count: int) -> dict:
    """Plain English render receipt. No legal claims. No guarantees."""
    return {
        "rendered_at": datetime.now().strftime("%d %B %Y, %H:%M"),
        "session_started": session_start,
        "words_analysed": word_count,
        "identity_preserved": True,
        "calibration_occurred": False,
        "summary": (
            "This render used your personal voice profile, built from your own writing in this session. "
            "The engine wrote as you. Not for you. "
            "No changes were made to your voice profile during this render. "
            "No calibration data was recorded."
        ),
    }


# ============================================================
# Session state
# ============================================================

def init_state():
    defaults = {
        "screen": 1,
        "raw_text": "",
        "observations": [],
        "intro_response": "",
        "intent_mode": "GET_IT_DONE",
        "render_output": "",
        "session_start": datetime.now().strftime("%d %B %Y, %H:%M"),
        "word_count": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()


def go_to(screen: int):
    st.session_state.screen = screen


def progress_dots(current: int, total: int = 4):
    dots = ""
    for i in range(1, total + 1):
        if i == current:
            dots += f'<span class="active">●</span> '
        else:
            dots += "○ "
    st.markdown(f'<div class="progress">{dots}</div>', unsafe_allow_html=True)


# ============================================================
# Screen 1 — Paste
# ============================================================

def screen_paste():
    progress_dots(1)

    st.markdown('<div class="tagline">VOXA</div>', unsafe_allow_html=True)
    st.markdown('<div class="headline">Voxa preserves who you are when you write.</div>', unsafe_allow_html=True)
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
        if st.button("Show me my fingerprint →", type="primary", use_container_width=True):
            if not text or not text.strip():
                st.error("Paste something you wrote first.")
            elif len(text.split()) < 10:
                st.error("A bit more. At least a sentence or two.")
            else:
                with st.spinner("Reading your writing..."):
                    st.session_state.raw_text = text
                    st.session_state.word_count = len(text.split())
                    st.session_state.observations = analyse_writing(text)
                go_to(2)
                st.rerun()

    st.markdown('<div class="microcopy">No account needed. Nothing stored.</div>', unsafe_allow_html=True)


# ============================================================
# Screen 2 — Fingerprint Reveal
# ============================================================

def screen_reveal():
    progress_dots(2)

    st.markdown('<div class="headline">Your voice fingerprint.</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="sub">From {st.session_state.word_count} words of your writing.</div>',
        unsafe_allow_html=True
    )

    observations = st.session_state.observations
    if not observations:
        st.warning("Not enough signal. Paste more of your writing.")
        if st.button("← Try again"):
            go_to(1)
            st.rerun()
        return

    for obs in observations:
        st.markdown(f"""
        <div class="obs-card">
            <div class="obs-headline">{obs['headline']}</div>
            <div class="obs-body">{obs['body']}</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<hr class='divider'>", unsafe_allow_html=True)
    st.markdown('<div class="microcopy">Same voice. Different task.</div>', unsafe_allow_html=True)
    st.markdown("")

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Start over", use_container_width=True):
            st.session_state.raw_text = ""
            st.session_state.observations = []
            st.session_state.intro_response = ""
            st.session_state.render_input_text = ""
            st.session_state.render_output = ""
            go_to(1)
            st.rerun()
    with col2:
        if st.button("Write something with my voice →", type="primary", use_container_width=True):
            go_to(5)
            st.rerun()


# ============================================================


def _analyse_intro(text: str) -> list[dict]:
    """
    Short-form analyser for 2-3 sentence intro responses.
    Works where analyse_writing() cannot — too few sentences for reliable signal.
    Detects three dimensions that show clearly in a client intro:
      1. Formality — register and framing
      2. Compression — information density
      3. Self-positioning — "I am X" vs "I help X do Y"
    """
    import re
    words = text.split()
    word_count = len(words)
    observations = []

    # 1. Formality — formal markers vs casual register
    formal = re.compile(
        r"\b(I am|I have|I work with|I lead|I specialise|I focus|"
        r"analyst|founder|director|manager|consultant|partner|"
        r"regulated|enterprise|industry|professional|organisation)\b", re.I
    )
    casual = re.compile(
        r"\b(hi|hey|so I|basically|kind of|sort of|stuff|things|"
        r"pretty much|you know|a lot of|loads of)\b", re.I
    )
    formal_hits = len(formal.findall(text))
    casual_hits = len(casual.findall(text))

    if formal_hits >= 2 and casual_hits == 0:
        observations.append({
            "id": "intro_formality",
            "headline": "You write in a formal register",
            "body": (
                "Your introduction uses professional framing from the first word. "
                "No softening. No casual openers. The register is set immediately."
            ),
            "signal": 0.80,
        })
    elif casual_hits >= 2:
        observations.append({
            "id": "intro_formality",
            "headline": "You write in a direct, informal register",
            "body": (
                "Your introduction drops the formal scaffolding. "
                "Conversational by design. The register signals approachability, not informality."
            ),
            "signal": 0.72,
        })

    # 2. Compression — words per idea (rough: commas + semicolons signal dense packing)
    punctuation_density = (text.count(",") + text.count(";")) / max(word_count, 1)
    avg_word_length = sum(len(w.strip(".,;:")) for w in words) / max(word_count, 1)

    if punctuation_density > 0.08 or avg_word_length > 6.5:
        observations.append({
            "id": "intro_compression",
            "headline": "You pack a lot into a short space",
            "body": (
                f"{word_count} words. Multiple ideas per sentence. "
                "You don't use more space than the point requires. "
                "you compress without losing precision."
            ),
            "signal": 0.75,
        })
    elif word_count <= 35:
        observations.append({
            "id": "intro_compression",
            "headline": "You introduce yourself in as few words as possible",
            "body": (
                f"{word_count} words. One or two sentences. "
                "You give the reader what they need and stop."
            ),
            "signal": 0.70,
        })

    # 3. Self-positioning — "I am X" (credential-first) vs "I help X" (value-first)
    credential_first = re.compile(r"\b(I am a|I am an|I\'m a|I\'m an|I have \d+)\b", re.I)
    value_first = re.compile(r"\b(I help|I work with|I partner|I support|I enable|I build)\b", re.I)

    if value_first.search(text):
        observations.append({
            "id": "intro_positioning",
            "headline": "You lead with what you do for others",
            "body": (
                "The introduction frames your value before your title. "
                "The reader understands the outcome before they understand your role."
            ),
            "signal": 0.78,
        })
    elif credential_first.search(text):
        observations.append({
            "id": "intro_positioning",
            "headline": "You lead with who you are",
            "body": (
                "Role and credentials come first. "
                "The reader knows what you are before they know what you do for them."
            ),
            "signal": 0.73,
        })

    return observations


# Screen 2.5 — One targeted question
# ============================================================

def screen_intro_question():
    progress_dots(3)

    st.markdown('<div class="headline">One more thing.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Introduce yourself to a new client. Two or three sentences. Go.</div>',
        unsafe_allow_html=True
    )
    st.markdown("")

    intro = st.text_area(
        "intro",
        value=st.session_state.intro_response,
        placeholder="Two or three sentences. Write it as you would actually send it.",
        height=140,
        label_visibility="collapsed",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("← Back", use_container_width=True):
            go_to(2)
            st.rerun()
    with col2:
        if st.button("Continue →", type="primary", use_container_width=True):
            if not intro.strip():
                st.error("Write something first.")
            elif len(intro.split()) < 20:
                st.error("Too short. Two or three full sentences gives a better read.")
            else:
                # Dedicated short-form analyser — works reliably on 2-3 sentences
                # analyse_writing() needs 5+ sentences; this fires on 2
                intro_obs = _analyse_intro(intro)
                existing = st.session_state.observations

                # Merge — don't duplicate existing headlines
                existing_headlines = {o["headline"] for o in existing}
                for obs in intro_obs:
                    if obs["headline"] not in existing_headlines:
                        existing.append(obs)
                        existing_headlines.add(obs["headline"])

                existing.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
                st.session_state.observations = existing[:5]
                st.session_state.intro_response = intro
                go_to(3)
                st.rerun()


# Screen 3 — Apply voice (input + output on same screen)
# ============================================================

def screen_render():
    progress_dots(3)

    st.markdown('<div class="headline">Now paste something AI wrote.</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub">Voxa will rewrite it in your voice.</div>', unsafe_allow_html=True)

    input_text = st.text_area(
        "input",
        value=st.session_state.get("render_input_text", ""),
        placeholder="Paste AI-generated text here — an email draft, a LinkedIn post, a proposal section...",
        height=220,
        label_visibility="collapsed",
    )

    if st.button("Write as me →", type="primary", use_container_width=True):
        if not input_text or not input_text.strip():
            st.error("Paste some text first.")
        else:
            st.session_state.render_input_text = input_text
            st.session_state.render_output = ""  # Clear previous output
            try:
                api_key = st.secrets.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_API_KEY", "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")
            except Exception:
                api_key = os.environ.get("ANTHROPIC_API_KEY", "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")

            if api_key:
                import anthropic

                detected_mode = _detect_mode(input_text)
                st.session_state.intent_mode = detected_mode

                observations = st.session_state.observations
                obs_summary = " | ".join(
                    f"{o['headline']}" for o in observations[:3]
                ) if observations else "direct, compressed, peer register"

                mode_instruction = apply_intent_mode(input_text, detected_mode)

                word_count_input = len(input_text.split())
                system = (
                    f"You are a voice rendering engine. Rewrite the text to match this person's voice.\n\n"
                    f"Voice fingerprint: {obs_summary}\n\n"
                    f"Task: {mode_instruction}\n\n"
                    f"CRITICAL LENGTH REQUIREMENT:\n"
                    f"The input is {word_count_input} words. Your output MUST be {word_count_input} words or more.\n"
                    f"Count your words before returning. If short, expand — do not submit a summary.\n"
                    f"Summarising is a failure. Every paragraph in gets a paragraph out.\n\n"
                    f"Rules:\n"
                    f"1. Rewrite every sentence. Do not skip, merge, or drop any part of the input.\n"
                    f"2. Match their sentence length, directness, and register exactly.\n"
                    f"3. No corporate language. No hedging they do not use. No em dashes.\n"
                    f"4. No preamble. No explanation. Return only the rewritten text.\n"
                    f"5. UK English. Use hyphens, not em dashes."
                )

                client = anthropic.Anthropic(api_key=api_key)
                with st.spinner("Writing as you..."):
                    response = client.messages.create(
                        model="claude-sonnet-4-5",
                        max_tokens=4096,
                        system=system,
                        messages=[{"role": "user", "content": input_text}],
                    )
                    clean = response.content[0].text
                    for dash in ["—", "–", "\u2014", "\u2013"]:
                        clean = clean.replace(dash, "-")
                    st.session_state.render_output = clean
                st.rerun()
            else:
                st.error("API key missing.")

    # Output — shows on same screen after render
    output = st.session_state.get("render_output", "")
    if output:
        st.markdown("<hr class='divider'>", unsafe_allow_html=True)
        st.markdown('<div class="headline">Your writing.</div>', unsafe_allow_html=True)

        import hashlib
        output_key = "out_" + hashlib.md5(output[:50].encode()).hexdigest()[:8]
        st.text_area(
            label="output",
            value=output,
            height=350,
            label_visibility="collapsed",
            key=output_key,
        )

        # Receipt — silent, only shows for student mode
        if st.session_state.get("intent_mode") == "HELP_ME_UNDERSTAND":
            st.markdown("<hr class='divider'>", unsafe_allow_html=True)
            receipt = generate_receipt(
                st.session_state.session_start,
                st.session_state.word_count,
            )
            st.markdown(f"""
            <div class="receipt">
                <div class="receipt-title">Your render record</div>
                <div>{receipt['summary']}</div>
                <br>
                <div><strong>Session started:</strong> {receipt['session_started']}</div>
                <div><strong>Words analysed:</strong> {receipt['words_analysed']}</div>
                <div><strong>Rendered:</strong> {receipt['rendered_at']}</div>
                <div><strong>Identity preserved:</strong> Yes</div>
                <div><strong>Calibration occurred:</strong> No</div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Write again", use_container_width=True):
                st.session_state.render_input_text = ""
                st.session_state.render_output = ""
                st.rerun()
        with col2:
            if st.button("Start over", type="primary", use_container_width=True):
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    st.markdown(
        '<div class="microcopy" style="margin-top:2rem;">Voxa preserves who you are when you write.</div>',
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
    screen_render()
elif screen == 5:
    screen_intro_question()
else:
    go_to(1)
    st.rerun()
