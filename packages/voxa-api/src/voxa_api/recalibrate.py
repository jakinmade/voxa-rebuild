"""
Voxa — Full-Draft Recalibration (ported from the Streamlit build, 25 July 2026)

JA was right: this pipeline in app.py was more complete and more carefully
tuned than a from-scratch rebuild would be. Ported close to verbatim rather
than reinvented - the prompt rules and regex cleanup below were tuned
against real observed output, not written blind.

Two baselines now exist side by side, deliberately:
  - The five-dimension boolean baseline (fitness-gated, majority-vote)
    powers the CHECKER (/check-profile) - unchanged by this file.
  - The four-metric numeric baseline (hedge density, sentence rhythm,
    ownership ratio, directive ratio) below powers RECALIBRATION -
    richer, continuous data is what a rewrite needs; a checker only
    needs true/false per dimension.

Not yet ported from app.py: vocabulary fingerprint extraction, function
pattern extraction, thought density scoring, intent-mode selection, and
the grammar-fix Claude pass. Flagged honestly rather than silently
simplified - the core restoration-target + system-prompt + deterministic
cleanup pipeline is what's live now; the richer voice DNA can follow.
"""

from __future__ import annotations

import os
import re

import anthropic

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2000


def compute_baseline_metrics(text: str) -> dict:
    """Ported verbatim from app.py compute_baseline_metrics.

    NOTE — root voice_engine.py has its own compute_baseline_metrics
    with the same name and return shape, used by the five-dimension
    checker. Deliberately not the same function (see this module's
    top-level docstring for why the two paths are kept separate).
    They will NOT return identical numbers on the same input: this
    version's hedge regex is single-word-only, missing the clause-level
    hedges (Hyland taxonomy — "curious whether", "it seems", "kind of")
    that voice_engine.py's _HEDGE_PATTERN catches; this version's
    sentence split is a raw re.split with no abbreviation guard, so
    "Dr. Smith called." splits after "Dr." here but not in
    voice_engine.py. See tests/unit/test_baseline_metrics_divergence.py
    for a pinned example. If you're touching either implementation,
    check whether this note (and that test) still describes reality.
    """
    words = text.split()
    total_words = max(len(words), 1)
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip() and len(s.split()) >= 2]
    total_sents = max(len(sentences), 1)

    hedge = re.compile(r"\b(might|could|perhaps|possibly|maybe|somewhat|quite|rather|potentially)\b", re.I)
    hedge_count = len(hedge.findall(text))
    hedge_density = round((hedge_count / total_words) * 100, 2)

    lengths = [len(s.split()) for s in sentences]
    avg_len = sum(lengths) / total_sents
    variance = sum((l - avg_len) ** 2 for l in lengths) / total_sents
    sentence_length_sd = round(variance ** 0.5, 2)

    first_person = re.compile(r"\b(I |I'|I'm|I've|I'd|I'll|my |mine\b|myself\b)", re.I)
    fp_sents = sum(1 for s in sentences if first_person.search(s))
    first_person_ratio = round(fp_sents / total_sents, 3)

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


def merge_baseline(existing: dict | None, new_metrics: dict) -> dict:
    """Ported verbatim from app.py _merge_baseline. Weighted by word count."""
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


def _build_restoration_targets(baseline: dict) -> str:
    """Ported verbatim from app.py _build_restoration_targets."""
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
    if directive >= 0.06:
        lines.append(f"  Directness: {directive:.0%} of sentences are action statements — match this proportion")
    else:
        lines.append("  Directness: low imperative rate in baseline — do not force directives")
    lines.append(f"  {confidence_note}")
    lines.append("  Treat these as specifications you are being measured against, not style suggestions.")

    return "\n".join(lines)


def _build_system_prompt(restoration_targets: str, word_count_input: int) -> str:
    """
    Simplified from app.py _build_system_prompt: keeps the battle-tested
    ABSOLUTE RULES and register instructions, drops the AI-contamination
    branch (needs the AI-signal scorer, not yet ported) in favour of
    always treating input as a draft that needs realigning to baseline.
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

    return (
        "You are a voice rendering engine. Your job is to rewrite this text so it sounds "
        "like the person described in the restoration targets below.\n\n"
        f"{restoration_targets}\n\n"
        "RENDERING INSTRUCTIONS:\n"
        "- Match the sentence rhythm from the targets exactly.\n"
        "- Match the directness and hedge rate. Do not add caution that is not in the targets.\n"
        "- Do not add warmth, polish, or formality not already implied by the targets.\n"
        "- Do not smooth rough edges. Keep the content and ideas the same. Change only how it is said.\n"
        "- CRITICAL: STOP WHEN THE CONTENT IS DONE. Do not add sentences after the final point. "
        "Do not summarise. Do not close. Do not reflect.\n\n"
        f"{base_rules}"
    )


def _detect_locale(text: str) -> str:
    """Ported verbatim from app.py _detect_locale."""
    uk_markers = [
        r"\bcolour\b", r"\bcolours\b", r"\bhonour\b", r"\bhonours\b",
        r"\bbehaviour\b", r"\bbehaviours\b", r"\borganis", r"\brecognis", r"\bprioritis",
        r"\banalyse\b", r"\banalyses\b", r"\bcentre\b", r"\bcentres\b",
        r"\bfavour\b", r"\bfavours\b", r"\bneighbour\b", r"\bneighbours\b",
        r"\bwhilst\b", r"\bfortnight\b", r"\bprogramme\b", r"\bcheque\b",
        r"\btravelled\b", r"\bcancelled\b",
    ]
    us_markers = [
        r"\bcolor\b", r"\bcolors\b", r"\bhonor\b", r"\bhonors\b",
        r"\bbehavior\b", r"\bbehaviors\b", r"\borganize\b", r"\brecognize\b", r"\bprioritize\b",
        r"\banalyze\b", r"\banalyzes\b", r"\bcenter\b", r"\bcenters\b",
        r"\bfavor\b", r"\bfavors\b", r"\bneighbor\b", r"\bneighbors\b",
        r"\btraveled\b", r"\bcanceled\b",
    ]
    uk_hits = sum(1 for m in uk_markers if re.search(m, text, re.I))
    us_hits = sum(1 for m in us_markers if re.search(m, text, re.I))
    return "us" if us_hits > uk_hits else "uk"


def _apply_uk_english(text: str) -> str:
    """Ported verbatim from app.py _apply_uk_english."""
    replacements = [
        (r"\bsurfaces\b", "brings up"),
        (r"\bleverage\b", "use"), (r"\bleverages\b", "uses"),
        (r"\breach out\b", "contact"), (r"\breaching out\b", "contacting"),
        (r"\butilize\b", "use"), (r"\butilizes\b", "uses"), (r"\butilization\b", "use"),
        (r"\bprioritize\b", "prioritise"), (r"\bprioritizes\b", "prioritises"), (r"\bprioritizing\b", "prioritising"),
        (r"\banalyze\b", "analyse"), (r"\banalyzes\b", "analyses"), (r"\banalyzing\b", "analysing"),
        (r"\borganize\b", "organise"), (r"\borganizes\b", "organises"), (r"\borganizing\b", "organising"),
        (r"\brecognize\b", "recognise"), (r"\brecognizes\b", "recognises"), (r"\brecognizing\b", "recognising"),
        (r"\bcolor\b", "colour"), (r"\bcolors\b", "colours"), (r"\bcenter\b", "centre"), (r"\bcenters\b", "centres"),
        (r"\bfavor\b", "favour"), (r"\bfavors\b", "favours"), (r"\bhonor\b", "honour"), (r"\bhonors\b", "honours"),
        (r"\bbehavior\b", "behaviour"), (r"\bbehaviors\b", "behaviours"),
        (r"\bneighbor\b", "neighbour"), (r"\bneighbors\b", "neighbours"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    text = re.sub(
        r"\blike\s+(cost of living|NHS|housing|inflation|unemployment|\w+(?:,\s*\w+)+)",
        lambda m: "such as " + m.group(1), text
    )
    text = re.sub(r"\b(issues|problems|areas|things|factors|topics)\s+like\b",
                  lambda m: m.group(1) + " such as", text)
    return text


def _regex_sweep(text: str) -> str:
    """
    Deterministic guardrail sweep. Delegates to voxa_core.text_guardrail,
    the canonical implementation for the packages/ ecosystem.

    DELIBERATE, DOCUMENTED DUPLICATION — read before editing either side:
    This used to be its own local port of app.py's _regex_sweep, "ported
    verbatim" at the time — 25 July 2026 — but never kept in sync after
    that. An August 2026 audit found it had drifted to a 14-entry Claude-
    construction list against the live app's 46, with no hedge stripping,
    no plausibility-shield removal, no literary-closer stripping, no
    tricolon collapse, no editorial-addition stripping, and no AI-tell
    verification at all. Recalibrated drafts were shipping with
    materially weaker cleanup than the Streamlit app gave the same input.

    Fixed by delegating to voxa_core.text_guardrail.sweep() — voxa-api
    already depends on voxa-core (see pyproject.toml), so this adds no
    new dependency edge. That module is itself a verbatim port of
    root-level prompts.py's _regex_sweep, NOT automatically kept in sync
    with it — see voxa_core.text_guardrail's docstring for the full
    duplication note and why root (the live Railway deployment) isn't
    part of this dependency graph.
    """
    from voxa_core.text_guardrail import sweep as _core_sweep
    return _core_sweep(text)


async def recalibrate_draft(draft_text: str, restoration_metrics: dict) -> dict:
    """
    Full-draft recalibration against the profile's numeric baseline.
    Returns {"rewritten": str, "status": str}. status is diagnosable,
    never silently swallowed - same discipline as the line-level rewrite.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"rewritten": None, "status": "no_api_key"}

    restoration_targets = _build_restoration_targets(restoration_metrics)
    word_count = len(draft_text.split())
    system_prompt = _build_system_prompt(restoration_targets, word_count)

    try:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": draft_text}],
        )
        rewritten = response.content[0].text.strip()
    except Exception as e:
        return {"rewritten": None, "status": f"api_error:{type(e).__name__}: {str(e)[:150]}"}

    rewritten = _regex_sweep(rewritten)
    if _detect_locale(draft_text) == "uk":
        rewritten = _apply_uk_english(rewritten)

    return {"rewritten": rewritten, "status": "ok"}
