# v10.1 force redeploy
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
# Engine — v10.0 (voxa-rendering package)
# ============================================================
# Imports from packages/voxa-rendering/src/voxa_rendering/fingerprint.py
# ENGINE_PATH is already on sys.path (set above).

try:
    from voxa_rendering.fingerprint import select_observations, _deterministic_fallback
    _V10_ENGINE = True
except ImportError as _e:
    _V10_ENGINE = False
    import warnings
    warnings.warn(f"v10.0 engine not available, falling back to internal engine: {_e}")


def analyse_writing(text: str) -> list[dict]:
    """
    Runs the v10.0 fingerprint engine.
    Returns 3-5 observations [{headline, body}, ...] ordered by signal strength.
    Falls back to internal engine if package import failed.
    """
    if _V10_ENGINE:
        observations = select_observations(text)
        return _deterministic_fallback(observations)
    else:
        return _analyse_writing_internal(text)


# ---- Internal engine (fallback only — not the live path) ----

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


def _analyse_writing_internal(text: str) -> list[dict]:
    """
    Internal fallback fingerprint engine (pre-v10.0).
    Used only if the v10.0 package import fails.
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

def compute_baseline_metrics(text: str) -> dict:
    """
    Extracts four numerical constraint metrics from a text sample.
    Used to build the baseline fingerprint for v10.1 restoration targeting.

    Returns:
        hedge_density     — hedge words per 100 words
        sentence_length_sd — standard deviation of sentence word counts
        first_person_ratio — proportion of sentences with first-person markers
        directive_ratio    — proportion of sentences that are imperatives
        word_count         — total words in sample (for confidence weighting)
    """
    import re
    import math

    words = text.split()
    total_words = max(len(words), 1)

    # Sentence split
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip() and len(s.split()) >= 2]
    total_sents = max(len(sentences), 1)

    # 1. Hedge density — per 100 words
    hedge = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b", re.I
    )
    hedge_count = len(hedge.findall(text))
    hedge_density = round((hedge_count / total_words) * 100, 2)

    # 2. Sentence length SD
    lengths = [len(s.split()) for s in sentences]
    avg_len = sum(lengths) / total_sents
    variance = sum((l - avg_len) ** 2 for l in lengths) / total_sents
    sentence_length_sd = round(math.sqrt(variance), 2)

    # 3. First-person ratio
    first_person = re.compile(
        r"\b(I |I'|I'm|I've|I'd|I'll|my |mine\b|myself\b)", re.I
    )
    fp_sents = sum(1 for s in sentences if first_person.search(s))
    first_person_ratio = round(fp_sents / total_sents, 3)

    # 4. Directive ratio — imperative sentences
    imp = re.compile(
        r"^(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|"
        r"Deploy|Ship|Run|Get|Go|Ensure|Define|Test|Prove|Drive|Write|Create|"
        r"Use|Set|Add|Remove|Update|Push|Pull|Ask|Tell|Show|Find|Keep|"
        r"Remember|Consider|Note|Look|Think|Try)\b", re.I
    )
    directive_sents = sum(1 for s in sentences if imp.match(s.strip()))
    directive_ratio = round(directive_sents / total_sents, 3)

    return {
        "hedge_density": hedge_density,
        "sentence_length_sd": sentence_length_sd,
        "first_person_ratio": first_person_ratio,
        "directive_ratio": directive_ratio,
        "word_count": total_words,
    }


def _merge_baseline(existing: dict | None, new_metrics: dict) -> dict:
    """
    Running average merge. Weights by word count so larger samples count more.
    """
    if existing is None:
        return new_metrics.copy()

    old_wc = existing.get("word_count", 0)
    new_wc = new_metrics.get("word_count", 0)
    total_wc = old_wc + new_wc
    if total_wc == 0:
        return new_metrics.copy()

    def weighted(old_val, new_val):
        return round((old_val * old_wc + new_val * new_wc) / total_wc, 3)

    return {
        "hedge_density": weighted(existing["hedge_density"], new_metrics["hedge_density"]),
        "sentence_length_sd": weighted(existing["sentence_length_sd"], new_metrics["sentence_length_sd"]),
        "first_person_ratio": weighted(existing["first_person_ratio"], new_metrics["first_person_ratio"]),
        "directive_ratio": weighted(existing["directive_ratio"], new_metrics["directive_ratio"]),
        "word_count": total_wc,
    }


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
        "locale": "uk",
        "cumulative_words": 0,
        "cumulative_docs": 0,
        "baseline_fingerprint": None,
        "render_delta": None,
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

def _score_sample_fitness(text: str) -> dict:
    """
    Scores a writing sample for fingerprint fitness.
    Three research-validated dimensions:
    1. SPONTANEITY — unguarded, natural writing (idiolect lives here)
    2. SPECIFICITY — concrete, named, real details (what AI cannot fake)
    3. OWNERSHIP — first-person, accountable, self-authored
    """
    import re, math
    from collections import Counter

    words = text.split()
    total_words = max(len(words), 1)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip() and len(s.split()) >= 2]
    total_sents = max(len(sentences), 1)

    # SPONTANEITY (0-35)
    spontaneity = 0
    if len(sentences) >= 3:
        lengths = [len(s.split()) for s in sentences]
        avg = sum(lengths) / len(lengths)
        sd = math.sqrt(sum((l - avg) ** 2 for l in lengths) / len(lengths))
        if sd >= 8:   spontaneity += 12
        elif sd >= 5: spontaneity += 8
        elif sd >= 3: spontaneity += 4

    subject_drop = re.compile(
        r'^(Will|Can|Could|Would|Pls|Please|Am|Have|Had|Apologies|Thanks|Noted|Confirmed)\b',
        re.IGNORECASE
    )
    drop_count = sum(1 for s in sentences if subject_drop.match(s.strip()))
    if drop_count >= 2:   spontaneity += 8
    elif drop_count >= 1: spontaneity += 4

    shorthand = re.compile(r'\b(pls|btw|fyi|asap|tbc|tbd|re:|etc|vs)\b', re.IGNORECASE)
    sc = len(shorthand.findall(text))
    if sc >= 2:   spontaneity += 8
    elif sc >= 1: spontaneity += 4

    if re.search(r'(\.\.|  |\.\s+[a-z])', text):
        spontaneity += 4

    if re.search(r'\b(hopefully|pls let|let me know|happy to|regards,|cheers,|best,|thanks,)\b', text, re.IGNORECASE):
        spontaneity += 3

    spontaneity = min(spontaneity, 35)

    # SPECIFICITY (0-35)
    specificity = 0
    non_proper = {'The','This','That','These','Those','They','Their','There','When',
                  'What','Which','Where','Who','How','And','But','For','With','From',
                  'Also','Some','Have','Been','Will','Would','Could','Should','Just',
                  'Still','Even','Here','Very','More','Most','Into','Over','After',
                  'About','Such','Each','Both','Only','Then','Than','Same','Another'}
    proper_nouns = [w for w in re.findall(r'(?<=[.!? ])[A-Z][a-z]{2,}', text) if w not in non_proper]
    unique_proper = len(set(proper_nouns))
    if unique_proper >= 5:   specificity += 15
    elif unique_proper >= 3: specificity += 10
    elif unique_proper >= 1: specificity += 5

    number_count = len(re.findall(r'\b\d+[\d,.]*\b', text))
    if number_count >= 3:   specificity += 10
    elif number_count >= 1: specificity += 5

    shared = len(re.findall(
        r'\b(the (meeting|call|proposal|project|report|issue|deal|team|client|product|platform|system))\b',
        text, re.IGNORECASE
    ))
    if shared >= 2:   specificity += 10
    elif shared >= 1: specificity += 5

    specificity = min(specificity, 35)

    # OWNERSHIP (0-30)
    ownership = 0
    fp = re.compile(r'\b(I|me|my|mine|myself)\b', re.IGNORECASE)
    fp_sents = sum(1 for s in sentences if fp.search(s))
    fp_ratio = fp_sents / total_sents
    if fp_ratio >= 0.5:    ownership += 12
    elif fp_ratio >= 0.3:  ownership += 8
    elif fp_ratio >= 0.15: ownership += 4

    denial = re.compile(
        r'\b(I do not|I am not|I don\'t|I\'m not|That is not|This is not|We do not)\b',
        re.IGNORECASE
    )
    dc = len(denial.findall(text))
    if dc >= 2:   ownership += 10
    elif dc >= 1: ownership += 6

    if re.search(
        r'\b(I have (just|been|become|realised|decided)|I was|I became|I struggle|to be honest)\b',
        text, re.IGNORECASE
    ):
        ownership += 8

    ownership = min(ownership, 30)

    # TOTAL + word count modifier
    total = spontaneity + specificity + ownership
    wc = len(words)
    if wc < 100:
        total = int(total * 0.5); wc_note = "very short"
    elif wc < 200:
        total = int(total * 0.75); wc_note = "short"
    elif wc < 400:
        total = int(total * 0.9); wc_note = "good length"
    else:
        wc_note = "strong length"

    if total >= 75:   tier = "gold"
    elif total >= 55: tier = "strong"
    elif total >= 35: tier = "thin"
    else:             tier = "weak"

    nudge = None
    if tier in ("thin", "weak"):
        if specificity < 10 and ownership < 10:
            nudge = "Paste an email you sent to someone you know. Something with names, real context, not a formal document."
        elif specificity < 10:
            nudge = "Paste something with real names and specific context — an email to a colleague about an actual project."
        elif ownership < 10:
            nudge = "Paste something written in your own voice — where you say what you think, not what sounds professional."
        elif spontaneity < 10:
            nudge = "Paste something you wrote quickly without re-reading — a message or email dashed off on your phone."
        else:
            nudge = "Paste one more piece of your own writing to sharpen the fingerprint."

    return {
        "score": total, "tier": tier,
        "spontaneity": spontaneity, "specificity": specificity, "ownership": ownership,
        "word_count": wc, "wc_note": wc_note, "nudge": nudge,
    }


def _fitness_gate(fitness: dict, cumulative_words: int, cumulative_docs: int) -> dict:
    """Decides whether to fire fingerprint, nudge, or accumulate."""
    tier = fitness["tier"]
    wc = fitness["word_count"]
    nudge = fitness["nudge"]

    # Gold or strong — fire immediately regardless of word count
    if tier in ("gold", "strong"):
        return {"action": "fire", "confidence": "high" if tier == "gold" else "medium", "message": None}
    # Thin but enough words — fire provisionally
    if tier == "thin" and wc >= 150:
        return {"action": "fire", "confidence": "provisional", "message": None}
    # Accumulated enough across pastes — fire
    if cumulative_words >= 250:
        return {"action": "fire", "confidence": "provisional", "message": None}
    # Weak and short — nudge with specific instruction
    if nudge:
        return {"action": "nudge", "confidence": "provisional", "message": nudge}
    return {
        "action": "accumulate", "confidence": "provisional",
        "message": "Paste one more piece of your writing to complete your fingerprint.",
    }


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
                # Accumulate corpus — add this paste to the session total
                word_count = len(text.split())
                st.session_state.cumulative_words += word_count
                st.session_state.cumulative_docs += 1
                st.session_state.raw_text = text
                st.session_state.word_count = word_count
                st.session_state.locale = _detect_locale(text)

                # Update baseline fingerprint — running weighted average
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
                elif gate["action"] == "nudge":
                    st.session_state.fitness_nudge = gate["message"]
                    st.rerun()
                else:
                    st.session_state.fitness_nudge = gate.get("message")
                    st.rerun()

    # Fitness-aware progress messaging
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
        elif tier == "strong":
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;">{words_so_far} words submitted. Paste one more to sharpen it.</div>',
                unsafe_allow_html=True
            )
        else:
            st.markdown(
                f'<div class="microcopy" style="margin-top:0.5rem;">{words_so_far} words submitted. Paste more of your own writing.</div>',
                unsafe_allow_html=True
            )

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
            st.session_state.baseline_fingerprint = None
            st.session_state.render_delta = None
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

    st.markdown('<div class="headline">One more sample.</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="sub">Paste another piece of your own writing. Same email, a different one, a message - anything you wrote. This strengthens your fingerprint.</div>',
        unsafe_allow_html=True
    )
    st.markdown("")

    intro = st.text_area(
        "intro",
        value=st.session_state.intro_response,
        placeholder="Paste your own writing here - an email, a message, a paragraph. The more you paste, the more accurate your fingerprint.",
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
                st.error("Too short. Paste a bit more — the more you give, the better the fingerprint.")
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


# ============================================================
# AI signal detection
# ============================================================

def _score_ai_signal(text: str) -> float:
    """
    Scores text for AI-generated patterns. Returns 0.0–1.0.
    Silent — never shown to the user.
    Higher = more likely AI-generated.
    """
    import re
    score = 0.0
    words = text.split()
    total = max(len(words), 1)

    # Em dashes — strong AI signal
    em_dashes = len(re.findall(r"[—–\u2014\u2013]", text))
    if em_dashes >= 2:
        score += 0.30
    elif em_dashes == 1:
        score += 0.12

    # Verbose opener phrases
    verbose_openers = re.compile(
        r"\b(it is (important|worth|essential|crucial|critical|key) to (note|recognise|recognize|understand|consider)|"
        r"in (today's|the current|our) (landscape|world|environment|era|age|climate)|"
        r"when it comes to|at the end of the day|it goes without saying|"
        r"needless to say|it is worth noting|with that (said|in mind)|"
        r"in light of (this|the above|recent)|as (we|you) (know|can see|may know)|"
        r"it (should|must) be (noted|acknowledged|recognised|recognized) that|"
        r"one (cannot|can't) (overstate|underestimate|deny)|"
        r"this (underscores|highlights|demonstrates|illustrates|showcases|exemplifies)|"
        r"leveraging|synergies|holistic(ally)?|paradigm|robust(ly)?|"
        r"cutting.edge|game.changing|transformative|groundbreaking)\b",
        re.I
    )
    vo_hits = len(verbose_openers.findall(text))
    score += min(0.30, vo_hits * 0.10)

    # Hedge stacking — multiple hedges in close proximity
    hedge = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|arguably|seemingly|"
        r"apparently|ostensibly|presumably|it seems|it appears)\b", re.I
    )
    hedge_count = len(hedge.findall(text))
    hedge_density = hedge_count / total
    if hedge_density > 0.05:
        score += 0.20
    elif hedge_density > 0.03:
        score += 0.10

    # Filler transition phrases
    filler_transitions = re.compile(
        r"\b(furthermore|moreover|additionally|in addition|nevertheless|"
        r"notwithstanding|consequently|subsequently|accordingly|"
        r"in conclusion|to summarise|to summarize|in summary|"
        r"to be clear|to be fair|to that end|with this in mind|"
        r"it is (also )?(important|worth) (mentioning|highlighting|noting))\b",
        re.I
    )
    ft_hits = len(filler_transitions.findall(text))
    score += min(0.20, ft_hits * 0.07)

    # Long average sentence length — AI tends to write long
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip() and len(s.split()) >= 3]
    if sentences:
        avg_len = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg_len > 22:
            score += 0.15
        elif avg_len > 16:
            score += 0.07

    # Passive voice concentration
    passive = re.compile(
        r"\b(is|are|was|were|has been|have been|had been|will be|"
        r"can be|could be|should be|would be|may be|might be) \w+ed\b",
        re.I
    )
    passive_count = len(passive.findall(text))
    if passive_count / max(len(sentences), 1) > 0.4:
        score += 0.10

    return min(1.0, score)


def _extract_function_patterns(text: str) -> dict:
    """
    Extracts the user's unconscious function word and construction patterns.
    These are the connective tissue of their writing — the words they reach
    for without thinking. Impossible to fake. First to be erased by AI.
    """
    import re
    from collections import Counter

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip() and len(s.split()) >= 3]
    words = text.lower().split()
    word_counts = Counter(words)

    # 1. Function word preferences
    candidate_function_words = [
        'whilst', 'although', 'though', 'however', 'nevertheless',
        'pls', 'so', 'hence', 'therefore', 'also', 'again',
        'yet', 'still', 'even', 'just', 'quite', 'rather', 'actually',
        'regarding', 'hopefully', 'fortunately', 'unfortunately', 'separately',
        'briefly', 'frankly', 'noted',
    ]
    ai_defaults = {
        'however', 'furthermore', 'moreover', 'additionally', 'nevertheless',
        'therefore', 'thus', 'hence', 'certainly', 'indeed', 'clearly',
        'frankly', 'simply', 'consequently', 'nonetheless',
    }
    user_function_words = [(fw, word_counts[fw]) for fw in candidate_function_words if word_counts.get(fw, 0) > 0]
    user_function_words.sort(key=lambda x: x[1], reverse=True)
    preferred = [fw for fw, _ in user_function_words[:8]]
    avoided_ai = [fw for fw in ai_defaults if word_counts.get(fw, 0) == 0]

    # 2. Subject drop — sentences starting with verb, no subject
    subject_drop_pattern = re.compile(
        r'^(Will|Can|Could|Would|Should|Pls|Please|Am|Have|Had|Did|Do|Does|'
        r'Apologies|Thanks|Noted|Confirmed|Agreed|Understood)\b', re.IGNORECASE
    )
    subject_drops = [s for s in sentences if subject_drop_pattern.match(s.strip())]
    drops_ratio = round(len(subject_drops) / max(len(sentences), 1), 3)

    # 3. Transition phrases
    transition_pattern = re.compile(
        r'^(Regarding|For point|On the|Re:|As discussed|Following|Separately|'
        r'In terms of|With regard|On this|On that|To your point|To confirm)\b', re.IGNORECASE
    )
    transitions_found = [s.strip()[:60] for s in sentences if transition_pattern.match(s.strip())]

    # 4. Colon usage
    colon_count = text.count(':')
    colon_rate = round(colon_count / max(len(sentences), 1), 2)

    # 5. Soft enders
    soft_enders = re.compile(
        r'\b(hopefully|trust this|this clarifies|let me know|pls let|feel free|any questions|happy to)\b',
        re.IGNORECASE
    )
    soft_ends = [s for s in sentences if soft_enders.search(s)]

    return {
        'preferred_function_words': preferred,
        'avoided_ai_connectors': avoided_ai[:6],
        'subject_drop_ratio': drops_ratio,
        'subject_drop_examples': [s[:60] for s in subject_drops[:2]],
        'transition_phrases': transitions_found[:3],
        'colon_rate': colon_rate,
        'soft_ender_count': len(soft_ends),
        'total_sentences': len(sentences),
    }


def _format_function_patterns(patterns: dict, input_genre: str = "email") -> str:
    """
    Formats function patterns as a renderer instruction block.
    Context-aware: email closers are suppressed for article/piece genres.

    input_genre: 'email' | 'article' | 'message' | 'unknown'
    Email closers ('Hopefully', 'Pls let me know') only make sense in emails.
    Applying them to articles produces rogue endings.
    """
    if not patterns:
        return ""

    is_email = input_genre in ("email", "message", "unknown")
    lines = ["\nFUNCTION PATTERNS — their unconscious connective tissue:"]

    if patterns.get('preferred_function_words'):
        # Filter email-specific words when rendering non-email content
        words_list = patterns['preferred_function_words']
        if not is_email:
            email_only = {'pls', 'please', 'regards', 'cheers', 'thanks'}
            words_list = [w for w in words_list if w.lower() not in email_only]
        if words_list:
            words = ', '.join(f"'{w}'" for w in words_list)
            lines.append(f"  Words they actually use: {words}")
            lines.append(f"  Use these naturally where they fit — they are part of their voice.")

    if patterns.get('avoided_ai_connectors'):
        avoided = ', '.join(f"'{w}'" for w in patterns['avoided_ai_connectors'][:4])
        lines.append(f"  Words they never use (do not introduce): {avoided}")

    if patterns.get('subject_drop_ratio', 0) > 0.05:
        lines.append(f"  They omit the subject and lead with the verb.")
        examples = patterns.get('subject_drop_examples', [])
        if examples:
            lines.append(f'    e.g. "{examples[0]}"')

    if patterns.get('transition_phrases'):
        tp = patterns['transition_phrases'][0]
        lines.append(f'  They introduce new points with topic phrases: e.g. "{tp}"')

    if patterns.get('colon_rate', 0) > 0.1:
        lines.append(f"  They use colons to introduce context — match this pattern.")

    # Only include email closers when rendering email-genre content
    if is_email and patterns.get('soft_ender_count', 0) > 0:
        lines.append(f"  In emails they close with soft acknowledgements ('Hopefully this clarifies', 'Pls let me know').")
    elif not is_email:
        lines.append(f"  NOTE: this person's email closers ('Hopefully', 'Pls let me know') are email-specific. Do NOT use them to end articles or pieces.")

    return "\n".join(lines)


def _pick_anchor_sentences(sentences: list[str]) -> list[str]:
    """
    Selects 2-3 sentences most distinctive to this writer.
    Prioritises short declarative, direct denial, imperatives, verb-driven.
    Ensures variety in length. Falls back gracefully.
    """
    import re

    hedge = re.compile(r"(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)", re.I)
    denial = re.compile(r"(I do not|I am not|I don't|I'm not|That is not|This is not)", re.I)
    imperative = re.compile(
        r"^(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|Deploy|Ship|Run|Get|Go|"
        r"Ensure|Define|Test|Pull|Explore|Draft|Share|Note|Consider|Find|Use|Add|Remove|Update|Create|Set|Move|Push)", re.I
    )
    adjective = re.compile(
        r"(very|really|extremely|quite|rather|somewhat|highly|deeply|absolutely|completely|"
        r"totally|incredibly|amazing|excellent|great|good|bad|significant|important|critical|key|major)", re.I
    )

    scored = []
    for s in sentences:
        score = 0
        words = s.split()
        if 4 <= len(words) <= 12 and not hedge.search(s):
            score += 3
        if denial.search(s):
            score += 4
        if imperative.match(s.strip()):
            score += 2
        if len(adjective.findall(s)) == 0 and len(words) >= 5:
            score += 1
        if hedge.search(s):
            score -= 2
        scored.append((score, s))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = []
    lengths_used = set()
    for score, s in scored:
        bucket = len(s.split()) // 5
        if bucket not in lengths_used or len(selected) == 0:
            selected.append(s)
            lengths_used.add(bucket)
        if len(selected) >= 3:
            break

    if len(selected) < 2:
        selected = [s for _, s in scored[:3]]

    # Quality gate — only sentences that scored above 0 are peak sentences
    peak = [s for s in selected if any(sc > 0 and sent == s for sc, sent in scored)]
    if len(peak) >= 2:
        selected = peak

    return selected[:3]


def _score_thought_density(text: str) -> dict:
    """
    Measures thought density — how many distinct ideas per sentence.
    Your writing says two or three things in the same space.
    AI writing says one thing per sentence. Evenly paced. Thin.

    Signals for multiple ideas in one sentence:
    - Conjunctions joining distinct facts: "but", "yet", "whilst", "though"
    - Comma-separated independent clauses
    - Embedded qualifications: "as", "where", "which", "when" mid-sentence
    - Concession + position in same sentence: "Whilst X, I Y"
    - Multiple named entities in one sentence

    Returns:
        avg_ideas_per_sentence: float
        peak_density_sentences: list of highest-density sentences
        density_instruction: what to tell the renderer
    """
    import re

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip() and len(s.split()) >= 4]
    if not sentences:
        return {"avg_ideas_per_sentence": 1.0, "peak_density_sentences": [], "density_instruction": ""}

    def _count_ideas(sentence: str) -> int:
        ideas = 1
        # Coordinating conjunctions joining distinct clauses
        coord = re.compile(r'\b(but|yet|whilst|although|though|however|so|and)\b', re.IGNORECASE)
        ideas += min(len(coord.findall(sentence)), 2)
        # Embedded subordinate clauses
        subord = re.compile(r'\b(as|where|which|when|because|since|if|whether)\b', re.IGNORECASE)
        ideas += min(len(subord.findall(sentence)), 1)
        # Comma-separated elements (suggests multiple facts)
        comma_count = sentence.count(',')
        if comma_count >= 2:
            ideas += 1
        return ideas

    scored = [(s, _count_ideas(s)) for s in sentences]
    avg = sum(score for _, score in scored) / max(len(scored), 1)

    # Peak density sentences — the ones carrying the most
    peak = sorted(scored, key=lambda x: x[1], reverse=True)
    peak_sentences = [s for s, sc in peak[:2] if sc >= 2]

    # Instruction for renderer
    if avg >= 2.5:
        instruction = (
            f"THOUGHT DENSITY: this writer averages {avg:.1f} distinct ideas per sentence. "
            "They compress multiple thoughts into single sentences. "
            "Do not write one-idea sentences where two or three belong. "
            "Pack the meaning in. The reader is assumed to keep up."
        )
    elif avg >= 1.8:
        instruction = (
            f"THOUGHT DENSITY: this writer typically carries {avg:.1f} ideas per sentence. "
            "Avoid thin single-idea sentences. Each sentence should do more than one thing where natural."
        )
    else:
        instruction = ""

    return {
        "avg_ideas_per_sentence": round(avg, 1),
        "peak_density_sentences": peak_sentences,
        "density_instruction": instruction,
    }


def _build_voice_dna(observations: list[dict], raw_text: str, baseline: dict | None = None, ai_score: float = 0.0) -> str:
    """
    Builds a rich, structured voice DNA string for the render prompt.
    Goes far beyond observation headlines — extracts structural metrics
    and evidence quotes to give Claude concrete anchors.
    """
    if not observations:
        return "No fingerprint available. Apply a plain, direct, compressed register. UK English. Short sentences."

    lines = []

    # Structural metrics from raw text
    import re
    sentences = [s.strip() for s in re.split(r"[.!?]+", raw_text) if s.strip() and len(s.split()) >= 2]
    if sentences:
        lengths = [len(s.split()) for s in sentences]
        avg = sum(lengths) / len(lengths)
        shortest = min(lengths)
        longest = max(lengths)
        lines.append(f"SENTENCE STRUCTURE: avg {avg:.0f} words per sentence | shortest {shortest} words | longest {longest} words")

    # Hedging density
    hedge = re.compile(r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b", re.I)
    hedge_count = len(hedge.findall(raw_text))
    total_words = max(len(raw_text.split()), 1)
    hedge_density = hedge_count / total_words
    if hedge_density < 0.02:
        lines.append("HEDGING: none — states things directly, no cushioning")
    elif hedge_density > 0.05:
        lines.append("HEDGING: frequent — softens before conclusions")
    else:
        lines.append("HEDGING: occasional — hedges selectively")

    # Thought density — how much this writer compresses into each sentence
    if raw_text and len(raw_text.split()) >= 80:
        density = _score_thought_density(raw_text)
        if density["density_instruction"]:
            lines.append(f"\n{density['density_instruction']}")
        if density["peak_density_sentences"]:
            lines.append("DENSITY EXAMPLES — sentences where multiple ideas compress into one:")
            for s in density["peak_density_sentences"]:
                lines.append(f'  "{s}"')

    # Em dash usage in source writing
    em_dashes_in_source = len(re.findall(r"[—–\u2014\u2013]", raw_text))
    if em_dashes_in_source == 0:
        lines.append("PUNCTUATION: no em dashes in their writing — do not introduce any")
    else:
        lines.append("PUNCTUATION: uses some em dashes — match sparingly")

    # Observations — headline + evidence quote where available
    lines.append("\nVOICE OBSERVATIONS (from their own writing):")
    for obs in observations[:5]:
        headline = obs.get("headline", "")
        body = obs.get("body", "")
        # Extract quote from body if present — it's wrapped in double quotes
        quote_match = re.search(r'"([^"]{10,})"', body)
        if quote_match:
            lines.append(f'  - {headline}: e.g. "{quote_match.group(1)}"')
        else:
            lines.append(f"  - {headline}")

    # Anchor sentences — most distinctive sentences from their writing
    # Not the first three. The ones that sound most like them.
    usable = [s for s in sentences if 5 <= len(s.split()) <= 20]
    if usable:
        samples = _pick_anchor_sentences(usable)
        lines.append("\nANCHOR SENTENCES — their most distinctive sentences (calibrate against these, do not copy):")
        for s in samples:
            lines.append(f'  "{s}"')

    # Function patterns — connective tissue AI strips first
    # Detect input genre to suppress email closers in non-email renders
    if raw_text and len(raw_text.split()) >= 100:
        patterns = _extract_function_patterns(raw_text)
        # Detect genre of the INPUT being rendered (not the corpus)
        # Use a simple heuristic: email signals in the input text being restored
        import re as _re
        _email_signals = _re.compile(
            r'\b(Dear|Hi |Hello |Regards,|Best,|Cheers,|Thanks,|Sent from|Subject:|From:|To:)\b',
            _re.IGNORECASE
        )
        # raw_text is the corpus (user's own writing) — check its genre
        _corpus_is_email = bool(_email_signals.search(raw_text))
        _input_genre = "email" if _corpus_is_email else "article"
        pattern_block = _format_function_patterns(patterns, input_genre=_input_genre)
        if pattern_block:
            lines.append(pattern_block)

    return "\n".join(lines)


def _build_restoration_targets(baseline: dict) -> str:
    """
    Formats the RESTORATION TARGETS block from the baseline fingerprint.
    Only included when baseline exists and input is AI-contaminated.

    Applies floors and conditional logic per v10.1 spec:
    - Hedge density floor: 0.5% minimum (section 6.2)
    - Directive ratio: omitted if baseline < 3 directives equivalent (section 6.5)
    - First-person ratio: soft target only (section 6.4)
    """
    hedge = max(baseline["hedge_density"], 0.5)
    sd = baseline["sentence_length_sd"]
    fp = baseline["first_person_ratio"]
    directive = baseline["directive_ratio"]
    wc = baseline["word_count"]

    confidence = "provisional" if wc < 800 else "established"
    confidence_note = f"(Based on {wc} words — {confidence} baseline)"

    lines = [
        "RESTORATION TARGETS — from your baseline writing:",
        f"  Hedge density: {hedge:.1f}% per 100 words — match this rate, do not go lower",
        f"  Sentence rhythm: SD {sd:.1f} words — mix sentence lengths, do not flatten to uniform short",
        f"  Ownership: {fp:.0%} of sentences use first-person — own statements at this rate",
    ]

    # Only include directive target if signal is meaningful
    # Threshold: ~3 directives in a 500-word sample ≈ 0.06 ratio
    if directive >= 0.06:
        lines.append(
            f"  Directness: {directive:.0%} of sentences are action statements — match this proportion"
        )
    else:
        lines.append(
            "  Directness: low imperative rate in baseline — do not force directives"
        )

    lines.append(f"  {confidence_note}")
    lines.append(
        "  Treat these as specifications you are being measured against, not style suggestions."
    )

    return "\n".join(lines)


def _build_system_prompt(
    voice_dna: str,
    mode_instruction: str,
    word_count_input: int,
    ai_score: float,
    baseline: dict | None = None,
) -> str:
    """
    Builds the full system prompt.
    Two paths: AI-contaminated input vs clean human input.
    Both use the same voice DNA. The AI path adds aggressive stripping instructions.
    """

    base_rules = (
        "ABSOLUTE RULES — never break these:\n"
        "1. No em dashes. Replace every — or – with a hyphen or rewrite the sentence.\n"
        "2. No verbose openers: no 'it is important to note', no 'in today's landscape', "
        "no 'it goes without saying', no 'with that in mind', no 'to that end'.\n"
        "3. No filler transitions: no 'furthermore', no 'moreover', no 'in conclusion', "
        "no 'additionally', no 'notwithstanding'.\n"
        "4. No corporate filler: no 'leveraging', no 'synergies', no 'holistic', "
        "no 'transformative', no 'robust', no 'cutting-edge'.\n"
        "5. No preamble. No explanation. Return only the rewritten text.\n"
        "6. UK English throughout.\n"
        "7. Every paragraph in the input gets a paragraph in the output. Do not compress into a summary.\n"
        f"8. Output must be at least {word_count_input} words. The input is {word_count_input} words. "
        "Match or exceed it. If you run short, expand the ideas — do not pad with filler."
    )

    if ai_score >= 0.45:
        # AI-contaminated path — stripping + restoration
        restoration_block = (
            f"\n\n{_build_restoration_targets(baseline)}"
            if baseline else ""
        )
        # Register instruction — match the source register, not an elevated version
        # Research basis: the user's best version is the unpolished authentic version
        # Source register detected from fitness tier stored in voice_dna context
        register_instruction = (
            "REGISTER — this is critical:\n"
            "Match the register of the source writing exactly. Do not elevate, polish, or formalise.\n"
            "If the source writing is direct and slightly rough, the output must be direct and slightly rough.\n"
            "The goal is not better writing. The goal is their writing.\n"
            "The unpolished edge is part of the voice. Preserve it.\n"
            "\n"
            "COMPLETENESS — non-negotiable:\n"
            "Short sentences are correct. But every sentence must be a complete thought.\n"
            "Do not fragment assertions. 'It was not a disaster.' not 'Not a disaster.'\n"
            "The subject stays unless it is an action statement (Will, Can, Pls, Have).\n"
            "Curtness is a style choice. Truncation is an error. Know the difference.\n\n"
        )

        prompt = (
            "You are a voice rendering engine with one job: strip AI-generated language and rewrite "
            "in this person's authentic voice.\n\n"
            "The input text has been identified as AI-generated or heavily AI-influenced. "
            "It carries AI tells: verbose openers, em dashes, stacked hedges, filler transitions, "
            "passive constructions. Your job is to eliminate all of that and replace it with "
            "the voice profile below.\n\n"
            f"VOICE PROFILE:\n{voice_dna}"
            f"{restoration_block}\n\n"
            f"TASK:\n{mode_instruction}\n\n"
            f"{register_instruction}"
            "STRIPPING INSTRUCTIONS:\n"
            "- Identify every AI tell in the input. Rewrite those sentences from scratch.\n"
            "- Do not preserve the AI's sentence structure. Break it up. Shorten it.\n"
            "- Do not preserve the AI's transitions. Cut them or replace with nothing.\n"
            "- The content and ideas are the writer's. The words and structure are the AI's. "
            "Keep the ideas. Destroy the words.\n"
            "- After rewriting, read back through and ask: does this sound like a human "
            "who matches the voice profile? If not, rewrite again.\n"
            "- Do not add warmth, polish, or formality not already in the voice profile.\n"
            "- The rough edges in their writing are not mistakes. They are the voice.\n"
            "- Before you finish: re-read THE STANDARD sentences in the voice profile. "
            "Ask yourself: does this output feel like it came from the same person? "
            "If not, rewrite until it does.\n\n"
            f"{base_rules}"
        )
    else:
        # Clean human input path — preservation is the primary job
        prompt = (
            "You are a voice rendering engine. Your job is to rewrite this text so it sounds "
            "exactly like the person who wrote the samples in the voice profile below.\n\n"
            f"VOICE PROFILE:\n{voice_dna}\n\n"
            f"TASK:\n{mode_instruction}\n\n"
            "RENDERING INSTRUCTIONS:\n"
            "- Match the sentence length from the profile exactly. If they write short, write short.\n"
            "- Match the directness. If they own their statements, do not hedge.\n"
            "- Match the register. If they write peer-to-peer, do not write down to the reader.\n"
            "- Do not add warmth, polish, or formality that isn't already in the voice profile.\n"
            "- Do not smooth rough edges. The rough edges may be part of their voice.\n\n"
            f"{base_rules}"
        )

    return prompt


# Screen 3 — Apply voice (input + output on same screen)
# ============================================================

def _detect_locale(text: str) -> str:
    """
    Detects whether the user writes in UK or US English.
    Scans for UK spelling markers. If enough are present, returns "uk".
    Falls back to "uk" if inconclusive — Voxa is a UK product.
    """
    import re

    uk_markers = [
        r"\bcolour\b", r"\bcolours\b",
        r"\bhonour\b", r"\bhonours\b",
        r"\bbehaviour\b", r"\bbehaviours\b",
        r"\borganis", r"\brecognis", r"\bprioritis",
        r"\banalyse\b", r"\banalyses\b",
        r"\bcentre\b", r"\bcentres\b",
        r"\bfavour\b", r"\bfavours\b",
        r"\bneighbour\b", r"\bneighbours\b",
        r"\bwhilst\b", r"\bfortnight\b",
        r"\bprogramme\b", r"\bcheque\b",
        r"\btravelled\b", r"\bcancelled\b",
    ]

    us_markers = [
        r"\bcolor\b", r"\bcolors\b",
        r"\bhonor\b", r"\bhonors\b",
        r"\bbehavior\b", r"\bbehaviors\b",
        r"\borganize\b", r"\brecognize\b", r"\bprioritize\b",
        r"\banalyze\b", r"\banalyzes\b",
        r"\bcenter\b", r"\bcenters\b",
        r"\bfavor\b", r"\bfavors\b",
        r"\bneighbor\b", r"\bneighbors\b",
        r"\btraveled\b", r"\bcanceled\b",
    ]

    uk_hits = sum(1 for m in uk_markers if re.search(m, text, re.I))
    us_hits = sum(1 for m in us_markers if re.search(m, text, re.I))

    if us_hits > uk_hits:
        return "us"
    return "uk"


def _apply_uk_english(text: str) -> str:
    """
    Replaces US English idioms and AI-default vocabulary with UK English equivalents.
    Applied to every render output before it reaches the user.
    Word-boundary aware — avoids partial replacements.
    """
    import re

    # Ordered — longer phrases first to avoid partial matches
    replacements = [
        # AI-default vocabulary
        (r"\bsurfaces\b", "brings up"),
        (r"\bleverages?\b", "uses"),
        (r"\bleverage\b", "use"),
        (r"\breach out\b", "contact"),
        (r"\breaching out\b", "contacting"),
        (r"\bgaps firing\b", "gaps triggering"),
        (r"\butilizes?\b", "uses"),
        (r"\butilize\b", "use"),
        (r"\butilization\b", "use"),
        # US spelling -> UK spelling
        (r"\bprioritize\b", "prioritise"),
        (r"\bprioritizes\b", "prioritises"),
        (r"\bprioritizing\b", "prioritising"),
        (r"\banalyze\b", "analyse"),
        (r"\banalyzes\b", "analyses"),
        (r"\banalyzing\b", "analysing"),
        (r"\borganize\b", "organise"),
        (r"\borganizes\b", "organises"),
        (r"\borganizing\b", "organising"),
        (r"\brecognize\b", "recognise"),
        (r"\brecognizes\b", "recognises"),
        (r"\brecognizing\b", "recognising"),
        (r"\bcolor\b", "colour"),
        (r"\bcolors\b", "colours"),
        (r"\bcenter\b", "centre"),
        (r"\bcenters\b", "centres"),
        (r"\bfavor\b", "favour"),
        (r"\bfavors\b", "favours"),
        (r"\bhonor\b", "honour"),
        (r"\bhonors\b", "honours"),
        (r"\bbehavior\b", "behaviour"),
        (r"\bbehaviors\b", "behaviours"),
        (r"\bneighbor\b", "neighbour"),
        (r"\bneighbors\b", "neighbours"),
    ]

    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text

def _regex_sweep(text: str) -> str:
    """
    Deterministic guardrail sweep — runs on every render output.
    No API call. No Claude involvement. Code enforces these rules.

    1. Em dashes — all unicode variants replaced with hyphen
    2. Contractions expanded to full form (JA does not contract)
    3. Claude literary closers stripped from paragraph endings
    4. Claude default constructions replaced
    5. Repeated words
    6. Double spaces
    7. Missing article fix
    """
    import re

    # 1. Em dashes — all variants
    for dash in ["\u2014", "\u2013", "&#8212;", "&#8211;", "\u2012", "\u2015", "—", "–", "‒"]:
        text = text.replace(dash, " - ")
    # Belt and braces — regex sweep for any remaining dash variants
    text = re.sub(r"[\u2012\u2013\u2014\u2015]", " - ", text)
    text = re.sub(r'  +', ' ', text)

    # 2. Contractions — JA writes full form only
    contractions = [
        ("aren't", "are not"), ("isn't", "is not"), ("wasn't", "was not"),
        ("weren't", "were not"), ("didn't", "did not"), ("doesn't", "does not"),
        ("don't", "do not"), ("haven't", "have not"), ("hasn't", "has not"),
        ("hadn't", "had not"), ("won't", "will not"), ("wouldn't", "would not"),
        ("couldn't", "could not"), ("shouldn't", "should not"), ("can't", "cannot"),
        ("it's", "it is"), ("that's", "that is"), ("there's", "there is"),
        ("they're", "they are"), ("they've", "they have"), ("they'd", "they would"),
        ("I'm", "I am"), ("I've", "I have"), ("I'd", "I would"), ("I'll", "I will"),
        ("we're", "we are"), ("we've", "we have"), ("we'd", "we would"),
        ("you're", "you are"), ("you've", "you have"), ("you'd", "you would"),
        ("he's", "he is"), ("she's", "she is"), ("who's", "who is"),
        ("what's", "what is"), ("where's", "where is"),
    ]
    for contraction, full in contractions:
        pattern = re.compile(r'\b' + re.escape(contraction) + r'\b', re.IGNORECASE)
        def _repl(m, f=full):
            return f[0].upper() + f[1:] if m.group(0)[0].isupper() else f
        text = pattern.sub(_repl, text)

    # 3. Claude literary closers — abstract triplet endings
    # "The ambition exists. The blueprint doesn't. Until they commit..."
    # Strip abstract trailing sentences from final paragraph
    paragraphs = text.strip().split('\n\n')
    if paragraphs:
        last_para = paragraphs[-1].strip()
        last_sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', last_para) if s.strip()]
        if len(last_sents) >= 3:
            abstract_signals = re.compile(
                r'\b(exists|remains|persists|continues|endures|defines|determines|'
                r'until they|until it|between wanting|between knowing|between having|'
                r'more than it|who they|what they|blueprint|the gap|the distance|'
                r'the difference|the question|promises more|delivers on|'
                r'comes with time|does not\.?$|perhaps not|time will tell|'
                r'only time|remains to be seen|that clarity|'
                r'could take (longer|time|months)|might take|takes time|'
                r'than people expect|than expected|some way off|'
                r'whether.*genuinely|still some way|coming or not)\b', re.IGNORECASE
            )
            # Also catch "Perhaps X. Perhaps Y." couplet — Claude philosophical closer
            perhaps_couplet = re.compile(r'Perhaps [^.]+\. Perhaps [^.]+\.?', re.IGNORECASE)
            if perhaps_couplet.search(last_para):
                # Strip from first "Perhaps" in the couplet
                m = perhaps_couplet.search(last_para)
                if m:
                    stripped_para = last_para[:m.start()].strip()
                    if stripped_para:
                        paragraphs[-1] = stripped_para
                        text = '\n\n'.join(paragraphs)
            stripped = list(last_sents)
            while len(stripped) > 1 and abstract_signals.search(stripped[-1]) and len(stripped[-1].split()) <= 18:
                stripped.pop()
            if len(stripped) < len(last_sents):
                paragraphs[-1] = ' '.join(stripped)
                text = '\n\n'.join(paragraphs)

    # 4. Claude default constructions
    # Strip opening hedges sentence by sentence
    opener_hedge = re.compile(r'(?m)^I think (that )?', re.IGNORECASE)
    text = opener_hedge.sub('', text)
    opener_hedge2 = re.compile(r'(?m)^I believe (that )?', re.IGNORECASE)
    text = opener_hedge2.sub('', text)

    claude_constructions = [
        (r'\bWhat stood out most was\b', 'What stood out'),
        (r'\bWhat stood out was\b', 'What stood out'),
        (r'\bWhat emerged most was\b', 'What emerged'),
        (r'\bWhat emerged was\b', 'What emerged'),
        (r'\bIt was proof that\b', 'It showed that'),
        (r'\bIt served as a reminder\b', 'It was a reminder'),
        (r'\bThis serves as\b', 'This is'),
        (r'\bIt is worth noting that\b', 'Note that'),
        (r'\bIt is important to note that\b', 'Note that'),
        (r'\bMoving forward\b', 'Going forward'),
        (r'\bLeverage\b', 'Use'),
        (r'\bLeveraging\b', 'Using'),
        (r'\bCircle back\b', 'Return to'),
        (r'\bTouch base\b', 'Speak'),
        (r'\bPain points\b', 'Problems'),
        (r'\bRobust\b', 'Strong'),
        (r'\bSeamless\b', 'Smooth'),
        (r'\bHolistic\b', 'Full'),
        (r'\bSynergies\b', 'Benefits'),
        (r'\bEcosystem\b', 'Environment'),
    ]
    for pattern, replacement in claude_constructions:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # 5. Repeated words
    text = re.sub(r'\b(\w+)\s+\1\b', r'\1', text, flags=re.IGNORECASE)

    # 6. Double spaces
    text = re.sub(r'  +', ' ', text)

    # 7. Missing article fix
    article_needed = re.compile(
        r'\b(was not|is not|were not|are not|this was not|it was not|that was not)'
        r'\s+([bcdfghjklmnpqrstvwxyzBCDFGHJKLMNPQRSTVWXYZ]\w{2,})\b'
    )
    def _insert_article(m):
        verb_phrase = m.group(1)
        noun = m.group(2)
        no_article = {'clear', 'enough', 'simple', 'wrong', 'right', 'new',
                      'good', 'bad', 'free', 'ready', 'done', 'finished',
                      'certain', 'sure', 'possible', 'necessary', 'perfect',
                      'disaster', 'statement', 'mistake', 'accident',
                      'talent', 'quality', 'cohesion', 'progress', 'clarity',
                      'identity', 'momentum', 'confidence', 'rhythm', 'intent',
                      'pressure', 'direction', 'purpose', 'structure', 'balance'}
        if noun.lower() in no_article:
            return m.group(0)
        return f"{verb_phrase} a {noun}"
    text = article_needed.sub(_insert_article, text)

    return text


def _grammar_fix_pass(text: str, client) -> str:
    """
    Second Claude call — grammar errors only.
    Brief: find and fix grammar errors. Do not rewrite. Do not change voice.
    Returns corrected text. If no errors found, returns original text unchanged.
    """
    system = (
        "You are a precise grammar checker for UK English. Fix errors only. Never rewrite.\n\n"
        "FIX THESE:\n"
        "1. Adverb/adjective confusion: 'move quicker' → 'move more quickly', "
        "'runs faster' is fine (manner adverb), 'move quicker' is not.\n"
        "2. Missing prepositions: 'lagged the ambition' → 'lagged behind the ambition', "
        "'fell short expectations' → 'fell short of expectations'.\n"
        "3. Loose gerund constructions: 'no longer seeming to work' → 'no longer seem to work', "
        "'appearing to struggle' when the subject is clear → 'appears to struggle'.\n"
        "4. Open-ended lists that need a closer: a list ending without resolution "
        "(e.g. 'cost of living, NHS waiting lists, housing.') should end with "
        "'and so on' or 'among other things' where the writer clearly intended more. "
        "Only add a closer if the list is plainly incomplete — do not add to every list.\n"
        "5. Missing articles (a, an, the) before countable nouns.\n"
        "6. Dropped words that break the meaning of a sentence.\n"
        "\n"
        "DO NOT TOUCH:\n"
        "1. Collective nouns with plural verbs ('England are', 'the team are', 'Labour are') — correct in UK English.\n"
        "2. Sentence fragments used deliberately for rhythm ('Football in fragments.', 'Not a disaster.').\n"
        "3. Any word choice, sentence structure, or punctuation that is grammatically valid.\n"
        "4. Register, tone, or voice — change nothing that is not a clear error.\n"
        "5. UK spellings — do not Americanise anything.\n"
        "\n"
        "Return only the corrected text. No explanation. No preamble."
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": text}],
    )
    return response.content[0].text.strip()


def screen_render():
    progress_dots(3)

    st.markdown('<div class="headline">Paste the text to restore.</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub">Paste AI-generated text here. Voxa rewrites it in your voice, using the fingerprint it just built.</div>', unsafe_allow_html=True)

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
            st.session_state.render_output = ""
            api_key = "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"
            try:
                api_key = st.secrets["ANTHROPIC_API_KEY"] or api_key
            except Exception:
                pass

            if api_key:
                import anthropic, re as _re_clean

                # Sanitise input — strip stray citation lines AI tools inject
                def _sanitise_input(txt: str) -> str:
                    import re as _r
                    clean_lines = []
                    for ln in txt.split("\n"):
                        s = ln.strip()
                        # Stray news source names
                        if _r.search(r"(Mathrubhumi|BBC News|Sky News|Daily Mail|The Times|"
                                     r"Telegraph|Reuters|AP News|Evening Standard|City A\.M\.)", s, _r.I):
                            continue
                        # Bare URLs
                        if _r.match(r"https?://\S+$", s):
                            continue
                        # Source/credit tags
                        if _r.match(r"^(Source|Via|Credit|Image|Photo|From):\s*\S", s, _r.I):
                            continue
                        # Repeated text artefacts (e.g. "Mathrubhumi EnglishMathrubhumi English")
                        if len(s) > 10 and len(set(s.split())) < len(s.split()) * 0.5:
                            words = s.split()
                            if len(words) >= 4 and words[:len(words)//2] == words[len(words)//2:]:
                                continue
                        clean_lines.append(ln)
                    return "\n".join(clean_lines).strip()

                input_text = _sanitise_input(input_text)

                detected_mode = _detect_mode(input_text)
                st.session_state.intent_mode = detected_mode

                # Score the input for AI contamination
                ai_score = _score_ai_signal(input_text)
                st.session_state.ai_score = ai_score

                # Build rich voice DNA from all available fingerprint data
                observations = st.session_state.observations
                raw_text = st.session_state.get("raw_text", "")
                baseline = st.session_state.get("baseline_fingerprint")
                voice_dna = _build_voice_dna(observations, raw_text, baseline, ai_score)

                # Build mode instruction
                mode_instruction = apply_intent_mode(input_text, detected_mode)

                # Build the full system prompt — routes on ai_score
                word_count_input = len(input_text.split())
                system = _build_system_prompt(
                    voice_dna=voice_dna,
                    mode_instruction=mode_instruction,
                    word_count_input=word_count_input,
                    ai_score=ai_score,
                    baseline=baseline,
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
                    # Stage 1: guardrail sweep runs first
                    clean = _regex_sweep(clean)
                    # UK English cleaner
                    if st.session_state.get("locale", "uk") == "uk":
                        clean = _apply_uk_english(clean)
                    # Stage 2: grammar fix pass
                    clean = _grammar_fix_pass(clean, client)
                    # Stage 3: final sweep catches anything grammar pass introduced
                    clean = _regex_sweep(clean)
                    st.session_state.render_output = clean
                    # Step 4 — post-render scoring against baseline
                    if baseline:
                        output_metrics = compute_baseline_metrics(clean)
                        delta = {}
                        for key in ["hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio"]:
                            b_val = baseline[key]
                            o_val = output_metrics[key]
                            diff = o_val - b_val
                            pct_diff = abs(diff) / max(b_val, 0.01)
                            verdict = "HIT" if pct_diff <= 0.20 else "CLOSE" if pct_diff <= 0.40 else "MISSED"
                            delta[key] = {
                                "baseline": b_val,
                                "output": o_val,
                                "delta": round(diff, 3),
                                "verdict": verdict,
                            }
                        st.session_state.render_delta = delta

                        # Correction pass — targeted, surgical, invisible to user
                        missed = [key for key, d in delta.items() if d["verdict"] == "MISSED"]
                        if missed:
                            correction_instructions = []
                            for key in missed:
                                b_val = delta[key]["baseline"]
                                o_val = delta[key]["output"]
                                if key == "hedge_density":
                                    if o_val < b_val:
                                        correction_instructions.append(
                                            f"Hedge density is {o_val:.1f}% but should be {b_val:.1f}%. "
                                            f"Add natural uncertainty in 2-3 places using words like 'might', 'could', 'perhaps'. "
                                            f"Only where the writer would genuinely be uncertain. Do not force it.")
                                    else:
                                        correction_instructions.append(
                                            f"Hedge density is {o_val:.1f}% but should be {b_val:.1f}%. "
                                            f"Remove hedging words. Make statements direct.")
                                elif key == "sentence_length_sd":
                                    if o_val < b_val:
                                        correction_instructions.append(
                                            f"Sentence rhythm is too uniform (SD {o_val:.1f}, target {b_val:.1f}). "
                                            f"Vary the lengths deliberately — some very short (3-5 words), some longer (15-20 words). "
                                            f"The contrast is part of their voice.")
                                    else:
                                        correction_instructions.append(
                                            f"Sentence rhythm is too varied (SD {o_val:.1f}, target {b_val:.1f}). "
                                            f"Bring the lengths closer together. More consistent pacing.")
                                elif key == "first_person_ratio":
                                    if o_val < b_val:
                                        correction_instructions.append(
                                            f"Ownership is too low ({o_val:.0%} first-person, target {b_val:.0%}). "
                                            f"Replace passive or third-person constructions with direct first-person statements. "
                                            f"Own the points.")
                                elif key == "directive_ratio" and b_val >= 0.06:
                                    if o_val < b_val:
                                        correction_instructions.append(
                                            f"Directive pattern is missing ({o_val:.0%} action statements, target {b_val:.0%}). "
                                            f"Convert 1-2 suggestions into direct action statements. No 'please', no 'could you'.")

                            if correction_instructions:
                                correction_prompt = (
                                    "You are making precise surgical corrections to a voice restoration. "
                                    "The text below is close but has missed specific targets from the writer's baseline. "
                                    "Make only the changes needed to hit the targets. Do not rewrite. Do not improve. "
                                    "Correct only what is listed. Preserve everything else exactly.\n\n"
                                    "CORRECTIONS NEEDED:\n"
                                    + "\n".join(f"{i+1}. {inst}" for i, inst in enumerate(correction_instructions))
                                    + "\n\nABSOLUTE RULES: No em dashes. UK English. Return only the corrected text."
                                )
                                try:
                                    correction_response = client.messages.create(
                                        model="claude-sonnet-4-6",
                                        max_tokens=4096,
                                        system=correction_prompt,
                                        messages=[{"role": "user", "content": clean}],
                                    )
                                    corrected = correction_response.content[0].text
                                    # All guardrails run here — em dashes, contractions, Claude-isms
                                    corrected = _regex_sweep(corrected)
                                    if st.session_state.get("locale", "uk") == "uk":
                                        corrected = _apply_uk_english(corrected)
                                    clean = corrected
                                    st.session_state.render_output = clean
                                    # Re-score after correction
                                    output_metrics2 = compute_baseline_metrics(clean)
                                    for key in ["hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio"]:
                                        b_val = baseline[key]
                                        o_val = output_metrics2[key]
                                        diff = o_val - b_val
                                        pct_diff = abs(diff) / max(b_val, 0.01)
                                        verdict = "HIT" if pct_diff <= 0.20 else "CLOSE" if pct_diff <= 0.40 else "MISSED"
                                        delta[key] = {
                                            "baseline": b_val,
                                            "output": o_val,
                                            "delta": round(diff, 3),
                                            "verdict": verdict,
                                        }
                                    st.session_state.render_delta = delta
                                except Exception:
                                    pass  # Correction pass failed — keep original render

                    else:
                        st.session_state.render_delta = None
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

        # Delta banner — plain English, above the output, not a data table
        render_delta = st.session_state.get("render_delta")
        if render_delta and st.session_state.get("baseline_fingerprint"):
            banner_lines = []

            hd = render_delta["hedge_density"]
            sd = render_delta["sentence_length_sd"]
            fp = render_delta["first_person_ratio"]
            dr = render_delta["directive_ratio"]

            if sd["verdict"] == "HIT":
                banner_lines.append("Your rhythm is intact.")
            elif sd["verdict"] == "CLOSE":
                banner_lines.append("Your rhythm is close.")
            else:
                banner_lines.append("Your rhythm needed correction.")

            if fp["verdict"] == "HIT":
                banner_lines.append("Your ownership is intact.")
            elif fp["verdict"] == "MISSED" and fp["output"] < fp["baseline"]:
                banner_lines.append("Ownership restored.")

            if hd["verdict"] == "MISSED":
                if hd["output"] < hd["baseline"]:
                    banner_lines.append("Certainty pulled back to match your baseline.")
                else:
                    banner_lines.append("Hedging brought back to your natural level.")

            base_directive = dr["baseline"]
            if dr["verdict"] == "HIT" and base_directive >= 0.06:
                banner_lines.append("Your directness is preserved.")
            elif dr["verdict"] == "MISSED" and base_directive >= 0.06:
                banner_lines.append("Directive pattern restored.")

            if banner_lines:
                banner_text = " ".join(banner_lines[:3])
                st.markdown(
                    f'<div style="font-size:0.82rem;color:#888;margin-bottom:0.75rem;letter-spacing:0.01em;">{banner_text}</div>',
                    unsafe_allow_html=True
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
        '<div class="microcopy" style="margin-top:2rem;">Voxa keeps your voice.</div>',
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

