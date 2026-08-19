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

from scoring_rules import (
    DELTA_BAND_HIT_MAX_PCT,
    DELTA_BAND_CLOSE_MAX_PCT,
    DELTA_BAND_MIN_ABS_DIFF,
    RISK_HIGH_SEMANTIC_MATCH_BELOW,
    RISK_HIGH_MISSED_DIMENSIONS_AT_LEAST,
    RISK_MEDIUM_SEMANTIC_MATCH_BELOW,
    RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST,
    SEMANTIC_MATCH_ENTITY_WEIGHT,
    SEMANTIC_MATCH_CONTENT_WEIGHT,
)


# ============================================================
# Hedge detection — single source of truth
# ============================================================
#
# Was previously three separate, hand-copied word lists (here in
# score_hedging_signature, here again in compute_baseline_metrics, and
# a third copy in deterministic_fixers.py) that had already drifted out
# of sync — score_hedging_signature had "arguably", compute_baseline_
# metrics didn't. One canonical pattern now, imported everywhere it's
# needed, so a future change to what counts as a hedge can't silently
# apply to the narrative but not the actual scored metric, or vice versa.
#
# Expanded per Hyland's (1998, 2005) hedging taxonomy — the field's
# standard reference — which distinguishes single-lexical-item hedges
# (the original list here) from writer-oriented hedges realised through
# epistemic verb/clause constructions ("it seems that", "I wonder
# whether"). The original list only ever covered the first category.
# Real gap this closes: "Curious whether it holds up... because" reads
# as an unmistakable hedge in plain English but was invisible to this
# scorer before — it's a clause-level epistemic construction, not a
# single flagged word.
#
# Deliberately NOT included: "I think" / "I believe" on their own.
# These are genuinely ambiguous in casual professional writing (as
# opposed to the academic-research-article register Hyland's taxonomy
# was built on) — "I think you're wrong" is direct opinion-stating for
# many writers, not hedged uncertainty, and flagging it unconditionally
# would misread a direct writer's own voice as hedged. Only the less
# ambiguous clause-level constructions below were added; the risk of
# a false positive on those is lower.
_HEDGE_PATTERN = re.compile(
    r"\b("
    # Single-word epistemic adverbs / modals (original list, plus the
    # missing adverbs from the same Hyland category: presumably,
    # apparently, allegedly, seemingly, supposedly all modify a claim's
    # certainty the same way "possibly" or "perhaps" does).
    r"might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially|arguably|"
    r"presumably|apparently|allegedly|seemingly|supposedly|"
    # Clause-level epistemic hedges (Hyland's writer-oriented category)
    r"it seems|it appears|seems like|appears to|seems to|"
    r"curious whether|wonder if|wondering if|"
    r"not sure if|not sure whether|not certain if|not certain whether|"
    r"unsure if|unsure whether|hard to say|difficult to say|"
    # Softening quantifier hedges
    r"kind of|sort of|to some extent|in some ways"
    r")\b", re.I
)

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
    """Split into sentences. Returns non-empty sentences only.

    Paragraph breaks are normalised to a single space between
    sentence-ending punctuation, not unconditionally appended with a
    fresh period — the previous version did text = re.sub(r'\n+', '. ',
    text) regardless of what the paragraph already ended with, which
    produced literal double punctuation ("?." , "..") whenever a
    paragraph already ended in terminal punctuation. That corrupted
    real rendered output, not just internal tokenisation: every
    deterministic fixer in deterministic_fixers.py rebuilds its output
    text from this function's sentence list via " ".join(...), so the
    artifact shipped straight through to what a person actually saw.
    Confirmed directly against a real render before this was fixed,
    not a hypothetical edge case.
    """
    paragraphs = [p.strip() for p in re.split(r'\n+', text) if p.strip()]
    normalised = ' '.join(
        p if p.endswith(('.', '!', '?', ':')) else p + '.'
        for p in paragraphs
    )
    protected = _protect_abbreviations(normalised)
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

# Narrow, evidence-based exclusion: "Report in minutes", "Response in
# hours", "Delivery in days" are noun-phrase fragments common in
# sales/marketing copy (implicitly "[A] report, in minutes"), not
# commands, even though the sentence opens with a word from
# _IMPERATIVE_VERBS. Confirmed as a real false positive against a live
# render this session -- "Report in minutes, no build required on your
# side" was scored as a directive sentence purely because "Report"
# opens the imperative-verb list, which inflated directive_ratio and
# in turn produced a wildly exaggerated percentage-drift reading given
# how close to zero this dimension's baseline typically sits.
# Deliberately narrow to this one demonstrated shape rather than a
# general grammatical fix -- true part-of-speech disambiguation
# (verb vs. noun) is out of scope for a regex-level check, same
# standard already applied elsewhere in this codebase for similarly
# ambiguous cases (see deterministic_fixers.py's note on why POS
# tagging is deferred rather than guessed at).
_NOUN_PHRASE_FRAGMENT = re.compile(
    r"^\w+\s+in\s+(a\s+)?(minute|minutes|hour|hours|day|days|week|weeks|"
    r"month|months|second|seconds|no\s+time)\b", re.I
)


def _imperative_sentences(sentences: list[str]) -> list[str]:
    """Sentences that start with an imperative verb."""
    return [
        s for s in sentences
        if _imperative_pattern.match(s) and not _NOUN_PHRASE_FRAGMENT.match(s.strip())
    ]
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
    hedge_count = len(_HEDGE_PATTERN.findall(text))
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
                    f"The softening comes before the point." if quote else
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

    NOTE — a second, independent implementation of this same function
    name and shape exists in packages/voxa-api/src/voxa_api/recalibrate.py.
    That copy is not a duplicate to be merged; its own docstring explains
    it's a near-verbatim port of the older Streamlit app.py pipeline,
    kept deliberately separate because it powers full-draft recalibration
    rather than the five-dimension checker this file supports. The two
    are NOT expected to return identical numbers on the same input —
    confirmed divergences: this version's hedge detection uses the full
    _HEDGE_PATTERN (Hyland clause-level hedges like "curious whether",
    "it seems", "kind of" — see that pattern's own comment for why),
    recalibrate.py's is a single-word-only regex; this version's sentence
    splitting goes through _extract_sentences (abbreviation-guarded via
    _protect_abbreviations), recalibrate.py's is a raw re.split with no
    such guard. See tests/unit/test_baseline_metrics_divergence.py for a
    pinned example of the gap. If either implementation changes, check
    whether the divergence this documents is still accurate.

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
    hedge_count = len(_HEDGE_PATTERN.findall(text))
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


_BE_FORMS = r"\b(?:am|is|are|was|were|be|being|been)\b"

# Common irregular past participles — regular ones are caught by the
# \w+ed pattern below, this list covers the frequent irregulars that
# don't end in -ed (written, given, taken, etc). Not exhaustive by
# design: this is a heuristic flag, the same precision/simplicity
# trade-off _ANALYTICAL_TELL_PHRASES and _HEDGE_PATTERN already make
# elsewhere in this codebase, not a linguistically complete parser.
_IRREGULAR_PARTICIPLES = (
    r"written|given|taken|done|made|seen|known|shown|chosen|spoken|"
    r"broken|driven|eaten|fallen|forgotten|hidden|ridden|risen|sung|"
    r"drunk|run|held|told|sold|bought|brought|caught|taught|thought|"
    r"built|sent|spent|kept|left|felt|meant|met|paid|said|stood|"
    r"understood|won|begun|come|gone|found|grown|drawn|flown|worn|"
    r"torn|born|frozen|stolen|thrown|swum"
)

# Allows up to one intervening word (usually an adverb: "was quickly
# written") between the be-form and the participle.
_PASSIVE_PATTERN = re.compile(
    _BE_FORMS + r"\s+(?:\w+\s+){0,1}(?:\w+ed\b|" + _IRREGULAR_PARTICIPLES + r")\b",
    re.IGNORECASE,
)


def compute_passive_voice(text: str) -> dict:
    """
    Passive-voice heuristic — regex-based "be-form + past participle"
    matching, deliberately not a dependency-parse-based tool (PassivePy
    needs spaCy; ispassive needs a ~46s cold-start tagger train, both
    unsuitable for a Railway app that can spin down between requests).
    Same category of trade-off as this codebase's other deterministic
    text checks: simple pattern matching over full linguistic parsing.

    Known false positives: adjectival be+participle that isn't
    grammatically passive (e.g. "the window was closed" as a state,
    not an action). This is a heuristic flag for a line-editing
    signal, not a grammatical ground truth — same caveat that already
    applies to _ANALYTICAL_TELL_PHRASES and the other regex-based
    checks in this file.

    Standalone and read-only, like compute_sentence_economy: does NOT
    feed into compute_baseline_metrics, score_render_delta, or the
    correction-pass targeting pipeline.

    Returns:
        passive_count          — total regex matches in the text
        passive_sentence_ratio — proportion of sentences containing
                                  at least one match
    """
    sentences = _extract_sentences(text)
    if not sentences:
        return {"passive_count": 0, "passive_sentence_ratio": 0.0}

    per_sentence_matches = [_PASSIVE_PATTERN.findall(s) for s in sentences]
    passive_sentences = sum(1 for m in per_sentence_matches if m)
    total_matches = sum(len(m) for m in per_sentence_matches)

    return {
        "passive_count": total_matches,
        "passive_sentence_ratio": round(passive_sentences / len(sentences), 3),
    }


def _count_syllables(word: str) -> int:
    """
    Heuristic syllable counter — vowel-group counting with the standard
    silent-e adjustment. This is the same approach used by common
    readability libraries (e.g. textstat's fallback counter); it isn't
    a phonetic dictionary lookup, so it will be off by one on some
    irregular words, but it's accurate enough for a sentence-level
    economy signal, not exact phonetic transcription.
    """
    word = word.lower().strip(".,;:!?\"'()[]")
    if not word:
        return 0
    vowels = "aeiouy"
    count = 0
    prev_was_vowel = False
    for ch in word:
        is_vowel = ch in vowels
        if is_vowel and not prev_was_vowel:
            count += 1
        prev_was_vowel = is_vowel
    if word.endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def compute_sentence_economy(text: str) -> dict | None:
    """
    Flesch-Kincaid Grade Level, hand-rolled — deliberately not the
    textstat library. Adding a new pip dependency to the live Streamlit
    app is a separate, higher-risk decision (requirements.txt change,
    full Railway redeploy retest) that this change explicitly avoids;
    see the equivalent reasoning already documented in
    packages/voxa-core/src/voxa_core/text_guardrail.py for the same
    call made on a different dependency. The formula itself is public
    domain (Kincaid et al., 1975, developed for the US Navy) so
    reimplementing it directly carries none of that risk.

    Standalone and read-only: this does NOT feed into
    compute_baseline_metrics, score_render_delta, or the correction-
    pass targeting pipeline. It's a separate, additive signal — the
    18 Aug 2026 research into automated readability scoring (Gruteke
    Klein et al., arXiv:2502.11150) found these formulas are weak
    predictors of real-time reading ease, so this should be surfaced
    as a rough sentence-economy proxy (in the spirit of Strunk &
    White's "omit needless words"), never as a validated "how easy
    this is to read" claim.

    Returns None for empty/near-empty input (fewer than 3 sentences),
    since the underlying formula is unstable on very short samples —
    same threshold textstat itself uses for its SMOG equivalent.
    """
    sentences = _extract_sentences(text)
    if len(sentences) < 3:
        return None

    words = [w.strip(".,;:!?\"'()[]") for w in text.split()]
    words = [w for w in words if w]
    total_words = len(words)
    if total_words == 0:
        return None

    total_syllables = sum(_count_syllables(w) for w in words)
    avg_sentence_length = total_words / len(sentences)
    avg_syllables_per_word = total_syllables / total_words

    grade_level = (
        0.39 * avg_sentence_length + 11.8 * avg_syllables_per_word - 15.59
    )

    return {
        "grade_level": round(grade_level, 1),
        "avg_sentence_length": round(avg_sentence_length, 1),
        "avg_syllables_per_word": round(avg_syllables_per_word, 2),
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

    words = text.split()
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
            nudge = "Paste something with real names and specific context. An email to a colleague about an actual project."
        elif ownership < 10:
            nudge = "Paste something written in your own voice, where you say what you think, not what sounds professional."
        elif spontaneity < 10:
            nudge = "Paste something you wrote quickly without re-reading. A message or email dashed off on your phone."
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
            lines.append("  Use these naturally where they fit — they are part of their voice.")

    if patterns.get('avoided_ai_connectors'):
        avoided = ', '.join(f"'{w}'" for w in patterns['avoided_ai_connectors'][:4])
        lines.append(f"  Words they never use (do not introduce): {avoided}")

    if patterns.get('subject_drop_ratio', 0) > 0.05:
        lines.append("  They omit the subject and lead with the verb.")
        examples = patterns.get('subject_drop_examples', [])
        if examples:
            lines.append(f'    e.g. "{examples[0]}"')

    if patterns.get('transition_phrases'):
        tp = patterns['transition_phrases'][0]
        lines.append(f'  They introduce new points with topic phrases: e.g. "{tp}"')

    if patterns.get('colon_rate', 0) > 0.1:
        lines.append("  They use colons to introduce context — match this pattern.")

    # Only include email closers when rendering email-genre content
    if is_email and patterns.get('soft_ender_count', 0) > 0:
        lines.append("  In emails they close with soft acknowledgements ('Hopefully this clarifies', 'Pls let me know').")
    elif not is_email:
        lines.append("  NOTE: this person's email closers ('Hopefully', 'Pls let me know') are email-specific. Do NOT use them to end articles or pieces.")

    return "\n".join(lines)
_ANCHOR_SENTENCE_CAP = 5


def _pick_anchor_sentences(sentences: list[str], corpus_text: str = "") -> list[str]:
    """
    Selects the sentences most representative of this writer's actual
    function-word habits, scored via the same MFW machinery
    compute_burrows_delta already uses to grade a render after the
    fact (per that function's own docstring: function-word frequency
    is the field's most-cited, most robust style signal). This reuses
    it to SELECT anchors going INTO the prompt, closing a gap where
    two disconnected signals existed for the same underlying question
    -- one used to check whether a render sounds like the person
    afterward, a different, weaker one used to decide what to show
    the model beforehand.

    Replaces an earlier hand-rolled heuristic (rewarded short
    declarative sentences, denial phrasing, and imperative verbs;
    penalised hedges and adjectives unconditionally). That heuristic
    hardcoded an assumption about what "sounds like someone" rather
    than measuring it against their own writing -- a writer whose
    actual baseline hedges frequently would have every one of their
    most characteristic sentences penalised by it, precisely
    backwards. The MFW approach is corpus-driven instead: a sentence
    using this person's own most-frequent words scores higher,
    whatever those words happen to be, hedges included if that's
    genuinely their pattern.

    corpus_text: the fuller corpus to build the MFW profile from.
    Callers already have this available (raw_text as passed to
    _build_voice_dna is the blended Screen 1 + starters corpus, not
    just the initial paste) -- falls back to scoring against the
    sentence pool itself if not supplied, so this stays backward
    compatible with any caller not yet passing it.
    """
    if not sentences:
        return []

    profile_source = corpus_text if corpus_text.strip() else " ".join(sentences)
    profile = compute_mfw_profile(profile_source)
    if not profile:
        return sentences[:3]

    scored = []
    for s in sentences:
        words = re.findall(r"[a-zA-Z']+", s.lower())
        if not words:
            continue
        # Average per-word typicality, not a raw sum -- otherwise
        # longer sentences would win just by containing more matches
        # rather than by being made of genuinely characteristic words.
        typicality = sum(profile.get(w, 0.0) for w in words) / len(words)
        scored.append((typicality, s))

    if not scored:
        return sentences[:3]

    scored.sort(key=lambda x: x[0], reverse=True)

    # Length variety preserved from the original design: don't return
    # several sentences that all happen to be the same length just
    # because they scored highest.
    selected = []
    lengths_used = set()
    for score, s in scored:
        bucket = len(s.split()) // 5
        if bucket not in lengths_used or len(selected) == 0:
            selected.append(s)
            lengths_used.add(bucket)
        if len(selected) >= _ANCHOR_SENTENCE_CAP:
            break

    if len(selected) < 2:
        selected = [s for _, s in scored[:_ANCHOR_SENTENCE_CAP]]

    return selected[:_ANCHOR_SENTENCE_CAP]
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
        lines.append("  Use these where they fit. Do not substitute elevated synonyms.")

    if vocab.get('avoided_ai_words'):
        # Show specific substitutions
        lines.append("  SUBSTITUTIONS — words they never write and what to use instead:")
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

    lines.append("  KEY PRINCIPLE: write about the thing, not the description of the thing. "
                "Not 'a narrative that X was Y' — just 'X was Y'.")

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
    # Lookbehind covers [.!? ] OR start-of-string. Confirmed live: a
    # salutation name is structurally the very first word of the text,
    # which has no preceding [.!? ] character for the old lookbehind
    # to match against - so the single highest-consequence proper
    # noun in an email (who it's addressed to) was silently exempt
    # from entity-preservation checking. "Scott" -> "Josh" in a real
    # render's opening word scored 100% entity_preservation, because
    # neither name was ever extracted as an entity on either side.
    proper = {w for w in re.findall(r'(?:(?<=[.!? ])|^)[A-Z][a-zA-Z]{2,}', text) if w not in non_proper}
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


# Known-bad synonym substitutions the LEXICAL FIDELITY prompt
# instruction (32b93ca, prompts.py) is supposed to prevent but cannot
# guarantee on its own, since a prompt instruction has no code-level
# backstop. Each entry is (original, risky_replacement, note). Add new
# pairs here only after a confirmed live incident, same discipline
# scoring_rules.py's changelog holds for threshold changes - this is
# not meant to grow speculatively into a general synonym blocklist.
#
# First entry (19 Aug 2026): a live render swapped "surfaces" ->
# "brings up" in "It surfaces when someone finally asks...". "Brings
# up" is transitive and needs an object ("brings the issue up") - used
# intransitively like that it's ungrammatical. "Surfaces" was correct
# and needed no change. Confirmed the LEXICAL FIDELITY instruction
# (still present, prompts.py line ~561) did not stop this - it's a
# prompt-level ask, not an enforced rule, so this closes the gap one
# level down with a deterministic check.
LEXICAL_FIDELITY_WATCHLIST: list[tuple[str, str, str]] = [
    (
        "surfaces", "brings up",
        "'brings up' is transitive and needs an object; used "
        "intransitively here it breaks grammar. 'surfaces' was correct.",
    ),
]


def detect_lexical_fidelity_breaks(input_text: str, output_text: str) -> list[str]:
    """
    Flags known-risky synonym substitutions from the small curated
    LEXICAL_FIDELITY_WATCHLIST above - swaps the LEXICAL FIDELITY
    prompt instruction (32b93ca) is meant to prevent but can't enforce
    on its own, since the model has no code-level backstop for word
    choice the way it does for entity preservation or hedging.

    Deterministic, rule-based, per the standing architecture
    constraint - no model call, same input always produces the same
    flags. Narrow by design: this is not a general synonym detector
    (that would false-positive constantly on ordinary rephrasing the
    LEXICAL FIDELITY instruction is meant to allow when it genuinely
    improves the target). It only fires when BOTH sides of a
    known-bad pair are exactly what happened: the original word is
    gone from the output and the specific risky replacement is
    present.

    Informational only, deliberately NOT wired into
    has_content_integrity_hard_fail or compute_risk (19 Aug 2026,
    JA: "flag it for review rather than block" - unlike an attribution
    swap or dropped entity, a watchlist hit is a known-risky pattern,
    not a confirmed content-integrity failure every time, so it
    shouldn't gate delivery the way those do).
    """
    flags = []
    input_lower = input_text.lower()
    output_lower = output_text.lower()
    for original, replacement, note in LEXICAL_FIDELITY_WATCHLIST:
        original_in_input = re.search(r"\b" + re.escape(original) + r"\b", input_lower)
        original_in_output = re.search(r"\b" + re.escape(original) + r"\b", output_lower)
        replacement_in_output = re.search(r"\b" + re.escape(replacement) + r"\b", output_lower)
        if original_in_input and not original_in_output and replacement_in_output:
            flags.append(f"'{original}' became '{replacement}' - {note}")
    return flags


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
            flags.append(f"'your {noun}' became 'my {noun}', credit moved from them to you")
        elif "my" in input_pronouns and "your" in output_pronouns and "my" not in output_pronouns:
            flags.append(f"'my {noun}' became 'your {noun}', credit moved from you to them")
    return flags


def highlight_attribution_swaps(output_text: str, attribution_swaps: list[str]) -> str:
    """HTML-escaped output text with each attribution-swap phrase
    wrapped in a highlighted, tooltipped span - genuine inline
    highlighting, safe because the swapped phrase actually exists at
    a real position in the output (unlike a dropped entity, which by
    definition isn't in the output anywhere to point at). Each swap
    string is detect_attribution_swaps' own format ("'your X' became
    'my X', ..."); the target phrase after "became" is what gets
    highlighted. Read-only - no restore/splice action, deliberately
    left for a future session with room to test that safely."""
    import html
    escaped = html.escape(output_text)
    for swap in attribution_swaps:
        m = re.search(r"became '([^']+)'", swap)
        if not m:
            continue
        phrase = html.escape(m.group(1))
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b", re.I)
        tooltip = html.escape(swap)
        escaped = pattern.sub(
            lambda mo, t=tooltip: (
                f'<span style="background:#FBE4E2;border-bottom:2px solid #B3382C;'
                f'padding:1px 2px;border-radius:3px;" title="{t}">{mo.group(0)}</span>'
            ),
            escaped, count=1,
        )
    return escaped


def find_source_sentence(input_text: str, entity: str) -> str | None:
    """Returns the first sentence in input_text containing entity
    (case-insensitive, whole word), or None if not found.

    Built for dropped_entities warnings: naming a bare dropped word
    ("Missing from the rewrite: Curious") is accurate but thin - the
    actual sentence gives the person something to check against, not
    just a single word to puzzle over. Deterministic and read-only:
    does not touch the output text or attempt any splice/restore, on
    purpose - that's a materially riskier feature (sentence-boundary
    matching to safely re-insert text) left for a future session with
    room to test it properly.
    """
    pattern = re.compile(r"\b" + re.escape(entity) + r"\b", re.I)
    for sentence in _extract_sentences(input_text):
        if pattern.search(sentence):
            return sentence.strip()
    return None


def splice_dropped_sentence(output_text: str, source_sentence: str) -> str:
    """Appends a dropped original sentence to the end of output_text,
    clearly marked, so the person can see and reposition it themselves.

    Deliberately the safe subset of the splice/restore feature
    find_source_sentence's docstring flags as deferred: the risky half
    is picking WHERE inside the rewrite the sentence belongs, which
    needs judgement about paragraph structure and flow that a regex
    can't safely make and that isn't being attempted here. This
    function makes no positional decision at all - it appends, once,
    at the end, with a visible marker, and leaves placement entirely
    to the person. Deterministic, no API call, no model in the loop.

    Guards:
    - Empty/whitespace-only source_sentence returns output_text
      unchanged (nothing to add).
    - If source_sentence (case-insensitive, whitespace-normalised)
      already appears in output_text, returns output_text unchanged -
      never appends a duplicate, since a second live-model call earlier
      in the render pipeline could have already restored it before
      this button is ever clicked.
    - Never trims or rewords source_sentence - it is inserted exactly
      as extracted by find_source_sentence, since altering wording
      here would be a second uncontrolled edit stacked on top of the
      one this feature exists to catch.
    """
    if not source_sentence or not source_sentence.strip():
        return output_text

    sentence = source_sentence.strip()
    normalised_output = re.sub(r"\s+", " ", output_text).lower()
    normalised_sentence = re.sub(r"\s+", " ", sentence).lower()
    if normalised_sentence in normalised_output:
        return output_text

    separator = "\n\n" if output_text and not output_text.endswith("\n\n") else ""
    marker = "[Restored - not repositioned, check placement]"
    return f"{output_text}{separator}{marker} {sentence}"


def score_restructure_fidelity(pre_text: str, post_text: str) -> dict:
    """
    Verifies a linkedin_format correction call only rearranged and cut
    words — never added any. Built specifically because that
    instruction (see build_correction_prompt's PLATFORM FORMAT block
    in prompts.py) is currently enforced by wording alone, with
    nothing checking whether the model actually obeyed it. Confirmed
    live (18 Aug 2026): a real render restructured "A governance
    failure is loud. An agent does something..." into a "When X...
    When Y..." conditional construction, introducing "when" and
    "occurs" — words that don't appear anywhere in the pre-correction
    text — which is genuine sentence-level rewriting, not the
    rearrangement the instruction permits. Grammarly flagging the
    output was a symptom of this, not a separate issue.

    Comparison is word-BAG based (case-insensitive, punctuation
    stripped), not sequence-based — deliberately, since the whole
    point of linkedin_format is that word ORDER is allowed to change
    freely; only word ADDITION is forbidden. "the" appearing one more
    time in post_text than it did in pre_text is a violation even
    though "the" trivially exists in both texts — a naive set-based
    check (does this word appear ANYWHERE in pre_text) would miss
    that. Word REMOVAL is expected and fine — economy-mode cutting
    happens in the same correction call — so this only flags words
    whose count in post_text exceeds their count in pre_text, never
    flags a drop.

    Compares against pre_text (the text handed INTO the correction
    call), not the person's original raw input — the correction call's
    own instruction says "already present in the input", meaning its
    own input, and elevate mode's earlier line-edits (economy, old-to-
    new reordering) may have already legitimately changed word choice
    once before this call ever ran. Checking against the wrong "input"
    would flag those earlier, already-approved changes as violations.
    """
    def _word_bag(text: str) -> dict:
        bag: dict = {}
        for w in re.findall(r"[a-z']+", text.lower()):
            bag[w] = bag.get(w, 0) + 1
        return bag

    pre_bag = _word_bag(pre_text)
    post_bag = _word_bag(post_text)

    fabricated = {}
    for word, count in post_bag.items():
        excess = count - pre_bag.get(word, 0)
        if excess > 0:
            fabricated[word] = excess

    return {
        "clean": len(fabricated) == 0,
        "fabricated_words": fabricated,
    }


_OPENING_SALUTATION_PATTERN = re.compile(
    r"^\s*(?:Hi|Hello|Hey|Dear)?\s*,?\s*([A-Z][a-zA-Z]{1,})\s*[,.]",
)


def _extract_opening_salutation_name(text: str) -> str | None:
    """
    Returns the addressee's name if the text opens with a salutation
    ("Hi John,", "Josh,", "Dear Sarah.", "Hello, Josh,") — else None.
    Deterministic, regex-based, matches _entities_and_numbers' own
    definition of a proper noun (capitalised, 2+ letters after the
    first) so a name this function extracts is always also a member
    of the entity set that function produces.

    Built specifically to let score_semantic_drift exempt this one
    name from dropped_entities when platform_format == "social" — see
    that function's docstring for the incident this fixes. Narrow and
    conservative by design: only matches at the very start of the
    text, only a single capitalised word, and only immediately before
    a comma or period (not after other punctuation) — a false match
    here would wrongly exempt a genuine dropped name from every check
    downstream, so this errs toward returning None over guessing.
    """
    if not text:
        return None
    m = _OPENING_SALUTATION_PATTERN.match(text.strip())
    return m.group(1) if m else None


def score_semantic_drift(input_text: str, output_text: str, platform_format: str | None = None) -> dict:
    """
    Deterministic proxy for whether the rewrite preserved what was
    actually said, not just how it was said. Compares the render INPUT
    (the text being rewritten) against the render OUTPUT — a different
    axis from voice preservation, which compares output style against
    the fingerprint baseline.

    Returns a 0-100 semantic match score plus the entities that were
    dropped, so a correction pass can target them specifically.

    platform_format: when "social" (LinkedIn/X/Threads), the opening
    salutation name (see _extract_opening_salutation_name) is excluded
    from BOTH the dropped_entities list AND the entity_score
    denominator — not just hidden from the report. A social post is
    public, one-to-many, and should never carry a private recipient's
    name at all, so its correct, intentional removal must not count
    as a drop. Confirmed live (19 Aug 2026): an "Elevate" render for
    LinkedIn kept a private email's addressee name visible in the
    public post text — traced to three separate places in the prompt
    actively forcing that name to survive (base_rules rule 10, the
    social-format instruction's own "reposition, don't delete"
    wording, and the correction pass's dropped-entity restoration
    override). All three were fixed at the generation side (prompts.py)
    to omit the salutation for social posts; this is the matching fix
    on the measurement side, so the entity-preservation hard-fail in
    compute_risk doesn't then flag the now-correct omission as a
    content-integrity failure and re-introduce the exact gating
    friction JA asked to remove earlier the same day. Every other
    entity (a fact, a different name used substantively in the body, a
    number) is completely unaffected — this only ever excludes the
    single word matched as the opening salutation, and only for
    platform_format == "social".
    """
    input_entities = _entities_and_numbers(input_text)
    output_entities = _entities_and_numbers(output_text)

    if platform_format == "social":
        salutation_name = _extract_opening_salutation_name(input_text)
        if salutation_name and salutation_name in input_entities:
            input_entities = input_entities - {salutation_name}

    if input_entities:
        # Check literal presence in the raw output text, not just
        # membership in output_entities. _entities_and_numbers only
        # ever extracts CAPITALISED words (see its regex), so a word
        # legitimately recapitalised to lowercase in the output -
        # moved from sentence-initial ("Curious if...") to mid-
        # sentence ("...I am curious...") - is never added to
        # output_entities in any case; comparing the two extracted
        # sets, even case-insensitively, can't see a word that was
        # never extracted from the output side to begin with. This
        # checks whether the actual word survives anywhere in the
        # output text at all, case-insensitive, whole-word match -
        # the real question this check exists to answer. Genuinely
        # different words (the Scott -> Josh regression this exists
        # to catch) are unaffected: "scott" plainly doesn't appear
        # anywhere in an output that says "Josh, ...".
        output_lower = output_text.lower()
        preserved = {
            e for e in input_entities
            if re.search(r'\b' + re.escape(e.lower()) + r'\b', output_lower)
        }
        entity_score = len(preserved) / len(input_entities)
        dropped = sorted(input_entities - preserved)
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

    semantic_match = round(100 * (
        SEMANTIC_MATCH_ENTITY_WEIGHT * entity_score + SEMANTIC_MATCH_CONTENT_WEIGHT * content_score
    ))

    attribution_swaps = detect_attribution_swaps(input_text, output_text)
    lexical_fidelity_breaks = detect_lexical_fidelity_breaks(input_text, output_text)

    return {
        "semantic_match": semantic_match,
        "entity_preservation": round(entity_score * 100),
        "content_overlap": round(content_score * 100),
        "dropped_entities": dropped[:5],
        "attribution_swaps": attribution_swaps,
        "lexical_fidelity_breaks": lexical_fidelity_breaks,
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
            "Paste one more. If your writing holds steady there, this "
            "will strengthen things. If it lands differently again, that's "
            "telling you something real too: your voice may just shift more "
            "than most between these kinds of writing."
        )
    return None


def has_content_integrity_hard_fail(
    semantic: dict | None, ai_tells: dict | None = None,
    insertion_check: dict | None = None,
) -> bool:
    """
    True only for the genuine content-integrity failures: a surviving
    AI tell, an attribution swap, a dropped entity, or an invented
    sentence out of the correction pass. These are the "wrong name in
    the email" class of error — factually or attributionally wrong,
    not just stylistically off.

    Extracted out of compute_risk (19 Aug 2026) so this exact same
    check can gate the review-confirmation wall in review_gate.py
    without also gating on style drift. Root cause this fixes: Risk
    used to conflate two different things under one badge — genuine
    integrity failures AND missing a single style-voice dimension
    (RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST = 1, out of 4 tracked
    dimensions) — and review_gate.py gated the rewritten text behind a
    checkbox for BOTH cases equally. Since real renders miss at least
    one of four style targets constantly, the gate was firing on
    nearly every render, not just the genuinely risky ones. JA (19 Aug
    2026): "user friction is front and centre of VOICOVA, nothing
    should contribute to friction" — style drift alone must never
    block the rewritten text from showing immediately; only a real
    integrity failure should.

    compute_risk's own Low/Medium/High badge is UNCHANGED by this —
    it still folds in style-drift severity for the informational
    badge shown to the user. This function is deliberately narrower:
    it answers one question only ("does this need to be gated"), not
    "how risky does this look overall."
    """
    if ai_tells and not ai_tells.get("clean", True):
        return True
    if (semantic or {}).get("attribution_swaps"):
        return True
    if (semantic or {}).get("dropped_entities"):
        return True
    if (insertion_check or {}).get("sentence_growth", 0) > 0:
        return True
    return False


def compute_risk(
    delta: dict | None, semantic: dict | None, ai_tells: dict | None = None,
    insertion_check: dict | None = None,
) -> str:
    """
    How much this specific rewrite moved from the person's normal style
    and content — distinct from Confidence. Confidence is about the
    measurement; Risk is about this particular result.

    Informational badge only as of 19 Aug 2026 — see
    has_content_integrity_hard_fail's docstring for why gating was
    split out of this function's return value. This function's own
    Low/Medium/High logic is otherwise unchanged: it still folds in
    both hard fails and missed-dimension/semantic-match severity, so
    the badge itself still tells the full story. What changed is which
    of these two things review_gate.py listens to.

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

    A dropped entity is the same category again, for a sharper reason:
    _entities_and_numbers' own docstring calls these "the facts a
    rewrite must not lose", but until now nothing actually enforced
    that - a dropped proper noun only diluted the aggregate
    semantic_match number, the same way one invented sentence used to
    hide inside an otherwise-passing score_render_delta band. Confirmed
    live: a real render's recipient name flipped ('Scott' -> 'Josh')
    and still scored 96% semantic match, because one wrong name among
    a paragraph of otherwise-preserved content barely moves an
    aggregate number, even though sending an email to the wrong name
    is a far higher-consequence error than any voice-drift score in
    this function. Treated as High regardless of every other score,
    same as an AI tell or an attribution swap.

    A grown sentence count out of the LLM correction call is the same
    category again: score_render_delta and semantic_match are both
    aggregate checks that can absorb one invented sentence without
    breaching their own thresholds (see _check_uncorrected_insertions's
    docstring), so a fabricated sentence can sit underneath a clean
    headline number the same way an attribution swap does. There's no
    mechanical fix for which sentence is the invented one, so this
    can't be silently corrected the way new hedges are — it has to
    surface as risk instead, same as the other two hard failures here.
    """
    if has_content_integrity_hard_fail(semantic, ai_tells, insertion_check):
        return "High"

    missed = sum(1 for d in (delta or {}).values() if d.get("verdict") == "MISSED")
    semantic_match = (semantic or {}).get("semantic_match", 100)

    if semantic_match < RISK_HIGH_SEMANTIC_MATCH_BELOW or missed >= RISK_HIGH_MISSED_DIMENSIONS_AT_LEAST:
        return "High"
    if semantic_match < RISK_MEDIUM_SEMANTIC_MATCH_BELOW or missed >= RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST:
        return "Medium"
    return "Low"


def compute_risk_reason(
    delta: dict | None, semantic: dict | None, ai_tells: dict | None = None,
    insertion_check: dict | None = None,
) -> str:
    """
    Companion to compute_risk: identifies WHICH check actually drove
    the verdict, without changing compute_risk's own return type or
    call sites (still a plain "High"/"Medium"/"Low" string everywhere
    it's already used).

    Exists to close a specific gap in scoring_rules.py v1.0.0: that
    module's own changelog is explicit that its thresholds (the
    semantic_match < 70/85 bands in particular) were extracted
    unchanged, not recalibrated, because no live data yet isolates
    whether those specific numbers are well-tuned - every real render
    checked in the 16 Aug 2026 session hit a hard-fail (an AI tell,
    a dropped entity, sentence growth) before the aggregate bands
    ever got a chance to be the deciding factor. Recalibrating a
    number nobody has seen fire on its own would be tuning blind.

    This is the fix for that: log the reason alongside every render
    (see render_complete in app.py), and after enough real renders
    accumulate, it becomes possible to ask a grounded question -
    "of the renders that hit High risk, how many were hard-fails vs
    aggregate_band, and at what semantic_match did aggregate_band
    actually fire" - instead of adjusting 70/85 on a hunch. Same
    principle the scoring_rules.py docstring already commits to:
    monitor first, recalibrate only once there's a reason to.

    Returns one of: "ai_tell", "attribution_swap", "dropped_entity",
    "sentence_growth", "aggregate_band", "clean" (Low with nothing
    remotely close to a fail).
    """
    if ai_tells and not ai_tells.get("clean", True):
        return "ai_tell"

    if (semantic or {}).get("attribution_swaps"):
        return "attribution_swap"

    if (semantic or {}).get("dropped_entities"):
        return "dropped_entity"

    if (insertion_check or {}).get("sentence_growth", 0) > 0:
        return "sentence_growth"

    missed = sum(1 for d in (delta or {}).values() if d.get("verdict") == "MISSED")
    semantic_match = (semantic or {}).get("semantic_match", 100)

    if semantic_match < RISK_MEDIUM_SEMANTIC_MATCH_BELOW or missed >= RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST:
        return "aggregate_band"

    return "clean"


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
        abs_diff = abs(diff)
        pct_diff = abs_diff / max(b_val, 0.01)

        # Absolute floor first (see scoring_rules.DELTA_BAND_MIN_ABS_DIFF's
        # own comment): a baseline near zero makes pct_diff blow up for
        # a trivially small absolute move, so a move smaller than a
        # person could plausibly notice is a HIT regardless of what the
        # percentage says. Only once the absolute move clears that bar
        # do the existing percentage bands get to decide CLOSE vs MISSED.
        min_abs = DELTA_BAND_MIN_ABS_DIFF.get(key, 0.0)
        if abs_diff < min_abs:
            verdict = "HIT"
        elif pct_diff <= DELTA_BAND_HIT_MAX_PCT:
            verdict = "HIT"
        elif pct_diff <= DELTA_BAND_CLOSE_MAX_PCT:
            verdict = "CLOSE"
        else:
            verdict = "MISSED"

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
    close = [_DIMENSION_LABELS.get(k, k) for k, d in delta.items() if d["verdict"] == "CLOSE"]
    missed = [_DIMENSION_LABELS.get(k, k) for k, d in delta.items() if d["verdict"] == "MISSED"]
    # SKIPPED (18 Aug 2026): originally one reason only — the input
    # genuinely had nothing of that kind to convert (no first-person
    # content, no directive content), and the correction pass correctly
    # declined to fabricate it rather than failing to hit an achievable
    # target. A second, genuinely different reason was added the same
    # session: the input has PLENTY of that content, more than the
    # baseline even, and the residual drift is because it can't be
    # reduced further without deleting the person's actual stated
    # content (see ownership_miss_is_content_driven in
    # deterministic_fixers.py). Conflating the two under one message
    # was a real, live bug — "nothing to convert in the original" is
    # actively wrong for the second case, where there's abundant
    # content, that's the whole point. Split by skip_reason (defaults
    # to "no_content" if unset, so any caller not yet setting it keeps
    # the original message rather than silently changing behaviour).
    skipped_no_content = [
        _DIMENSION_LABELS.get(k, k) for k, d in delta.items()
        if d["verdict"] == "SKIPPED" and d.get("skip_reason", "no_content") != "content_ceiling"
    ]
    skipped_content_ceiling = [
        _DIMENSION_LABELS.get(k, k) for k, d in delta.items()
        if d["verdict"] == "SKIPPED" and d.get("skip_reason") == "content_ceiling"
    ]

    # "Drifted" was the original wording here — dropped because it's one
    # of the exact words voice_engine's own _ANALYTICAL_TELL_PHRASES
    # flags as an AI tell (score_ai_tells("...Drifted on hedging...")
    # returns clean=False, flagged=['AI-typical phrasing found: Drifted']
    # - confirmed live, not hypothetical). This is app-copy shown to the
    # user, not LLM output, so it was never actually run through the
    # scorer - but having the product's own explanation of "why this
    # doesn't sound like you" itself use a word the product flags as
    # not sounding human undermines the message. "Off on" reads
    # naturally in the same slot and is clean against score_ai_tells.
    #
    # CLOSE dimensions (17 Aug 2026 fix): previously silently omitted
    # from this sentence entirely - a dimension could sit in neither
    # "hits" nor "missed" (verdict == CLOSE) and vanish from the prose,
    # while still appearing as a numeric entry in build_voice_report's
    # biggest_changes list (which includes anything that isn't HIT).
    # Spotted live: a report showed "sentence rhythm 31%" as a biggest
    # change with no corresponding mention in "Held on X. Off on Y." -
    # the sentence and the numbers next to it told two different
    # stories. Given its own clause here so the two stay consistent.
    parts = []
    if hits:
        parts.append("Held on " + ", ".join(hits) + ".")
    if close:
        parts.append("Close on " + ", ".join(close) + ".")
    if missed:
        parts.append("Off on " + ", ".join(missed) + ".")
    if skipped_no_content:
        parts.append("N/A on " + ", ".join(skipped_no_content) + " (nothing to convert in the original).")
    if skipped_content_ceiling:
        parts.append(
            "N/A on " + ", ".join(skipped_content_ceiling) + " (your own writing here was "
            "already more opinionated than your baseline; further correction would mean "
            "cutting real content, not just tightening style)."
        )
    evidence = " ".join(parts) if parts else "No baseline comparison available."

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


def build_voice_report(delta: dict, semantic: dict, confidence: str, risk: str, ai_tells: dict | None = None, burrows_delta: dict | None = None, content_integrity_hard_fail: bool = False) -> dict:
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
        # SKIPPED dimensions are excluded here for the "no_content"
        # reason (there's genuinely nothing to show — the input never
        # had this kind of content, a 0-vs-baseline number would be
        # noise, not signal). "content_ceiling" SKIPPED dimensions are
        # deliberately NOT excluded: there IS real, substantial drift,
        # it's just judged unavoidable rather than a defect. Excluding
        # it here produced a real, live bug — "Biggest changes: No
        # significant drift" shown to the person when there was in
        # fact ~37% drift, just silently hidden because the verdict
        # said SKIPPED. The person deserves to see the number; the
        # evidence sentence (voice_match_label) is what explains why
        # it's not being treated as an error, not this list vanishing
        # it entirely.
        if d["verdict"] == "HIT":
            continue
        if d["verdict"] == "SKIPPED" and d.get("skip_reason", "no_content") != "content_ceiling":
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
        # Raw, individual phrase list — added 18 Aug 2026 for the
        # AI-Slop Firewall UI, which needs each flagged phrase listed
        # separately (e.g. for a "clean it up" action), not the
        # pre-joined "AI-typical phrasing found: X, Y, Z" prose string
        # ai_tell_flags carries. Distinct field rather than parsing
        # ai_tell_flags on the UI side, which would be fragile against
        # any future change to that string's wording.
        "ai_tell_phrases": (ai_tells or {}).get("flagged_phrases", []),
        "biggest_changes": biggest_changes[:3],
        "dropped_entities": semantic.get("dropped_entities", []),
        "attribution_swaps": semantic.get("attribution_swaps", []),
        # 19 Aug 2026: informational only, does not gate delivery -
        # see detect_lexical_fidelity_breaks' docstring for why this
        # is treated differently from dropped_entities/attribution_swaps.
        "lexical_fidelity_breaks": semantic.get("lexical_fidelity_breaks", []),
        # 19 Aug 2026: the ONLY thing that should gate the rewritten
        # text behind review_gate.py's confirmation wall — see
        # has_content_integrity_hard_fail's docstring. "risk" above
        # still reflects style-drift severity too (informational
        # badge), but style drift alone must never block delivery.
        "content_integrity_hard_fail": content_integrity_hard_fail,
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
    r"unlock the potential|game.changer|unparalleled|paramount|"
    # Soft check-in hedge closer — confirmed live (this session): a
    # correction/render call fabricated "Curious whether that framing
    # lands for you" wholesale, invisible to every existing pattern
    # here because it isn't corporate-marketing vocabulary, it's a
    # rapport-softening tic from chat-assistant register. Same family
    # as "does that (make sense|land|resonate)" — an unearned check
    # for buy-in the person themselves didn't write.
    #
    # "whether" only, not "if|how": the original incident and every
    # existing test used "curious whether" specifically. "Curious if
    # you got a chance to run it" is an ordinary, extremely common
    # human email opener - confirmed live against a real render where
    # a person's own preserved original wording ("curious if") got
    # flagged as an AI tell despite matching their unedited input.
    # The tell is the fabricated check-in construction, not the word
    # "curious" paired with any conjunction.
    r"curious whether|does (this|that) (land|resonate)|"
    r"(lands|resonates?) (for|with) you)\b",
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
    r"\b(drift(s|ed|ing)?|"
    # Excludes "surface area" (already handled) and, new: a possessive
    # immediately before "surface" ("the agent's surface", "the
    # model's surface", "its surface") — that's the genuine noun usage
    # (a technical term, same shape as "attack surface"), not the
    # AI-essay habit of pressing the noun into service as a verb
    # ("issues surface", "concerns surfaced") this pattern exists to
    # catch. Confirmed as a real false positive against a live render
    # this session: "tied to change in the agent's surface" was
    # flagged even though "surface" there is unambiguously a noun, the
    # person's own genuine phrasing, not an AI tell.
    # Earlier fix (this session, commit 4cd10c7) only excluded possessive
    # forms before "surface" ("agent's surface", "its surface"). Gap found
    # against a real render just now: plain-determiner noun usage ("the
    # surface is at least more legible") was still flagged, same false-
    # positive class, just a different word in front. Each lookbehind must
    # stay individually fixed-width (Python re constraint), so determiners
    # are listed separately rather than as one alternation.
    # Third fix in this same false-positive family: "Agent Surface" as
    # a coined proper-noun compound term (capitalised, no determiner
    # or possessive before it at all) - same genuine-noun-usage class
    # as the determiner cases above, just a bare noun-adjunct
    # construction instead. Confirmed against a live render.
    r"(?<!'s )(?<!its )(?<!the )(?<!this )(?<!that )(?<!any )(?<!our )"
    r"(?<!her )(?<!his )(?<!each )(?<!a )(?<!an )(?<!no )(?<!Agent )"
    r"surfac(e|es|ed|ing)(?!\s+area)|land(s|ed|ing)? on|"
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


def score_ai_tells(text: str, original_input_text: str = "") -> dict:
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
    'mixed'.

    original_input_text: the person's own actual input for this
    render, optional but strongly recommended by every caller that
    has it available. Confirmed as a real, repeated false-positive
    class (18 Aug 2026): several phrases in these lists — "curious
    whether", "i suspect", "i would push back" — are also just
    ordinary things a person might genuinely write. Two earlier
    sessions "fixed" instances of this by narrowing individual regexes
    (excluding "curious if" but not "curious whether", excluding
    possessive/determiner forms of "surface") — that approach caps out
    the moment the SAME phrase is both a genuine tell in one render and
    someone's authentic voice in another, which is exactly what
    happened here: a real render's own original input said "Curious
    whether your clients have solved that" verbatim, and continued to
    get flagged as an AI tell even after the render correctly preserved
    it unedited. No regex narrowing fixes that — only checking against
    what the person actually wrote does. Any matched phrase that
    appears verbatim (case-insensitive) in original_input_text is
    excluded from flagging: same principle as the ownership fixer's
    own original-input safety check in deterministic_fixers.py, now
    applied here too. Em dash / spaced-hyphen checks are NOT exempted
    this way — those enforce VOICOVA's own house style regardless of
    the person's usual habits, a deliberate product rule, not an
    AI-detection heuristic, so there is no "genuine" exception for them.

    Return shape is unchanged from before this parameter existed
    (original_input_text defaults to "", which exempts nothing — every
    existing caller that doesn't pass it keeps its current behaviour
    exactly), so every existing caller (app.py, dev_tools/harness.py)
    keeps working without modification, though callers with input_text
    available should be updated to pass it.
    """
    original_lower = original_input_text.lower()

    def _matches_excluding_genuine(pattern: "re.Pattern") -> list:
        """Same shape as the old .findall() call this replaces
        (returns the captured group content, not the raw match, to
        keep the exact display text every existing test/caller
        expects) — but first drops any match whose full matched text
        already appears verbatim in the person's own original input.
        Uses finditer + group(0) for the exemption check specifically
        because these patterns are always a single outer group
        wrapped in \\b...\\b (verified against every pattern this
        function uses), so group(0) and the captured group content
        are the same text; group(0) is used for the check since it's
        the actual matched substring, not a display convenience.
        """
        kept = []
        for m in pattern.finditer(text):
            if m.group(0).lower() in original_lower:
                continue
            kept.append(m.group(1) if m.lastindex else m.group(0))
        return kept

    em_dash_hits = len(re.findall(r"[\u2012\u2013\u2014\u2015]", text))
    spaced_hyphen_hits = len(_SPACED_HYPHEN_DASH_PATTERN.findall(text))
    phrase_hits = _matches_excluding_genuine(_AI_TELL_PHRASES)
    shield_hits = _matches_excluding_genuine(_PLAUSIBILITY_SHIELD_PHRASES)

    register = _classify_register(text)
    analytical_hits = []
    if register in ("analytical", "mixed"):
        analytical_hits = _matches_excluding_genuine(_ANALYTICAL_TELL_PHRASES)
        analytical_hits += _matches_excluding_genuine(_FRAGMENT_EMPHASIS_PATTERN)

    all_hits = phrase_hits + analytical_hits + shield_hits

    flagged = []
    if em_dash_hits:
        flagged.append(f"{em_dash_hits} em dash(es) survived the sweep")
    if spaced_hyphen_hits:
        flagged.append(
            f"{spaced_hyphen_hits} spaced hyphen(s) used as a dash substitute "
            f"(the sweep converts em dashes to ' - ' — that's still the tell)"
        )
    # unique_phrases computed once here (not re-derived from the
    # `flagged` display string below), so flagged_phrases is a genuine
    # structured list, not a parse of prose meant for a sentence. The
    # `flagged` entry below truncates to 5 for a compact summary line —
    # flagged_phrases deliberately does NOT truncate, since a UI
    # listing each phrase individually (e.g. for one-click removal)
    # needs the actual complete set, not a display-string's abbreviation.
    unique_phrases = sorted(set(p if isinstance(p, str) else p[0] for p in all_hits)) if all_hits else []
    if unique_phrases:
        flagged.append(f"AI-typical phrasing found: {', '.join(unique_phrases[:5])}")

    clean = em_dash_hits == 0 and spaced_hyphen_hits == 0 and len(all_hits) == 0

    return {
        "clean": clean,
        "em_dash_count": em_dash_hits,
        "spaced_hyphen_count": spaced_hyphen_hits,
        "phrase_hit_count": len(all_hits),
        "flagged": flagged,
        "flagged_phrases": unique_phrases,
        "register": register,
    }
