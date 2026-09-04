#!/usr/bin/env python3
"""
harness.py — portable, headless test runner for Voxa's core pipeline.

Built so another model (or a script another model writes) can simulate
a user and exercise the real fingerprint -> rewrite -> Voice Report
pipeline without a browser, without Streamlit, without touching the
live app at all. It imports the exact same two modules the production
app uses (voice_engine.py, prompts.py) — unmodified, copied as-is —
so results here are results the real product would produce, not a
simplified stand-in.

USAGE

  Dry run (no API key needed — fingerprint + fitness only, free):
    python3 harness.py personas/direct_founder.json --dry-run

  Full run (needs ANTHROPIC_API_KEY set — does the actual rewrite):
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 harness.py personas/direct_founder.json

  Cheap smoke test — one persona, low token ceiling, no refinement call:
    python3 harness.py personas/direct_founder.json --max-tokens 600 --no-refinement

  Batch — run every persona in a folder, one JSON report out:
    python3 harness.py personas/ --batch --out results.json

PERSONA FORMAT — see personas/README.md or any file in personas/ for
a worked example. Five fields, four required:

  persona_name        str            — label only, for your own reference
  sample1_text        str            — Sample 1: pasted writing
  sample2_completions  list[str]      — Sample 2: sentence-starter completions
                                        (see app.py's _build_starters()).
                                        Indices [0] and [3] are required, each
                                        at least SAMPLE2_REQUIRED_MIN_WORDS
                                        words - matches the live app's Screen 3
                                        gate. Picked for register contrast
                                        (defensive-professional vs unfiltered-
                                        emotional), not just "the first one".
                                        Indices [1] and [2] are optional
                                        enrichment, same as the app's "Deepen
                                        your fingerprint" expander - fill with
                                        "" or omit entirely.
  render_input         str            — the AI-ish text to rewrite
  refinement            dict, optional — {"tags": [...], "freetext": "..."}
                                         simulates the one-refinement step

WHAT YOU GET BACK — a JSON report per persona with every number the
live app computes: fingerprint observations, baseline metrics, fitness
tier, and (full run only) Voice Match, Semantic Match, Confidence,
Risk, AI-tell check, and the actual rewritten text. Nothing here is
summarised or rounded differently from the real app — same functions,
same thresholds.
"""

import os
import sys
import json
import argparse

# Repo layout: this file lives in dev_tools/, voice_engine.py and
# prompts.py live at repo root. Resolve that explicitly rather than
# assuming same-directory imports, since this harness is meant to run
# against the real repo, not just its own folder.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import voice_engine as ve
import prompts as pr

# Mirrors app.py's SAMPLE2_REQUIRED_MIN_WORDS and REQUIRED_STARTER_INDICES.
# Indices 0 and 3 are required (picked for register contrast); 1 and 2
# are optional "deepen your fingerprint" enrichment, same as the live
# app's expander.
SAMPLE2_REQUIRED_MIN_WORDS = 10
REQUIRED_STARTER_INDICES = (0, 3)


def _detect_locale_simple(text: str) -> str:
    """Same heuristic app.py uses via prompts._detect_locale — imported directly."""
    return pr._detect_locale(text)


def run_fingerprint_stage(persona: dict) -> dict:
    """
    Mirrors screens 1-3 of the live app: Sample 1 paste, Sample 2 starters
    (indices 0 and 3 required, 1 and 2 optional). Pure computation, no API
    call, free to run.
    """
    sample1 = persona["sample1_text"]
    completions = persona.get("sample2_completions", [])

    if len(sample1.split()) < 10:
        return {"error": "sample1_text must be at least 10 words, same floor the live app enforces."}

    # Sample 1
    observations = ve.analyse_writing(sample1)
    baseline = ve.compute_baseline_metrics(sample1)
    fitness = ve._score_sample_fitness(sample1)
    locale = _detect_locale_simple(sample1)
    fingerprint_samples = [baseline]

    # Sample 2 — same gate app.py enforces: completions[0] and
    # completions[3] are both required, at SAMPLE2_REQUIRED_MIN_WORDS
    # each. Missing or empty is a persona-fixture error, not a soft
    # failure - the live app can't reach Screen 4 without both, so a
    # harness run that let it through silently would report results the
    # live app could never produce.
    required_word_counts = {}
    missing = []
    for idx in REQUIRED_STARTER_INDICES:
        text = completions[idx].strip() if idx < len(completions) else ""
        wc = len(text.split())
        required_word_counts[idx] = wc
        if not text or wc < SAMPLE2_REQUIRED_MIN_WORDS:
            missing.append(idx)

    if missing:
        return {
            "error": (
                f"sample2_completions{missing} must each be at least "
                f"{SAMPLE2_REQUIRED_MIN_WORDS} words - the live app blocks "
                f"Continue on Screen 3 otherwise. "
                f"Got {[required_word_counts[i] for i in missing]} word(s) respectively."
            )
        }

    combined_sample2 = " ".join(c.strip() for c in completions if c.strip())
    sample2_word_count = len(combined_sample2.split())

    intro_obs = ve._analyse_intro(combined_sample2)
    existing_headlines = {o["headline"] for o in observations}
    for obs in intro_obs:
        if obs["headline"] not in existing_headlines:
            observations.append(obs)
            existing_headlines.add(obs["headline"])
    observations.sort(key=lambda o: o.get("signal", 0.5), reverse=True)
    observations = observations[:5]

    # Score each required starter SEPARATELY for the stability check -
    # combining them first would destroy the register contrast the
    # two-starter requirement exists to capture. The combined text
    # (below) is still used for the starter-only render check and for
    # _analyse_intro's narrative reading, same as before.
    required_metrics = [
        ve.compute_baseline_metrics(completions[idx].strip())
        for idx in REQUIRED_STARTER_INDICES
    ]
    fingerprint_samples.extend(required_metrics)
    dimension_stability = ve.compute_dimension_stability(fingerprint_samples)

    required_starter_text = " ".join(completions[idx].strip() for idx in REQUIRED_STARTER_INDICES)
    starter_baseline = ve.compute_baseline_metrics(required_starter_text)
    for m in required_metrics:
        baseline = ve._merge_baseline(baseline, m)

    fingerprint_corpus = sample1 + " " + combined_sample2
    keep_contractions = ve.uses_contractions(fingerprint_corpus)

    return {
        "observations": observations,
        "baseline": baseline,
        # Unmerged starter-only baseline - kept separately so the render
        # stage can run a second, independent check against it, same as
        # the live app now does (see app.py's starter_baseline).
        "starter_baseline": starter_baseline,
        "dimension_stability": dimension_stability,
        "fingerprint_hash": ve.fingerprint_hash(baseline),
        "fitness": fitness,
        "locale": locale,
        "sample2_word_count": sample2_word_count,
        "sample2_required_word_count": required_word_counts,
        "sample2_met_floor": not missing,
        "keep_contractions": keep_contractions,
    }


def run_render_stage(
    persona: dict, fingerprint: dict, api_key: str,
    max_tokens: int = 4096, skip_refinement: bool = False,
) -> dict:
    """
    Mirrors screen 4 / _run_render in the live app: the actual rewrite,
    correction pass, and the full Voice Report pipeline. Needs a real
    API key — this is the one stage that costs tokens.

    max_tokens defaults to 4096 to match production exactly. Test
    render_input strings are one to three sentences, so a lower value
    (e.g. --max-tokens 600 from the CLI) is realistic headroom for
    harness runs without changing what app.py itself does.

    skip_refinement drops the optional 4th call (Sample 3 simulation)
    even when the persona has a refinement block set - lets a cheap
    smoke-test pass skip it, without editing the persona fixture.
    """
    import anthropic

    input_text = persona["render_input"]
    observations = fingerprint["observations"]
    baseline = fingerprint["baseline"]
    # Mirrors app.py's 4 Sept 2026 fix: don't let a stale/context-
    # mismatched calibration verdict override what the current input
    # itself shows. See app.py's keep_contractions comment for the
    # real confirmed case this fixes.
    keep_contractions = fingerprint["keep_contractions"] or ve.uses_contractions(input_text)
    keep_dashes = pr.uses_em_dashes(f"{persona.get('sample1_text', '')} {input_text}")
    locale = fingerprint["locale"]

    detected_mode = pr._detect_mode(input_text)
    ai_score = ve._score_ai_signal(input_text)
    voice_dna = pr._build_voice_dna(observations, persona["sample1_text"], baseline, ai_score, current_input_text=input_text)
    mode_instruction = pr.apply_intent_mode(input_text, detected_mode)
    word_count_input = len(input_text.split())

    system = pr._build_system_prompt(
        voice_dna=voice_dna, mode_instruction=mode_instruction,
        word_count_input=word_count_input, ai_score=ai_score, baseline=baseline,
    )

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    response = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=max_tokens, temperature=0,
        system=system, messages=[{"role": "user", "content": input_text}],
    )
    clean = response.content[0].text
    clean = pr._regex_sweep(clean, keep_contractions=keep_contractions, keep_dashes=keep_dashes)
    if locale == "uk":
        clean = pr._apply_uk_english(clean)
    clean = pr._grammar_fix_pass(clean, client, original_input_text=input_text)
    clean = pr._regex_sweep(clean, keep_contractions=keep_contractions, keep_dashes=keep_dashes)

    delta = ve.score_render_delta(baseline, clean)
    semantic = ve.score_semantic_drift(input_text, clean)

    # Second, independent check against the starter-only baseline -
    # mirrors app.py exactly. Rule-based, no extra API call.
    starter_baseline = fingerprint.get("starter_baseline")
    starter_delta = ve.score_render_delta(starter_baseline, clean) if starter_baseline else None
    correction_delta = pr.merge_starter_evidence(delta, starter_delta)

    correction_prompt = pr.build_correction_prompt(correction_delta, semantic)
    correction_applied = False
    if correction_prompt:
        # Parity fix, 4 Sept 2026: this used to be a plain-text
        # completion with no contamination check, no retry, and an
        # incomplete _regex_sweep call (missing original_input_text/
        # keep_dashes) - a real breadth-benchmark run on this exact
        # code path produced a response with the model's own reasoning
        # leaked into the output ("Wait, let me re-read the
        # instructions... Let me redo:") that nothing here would have
        # caught. Production's app.py has three protections this
        # lacked: the forced CORRECTION_TOOL tool-call schema (no
        # free-text channel for reasoning to leak into), a bounded
        # retry against response_looks_contaminated, and complete
        # _regex_sweep parameters. All three now mirrored exactly so
        # this harness reflects what a real user would actually see,
        # not a weaker, unprotected path — every finding from this
        # harness is only as trustworthy as its fidelity to production.
        pre_llm_correction = clean
        corrected = None
        try:
            for attempt in range(2):
                correction_response = client.messages.create(
                    model="claude-sonnet-4-6", max_tokens=max_tokens, temperature=0,
                    system=correction_prompt,
                    messages=[{"role": "user", "content": clean}],
                    tools=[pr.CORRECTION_TOOL],
                    tool_choice={"type": "tool", "name": "return_correction"},
                )
                tool_use_block = next(
                    (b for b in correction_response.content if b.type == "tool_use"), None,
                )
                if tool_use_block is None:
                    continue
                candidate = tool_use_block.input.get("corrected_text", "")
                if candidate and not pr.response_looks_contaminated(candidate):
                    corrected = candidate
                    break
            if corrected is None:
                corrected = pre_llm_correction
            corrected = pr._regex_sweep(
                corrected, keep_contractions=keep_contractions,
                original_input_text=input_text, keep_dashes=keep_dashes,
            )
            if locale == "uk":
                corrected = pr._apply_uk_english(corrected)
            clean = corrected
            delta = ve.score_render_delta(baseline, clean)
            semantic = ve.score_semantic_drift(input_text, clean)
            correction_applied = True
        except Exception as e:
            correction_applied = f"failed: {e}"

    ai_tells = ve.score_ai_tells(clean, original_input_text=input_text)
    if not ai_tells["clean"]:
        clean = pr._regex_sweep(clean, keep_contractions=keep_contractions)
        ai_tells = ve.score_ai_tells(clean, original_input_text=input_text)

    confidence = ve.compute_confidence(fingerprint["fitness"], baseline, len(observations))
    risk = ve.compute_risk(delta, semantic, ai_tells)
    voice_report = ve.build_voice_report(delta, semantic, confidence, risk, ai_tells)

    # Simulated refinement — Sample 3, one round, per the v4 spec
    refinement_result = None
    if persona.get("refinement") and not skip_refinement:
        ref = persona["refinement"]
        tags = ref.get("tags", [])
        freetext = ref.get("freetext", "")
        note = ", ".join(tags)
        if freetext.strip():
            note = f"{note}. {freetext.strip()}" if note else freetext.strip()
        refined_input = f"{input_text}\n\n[Refinement requested: {note}]" if note else input_text

        response2 = client.messages.create(
            model="claude-sonnet-4-6", max_tokens=max_tokens, temperature=0,
            system=system, messages=[{"role": "user", "content": refined_input}],
        )
        clean2 = response2.content[0].text
        clean2 = pr._regex_sweep(clean2, keep_contractions=keep_contractions, keep_dashes=keep_dashes)
        if locale == "uk":
            clean2 = pr._apply_uk_english(clean2)
        clean2 = pr._grammar_fix_pass(clean2, client, original_input_text=refined_input)
        clean2 = pr._regex_sweep(clean2, keep_contractions=keep_contractions, keep_dashes=keep_dashes)

        delta2 = ve.score_render_delta(baseline, clean2)
        semantic2 = ve.score_semantic_drift(refined_input, clean2)
        ai_tells2 = ve.score_ai_tells(clean2, original_input_text=refined_input)
        risk2 = ve.compute_risk(delta2, semantic2, ai_tells2)
        report2 = ve.build_voice_report(delta2, semantic2, confidence, risk2, ai_tells2)

        refinement_result = {"output": clean2, "voice_report": report2}
    elif persona.get("refinement") and skip_refinement:
        refinement_result = "skipped_by_flag"

    return {
        "detected_mode": detected_mode,
        "ai_score_input": ai_score,
        "output": clean,
        "correction_pass_applied": correction_applied,
        "voice_report": voice_report,
        "refinement_result": refinement_result,
    }


def run_persona(
    persona: dict, dry_run: bool, api_key: str | None,
    max_tokens: int = 4096, skip_refinement: bool = False,
) -> dict:
    result = {"persona_name": persona.get("persona_name", "unnamed")}

    fingerprint = run_fingerprint_stage(persona)
    result["fingerprint_stage"] = fingerprint

    if "error" in fingerprint:
        result["status"] = "error"
        return result

    if dry_run:
        result["status"] = "dry_run_complete"
        return result

    if not api_key:
        result["status"] = "skipped_render_no_api_key"
        return result

    try:
        render = run_render_stage(persona, fingerprint, api_key, max_tokens, skip_refinement)
        result["render_stage"] = render
        result["status"] = "complete"
    except Exception as e:
        result["status"] = "render_error"
        result["error"] = str(e)

    return result


def main():
    parser = argparse.ArgumentParser(description="Portable headless Voxa test harness")
    parser.add_argument("path", help="Path to a persona JSON file, or a folder if --batch")
    parser.add_argument("--batch", action="store_true", help="Treat path as a folder of persona JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Fingerprint stage only — no API calls, free")
    parser.add_argument("--out", default=None, help="Write JSON report to this file instead of stdout")
    parser.add_argument(
        "--max-tokens", type=int, default=4096,
        help="Max output tokens per API call. Production default is 4096; "
             "test render_input strings are short, so a lower value (e.g. 600) "
             "cuts real cost without changing production app.py."
    )
    parser.add_argument(
        "--no-refinement", action="store_true",
        help="Skip the optional 4th API call (Sample 3 refinement round) even "
             "if the persona has a refinement block set. Cheaper first pass."
    )
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    personas = []
    if args.batch:
        for fname in sorted(os.listdir(args.path)):
            if fname.endswith(".json"):
                with open(os.path.join(args.path, fname)) as f:
                    personas.append(json.load(f))
    else:
        with open(args.path) as f:
            personas.append(json.load(f))

    results = [
        run_persona(p, args.dry_run, api_key, args.max_tokens, args.no_refinement)
        for p in personas
    ]
    output = results[0] if len(results) == 1 and not args.batch else {"results": results}

    output_json = json.dumps(output, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(output_json)
        print(f"Wrote {len(results)} result(s) to {args.out}", file=sys.stderr)
    else:
        print(output_json)


if __name__ == "__main__":
    main()
