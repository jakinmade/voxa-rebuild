#!/usr/bin/env python3
"""
stability_test.py — measures rewrite variance across repeated runs.

The scoring layer (voice_engine.py) is deterministic — same text in,
same score out, always. The rewrite itself is NOT deterministic —
same persona, same prompt, run twice against the model, can produce
different text and therefore a different Voice Match / Semantic
Match number purely from model sampling variance. Nothing in the
harness currently measures that gap. This does.

This is the single highest-value addition flagged in the external
review: an unmeasured variance is a credibility risk for a product
whose entire pitch is "trust the number."

USAGE

  Single persona, 10 runs:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 stability_test.py personas/direct_founder.json --runs 10

  Every persona in a folder, one combined report:
    python3 stability_test.py personas/ --batch --runs 10 --out stability.json

WHAT "GOOD" LOOKS LIKE

  stdev on voice_match and semantic_match in the low single digits.
  A persona whose voice_match swings 95/94/93/96/95 across runs is
  fine. One that swings 95/71/84/97/62 is a real problem — either
  the fingerprint is too thin to constrain the rewrite, or the model
  is not reliably following the voice DNA instruction, and either way
  it means the number shown to a user on any single run can't be
  trusted without knowing this spread.
"""

import os
import sys
import json
import glob
import argparse
import statistics

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness


def _stats(values: list) -> dict:
    values = [v for v in values if v is not None]
    if not values:
        return {"mean": None, "stdev": None, "min": None, "max": None, "n": 0}
    return {
        "mean": round(statistics.mean(values), 2),
        "stdev": round(statistics.stdev(values), 2) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
        "n": len(values),
    }


def run_stability_test(persona_path: str, n: int, api_key: str) -> dict:
    persona = json.load(open(persona_path))
    fingerprint = harness.run_fingerprint_stage(persona)

    if fingerprint.get("error"):
        return {"persona_name": persona.get("persona_name"), "error": fingerprint["error"]}

    voice_matches, semantic_matches, risks, confidences = [], [], [], []
    ai_tell_clean_count = 0
    errors = 0

    for i in range(n):
        try:
            result = harness.run_render_stage(persona, fingerprint, api_key)
        except Exception as e:
            errors += 1
            continue
        vr = result.get("voice_report", {})
        if not vr:
            errors += 1
            continue
        voice_matches.append(vr.get("voice_match"))
        semantic_matches.append(vr.get("semantic_match"))
        risks.append(vr.get("risk"))
        confidences.append(vr.get("confidence"))
        if vr.get("ai_tell_clean"):
            ai_tell_clean_count += 1

    completed = len(voice_matches)
    return {
        "persona_name": persona.get("persona_name"),
        "runs_requested": n,
        "runs_completed": completed,
        "runs_errored": errors,
        "voice_match": _stats(voice_matches),
        "semantic_match": _stats(semantic_matches),
        "ai_tell_clean_rate": round(ai_tell_clean_count / completed, 2) if completed else None,
        "risk_distribution": {r: risks.count(r) for r in set(risks)} if risks else {},
        "confidence_distribution": {c: confidences.count(c) for c in set(confidences)} if confidences else {},
        "raw_voice_matches": voice_matches,
        "raw_semantic_matches": semantic_matches,
    }


def _rating(voice_stdev: float | None, semantic_stdev: float | None) -> str:
    """
    Rating band for a stability run. Thresholds are a starting point,
    not calibrated against real production variance yet — tighten or
    loosen once stability_test.py has actually run against live data.
    """
    worst = max(v for v in (voice_stdev, semantic_stdev) if v is not None) \
        if (voice_stdev is not None or semantic_stdev is not None) else None
    if worst is None:
        return "Unknown"
    if worst <= 2:
        return "Excellent"
    if worst <= 5:
        return "Good"
    return "Needs investigation"


def print_card(report: dict) -> None:
    """Single-persona display card — the format a customer-facing UI would show."""
    if report.get("error"):
        print(f"Rewrite Stability — {report['persona_name']}\n  ERROR: {report['error']}\n")
        return

    vm, sm = report["voice_match"], report["semantic_match"]
    rating = _rating(vm["stdev"], sm["stdev"])

    print(f"Rewrite Stability — {report['persona_name']}")
    print(f"  Runs .......................... {report['runs_completed']}/{report['runs_requested']}")
    print(f"  Average Voice Match ........... {vm['mean']}%")
    print(f"  Voice Match std dev ........... {vm['stdev']}%")
    print(f"  Average Semantic Match ........ {sm['mean']}%")
    print(f"  Semantic Match std dev ........ {sm['stdev']}%")
    print(f"  AI-tell clean rate ............ {report['ai_tell_clean_rate']}")
    print(f"  Rating ........................ {rating}")
    if report["runs_errored"]:
        print(f"  ({report['runs_errored']} run(s) errored — see raw output)")
    print()


def print_summary(reports: list[dict]) -> None:
    print(f"{'Persona':<22} | {'Runs':<6} | {'VM mean':<8} | {'VM stdev':<9} | "
          f"{'SM mean':<8} | {'SM stdev':<9} | AI-tell clean rate")
    print("-" * 100)
    for r in reports:
        if r.get("error"):
            print(f"{r['persona_name']:<22} | ERROR: {r['error']}")
            continue
        vm, sm = r["voice_match"], r["semantic_match"]
        print(f"{r['persona_name']:<22} | {r['runs_completed']}/{r['runs_requested']:<4} | "
              f"{vm['mean']!s:<8} | {vm['stdev']!s:<9} | "
              f"{sm['mean']!s:<8} | {sm['stdev']!s:<9} | {r['ai_tell_clean_rate']}")

    print("\nFlagged (stdev > 5 on voice_match or semantic_match — investigate):")
    flagged = [r for r in reports if not r.get("error") and (
        (r["voice_match"]["stdev"] or 0) > 5 or (r["semantic_match"]["stdev"] or 0) > 5
    )]
    if not flagged:
        print("  none")
    for r in flagged:
        print(f"  {r['persona_name']}: voice_match stdev={r['voice_match']['stdev']}, "
              f"semantic_match stdev={r['semantic_match']['stdev']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="persona JSON file, or a folder with --batch")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--out", default=None)
    parser.add_argument("--table", action="store_true",
                         help="force table output even for a single persona")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY not set. This script only does full runs, no --dry-run mode "
              "(there's nothing to measure variance on without the actual rewrite call).")
        sys.exit(1)

    if args.batch:
        paths = sorted(glob.glob(os.path.join(args.path, "*.json")))
    else:
        paths = [args.path]

    reports = [run_stability_test(p, args.runs, api_key) for p in paths]

    if args.batch or args.table:
        print_summary(reports)
    else:
        print_card(reports[0])

    if args.out:
        with open(args.out, "w") as f:
            json.dump(reports, f, indent=2)
        print(f"\nFull detail written to {args.out}")


if __name__ == "__main__":
    main()
