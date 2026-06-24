"""
Voxa — Fingerprint Narrative Generator
Architecture Spec v9.8.0, Section 7.9.

The highest-leverage screen in the product.

Takes a VoiceProfile + original paste + extracted facts.
Returns three to five human-readable observations with evidence
quoted directly from the user's own writing.

Design constraints:
  - Each observation must be specific enough to be wrong about someone.
    If it applies to everyone, it applies to no one.
  - Each observation quotes the user's own words as proof.
    Not paraphrase. The actual words.
  - Maximum five observations. Minimum three.
    More than five = overwhelm. Fewer than three = thin.
  - No dimension names exposed to the user.
    "directness: high" is internal. "You lead with the answer" is the reveal.
  - No horoscope language.
    "You tend to..." is a horoscope. "You do X" is a mirror.
  - LLM handles prose only.
    Deterministic engine selects observations, pulls evidence, scores signal.
    LLM writes the human-readable version of what the engine found.

The five high-discriminating observations:
  1. Conclusion position — point first or point last
  2. Hedging signature — ownership vs cushioning
  3. Reader assumption — writes up, level, or down
  4. Compression philosophy — brevity as style vs structure
  5. Energy signature — intensity in verbs vs adjectives
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple

import structlog

from voxa_core.entities import VoiceProfile, ExtractedFact

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Evidence extraction — pulls actual quotes from the paste
# ---------------------------------------------------------------------------

def _extract_sentences(text: str) -> list[str]:
    """Split into sentences. Returns non-empty sentences only."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip() and len(s.split()) >= 2]


def _shortest_sentences(sentences: list[str], n: int = 2) -> list[str]:
    """Returns the n shortest sentences — evidence for compression."""
    return sorted(sentences, key=lambda s: len(s.split()))[:n]


def _imperative_sentences(sentences: list[str]) -> list[str]:
    """Sentences that start with an imperative verb."""
    pattern = re.compile(
        r"^(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|"
        r"Stop|Start|Deploy|Ship|Run|Get|Go|Ensure|Define|Test)\b", re.I
    )
    return [s for s in sentences if pattern.match(s)]


def _hedge_sentences(sentences: list[str]) -> list[str]:
    """Sentences containing hedge words."""
    hedges = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|"
        r"quite|rather|potentially|arguably|tend to|often)\b", re.I
    )
    return [s for s in sentences if hedges.search(s)]


def _first_words_of_sentences(sentences: list[str], n: int = 3) -> list[str]:
    """First word of each sentence — reveals conclusion position patterns."""
    return [s.split()[0] for s in sentences[:n] if s.split()]


def _verb_density(text: str) -> float:
    """Rough verb density — action words vs total words."""
    action_verbs = re.compile(
        r"\b(fix|send|call|build|close|check|review|do|make|take|stop|"
        r"start|deploy|ship|run|get|go|ensure|define|test|prove|drive|"
        r"write|read|find|use|set|move|push|pull|create|delete|update|"
        r"is|are|was|were|have|has|had|will|would|can|could|should)\b", re.I
    )
    adj_adv = re.compile(
        r"\b(very|really|extremely|quite|rather|somewhat|highly|deeply|"
        r"absolutely|completely|totally|incredibly|amazing|excellent|"
        r"great|good|bad|significant|important|critical|key|major)\b", re.I
    )
    words = text.split()
    if not words:
        return 0.0
    verb_count = len(action_verbs.findall(text))
    adj_count = len(adj_adv.findall(text))
    return verb_count / max(adj_count, 1)


# ---------------------------------------------------------------------------
# Observation scoring — deterministic selection of what to surface
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    """A single fingerprint observation with evidence."""
    id: str
    signal_strength: float      # 0.0–1.0 — how strongly this fires
    dimension_hint: str         # Internal — not shown to user
    evidence_quotes: list[str]  # Actual words from the paste
    data: dict                  # Metrics used by LLM prompt


def score_conclusion_position(sentences: list[str], text: str) -> Observation:
    """
    Does the point come first or last?
    Signal: opening sentences that are declarative vs building.
    High signal = direct opener pattern (assertion before reasoning).
    """
    if not sentences:
        return Observation("conclusion_position", 0.0, "directness", [], {})

    # Check if first sentences are short and declarative
    first_three = sentences[:3]
    avg_first_length = sum(len(s.split()) for s in first_three) / max(len(first_three), 1)
    all_avg = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)

    # Short openers relative to body = point-first pattern
    point_first = avg_first_length < all_avg * 0.85
    imperatives = _imperative_sentences(first_three)
    signal = 0.7 if point_first else 0.3
    if imperatives:
        signal = min(1.0, signal + 0.2)

    evidence = first_three[:2] if point_first else sentences[-2:]

    return Observation(
        id="conclusion_position",
        signal_strength=signal,
        dimension_hint="directness",
        evidence_quotes=evidence,
        data={
            "point_first": point_first,
            "avg_opener_length": round(avg_first_length, 1),
            "avg_sentence_length": round(all_avg, 1),
            "imperative_openers": len(imperatives),
        },
    )


def score_hedging_signature(sentences: list[str], text: str) -> Observation:
    """
    Does the writer own statements or cushion them?
    Signal: hedge word density. Either extreme is revealing.
    Low hedge density = direct ownership. High = cushioning pattern.
    """
    words = text.split()
    total = max(len(words), 1)
    hedge_pattern = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|"
        r"quite|rather|potentially|arguably)\b", re.I
    )
    hedge_count = len(hedge_pattern.findall(text))
    density = hedge_count / total

    hedge_sentences = _hedge_sentences(sentences)
    # Signal fires strongly at either extreme
    if density < 0.02:
        signal = 0.85   # Very low hedging — strong ownership signal
        owns = True
    elif density > 0.06:
        signal = 0.80   # Heavy hedging — strong cushioning signal
        owns = False
    else:
        signal = 0.40   # Middle — less discriminating
        owns = density < 0.04

    evidence = hedge_sentences[:2] if hedge_sentences else sentences[:2]

    return Observation(
        id="hedging_signature",
        signal_strength=signal,
        dimension_hint="confidence_expression",
        evidence_quotes=evidence,
        data={
            "hedge_density": round(density, 3),
            "hedge_count": hedge_count,
            "owns_statements": owns,
            "total_words": total,
        },
    )


def score_reader_assumption(sentences: list[str], text: str) -> Observation:
    """
    Does the writer explain or assume?
    Signal: presence/absence of explanatory scaffolding.
    "As you know", "let me explain", "what this means is" = writes down.
    Absence = assumes peer comprehension.
    """
    scaffolding = re.compile(
        r"\b(as you (know|may know|will know)|let me (explain|be clear|clarify)|"
        r"what (this|that) means (is|for you)|in other words|to put it (simply|another way)|"
        r"basically|simply put|the reason (is|being)|background(:|,))\b", re.I
    )
    found = scaffolding.findall(text)
    assumes_peer = len(found) == 0

    # Also check for definition patterns
    definition_pattern = re.compile(r"\b\w+ (is|are|refers to|means) (a |an |the )?\w+", re.I)
    definitions = definition_pattern.findall(text)

    signal = 0.75 if assumes_peer else 0.70
    evidence = sentences[:2]  # First sentences show the register assumption fastest

    return Observation(
        id="reader_assumption",
        signal_strength=signal,
        dimension_hint="audience_positioning",
        evidence_quotes=evidence,
        data={
            "assumes_peer": assumes_peer,
            "scaffolding_count": len(found),
            "definition_count": len(definitions),
        },
    )


def score_compression_philosophy(sentences: list[str], text: str) -> Observation:
    """
    Is brevity stylistic or structural?
    Signal: sentence length variance + paragraph termination patterns.
    Low variance + short = structural compression.
    High variance = stylistic (uses length deliberately).
    """
    if not sentences:
        return Observation("compression_philosophy", 0.0, "compression", [], {})

    lengths = [len(s.split()) for s in sentences]
    avg = sum(lengths) / max(len(lengths), 1)
    variance = sum((l - avg) ** 2 for l in lengths) / max(len(lengths), 1)
    stddev = variance ** 0.5

    shortest = _shortest_sentences(sentences, n=2)

    # Structural compression: short AND consistent
    if avg <= 10 and stddev <= 5:
        signal = 0.88
        structural = True
    elif avg <= 15 and stddev <= 8:
        signal = 0.70
        structural = True
    else:
        signal = 0.55
        structural = False

    return Observation(
        id="compression_philosophy",
        signal_strength=signal,
        dimension_hint="compression",
        evidence_quotes=shortest,
        data={
            "avg_sentence_length": round(avg, 1),
            "stddev": round(stddev, 1),
            "structural": structural,
            "sentence_count": len(sentences),
        },
    )


def score_energy_signature(sentences: list[str], text: str) -> Observation:
    """
    Where does the intensity live — verbs or adjectives?
    Signal: verb density ratio vs adjective/adverb density.
    Verb-dominant = force through action. Adj-dominant = force through emphasis.
    """
    ratio = _verb_density(text)
    imperatives = _imperative_sentences(sentences)

    # High verb density relative to adjectives = verb-dominant intensity
    verb_dominant = ratio > 2.5
    signal = 0.80 if verb_dominant else 0.65

    evidence = imperatives[:2] if imperatives else sentences[:2]

    return Observation(
        id="energy_signature",
        signal_strength=signal,
        dimension_hint="intensity",
        evidence_quotes=evidence,
        data={
            "verb_adj_ratio": round(ratio, 2),
            "verb_dominant": verb_dominant,
            "imperative_count": len(imperatives),
        },
    )


def score_directive_pattern(sentences: list[str], text: str) -> Observation:
    """
    Does the writer issue directives without softening?
    "Pull together proposition to present to MTN" — no please, no could you.
    High signal = person who issues action items directly.
    One of the first things AI strips — replacing direct action items with suggestions.
    """
    imperative = re.compile(
        r"^(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|Deploy|Ship|Run|Get|Go|"
        r"Ensure|Define|Test|Pull|Explore|Present|Prepare|Draft|Share|Note|Consider|"
        r"Look|Find|Use|Add|Remove|Update|Create|Set|Move|Push|Ask|Tell|Show|Keep|"
        r"Remember|Try|Confirm|Check|Follow|Reach)\b", re.I
    )
    prefixed = re.compile(
        r"^.{0,40}[-:]\s*(Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|Deploy|Ship|Run|Get|Go|"
        r"Ensure|Define|Test|Pull|Explore|Present|Prepare|Draft|Share|Note|Consider|"
        r"Look|Find|Use|Add|Remove|Update|Create|Set|Move|Push)\b", re.I
    )
    softening = re.compile(
        r"\b(please|could you|when you|if you|would you|kindly|feel free|don't hesitate)\b", re.I
    )

    all_imperatives = [s for s in sentences if imperative.match(s.strip()) or prefixed.match(s.strip())]
    hard = [s for s in all_imperatives if not softening.search(s)]
    soft_count = len([s for s in all_imperatives if softening.search(s)])
    hard_count = len(hard)

    if hard_count >= 2:
        signal = 0.90
    elif hard_count == 1:
        signal = 0.78
    elif soft_count > 0:
        signal = 0.35
    else:
        signal = 0.20

    return Observation(
        id="directive_pattern",
        signal_strength=signal,
        dimension_hint="directness",
        evidence_quotes=hard[:2] if hard else sentences[:1],
        data={
            "hard_directive_count": hard_count,
            "softened_directive_count": soft_count,
            "total_imperatives": len(all_imperatives),
            "issues_direct_actions": hard_count > 0,
        },
    )


def select_observations(text: str) -> list[Observation]:
    """
    Runs all five scorers. Returns 3–5 observations ordered by signal strength.
    Only includes observations above the minimum signal threshold.
    Guarantees: minimum 3 returned if any fire above threshold.
    """
    MIN_SIGNAL = 0.55
    sentences = _extract_sentences(text)

    all_obs = [
        score_conclusion_position(sentences, text),
        score_hedging_signature(sentences, text),
        score_reader_assumption(sentences, text),
        score_compression_philosophy(sentences, text),
        score_energy_signature(sentences, text),
        score_directive_pattern(sentences, text),
    ]

    # Sort by signal strength, take top 5 above threshold
    above_threshold = [o for o in all_obs if o.signal_strength >= MIN_SIGNAL]
    above_threshold.sort(key=lambda o: o.signal_strength, reverse=True)

    selected = above_threshold[:5]

    # Guarantee minimum 3 — take strongest even below threshold if needed
    if len(selected) < 3:
        below = [o for o in all_obs if o not in selected]
        below.sort(key=lambda o: o.signal_strength, reverse=True)
        selected.extend(below[:3 - len(selected)])

    logger.info(
        "observations_selected",
        count=len(selected),
        ids=[o.id for o in selected],
        signals=[round(o.signal_strength, 2) for o in selected],
    )

    return selected[:5]


# ---------------------------------------------------------------------------
# LLM prompt builder — deterministic structure, LLM fills the prose
# ---------------------------------------------------------------------------

def _build_narrative_prompt(observations: list[Observation], original_text: str) -> str:
    """
    Builds the LLM prompt. Deterministic structure.
    LLM writes the human-readable observations.
    LLM does not select what to surface — the engine already did that.
    """
    obs_instructions = []

    for i, obs in enumerate(observations, 1):
        quotes = " / ".join(f'"{q}"' for q in obs.evidence_quotes[:2])
        data_str = ", ".join(f"{k}={v}" for k, v in obs.data.items())

        if obs.id == "conclusion_position":
            instruction = (
                f"Observation {i}: CONCLUSION POSITION\n"
                f"Data: {data_str}\n"
                f"Evidence from their writing: {quotes}\n"
                f"Write one observation (2-3 sentences) about whether this person "
                f"leads with conclusions or builds toward them. "
                f"Be specific. Quote their words. No hedging.\n"
                f"Point-first={obs.data.get('point_first', False)}"
            )
        elif obs.id == "hedging_signature":
            instruction = (
                f"Observation {i}: HEDGING SIGNATURE\n"
                f"Data: {data_str}\n"
                f"Evidence from their writing: {quotes}\n"
                f"Write one observation (2-3 sentences) about whether this person "
                f"owns their statements or cushions them. "
                f"Hedge density={obs.data.get('hedge_density', 0):.3f}. "
                f"Owns statements={obs.data.get('owns_statements', True)}.\n"
                f"Be specific. Reference the density if low — that is the signal."
            )
        elif obs.id == "reader_assumption":
            instruction = (
                f"Observation {i}: READER ASSUMPTION\n"
                f"Data: {data_str}\n"
                f"Evidence from their writing: {quotes}\n"
                f"Write one observation (2-3 sentences) about the register assumption "
                f"this person makes about their reader. Do they assume the reader "
                f"already understands, or do they explain?\n"
                f"Assumes peer={obs.data.get('assumes_peer', True)}."
            )
        elif obs.id == "compression_philosophy":
            instruction = (
                f"Observation {i}: COMPRESSION PHILOSOPHY\n"
                f"Data: {data_str}\n"
                f"Evidence from their writing: {quotes}\n"
                f"Write one observation (2-3 sentences) about this person's "
                f"relationship with sentence length. Is it structural or stylistic?\n"
                f"Avg sentence length={obs.data.get('avg_sentence_length', 0):.1f} words. "
                f"Structural={obs.data.get('structural', True)}."
            )
        elif obs.id == "energy_signature":
            instruction = (
                f"Observation {i}: ENERGY SIGNATURE\n"
                f"Data: {data_str}\n"
                f"Evidence from their writing: {quotes}\n"
                f"Write one observation (2-3 sentences) about where this person's "
                f"intensity lives — in verbs (action) or adjectives (emphasis).\n"
                f"Verb dominant={obs.data.get('verb_dominant', True)}. "
                f"Ratio={obs.data.get('verb_adj_ratio', 0):.2f}."
            )
        else:
            instruction = f"Observation {i}: {obs.id}\nData: {data_str}\nEvidence: {quotes}"

        obs_instructions.append(instruction)

    obs_block = "\n\n".join(obs_instructions)

    return f"""You are writing a voice fingerprint reveal for the Voxa platform.

The user has pasted their own writing. The engine has analysed it and identified specific observations. Your job is to write the human-readable version of each observation.

RULES — non-negotiable:
1. Each observation is 2-3 sentences maximum. No more.
2. Quote the user's actual words as evidence. Use quotation marks.
3. Never say "you tend to" or "you might". Say "you do" or "you don't".
4. No dimension names (directness, formality, cadence). Plain English only.
5. No horoscope language. Be specific enough to be wrong about someone.
6. No preamble. No "based on your writing". Start with the observation.
7. UK English throughout.

FORMAT — return JSON only. No markdown. No preamble.
{{
  "observations": [
    {{"id": "...", "headline": "...", "body": "..."}},
    ...
  ]
}}

Headline: 4-6 words. The observation in a phrase.
Body: 2-3 sentences. The observation with evidence quoted.

ORIGINAL WRITING (for context only — do not quote more than 10 words at a time):
{original_text[:800]}

OBSERVATIONS TO WRITE:
{obs_block}"""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

class FingerprintNarrative(NamedTuple):
    observations: list[dict]     # [{id, headline, body}, ...]
    observation_count: int
    signal_strengths: list[float]
    is_high_confidence: bool     # True if avg signal > 0.75 — safe to show boldly


async def generate_fingerprint_narrative(
    text: str,
    profile: VoiceProfile,
) -> FingerprintNarrative:
    """
    Generates the fingerprint reveal narrative.

    Deterministic engine selects observations and pulls evidence.
    LLM writes the human-readable prose.
    Returns structured observations ready for the reveal screen.

    Falls back to deterministic descriptions if LLM unavailable.
    The reveal always works — never blank.
    """
    import json

    observations = select_observations(text)
    prompt = _build_narrative_prompt(observations, text)

    # LLM call — through the rendering layer boundary
    try:
        from voxa_rendering.llm_boundary import rewrite_with_constraints

        system = (
            "You write precise, specific voice fingerprint observations. "
            "Return JSON only. No markdown. No preamble. No explanation."
        )
        raw = await rewrite_with_constraints(system, prompt)
        raw = raw.strip()

        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("```").strip()

        parsed = json.loads(raw)
        llm_observations = parsed.get("observations", [])

        logger.info(
            "fingerprint_narrative_generated",
            observation_count=len(llm_observations),
            used_llm=True,
        )

    except Exception as e:
        logger.warning("fingerprint_llm_failed_using_fallback", error=str(e))
        # Deterministic fallback — always works
        llm_observations = _deterministic_fallback(observations)

    # Deterministic cleaner — runs on every observation body regardless of LLM output.
    # Em dashes are a credibility killer. Code enforcement, not a prompt instruction.
    from voxa_rendering.cleaner import clean_render_output
    for obs in llm_observations:
        if "body" in obs:
            obs["body"] = clean_render_output(obs["body"])
        if "headline" in obs:
            obs["headline"] = clean_render_output(obs["headline"])

    avg_signal = (
        sum(o.signal_strength for o in observations) / len(observations)
        if observations else 0.0
    )

    return FingerprintNarrative(
        observations=llm_observations,
        observation_count=len(llm_observations),
        signal_strengths=[round(o.signal_strength, 2) for o in observations],
        is_high_confidence=avg_signal > 0.75,
    )


def _deterministic_fallback(observations: list[Observation]) -> list[dict]:
    """
    Fallback when LLM is unavailable.
    Character claim observations — specific enough to be wrong about someone.
    Unique evidence quotes across all observations.
    Sign-offs filtered. Directive pattern included.
    """
    import re as _re

    result = []
    used_quotes: set = set()  # Prevent same quote appearing in multiple observations

    # Sign-off filter — never use as evidence
    _signoff = _re.compile(
        r'^(Regards|Best|Cheers|Thanks|Sincerely|Kind regards|Many thanks|JA|John)[,.]?\s*$',
        _re.I
    )

    def _pick_quote(quotes: list[str], fallback: str = "") -> str:
        """Pick first quote not already used and not a sign-off."""
        for q in quotes:
            q = q.strip()
            if not q:
                continue
            if _signoff.match(q):
                continue
            if len(q.split()) < 4:
                continue
            if q in used_quotes:
                continue
            used_quotes.add(q)
            return q
        return fallback

    for obs in observations:
        if obs.id == "conclusion_position":
            point_first = obs.data.get("point_first", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "You lead with the answer" if point_first else "You build to the conclusion"
            if point_first and quote:
                body = (
                    f"The point comes first. Context and reasoning follow. "
                    f'"{quote}"'
                )
            elif point_first:
                body = "You lead with the conclusion. Reasoning follows, it does not precede."
            else:
                body = (
                    f"You develop context before landing the point. "
                    f"The conclusion arrives after the reasoning is laid."
                )

        elif obs.id == "hedging_signature":
            owns = obs.data.get("owns_statements", True)
            denial_count = obs.data.get("denial_count", 0)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "You own your statements" if owns else "You soften before you land"
            if owns and denial_count > 0 and quote:
                body = (
                    f"You correct the record without apology. "
                    f'"{quote}" '
                    f"No preamble. Someone had it wrong and you put it right."
                )
            elif owns and quote:
                body = (
                    f"You say what you do not know. "
                    f'"{quote}" '
                    f"No inflation. That takes more confidence than pretending."
                )
            elif owns:
                body = "You state things directly. No cushioning before the point."
            else:
                body = (
                    f"Your writing uses cushioning language before conclusions. "
                    f'"{quote}" — the softening comes before the point.' if quote else
                    "You soften before landing the point."
                )

        elif obs.id == "reader_assumption":
            peer = obs.data.get("assumes_peer", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "You write to an equal" if peer else "You write to inform"
            if peer and quote:
                body = (
                    f"No explanatory scaffolding. No 'as you know' or 'let me explain'. "
                    f"You assume the reader is already in the room. "
                    f'"{quote}" They usually are.'
                )
            elif peer:
                body = "No explanatory scaffolding. You assume the reader is already in the room."
            else:
                body = "You build context before the point. The reader needs grounding before the conclusion."

        elif obs.id == "compression_philosophy":
            avg = obs.data.get("avg_sentence_length", 8)
            # Filter sign-offs before picking
            clean_quotes = [q for q in obs.evidence_quotes if not _signoff.match(q.strip())]
            quote = _pick_quote(clean_quotes)
            headline = "You close and move"
            if quote:
                body = (
                    f"Short sentences. No padding. "
                    f"You finish when the point is made. "
                    f'"{quote}"'
                )
            else:
                body = (
                    f"Short sentences. No padding. "
                    f"Average sentence: {avg:.0f} words. "
                    f"You finish when the point is made."
                )

        elif obs.id == "energy_signature":
            verb_dom = obs.data.get("verb_dominant", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "Your force comes from verbs" if verb_dom else "Your force comes from emphasis"
            if verb_dom and quote:
                body = (
                    f"Intensity through action, not adjectives. "
                    f"The writing moves because the verbs move it. "
                    f'"{quote}"'
                )
            elif verb_dom:
                body = "Intensity through action, not adjectives. The writing moves because the verbs move it."
            else:
                body = "You use emphasis words to carry weight. The intensity is in what you call things."

        elif obs.id == "directive_pattern":
            hard_count = obs.data.get("hard_directive_count", 0)
            quotes = obs.evidence_quotes
            quote1 = _pick_quote(quotes)
            quote2 = _pick_quote([q for q in quotes if q != quote1])
            headline = "You instruct without cushioning"
            if hard_count >= 2 and quote1:
                body = (
                    f'"{quote1}" '
                    f"No please. No when you get a chance. "
                    f"The instruction is the sentence."
                    + (f' "{quote2}" Same again.' if quote2 else "")
                )
            elif hard_count >= 1 and quote1:
                body = (
                    f'"{quote1}" '
                    f"No cushioning before the ask. The directive stands on its own."
                )
            else:
                headline = "You soften your asks"
                body = "Your action items come with softening. The ask is cushioned before it lands."

        else:
            headline = obs.id.replace("_", " ").title()
            body = f"Signal strength: {obs.signal_strength:.0%}"

        result.append({"id": obs.id, "headline": headline, "body": body})

    return result
