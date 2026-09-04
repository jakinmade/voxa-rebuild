#!/usr/bin/env python3
"""
breadth_benchmark.py — does the rewrite pipeline hold up across
DIFFERENT voices, not just the one real example this session's fixes
were derived from?

Distinct from stability_test.py, which answers a different question:
"does the SAME persona reproduce consistently across repeated runs"
(needs many runs of one persona). This answers "does the pipeline
work across a DIVERSE set of voices" (needs one run of many personas,
not many runs of one) — different question, different sample-size
shape, an order of magnitude cheaper as a result.

Every fix shipped in this session (4 Sept 2026 — contraction/em-dash/
comma-splice stripping, the missing pre-generation instructions, the
sentence-growth false positive, temperature pinning) was derived from
ONE real business-development email in ONE person's voice. This
script exists to check whether those fixes generalise, or whether
they were narrowly right for that one register and something
different breaks on, say, a hedging academic or a terse engineer.

USAGE

  export ANTHROPIC_API_KEY=sk-ant-...
  python3 breadth_benchmark.py --personas terse_engineer hedging_academic \\
      narrative_storyteller formal_civil_servant --out breadth_report.json

  # Or run every persona in dev_tools/personas/ once each:
  python3 breadth_benchmark.py --all --out breadth_report.json

COST

  ~$0.015-0.02 per persona (one real render: main rewrite + grammar-fix
  pass + conditional correction pass). 4 personas ~= $0.07. All 11
  ~= $0.19. See the 4 Sept 2026 session notes for the full breakdown.

WHAT "GOOD" LOOKS LIKE

  Every persona: risk not High, content lock all-clear (facts,
  attribution, no invented sentences, no new hedging), and the four
  scored dimensions not showing MISSED. A persona that comes back
  High risk or with a Content Lock flag is exactly the signal this
  script exists to surface — a failure mode this session's fixes
  didn't cover, worth tracing the same way every fix this session was
  traced: real text, real diff, real root cause.
"""

import os
import sys
import json
import glob
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness


def run_breadth_benchmark(persona_paths: list[str], api_key: str) -> dict:
    results = []
    for path in persona_paths:
        persona = json.load(open(path))
        name = persona.get("persona_name", os.path.basename(path))
        print(f"Running {name}...", file=sys.stderr)

        outcome = harness.run_persona(persona, dry_run=False, api_key=api_key, skip_refinement=True)

        if outcome.get("status") != "complete":
            results.append({
                "persona_name": name,
                "status": outcome.get("status", "unknown"),
                "detail": outcome.get("error") or outcome.get("fingerprint_stage", {}).get("error", ""),
            })
            continue

        render_stage = outcome.get("render_stage", {})
        report = render_stage.get("voice_report", {})
        results.append({
            "persona_name": name,
            "status": "ok",
            "risk": report.get("risk"),
            "voice_match": report.get("voice_match"),
            "voice_match_tier": report.get("voice_match_tier"),
            "semantic_match": report.get("semantic_match"),
            "confidence": report.get("confidence"),
            "ai_tell_clean": report.get("ai_tell_clean"),
            "content_integrity_hard_fail": report.get("content_integrity_hard_fail"),
            "dropped_entities": report.get("dropped_entities", []),
            "attribution_swaps": report.get("attribution_swaps", []),
            "correction_pass_applied": render_stage.get("correction_pass_applied"),
            "deterministic_fixers_applied": render_stage.get("deterministic_fixers_applied"),
            "output": render_stage.get("output", ""),
        })

    flagged = [r for r in results if r.get("status") != "ok"
               or r.get("risk") == "High"
               or r.get("content_integrity_hard_fail")]

    return {
        "personas_tested": len(results),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "all_results": results,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--personas", nargs="+", help="persona names (without .json) to test")
    parser.add_argument("--all", action="store_true", help="test every persona in personas/")
    parser.add_argument("--out", help="write full JSON report here")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set.", file=sys.stderr)
        sys.exit(1)

    personas_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas")
    if args.all:
        persona_paths = sorted(glob.glob(os.path.join(personas_dir, "*.json")))
    elif args.personas:
        persona_paths = [os.path.join(personas_dir, f"{name}.json") for name in args.personas]
    else:
        print("Pass --personas NAME [NAME ...] or --all", file=sys.stderr)
        sys.exit(1)

    report = run_breadth_benchmark(persona_paths, api_key)

    print(f"\n{'='*70}")
    print(f"Tested {report['personas_tested']} personas, {report['flagged_count']} flagged")
    print(f"{'='*70}")
    for r in report["all_results"]:
        status = "FLAGGED" if r in report["flagged"] else "clean"
        print(f"  {r['persona_name']:<24} {status:<8} risk={r.get('risk')}  "
              f"voice_match={r.get('voice_match')}  hard_fail={r.get('content_integrity_hard_fail')}")

    if report["flagged"]:
        print(f"\n{'!'*70}")
        print("FLAGGED PERSONAS — trace these the same way every fix this session was traced:")
        for r in report["flagged"]:
            print(f"  {r['persona_name']}: {r}")
        print(f"{'!'*70}")

    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\nFull report written to {args.out}")


if __name__ == "__main__":
    main()
