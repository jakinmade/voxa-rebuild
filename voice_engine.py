"""
voice_engine.py — Voicova's measurement core.

No Streamlit. No API calls. This module measures. It does not generate.

Everything in here is deterministic — same input, same output, every time.
That is the whole point: per the v4 spec's design principles, never trust
a claim about voice when you can measure it against a numeric baseline.

Ported from the original app.py monolith (proven in production), with two
additions new in this rebuild:
  - score_semantic_drift(): a deterministic proxy for "did the meaning
    survive the rewrite", parallel to the existing voice-drift check.
  - compute_confidence() / compute_risk(): the two trust indicators from
    the v4 spec, kept as separate questions (how sure is the measurement,
    vs how far did this rewrite move from the baseline) rather than one
    score doing both jobs.
"""

import re
import math
import json
import hashlib
from dataclasses import dataclass
from collections import Counter

_DOTTED_ABBREV = re.compile(r'\b(?:[A-Za-z]\.){2,}')
_WORD_ABBREV = re.compile(
    r'\b(Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|Rev|Hon|vs|etc|approx|dept|no|vol|pp|'
    r'Ltd|Inc|Co|Corp|Ave|Blvd|Rd)\.',
    re.I
)


def _protect_abbreviations(text: str) -> str:
    """
    Swaps periods inside known abbreviations (Mr., Dr., U.K., e.g., i.e.)
    for a placeholder so they don't get read as sentence boundaries.
    Restored after splitting.
    """
    text = _DOTTED_ABBREV.sub(lambda m: m.group(0).replace('.', '\u0000'), text)
    text = _WORD_ABBREV.sub(lambda m: m.group(0).replace('.', '\u0000'), text)
    return text


def _extract_sentences(text: str) -> list[str]:
    """Split into sentences. Returns non-empty sentences only."""
    text = re.sub(r'\n+', '. ', text)
    protected = _protect_abbreviations(text)
    sentences = re.split(r"(?<=[.!?])\s+", protected.strip())
    sentences = [s.replace('\u0000', '.') for s in sentences]
    return [s.strip() for s in sentences if s.strip() and len(s.split()) >= 2]
def _shortest_sentences(sentences: list[str], n: int = 2) -> list[str]:
    """Returns the n shortest sentences — evidence for compression."""
    return sorted(sentences, key=lambda s: len(s.split()))[:n]
# Expanded from the original 20-word list per external review — still a
# fixed deterministic list, not NLP/POS tagging, just wider coverage of
# common imperative openers (instructional/directive verbs).
_IMPERATIVE_VERBS = (
    "Fix|Send|Call|Build|Close|Check|Review|Do|Make|Take|Stop|Start|Deploy|"
    "Ship|Run|Get|Go|Ensure|Define|Test|Remember|Consider|Treat|Avoid|Keep|"
    "Let|Try|Add|Bring|Cut|Drop|Follow|Focus|Handle|Hold|Improve|Increase|"
    "Reduce|Confirm|Contact|Schedule|Plan|Prepare|Draft|Submit|Approve|Reject|"
    "Escalate|Flag|Verify|Validate|Assess|Evaluate|Note|Watch|Look|Read|Write|"
    "Update|Delete|Create|Move|Push|Pull|Set|Use|Find|Prove|Drive|Establish|"
    "Maintain|Monitor|Track|Log|Report|Notify|Alert|Warn|Prevent|Limit|"
    "Restrict|Allow|Enable|Disable|Remove|Cancel|Postpone|Reschedule|Book|"
    "Arrange|Organise|Organize|Coordinate|Delegate|Assign|Own|Lead|Manage|"
    "Oversee|Supervise|Audit|Inspect|Investigate|Research|Analyse|Analyze|"
    "Compare|Measure|Calculate|Estimate|Forecast|Project|Predict|Anticipate|"
    "Expect|Assume|Question|Challenge|Insist|Demand|Request|Ask|Tell|Explain|"
    "Clarify|Simplify|Summarise|Summarize|Highlight|Emphasise|Emphasize|"
    "Stress|Repeat|Restate|Rephrase|Reconsider|Rethink|Revisit|Return|Revert|"
    "Restore|Recover|Repair|Rebuild|Redesign|Refactor|Rewrite|Revise|Edit|"
    "Polish|Finalise|Finalize|Complete|Finish|Wrap|Open|Launch|Release|"
    "Publish|Post|Share|Circulate|Distribute|Forward|Attach|Include|Exclude|"
    "Omit|Skip|Ignore|Dismiss|Discard|Retain|Preserve|Protect|Secure|Lock|"
    "Unlock|Grant|Revoke|Withdraw"
)
_imperative_pattern = re.compile(rf"^({_IMPERATIVE_VERBS})\b", re.I)


def _imperative_sentences(sentences: list[str]) -> list[str]:
    """Sentences that start with an imperative verb."""
    return [s for s in sentences if _imperative_pattern.match(s)]
def _hedge_sentences(sentences: list[str]) -> list[str]:
    """Sentences containing hedge words."""
    hedges = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|"
        r"quite|rather|potentially|arguably|tend to|often)\b", re.I
    )
    return [s for s in sentences if hedges.search(s)]
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
@dataclass
class Observation:
    """A single fingerprint observation with evidence."""
    id: str
    signal_strength: float      # 0.0-1.0 - how strongly this fires
    dimension_hint: str         # Internal - not shown to user
    evidence_quotes: list[str]  # Actual words from the paste
    data: dict                  # Metrics used downstream
def score_conclusion_position(sentences: list[str], text: str) -> Observation:
    """Does the point come first or last?"""
    if not sentences:
        return Observation("conclusion_position", 0.0, "directness", [], {})

    first_three = sentences[:3]
    avg_first_length = sum(len(s.split()) for s in first_three) / max(len(first_three), 1)
    all_avg = sum(len(s.split()) for s in sentences) / max(len(sentences), 1)

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
    """Does the writer own statements or cushion them?"""
    words = text.split()
    total = max(len(words), 1)
    hedge_pattern = re.compile(
        r"\b(might|could|perhaps|possibly|maybe|somewhat|"
        r"quite|rather|potentially|arguably)\b", re.I
    )
    hedge_count = len(hedge_pattern.findall(text))
    density = hedge_count / total

    hedge_sentences = _hedge_sentences(sentences)
    if density < 0.02:
        signal = 0.85
        owns = True
    elif density > 0.06:
        signal = 0.80
        owns = False
    else:
        signal = 0.40
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
    """Does the writer explain or assume the reader is already up to speed?"""
    scaffolding = re.compile(
        r"\b(as you (know|may know|will know)|let me (explain|be clear|clarify)|"
        r"what (this|that) means (is|for you)|in other words|to put it (simply|another way)|"
        r"basically|simply put|the reason (is|being)|background(:|,))\b", re.I
    )
    found = scaffolding.findall(text)
    assumes_peer = len(found) == 0

    definition_pattern = re.compile(r"\b\w+ (is|are|refers to|means) (a |an |the )?\w+", re.I)
    definitions = definition_pattern.findall(text)

    signal = 0.75 if assumes_peer else 0.70
    evidence = sentences[:2]

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
    """Is brevity stylistic or structural?"""
    if not sentences:
        return Observation("compression_philosophy", 0.0, "compression", [], {})

    lengths = [len(s.split()) for s in sentences]
    avg = sum(lengths) / max(len(lengths), 1)
    variance = sum((l - avg) ** 2 for l in lengths) / max(len(lengths), 1)
    stddev = variance ** 0.5

    shortest = _shortest_sentences(sentences, n=2)

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
    """Where does the intensity live - verbs or adjectives?"""
    ratio = _verb_density(text)
    imperatives = _imperative_sentences(sentences)

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
    """Does the writer issue directives without softening?"""
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
    Runs all six scorers. Returns 3-5 observations ordered by signal strength.
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

    above_threshold = [o for o in all_obs if o.signal_strength >= MIN_SIGNAL]
    above_threshold.sort(key=lambda o: o.signal_strength, reverse=True)

    selected = above_threshold[:5]

    if len(selected) < 3:
        below = [o for o in all_obs if o not in selected]
        below.sort(key=lambda o: o.signal_strength, reverse=True)
        selected.extend(below[:3 - len(selected)])

    return selected[:5]
def _deterministic_fallback(observations: list[Observation]) -> list[dict]:
    """
    Turns scored observations into headline + body copy.
    Character claim observations - specific enough to be wrong about someone.
    Unique evidence quotes across all observations. Sign-offs filtered.
    """
    result = []
    used_quotes: set = set()

    _signoff = re.compile(
        r'^(Regards|Best|Cheers|Thanks|Sincerely|Kind regards|Many thanks|JA|John)[,.]?\s*$',
        re.I
    )

    def _pick_quote(quotes: list[str], fallback: str = "") -> str:
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
                body = f'The point comes first. Context and reasoning follow. "{quote}"'
            elif point_first:
                body = "You lead with the conclusion. Reasoning follows, it does not precede."
            else:
                body = (
                    "You develop context before landing the point. "
                    "The conclusion arrives after the reasoning is laid."
                )

        elif obs.id == "hedging_signature":
            owns = obs.data.get("owns_statements", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "You own your statements" if owns else "You soften before you land"
            if owns and quote:
                body = (
                    f'You say what you do not know. "{quote}" '
                    f"No inflation. That takes more confidence than pretending."
                )
            elif owns:
                body = "You state things directly. No cushioning before the point."
            else:
                body = (
                    f'Your writing uses cushioning language before conclusions. "{quote}" '
                    f"— the softening comes before the point." if quote else
                    "You soften before landing the point."
                )

        elif obs.id == "reader_assumption":
            peer = obs.data.get("assumes_peer", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "You write to an equal" if peer else "You write to inform"
            if peer and quote:
                body = (
                    f"No explanatory scaffolding. No 'as you know' or 'let me explain'. "
                    f'You assume the reader is already in the room. "{quote}" They usually are.'
                )
            elif peer:
                body = "No explanatory scaffolding. You assume the reader is already in the room."
            else:
                body = "You build context before the point. The reader needs grounding before the conclusion."

        elif obs.id == "compression_philosophy":
            avg = obs.data.get("avg_sentence_length", 8)
            clean_quotes = [q for q in obs.evidence_quotes if not _signoff.match(q.strip())]
            quote = _pick_quote(clean_quotes)
            headline = "You close and move"
            if quote:
                body = f'Short sentences. No padding. You finish when the point is made. "{quote}"'
            else:
                body = (
                    f"Short sentences. No padding. "
                    f"Average sentence: {avg:.0f} words. You finish when the point is made."
                )

        elif obs.id == "energy_signature":
            verb_dom = obs.data.get("verb_dominant", True)
            quote = _pick_quote(obs.evidence_quotes)
            headline = "Your force comes from verbs" if verb_dom else "Your force comes from emphasis"
            if verb_dom and quote:
                body = (
                    f'Intensity through action, not adjectives. '
                    f'The writing moves because the verbs move it. "{quote}"'
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
                    f'"{quote1}" No please. No when you get a chance. '
                    f"The instruction is the sentence." + (f' "{quote2}" Same again.' if quote2 else "")
                )
            elif hard_count >= 1 and quote1:
                body = f'"{quote1}" No cushioning before the ask. The directive stands on its own.'
            else:
                headline = "You soften your asks"
                body = "Your action items come with softening. The ask is cushioned before it lands."

        else:
            headline = obs.id.replace("_", " ").title()
            body = f"Signal strength: {obs.signal_strength:.0%}"

        result.append({"id": obs.id, "headline": headline, "body": body})

    return result
def analyse_writing(text: str) -> list[dict]:
    """
    Runs the fingerprint engine. Returns 3-5 observations
    [{headline, body}, ...] ordered by signal strength.
    """
    observations = select_observations(text)
    return _deterministic_fallback(observations)
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
    words = text.split()
    total_words = max(len(words), 1)

    # Sentence split — shared function, so the abbreviation guard
    # (Mr./Dr./U.K./e.g.) applies here too, not just to the observation
    # narrative. Was previously a separate inline re.split() that missed it.
    sentences = _extract_sentences(text)
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

    # 4. Directive ratio — imperative sentences.
    # Uses the same _imperative_sentences()/_IMPERATIVE_VERBS list as the
    # fingerprint observations (score_reader_assumption, score_directive_pattern)
    # rather than a separate inline list — one source of truth, so an
    # expansion to the verb list can't silently apply to the narrative but
    # not the actual scored metric.
    directive_sents = len(_imperative_sentences(sentences))
    directive_ratio = round(directive_sents / total_sents, 3)

    return {
        "hedge_density": hedge_density,
        "sentence_length_sd": sentence_length_sd,
        "first_person_ratio": first_person_ratio,
        "directive_ratio": directive_ratio,
        "word_count": total_words,
    }


def fingerprint_hash(baseline: dict) -> str:
    """
    Deterministic hash of the baseline metrics dict. Same input text
    must always produce the same hash — this is a demonstration and
    regression check of the engine's determinism (per the v4 spec's
    core design principle), not a security control.

    Uses canonical JSON (sorted keys, fixed separators) so key
    ordering can never change the hash, then SHA-256, truncated to
    12 hex chars and grouped for display, e.g. "5F4A-92BC-11DD".

    Only the four scored dimensions are hashed, not word_count —
    word_count changes between Sample 1 alone and the merged Sample
    1+2 baseline (see _merge_baseline), which would make the hash
    look non-deterministic across pipeline stages when the underlying
    voice measurement hasn't actually changed.
    """
    scored_dims = {
        k: baseline[k]
        for k in ("hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio")
        if k in baseline
    }
    canonical = json.dumps(scored_dims, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest().upper()
    return "-".join(digest[i:i + 4] for i in range(0, 12, 4))


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
def _extract_vocabulary_fingerprint(text: str) -> dict:
    """
    Extracts the user's actual vocabulary — the words they reach for without thinking.
    Not the most polished words. The most frequent content words.
    These are what the renderer should use rather than elevated synonyms.

    Also identifies AI-default substitutions — words the user never writes
    that AI reaches for instead.
    """
    import re
    from collections import Counter

    # Strip punctuation and lowercase
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())

    # Function words and stopwords to exclude
    stopwords = {
        'the','and','for','are','was','were','has','have','had','not','but',
        'with','from','this','that','they','their','there','what','when','who',
        'which','been','will','would','could','should','can','may','might',
        'its','our','your','his','her','also','just','even','only','more',
        'some','such','than','then','into','out','about','all','any','each',
        'said','does','did','him','her','she','per','how','now','new','one',
        'two','very','too','yet','both','over','after','before','since','while',
        'though','although','because','however','therefore','thus','hence',
        'furthermore','moreover','additionally','nevertheless','nonetheless',
        'subsequently','consequently','accordingly','meanwhile'
    }

    content_words = [w for w in words if w not in stopwords and len(w) >= 4]
    word_freq = Counter(content_words)

    # Top 40 most frequent content words — these are their vocabulary fingerprint
    top_words = [word for word, count in word_freq.most_common(40) if count >= 1]

    # AI-default substitutions — words AI reaches for that this user avoids
    # Detected by absence from corpus + being common AI vocabulary
    ai_substitutions = {
        'narrative': 'story or impression',
        'landscape': 'situation or environment',
        'transition': 'change',
        'underscores': 'shows',
        'highlights': 'shows',
        'showcases': 'shows',
        'illustrates': 'shows',
        'demonstrates': 'shows',
        'reflects': 'shows',
        'signifies': 'means',
        'trajectory': 'direction or path',
        'paradigm': 'approach',
        'ecosystem': 'environment',
        'leverage': 'use',
        'robust': 'strong',
        'pivotal': 'important',
        'crucial': 'important',
        'significant': 'important',
        'substantial': 'large',
        'comprehensive': 'full',
        'nuanced': 'detailed',
        'multifaceted': 'complex',
        'holistic': 'full',
        'synergy': 'benefit',
        'operationalise': 'put into practice',
        'contextualise': 'put in context',
        'characterised': 'marked',
        'underpinned': 'supported',
        'encapsulates': 'captures',
        'epitomises': 'represents',
    }

    # Which AI words does this user actually avoid?
    avoided = {ai_word: replacement
               for ai_word, replacement in ai_substitutions.items()
               if ai_word not in word_freq}

    # Average word length — proxy for register level
    avg_word_length = sum(len(w) for w in content_words) / max(len(content_words), 1)

    return {
        'top_words': top_words[:30],
        'avoided_ai_words': avoided,
        'avg_word_length': round(avg_word_length, 1),
        'word_count': len(words),
    }
def _format_vocabulary_fingerprint(vocab: dict, input_genre: str = "article") -> str:
    """
    Formats vocabulary fingerprint as renderer instruction.
    Plain English. Specific. Not a description — actual words to use.
    """
    if not vocab:
        return ""

    lines = ["\nVOCABULARY FINGERPRINT — their actual words, not polished synonyms:"]

    if vocab.get('top_words'):
        # Show most frequent words — these are their natural vocabulary
        words_str = ', '.join(f"'{w}'" for w in vocab['top_words'][:20])
        lines.append(f"  Words they actually reach for: {words_str}")
        lines.append(f"  Use these where they fit. Do not substitute elevated synonyms.")

    if vocab.get('avoided_ai_words'):
        # Show specific substitutions
        lines.append(f"  SUBSTITUTIONS — words they never write and what to use instead:")
        for ai_word, replacement in list(vocab['avoided_ai_words'].items())[:8]:
            lines.append(f"    '{ai_word}' → '{replacement}'")

    avg_len = vocab.get('avg_word_length', 0)
    if avg_len > 0:
        if avg_len < 5.5:
            lines.append(f"  REGISTER: short words, plain register (avg {avg_len:.1f} chars). "
                        f"Do not reach for longer alternatives.")
        elif avg_len < 6.5:
            lines.append(f"  REGISTER: moderate word length (avg {avg_len:.1f} chars). "
                        f"Match this level.")

    lines.append(f"  KEY PRINCIPLE: write about the thing, not the description of the thing. "
                f"Not 'a narrative that X was Y' — just 'X was Y'.")

    return "\n".join(lines)
# ============================================================
# New in v4 — semantic preservation, confidence, risk
# ============================================================
#
# Honest scoping note: semantic drift below is a deterministic lexical
# proxy (named-entity preservation + content-word overlap), not an
# embeddings-based semantic similarity model. That's a real limitation,
# not a hidden one — no embedding/vector service is wired into this
# product yet. It's the correct v1 choice per the v4 spec's own design
# principle (favour deterministic checks over subjective judgement
# wherever practical), and it catches the failure mode that matters
# most: a rewrite that drops or invents facts, names, or numbers.
# A stronger embeddings-based check is a natural, isolated upgrade —
# it would replace this function's internals without touching anything
# that calls it.

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of",
    "in", "on", "at", "for", "with", "as", "by", "is", "are", "was",
    "were", "be", "been", "being", "this", "that", "these", "those",
    "it", "its", "i", "you", "we", "they", "he", "she", "his", "her",
    "their", "our", "your", "my", "not", "no", "do", "does", "did",
    "will", "would", "can", "could", "should", "may", "might", "have",
    "has", "had", "from", "up", "down", "out", "about", "into", "over",
    "after", "before", "than", "just", "also", "there", "here",
}


def _content_words(text: str) -> set:
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _entities_and_numbers(text: str) -> set:
    """Proper nouns and numbers — the facts a rewrite must not lose."""
    non_proper = {
        'The', 'This', 'That', 'These', 'Those', 'They', 'Their', 'There',
        'When', 'What', 'Which', 'Where', 'Who', 'How', 'And', 'But',
        'For', 'With', 'From', 'Also', 'Some', 'Have', 'Been', 'Will',
        'Would', 'Could', 'Should', 'Just', 'Still', 'Even', 'Here',
        'Very', 'More', 'Most', 'Into', 'Over', 'After', 'About',
    }
    proper = {w for w in re.findall(r'(?<=[.!? ])[A-Z][a-zA-Z]{2,}', text) if w not in non_proper}
    numbers = set(re.findall(r'\b\d+[\d,.]*%?\b', text))
    return proper | numbers


def _possessive_attributions(text: str) -> dict:
    """
    Maps each noun that follows 'your' or 'my' to the set of pronouns it
    appeared with. Used to catch attribution swaps that score_semantic_drift
    cannot see by construction — 'your' and 'my' are both in _STOPWORDS
    (correctly, for ordinary content-overlap scoring), which means 'your
    point' -> 'my point' looks identical to that scorer: 'point' survived,
    nothing dropped. But that swap changes who the point belongs to - it's
    a meaning change, not a style change, and content-word overlap can't
    detect it by design.
    """
    pattern = re.compile(r"\b(your|my)\s+([a-z]+)\b", re.I)
    mapping: dict = {}
    for pronoun, noun in pattern.findall(text):
        noun_l = noun.lower()
        mapping.setdefault(noun_l, set()).add(pronoun.lower())
    return mapping


def detect_attribution_swaps(input_text: str, output_text: str) -> list[str]:
    """
    Flags nouns where 'your <noun>' in the input became 'my <noun>' in
    the output, or vice versa - a credit/attribution flip, not a voice
    adjustment. Deterministic, rule-based, per the standing architecture
    constraint - no model call, same input always produces the same flags.

    Narrow by design: only fires when a noun's possessive pronoun
    actually flipped between your/my, not on every 'your'/'my' in the
    text (most won't have a same-noun counterpart on the other side,
    and aren't flagged). This will not catch every attribution error -
    'as you said' -> 'as I said' uses different words per side, not the
    same noun with a flipped pronoun - but it catches the specific,
    high-consequence pattern seen in production: crediting yourself for
    someone else's point, or vice versa, in the same reply.
    """
    input_map = _possessive_attributions(input_text)
    output_map = _possessive_attributions(output_text)

    flags = []
    for noun, input_pronouns in input_map.items():
        output_pronouns = output_map.get(noun)
        if not output_pronouns:
            continue
        if "your" in input_pronouns and "my" in output_pronouns and "your" not in output_pronouns:
            flags.append(f"'your {noun}' became 'my {noun}' — credit moved from them to you")
        elif "my" in input_pronouns and "your" in output_pronouns and "my" not in output_pronouns:
            flags.append(f"'my {noun}' became 'your {noun}' — credit moved from you to them")
    return flags


def score_semantic_drift(input_text: str, output_text: str) -> dict:
    """
    Deterministic proxy for whether the rewrite preserved what was
    actually said, not just how it was said. Compares the render INPUT
    (the text being rewritten) against the render OUTPUT — a different
    axis from voice preservation, which compares output style against
    the fingerprint baseline.

    Returns a 0-100 semantic match score plus the entities that were
    dropped, so a correction pass can target them specifically.
    """
    input_entities = _entities_and_numbers(input_text)
    output_entities = _entities_and_numbers(output_text)
    if input_entities:
        preserved = input_entities & output_entities
        entity_score = len(preserved) / len(input_entities)
        dropped = sorted(input_entities - output_entities)
    else:
        entity_score = 1.0
        dropped = []

    input_content = _content_words(input_text)
    output_content = _content_words(output_text)
    if input_content or output_content:
        overlap = len(input_content & output_content)
        union = len(input_content | output_content)
        content_score = overlap / union if union else 1.0
    else:
        content_score = 1.0

    semantic_match = round(100 * (0.6 * entity_score + 0.4 * content_score))

    attribution_swaps = detect_attribution_swaps(input_text, output_text)

    return {
        "semantic_match": semantic_match,
        "entity_preservation": round(entity_score * 100),
        "content_overlap": round(content_score * 100),
        "dropped_entities": dropped[:5],
        "attribution_swaps": attribution_swaps,
    }


_STABILITY_DIMENSIONS = (
    "hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio",
)

# Coefficient-of-variation threshold below which a dimension counts as
# "stable" (consistent across registers -> likely genuine idiolect, not
# a register artefact). Fixed and deterministic, same threshold for
# every user - no per-persona tuning. 0.35 chosen as a first-pass value:
# tight enough to catch a dimension that swings roughly proportional to
# or beyond its own mean, loose enough that ordinary sample-to-sample
# noise on short samples doesn't get everything flagged volatile.
STABILITY_CV_THRESHOLD = 0.35


def compute_dimension_stability(samples: list[dict]) -> dict:
    """
    Takes the per-sample baseline metrics for each register-distinct
    sample collected (Screen 1 paste + the required contrasting
    starters — NOT the blended/merged baseline) and reports, per
    dimension, whether the value held steady across registers or
    swung with them.

    Why this exists: research on stylometry and idiolect (register
    variation as the mechanism underlying most authorship-attribution
    signal) says the only way to tell "this is genuinely how this
    person writes" apart from "this is just what this scenario pulled
    out of them" is to compare the same measurement across
    deliberately different registers. A single blended average
    (_merge_baseline) can't make that distinction — it was never given
    more than one register's worth of independent data at once. This
    function is that missing comparison. It doesn't replace
    _merge_baseline (still needed as the single target number for
    render-matching); it's a second, independent read that says how
    much to trust each dimension of that number.

    Deterministic and rule-based only, per the standing architecture
    constraint - no model call, no randomness, same samples always
    produce the same verdict.

    Requires at least 2 samples to say anything about stability (one
    sample has no variation to measure). With exactly 1 sample,
    every dimension is reported "insufficient_data" rather than
    guessed at.
    """
    if not samples:
        return {"dimensions": {}, "stable_count": 0, "volatile_count": 0, "sample_count": 0}

    if len(samples) < 2:
        return {
            "dimensions": {d: "insufficient_data" for d in _STABILITY_DIMENSIONS},
            "stable_count": 0,
            "volatile_count": 0,
            "sample_count": len(samples),
        }

    dimensions = {}
    stable_count = 0
    volatile_count = 0

    for dim in _STABILITY_DIMENSIONS:
        values = [s.get(dim, 0.0) for s in samples]
        n = len(values)
        mean = sum(values) / n
        variance = sum((v - mean) ** 2 for v in values) / n
        stdev = math.sqrt(variance)

        if mean == 0:
            # No baseline to compare against (e.g. zero hedge words in
            # every sample). Absence of the feature everywhere is
            # itself a consistent signal, not missing data.
            label = "stable" if stdev == 0 else "volatile"
        else:
            cv = stdev / abs(mean)
            label = "stable" if cv <= STABILITY_CV_THRESHOLD else "volatile"

        dimensions[dim] = label
        if label == "stable":
            stable_count += 1
        elif label == "volatile":
            volatile_count += 1

    return {
        "dimensions": dimensions,
        "stable_count": stable_count,
        "volatile_count": volatile_count,
        "sample_count": len(samples),
    }


def compute_confidence(
    fitness: dict | None,
    baseline: dict | None,
    num_observations: int,
    stability: dict | None = None,
) -> str:
    """
    How much to trust the Voice Match / Semantic Match numbers themselves.
    Built entirely from signal already computed elsewhere — not a new
    measurement, a read on the ones that already exist.

    stability (from compute_dimension_stability) is now part of that
    read. Word count and fitness tier say whether there's enough
    writing to measure. Stability says whether what was measured is
    actually the person's voice or just what one register happened to
    pull out of them - a wordy, gold-tier sample that's register-driven
    on most dimensions is not more trustworthy than the badge implies,
    it's differently untrustworthy. High confidence now requires both:
    plenty of good material AND most dimensions holding steady across
    the register-distinct samples that were collected.
    """
    if not baseline:
        return "Low"

    wc = baseline.get("word_count", 0)
    tier = (fitness or {}).get("tier", "thin")

    # No stability data (e.g. called before Screen 3, or on an older
    # single-sample flow) - fall back to the word-count/tier read alone
    # rather than penalising for data that was never collected.
    if not stability or stability.get("sample_count", 0) < 2:
        if wc >= 800 and tier in ("gold", "strong") and num_observations >= 4:
            return "High"
        if wc >= 250 and tier in ("gold", "strong", "thin"):
            return "Medium"
        return "Low"

    stable = stability.get("stable_count", 0)
    volatile = stability.get("volatile_count", 0)
    total = stable + volatile
    stable_ratio = (stable / total) if total else 0.0

    if (
        wc >= 800 and tier in ("gold", "strong") and num_observations >= 4
        and stable_ratio >= 0.75
    ):
        return "High"
    if wc >= 250 and tier in ("gold", "strong", "thin") and stable_ratio >= 0.5:
        return "Medium"
    return "Low"


def confidence_caveat(stability: dict | None) -> str | None:
    """
    One plain-English line for the UI, shown only when the Confidence
    badge is being held back specifically because the two required
    starters read differently from each other - not for every case, not
    as a permanent fixture, and never naming a dimension or a metric.

    Deliberately narrow: this only fires on the actual limiting
    condition (stable_ratio below the Medium threshold used in
    compute_confidence), so it never nags a user whose Confidence is
    already High, and it never appears at all before Screen 3 has run
    (sample_count < 2). No jargon - "stable", "volatile", "dimension"
    and "coefficient of variation" stay engine-internal; the badge and
    this one line are the only things the product surfaces.

    Returns None (nothing shown) when there's nothing useful to say.
    """
    if not stability or stability.get("sample_count", 0) < 2:
        return None

    stable = stability.get("stable_count", 0)
    volatile = stability.get("volatile_count", 0)
    total = stable + volatile
    if total == 0:
        return None
    stable_ratio = stable / total

    if stable_ratio < 0.5:
        return (
            "Your two samples read pretty differently from each other. "
            "Paste one more — if your writing holds steady there, this "
            "will strengthen things. If it lands differently again, that's "
            "telling you something real too: your voice may just shift more "
            "than most between these kinds of writing."
        )
    return None


def compute_risk(delta: dict | None, semantic: dict | None, ai_tells: dict | None = None) -> str:
    """
    How much this specific rewrite moved from the person's normal style
    and content — distinct from Confidence. Confidence is about the
    measurement; Risk is about this particular result.

    A surviving AI tell (em dash, banned phrase) is a hard failure on
    its own — High risk regardless of how the voice/semantic scores
    look, since the whole product promise breaks the moment an AI tell
    reaches the user, independent of everything else scoring well.

    An attribution swap ('your point' -> 'my point' or reverse) is the
    same category of hard failure, for the same reason: semantic_match
    can sit at 97% with an attribution swap present, because the word-
    overlap scorer that number comes from can't see it - 'your' and
    'my' are stopwords for that comparison by design. A high headline
    number next to a credit-reassignment error is worse than a low one,
    since it tells the person everything's fine when it isn't. Treated
    as High regardless of every other score, same as an AI tell.
    """
    if ai_tells and not ai_tells.get("clean", True):
        return "High"

    if (semantic or {}).get("attribution_swaps"):
        return "High"

    missed = sum(1 for d in (delta or {}).values() if d.get("verdict") == "MISSED")
    semantic_match = (semantic or {}).get("semantic_match", 100)

    if semantic_match < 70 or missed >= 3:
        return "High"
    if semantic_match < 85 or missed >= 1:
        return "Medium"
    return "Low"


def score_render_delta(baseline: dict, output_text: str) -> dict:
    """
    Per-dimension comparison of the render output against the numeric
    baseline — the structural check that is the actual product, not a
    system-prompt instruction taken on faith. Refactored out of the
    original inline render logic so it's testable and reusable (used
    once for the initial render, again after the correction pass).
    """
    output_metrics = compute_baseline_metrics(output_text)
    delta = {}
    for key in ["hedge_density", "sentence_length_sd", "first_person_ratio", "directive_ratio"]:
        b_val = baseline[key]
        o_val = output_metrics[key]
        diff = o_val - b_val
        pct_diff = abs(diff) / max(b_val, 0.01)
        verdict = "HIT" if pct_diff <= 0.20 else "CLOSE" if pct_diff <= 0.40 else "MISSED"
        delta[key] = {
            "baseline": b_val, "output": o_val,
            "delta": round(diff, 3), "pct_diff": round(pct_diff, 3),
            "verdict": verdict,
        }
    return delta


def voice_match_pct(delta: dict) -> int:
    """Aggregate the per-dimension delta into a single internal score.
    Not shown to users as a headline number — see voice_match_label().
    Precise percentages implied a precision the underlying metrics
    (surface stats, not voice) can't support; scoring methods in this
    space score below the measured human floor and don't correlate
    well with each other across approaches."""
    if not delta:
        return 0
    scores = [max(0.0, 1.0 - d["pct_diff"]) for d in delta.values()]
    return round(100 * sum(scores) / len(scores))


def voice_match_label(delta: dict) -> dict:
    """
    Qualitative replacement for the bare Voice Match %. Buckets the
    internal score into a plain-language tier and surfaces the actual
    evidence (which dimensions held, which drifted) instead of implying
    false precision with a number.
    """
    pct = voice_match_pct(delta)
    if pct >= 85:
        tier, badge = "Strong", "badge-green"
    elif pct >= 65:
        tier, badge = "Good", "badge-green"
    elif pct >= 45:
        tier, badge = "Developing", "badge-amber"
    else:
        tier, badge = "Limited", "badge-red"

    hits = [_DIMENSION_LABELS.get(k, k) for k, d in delta.items() if d["verdict"] == "HIT"]
    missed = [_DIMENSION_LABELS.get(k, k) for k, d in delta.items() if d["verdict"] == "MISSED"]

    if hits and not missed:
        evidence = "Held on " + ", ".join(hits) + "."
    elif hits and missed:
        evidence = "Held on " + ", ".join(hits) + " — drifted on " + ", ".join(missed) + "."
    elif missed:
        evidence = "Drifted on " + ", ".join(missed) + "."
    else:
        evidence = "No baseline comparison available."

    return {"tier": tier, "badge": badge, "evidence": evidence, "_raw_pct": pct}


_DIMENSION_LABELS = {
    "hedge_density": "hedging",
    "sentence_length_sd": "sentence rhythm",
    "first_person_ratio": "ownership (first person)",
    "directive_ratio": "directness",
}


# ============================================================
# Burrows' Delta — function-word frequency distance
# ============================================================
#
# Gap this closes: the existing voice-match signal (score_render_delta,
# above) rests on four hand-picked heuristics, and _extract_vocabulary_
# fingerprint (elsewhere in this file) explicitly EXCLUDES function
# words, keeping only content/topic vocabulary. That's backwards from
# the field's actual gold standard. Burrows' Delta (Burrows, 2002,
# "'Delta': A Measure of Stylistic Difference and a Guide to Likely
# Authorship", Literary and Linguistic Computing 17(3):267-287) remains,
# per multiple 2025/2026 replications, the most-cited and most robust
# method in forensic and literary stylometry — precisely because it
# fingerprints the MOST FREQUENT words in a corpus, which are
# overwhelmingly function words (the, of, and, but, I, to). These are
# used unconsciously and are topic-independent, which is exactly why
# they're reliable style signals where content words aren't.
#
# Important methodological note, stated plainly rather than glossed
# over: true Burrows' Delta does NOT use a fixed, pre-defined function-
# word list. The most-frequent-words (MFW) set is computed dynamically
# from the corpus under study — for any normal amount of English text
# this naturally resolves to being almost entirely function words,
# without needing to hardcode which ones, and it lets each user's own
# distinguishing habitual words (not just classic function words) show
# up if they're genuinely frequent for that person. This implementation
# follows that: the MFW set is derived from the user's own baseline
# corpus, not a hardcoded list.
#
# Second honest limitation: classic Delta z-scores each MFW's frequency
# against the mean/SD of many candidate-author texts. This implementation
# requires at least 2 of the user's own baseline writing samples for the
# same reason — one sample has no variance to measure against. Per-word
# mean/SD are computed across those baseline samples independently, and
# the single rendered output is then z-scored against that established
# distribution. See compute_burrows_delta's docstring for a worked
# example of why the naive alternative (z-scoring baseline vs output as
# a single pair) is mathematically degenerate and was caught and fixed
# during testing, not shipped.

_DELTA_MFW_COUNT = 100


def compute_mfw_profile(text: str, n: int = _DELTA_MFW_COUNT) -> dict:
    """
    Most-frequent-words profile — the actual Burrows' Delta input.
    Unlike _extract_vocabulary_fingerprint (elsewhere in this file),
    this does NOT exclude stopwords/function words. It keeps every
    word, because function words are the signal here, not noise to
    filter out. Returns {word: relative_frequency_per_1000_words}.
    """
    words = re.findall(r"\b[a-zA-Z']+\b", text.lower())
    total = len(words)
    if total == 0:
        return {}

    counts = Counter(words)
    top_words = [w for w, _ in counts.most_common(n)]
    return {w: round((counts[w] / total) * 1000, 4) for w in top_words}


def compute_burrows_delta(baseline_samples: list[str], output_text: str, n: int = _DELTA_MFW_COUNT) -> dict:
    """
    Burrows' Delta between a user's established baseline and a rendered
    output. Lower delta means the output's function-word habits sit
    closer to the baseline's; this is a genuinely different signal from
    voice_match_pct (four hand-picked heuristics) and semantic_match
    (content-word overlap) — it measures the unconscious, topic-
    independent word-choice habits stylometry research treats as the
    most reliable style fingerprint available.

    CORRECTED DESIGN — read before changing this function:
    An earlier version of this function took a single baseline string
    and z-scored it pairwise against the single output string, using
    the mean/SD of just those two values. That is mathematically
    degenerate: for any two unequal numbers a and b, z-scoring each
    against their own shared pairwise mean/SD always produces
    |z_a - z_b| = 2, regardless of how far apart a and b actually are.
    Verified directly: (10, 12), (10, 50), and (10, 500) all produced
    exactly delta 2.0. That version could not distinguish a near-
    identical register from a wildly different one — caught in testing
    before this shipped, not after.

    The fix: this function requires baseline_samples as a LIST of the
    user's own distinct writing samples (matching the list[dict] pattern
    compute_dimension_stability already uses elsewhere in this module,
    which exists for the same underlying reason — a single blended
    baseline has no variance to measure against). Per-word mean and SD
    are computed ACROSS the baseline samples independently, THEN the
    output is z-scored against that established reference distribution.
    The output is not one of the points used to compute the SD, so the
    two-point degeneracy above does not recur. This mirrors the
    conventional application of Delta: a known-author reference corpus
    defines the distribution, and a disputed/candidate text is measured
    against it, not folded into computing it.

    Requires at least 2 baseline samples for the same reason
    compute_dimension_stability does — one sample has no variance to
    measure. With fewer than 2, returns tier="Insufficient baseline
    samples" rather than a fabricated score. See module-level note on
    the MFW set being corpus-derived (from the baseline), not a fixed
    universal word list — that part of the original design was correct
    and is unchanged.

    Returns:
        delta          — mean |z-score| of the output's per-word
                          frequencies against the baseline distribution.
                          0.0 for a perfect match; no fixed upper bound.
        word_count     — MFW pairs actually compared
        biggest_divergences — up to 3 words contributing most to the
                          score
        tier           — qualitative read; see module docstring on why
                          these bands are a starting point, not gospel
    """
    if len(baseline_samples) < 2:
        return {
            "delta": None, "word_count": 0,
            "biggest_divergences": [], "tier": "Insufficient baseline samples",
        }

    combined_baseline = " ".join(baseline_samples)
    mfw_set = compute_mfw_profile(combined_baseline, n=n)
    if not mfw_set:
        return {
            "delta": None, "word_count": 0,
            "biggest_divergences": [], "tier": "Insufficient baseline",
        }

    output_words = re.findall(r"\b[a-zA-Z']+\b", output_text.lower())
    output_total = len(output_words)
    if output_total == 0:
        return {
            "delta": None, "word_count": 0,
            "biggest_divergences": [], "tier": "Insufficient output",
        }
    output_counts = Counter(output_words)

    # Per-word frequency in EACH baseline sample separately — this is
    # what gives a genuine, non-degenerate mean/SD with sample_count
    # independent points, not just the 2 values (baseline, output)
    # being compared against each other.
    per_sample_freqs = []
    for sample in baseline_samples:
        sample_words = re.findall(r"\b[a-zA-Z']+\b", sample.lower())
        sample_total = len(sample_words)
        sample_counts = Counter(sample_words)
        if sample_total == 0:
            continue
        per_sample_freqs.append(
            {w: (sample_counts.get(w, 0) / sample_total) * 1000 for w in mfw_set}
        )

    if len(per_sample_freqs) < 2:
        return {
            "delta": None, "word_count": 0,
            "biggest_divergences": [], "tier": "Insufficient baseline samples",
        }

    z_scores = []
    per_word = []
    for word in mfw_set:
        values = [sf[word] for sf in per_sample_freqs]
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        sd = math.sqrt(variance)

        output_freq = round((output_counts.get(word, 0) / output_total) * 1000, 4)

        if sd == 0:
            # Every baseline sample used this word at exactly the same
            # rate — a genuinely stable habit. If the output matches it,
            # zero divergence; if not, treat any deviation from a
            # zero-variance reference as maximally divergent for this
            # word rather than dividing by zero.
            z = 0.0 if output_freq == mean else 3.0
        else:
            z = abs((output_freq - mean) / sd)

        z_scores.append(z)
        per_word.append((word, z, mean, output_freq))

    delta = round(sum(z_scores) / len(z_scores), 3) if z_scores else 0.0

    per_word.sort(key=lambda t: t[1], reverse=True)
    biggest_divergences = [
        {"word": w, "baseline_freq": round(bf, 4), "output_freq": of}
        for w, z, bf, of in per_word[:3] if z > 0
    ]

    # Bands are a documented starting point, not a settled field
    # standard — see module docstring. Calibrate against real user data
    # once available; these are wider than published literary-Delta
    # thresholds (typically <1.0 = same-author-likely on much larger
    # corpora) since this runs on far less reference data per user.
    if delta <= 1.5:
        tier = "Close"
    elif delta <= 3.0:
        tier = "Moderate"
    else:
        tier = "Wide"

    return {
        "delta": delta,
        "word_count": len(mfw_set),
        "biggest_divergences": biggest_divergences,
        "tier": tier,
    }


def build_voice_report(delta: dict, semantic: dict, confidence: str, risk: str, ai_tells: dict | None = None, burrows_delta: dict | None = None) -> dict:
    """
    Assembles the Voice Report — the actual differentiated output, per
    the v4 spec. Not just rewritten text: Voice Match, Semantic Match,
    Confidence, Risk, an explicit AI-tell check, and the biggest changes,
    all built from data already computed elsewhere in this module.

    burrows_delta (optional): output of compute_burrows_delta(), a
    second, independently-grounded voice-match signal alongside the
    existing four-heuristic voice_match. Additive, not a replacement —
    see compute_burrows_delta's docstring for why function-word
    frequency distance is a genuinely different, field-established
    measurement rather than a restatement of the existing metrics.
    """
    biggest_changes = []
    for key, d in sorted(delta.items(), key=lambda kv: kv[1]["pct_diff"], reverse=True):
        if d["verdict"] == "HIT":
            continue
        label = _DIMENSION_LABELS.get(key, key)
        direction = "+" if d["delta"] > 0 else ""
        pct = round(d["pct_diff"] * 100)
        biggest_changes.append(f"{label} {direction}{pct}%")

    voice_match = voice_match_label(delta)

    report = {
        "voice_match": voice_match["_raw_pct"],  # kept internally, not shown as headline
        "voice_match_tier": voice_match["tier"],
        "voice_match_badge": voice_match["badge"],
        "voice_match_evidence": voice_match["evidence"],
        "semantic_match": semantic["semantic_match"],
        "confidence": confidence,
        "risk": risk,
        "ai_tell_clean": (ai_tells or {}).get("clean", True),
        "ai_tell_flags": (ai_tells or {}).get("flagged", []),
        "biggest_changes": biggest_changes[:3],
        "dropped_entities": semantic.get("dropped_entities", []),
        "attribution_swaps": semantic.get("attribution_swaps", []),
    }

    if burrows_delta is not None:
        report["function_word_delta"] = burrows_delta.get("delta")
        report["function_word_delta_tier"] = burrows_delta.get("tier")
        report["function_word_biggest_divergences"] = burrows_delta.get("biggest_divergences", [])

    return report

# ============================================================
# New — measured verification of the AI-tell guardrail itself
# ============================================================
#
# Every other claim this product makes is checked against a number:
# voice match, semantic match. Whether the output actually reads as
# human — not just "we told the model not to use em dashes" — was the
# one guardrail applied and trusted rather than measured. This closes
# that gap. Same principle as everything else here: don't trust the
# prompt, or the regex sweep, on faith — measure the outcome.

_CONTRACTION_PATTERN = re.compile(
    r"\b\w+'(?:t|s|re|ve|d|ll|m)\b", re.I
)

# The banned-phrase list mirrors _regex_sweep's claude_constructions and
# _score_ai_signal's verbose_openers/filler_transitions in prompts.py —
# kept here as the measurement side of the same guardrail, so the check
# and the fix are drawing from a shared understanding of what an AI
# tell actually is, not two lists that can quietly drift apart.
# ---------------------------------------------------------------------------
# Plausibility shields — first-person lexical-verb hedges that attribute a
# claim to the writer's own judgement rather than stating it as fact:
# "I think", "I see it as", "in my view". Documented category, not an ad
# hoc list — Prince, Frader & Bosk (1982) coined "plausibility shield" for
# exactly this; Hyland's hedging taxonomy (1998), the standard reference
# in this field, groups the same verbs (think, believe, see, view, take,
# argue, say) under "lexical verb hedges" alongside modals and epistemic
# adverbs (which _HEDGE_WORD_PATTERN in prompts.py already covers).
#
# Two shapes need different repair, not one blanket strip:
#   - "I think that X" -> "X" stands alone; the clause after the shield
#     already has its own subject and verb.
#   - "I see it as X" -> deleting the shield leaves a fragment with no
#     verb ("X" has no "is"). These need the shield replaced with a verb,
#     not removed outright.
# _PLAUSIBILITY_SHIELD_STRIP (prompts.py) keys off which group matched to
# apply the right repair. Kept here, not in prompts.py, because prompts.py
# already imports its shared vocabulary from this module (_classify_register,
# compute_baseline_metrics etc.) — single source of truth, not a second list
# to keep in sync.
# ---------------------------------------------------------------------------
_PLAUSIBILITY_SHIELD_DROP = re.compile(
    r"^(I think|I believe|I would argue|I would say|I'd say|My view is|"
    r"My take is|My sense is|It seems to me)\b,?\s*(that\s+)?",
    re.IGNORECASE,
)
_PLAUSIBILITY_SHIELD_MIDSENTENCE = re.compile(
    r"(,\s*)?\b(in my view|in my opinion|as I see it|as I view it)\b"
    r"(\s*,\s*|\s+|(?=[.!?]|$))",
    re.IGNORECASE,
)
_PLAUSIBILITY_SHIELD_REPLACE = re.compile(
    r"^(I see it as|I see this as|I view it as|I view this as|I take it as)\b\s*",
    re.IGNORECASE,
)
# Combined pattern used only for detection (score_ai_tells) — mirrors the
# three strip patterns above so "clean" can't mean "the strip regex never
# ran on this text" the way the em-dash-launder bug did.
_PLAUSIBILITY_SHIELD_PHRASES = re.compile(
    r"\b(I think that|I believe that|I would argue that|I would say that|"
    r"I'd say that|my view is that|my take is that|my sense is that|"
    r"it seems to me that|in my view|in my opinion|as I see it|as I view it|"
    r"I see it as|I see this as|I view it as|I view this as|I take it as)\b",
    re.IGNORECASE,
)

_AI_TELL_PHRASES = re.compile(
    r"\b(it is (important|worth|essential|crucial|critical|key) to (note|recognise|recognize|understand)|"
    r"in (today's|the current|our) (landscape|world|environment|era)|"
    r"it goes without saying|needless to say|with that (said|in mind)|"
    r"as (we|you) (know|can see|may know)|"
    r"this (underscores|highlights|demonstrates|illustrates|showcases)|"
    r"leverag(e|ing)|synerg(y|ies)|holistic(ally)?|paradigm|robust(ly)?|"
    r"cutting.edge|game.changing|transformative|groundbreaking|"
    r"furthermore|moreover|nevertheless|notwithstanding|"
    r"in conclusion|to summarise|to summarize|in summary|"
    r"moving forward|circle back|touch base|pain points|"
    r"seamless(ly)?|delve into|tapestry|testament to|boasts|elevate|"
    r"unlock the potential|game.changer|unparalleled|paramount)\b",
    re.I
)

# ---------------------------------------------------------------------------
# Analytical-register AI tells — added because _AI_TELL_PHRASES above is
# tuned for corporate-marketing-slop vocabulary (leverage, synergy, robust)
# and misses a completely different tell vocabulary that shows up in
# analytical/argumentative writing: essay-style connective tissue and the
# habit of pressing abstract nouns into service as verbs. "Drift", "surface"
# and "land on" are as diagnostic in this register as "leverage" is in the
# corporate one. This is additive — _AI_TELL_PHRASES is untouched, this is
# a second, separate list applied only when _classify_register says the
# text warrants it. See _classify_register below.
# ---------------------------------------------------------------------------
_ANALYTICAL_TELL_PHRASES = re.compile(
    r"\b(drift(s|ed|ing)?|surfac(e|es|ed|ing)(?!\s+area)|land(s|ed|ing)? on|"
    r"unpack(s|ed|ing)?|gestur(e|es|ed|ing) (at|toward|towards)|"
    r"sit(s)? with|push back (on|against)|"
    r"worth noting|to be fair|on reflection|"
    r"the (real|deeper) (question|issue|tension) (is|here)|"
    r"i suspect|i would push back|closer to .* than to)\b",
    re.I
)

# Fragment-as-emphasis: a short sentence ending in a period, immediately
# followed by a capitalised noun-phrase fragment with no verb of its own
# (e.g. "Distinct stage. Not a subdivision."). Common essay-voice tic.
_FRAGMENT_EMPHASIS_PATTERN = re.compile(
    r"[.!?]\s+(Not\s+\w+|Different\s+\w+|No\s+\w+(?:\s+\w+){0,3})[.!?]",
    re.I
)

# Spaced-hyphen dash substitute: _regex_sweep (prompts.py) converts every
# em/en dash to " - " as its fix, but the em-dash check below only looks
# for the literal unicode dash characters that substitution just removed.
# Result: the sweep launders the tell into a form its own detector can't
# see, and "clean" stops meaning "no dash-smell" and starts meaning "no
# *unconverted* dash." Found via John's Premier League rewrite test —
# "drama - the league runs" scored clean.
#
# Matches a hyphen with a space on both sides, i.e. used as a standalone
# connective, not a hyphenated compound ("well-known", no spaces) and not
# a number range ("10-15", no spaces) and not a line-leading list bullet
# ("- item", no preceding non-space char for the lookbehind to anchor on).
_SPACED_HYPHEN_DASH_PATTERN = re.compile(r"(?<=\S)\s-\s(?=\S)")


def _classify_register(text: str) -> str:
    """
    Buckets input text as 'corporate', 'analytical', or 'mixed' so
    score_ai_tells and the rewrite sweep know which tell-vocabulary
    to apply. Heuristic, not ML — counts hits against each existing
    tell list plus a couple of structural markers, and compares.

    Self-contained by design: does not call other private scoring
    helpers, so it can't be broken by changes to unrelated functions.

    Deliberately conservative — ties or thin margins fall to 'mixed',
    which means both lists get applied. A false positive from the
    analytical list on corporate text costs nothing since the words
    in that list barely occur in corporate writing anyway; a missed
    analytical tell (false negative) is the failure mode we're
    actually trying to close, so 'mixed' is the safe default.
    """
    if not text or not text.strip():
        return "mixed"

    corporate_hits = len(_AI_TELL_PHRASES.findall(text))
    analytical_hits = len(_ANALYTICAL_TELL_PHRASES.findall(text))
    analytical_hits += len(_FRAGMENT_EMPHASIS_PATTERN.findall(text))

    # Structural signal: analytical/essay writing tends to run longer,
    # more varied sentence lengths than corporate copy. Cheap proxy —
    # standard deviation of sentence word-counts.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    word_counts = [len(s.split()) for s in sentences] if sentences else []
    if len(word_counts) >= 3:
        mean = sum(word_counts) / len(word_counts)
        variance = sum((c - mean) ** 2 for c in word_counts) / len(word_counts)
        stdev = variance ** 0.5
    else:
        stdev = 0.0

    structural_leans_analytical = stdev >= 6.0  # empirically wide spread

    if corporate_hits == 0 and analytical_hits == 0:
        return "mixed" if structural_leans_analytical else "corporate"
    if analytical_hits > corporate_hits:
        return "analytical"
    if corporate_hits > analytical_hits:
        return "corporate"
    return "mixed"


def uses_contractions(text: str) -> bool:
    """
    Does this person's own writing use contractions? Baseline-driven,
    not assumed. Feeds keep_contractions through to the regex sweep so
    contraction handling follows the user's actual voice rather than
    one hardcoded convention applied to everyone.
    """
    words = max(len(text.split()), 1)
    hits = len(_CONTRACTION_PATTERN.findall(text))
    return (hits / words) > 0.01  # roughly 1+ contraction per 100 words


def score_ai_tells(text: str) -> dict:
    """
    Measured verification that the output doesn't read as AI-written —
    run AFTER the regex sweep and grammar pass, not instead of them.
    This is the check that confirms the guardrail actually worked,
    rather than trusting that it did.

    clean=True only if there are zero hard-fail hits. Em dashes are a
    hard fail on their own — a single one surviving is enough to fail,
    since the whole point of the sweep is that there should be none.

    Checks two separate tell vocabularies: _AI_TELL_PHRASES (corporate
    register — leverage, synergy, robust) always runs. _ANALYTICAL_TELL_
    PHRASES (essay/argumentative register — drift, surface, land on)
    runs when _classify_register says the text is 'analytical' or
    'mixed'. Return shape is unchanged from before this check existed,
    so every existing caller (app.py, dev_tools/harness.py) keeps
    working without modification.
    """
    em_dash_hits = len(re.findall(r"[\u2012\u2013\u2014\u2015]", text))
    spaced_hyphen_hits = len(_SPACED_HYPHEN_DASH_PATTERN.findall(text))
    phrase_hits = list(_AI_TELL_PHRASES.findall(text))
    shield_hits = list(_PLAUSIBILITY_SHIELD_PHRASES.findall(text))

    register = _classify_register(text)
    analytical_hits = []
    if register in ("analytical", "mixed"):
        analytical_hits = list(_ANALYTICAL_TELL_PHRASES.findall(text))
        analytical_hits += list(_FRAGMENT_EMPHASIS_PATTERN.findall(text))

    all_hits = phrase_hits + analytical_hits + shield_hits

    flagged = []
    if em_dash_hits:
        flagged.append(f"{em_dash_hits} em dash(es) survived the sweep")
    if spaced_hyphen_hits:
        flagged.append(
            f"{spaced_hyphen_hits} spaced hyphen(s) used as a dash substitute "
            f"(the sweep converts em dashes to ' - ' — that's still the tell)"
        )
    if all_hits:
        unique_phrases = sorted(set(p if isinstance(p, str) else p[0] for p in all_hits))
        flagged.append(f"AI-typical phrasing found: {', '.join(unique_phrases[:5])}")

    clean = em_dash_hits == 0 and spaced_hyphen_hits == 0 and len(all_hits) == 0

    return {
        "clean": clean,
        "em_dash_count": em_dash_hits,
        "spaced_hyphen_count": spaced_hyphen_hits,
        "phrase_hit_count": len(all_hits),
        "flagged": flagged,
        "register": register,
    }
