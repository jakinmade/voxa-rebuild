#!/usr/bin/env python3
"""
free_determinism_check.py — zero-API-cost evidence for two of the three
questions stability_test.py answers with real (paid) API calls.

WHY THIS EXISTS

stability_test.py is the right tool for measuring actual run-to-run
LLM sampling variance, but every run costs real ANTHROPIC_API_KEY
spend (10 runs x 11 personas x ~3 calls each). This script answers
what it can WITHOUT calling the model at all, using only the parts of
the pipeline that are pure code:

  1. PROVES scoring-layer determinism (not just "no LLM calls found
     in the code" — an actual repeated-run hash check).
  2. APPROXIMATES rewrite-sensitivity: instead of watching the real
     model produce different wording across samples, this hand-writes
     a few plausible alternate phrasings of the same underlying
     rewrite and scores each one, showing how much the reported
     Voice Match / Semantic Match numbers move for a given amount of
     wording change. This is NOT a substitute for the real thing —
     hand-authored variants are not a random sample from the model's
     actual output distribution, so this cannot tell you the real
     stdev. What it CAN tell you: whether the scorer is oversensitive
     (a trivial rewording swings the number a lot — bad) or usefully
     stable (only genuine register/structure changes move it).

  3. Genuinely CANNOT be done for free: real rewrite-to-rewrite
     variance from actual LLM sampling. That requires calling the
     model. When you're ready to spend on that specific check, use
     stability_test.py — nothing here replaces it, this only covers
     the two questions that don't require spending anything.

USAGE

  python3 dev_tools/free_determinism_check.py
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import voice_engine as ve
import harness


# ---------------------------------------------------------------------
# Part 1: prove scoring determinism with an actual repeated-run check,
# not just an absence-of-LLM-calls code inspection.
# ---------------------------------------------------------------------

def check_scoring_determinism(persona: dict, runs: int = 25) -> dict:
    """Runs the free fingerprint stage N times on identical input and
    confirms the fingerprint_hash (and the full baseline dict) is
    byte-identical every time. Any variation here would mean the
    'deterministic scoring' claim is false — this is falsifiable,
    not just asserted."""
    hashes = set()
    baselines = []
    for _ in range(runs):
        fp = harness.run_fingerprint_stage(persona)
        if "error" in fp:
            return {"error": fp["error"]}
        hashes.add(fp["fingerprint_hash"])
        baselines.append(fp["baseline"])

    all_identical = len(hashes) == 1 and all(b == baselines[0] for b in baselines)
    return {
        "runs": runs,
        "unique_hashes": len(hashes),
        "all_identical": all_identical,
        "fingerprint_hash": next(iter(hashes)) if len(hashes) == 1 else sorted(hashes),
    }


# ---------------------------------------------------------------------
# Part 2: sensitivity approximation using hand-authored variants of
# the SAME underlying rewrite, standing in for what different model
# samples might plausibly look like. Real variance requires real
# sampling (stability_test.py, costs money) - this is a proxy, and is
# labelled as one everywhere it's reported.
# ---------------------------------------------------------------------

# Four hand-written rewrites of direct_founder's actual render_input
# ("It is important to note that, in today's fast-paced business
# landscape, effective stakeholder communication is absolutely
# essential...") - varying only in the kind of thing LLM sampling
# variance actually tends to touch: hedge words, sentence-splitting,
# opener choice, connective words. Deliberately NOT varying
# content/facts - that's not what sampling variance does.
VARIANT_REWRITES = [
    "Stakeholder communication matters a lot right now. We also need a proposal process that actually works end to end - that's the real shift we need to make.",
    "I think stakeholder communication matters a lot right now, and we probably need a proposal process that works end to end too. That's the shift, I'd say.",
    "This is the shift we need: stakeholder communication that actually lands, and a proposal process that works end to end without the usual friction.",
    "Stakeholder communication matters right now, and honestly so does having a proposal process that just works end to end. That's the real change here.",
]


def check_rewrite_sensitivity(persona: dict) -> dict:
    fp = harness.run_fingerprint_stage(persona)
    if "error" in fp:
        return {"error": fp["error"]}
    baseline = fp["baseline"]
    input_text = persona["render_input"]

    results = []
    for variant in VARIANT_REWRITES:
        delta = ve.score_render_delta(baseline, variant)
        semantic = ve.score_semantic_drift(input_text, variant)
        ai_tells = ve.score_ai_tells(variant, original_input_text=input_text)
        confidence = ve.compute_confidence(fp["fitness"], baseline, len(fp["observations"]))
        risk = ve.compute_risk(delta, semantic, ai_tells)
        report = ve.build_voice_report(delta, semantic, confidence, risk, ai_tells)
        results.append({
            "variant": variant,
            "voice_match": report.get("voice_match"),
            "semantic_match": report.get("semantic_match"),
            "risk": report.get("risk"),
        })

    voice_matches = [r["voice_match"] for r in results if isinstance(r["voice_match"], (int, float))]
    spread = (max(voice_matches) - min(voice_matches)) if voice_matches else None
    return {"variant_results": results, "voice_match_spread": spread}


def main():
    personas_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas")
    persona_files = sorted(f for f in os.listdir(personas_dir) if f.endswith(".json"))

    print("=" * 78)
    print("PART 1 — Scoring determinism (proven, not approximated, zero cost)")
    print("=" * 78)
    det_results = []
    for fname in persona_files:
        persona = json.load(open(os.path.join(personas_dir, fname)))
        result = check_scoring_determinism(persona, runs=25)
        det_results.append((persona.get("persona_name", fname), result))
        status = "ERROR" if "error" in result else ("IDENTICAL x25" if result["all_identical"] else "*** VARIED ***")
        print(f"  {persona.get('persona_name', fname):<24} {status}")

    all_ok = all("error" not in r and r["all_identical"] for _, r in det_results)
    print()
    print("Result: scoring is provably deterministic across all personas." if all_ok
          else "Result: *** at least one persona showed non-deterministic scoring — investigate before v2.0 ***")

    print()
    print("=" * 78)
    print("PART 2 — Rewrite-sensitivity approximation (proxy, NOT real variance)")
    print("=" * 78)
    print("Same underlying meaning, 4 hand-written phrasings, run against the")
    print("first persona whose fixture includes a matching render_input.")
    print()

    # Only meaningful against a persona whose render_input matches what
    # VARIANT_REWRITES was actually written to be a rewrite of.
    target_name = "direct_founder.json"
    if target_name in persona_files:
        persona = json.load(open(os.path.join(personas_dir, target_name)))
        sens = check_rewrite_sensitivity(persona)
        if "error" in sens:
            print(f"  ERROR: {sens['error']}")
        else:
            for r in sens["variant_results"]:
                print(f"  Voice Match {r['voice_match']:>4}  Semantic Match {r['semantic_match']:>4}  "
                      f"Risk {r['risk']:<8}  \"{r['variant'][:60]}...\"")
            print(f"\n  Voice Match spread across 4 hand-written variants: {sens['voice_match_spread']} points")
            print("  (This is NOT the real model's sampling variance — it's how much the")
            print("   scorer moves for plausible human-written rewording. Real variance")
            print("   needs stability_test.py against the live model.)")
    else:
        print(f"  Skipped: {target_name} not found among personas.")

    print()
    print("=" * 78)
    print("What this script does NOT tell you (needs real API spend):")
    print("  - Actual run-to-run variance from live model sampling")
    print("  - Whether temperature tuning would reduce that variance")
    print("  - Independent (non-self-referential) fidelity judgment")
    print("Use dev_tools/stability_test.py when ready to spend on those.")
    print("=" * 78)


if __name__ == "__main__":
    main()
