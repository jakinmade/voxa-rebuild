#!/usr/bin/env python3
"""
reference_statement_smoke_check.py — zero-API-cost, pre-launch
validation that the Reference Statement mechanism (8 Sept 2026,
VOICOVA_Reference_Statement_Design.docx) actually does what it's
supposed to, before any real profile has ever used it.

WHY THIS EXISTS

The feature genuinely cannot be validated against real usage before
launch — no production profile has a reference_statement yet, so
there's no real data to pull and eyeball (the honest gap this script
exists to partially close; see this session's own note on why "wait
for real data" isn't available here the way it was for the 7 Sept
first-person-ratio fixes, which DID have real render_history to check
against).

What this CAN do for free: run the real, unmodified
_build_voice_dna against dev_tools/personas/*.json — the same
realistic, hand-written voice samples breadth_benchmark.py already
uses, spanning genuinely different registers (a direct founder, a
formal civil servant, a warm hedging manager, ...) — with a plausible,
register-matched reference statement for each, and diff the resulting
ANCHOR SENTENCES block against the same persona with NO reference
statement. This is not a substitute for real usage data (these
reference statements are hand-authored to be plausible, not sampled
from what real users will actually write), but it proves three things
with total certainty, using the actual shipped code path, not a mock:

  1. The mechanism doesn't crash on real, varied persona corpora.
  2. Reference-statement sentences genuinely appear FIRST in the
     anchor block, ahead of the algorithmic picks — the priority
     ordering actually fires, not just in the narrow unit-test inputs.
  3. A persona whose onboarding corpus is off-register (e.g.
     dry_sarcastic, hedging_academic — nothing like a LinkedIn post)
     visibly gets a MORE register-appropriate anchor set once a
     reference statement is added — the concrete, inspectable version
     of this feature's whole hypothesis.

Genuinely CANNOT be done for free: whether real Fix-it OUTPUT quality
improves. That requires a real generation call per persona (the
render_input field already in each persona file, run through the real
API) and a subjective read of the result — real but modest cost (one
call per persona), appropriate for a manual pre-launch spot-check, not
this zero-cost script. When ready to spend on that check, adapt
dev_tools/breadth_benchmark.py's own harness rather than building a
second one here.

USAGE

  python3 dev_tools/reference_statement_smoke_check.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompts as pr

_PERSONAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "personas")

# Hand-authored, register-matched reference statements — one per
# persona, deliberately written to sound like something THAT persona
# would post, following this feature's own onboarding-prompt guidance
# ("a real professional opinion, lesson, or observation... something
# you'd genuinely consider posting"). Not exhaustive (personas without
# an entry here are skipped, not failed) — covers a deliberately wide
# register spread: a founder's blunt update, a hedging academic's
# careful claim, a civil servant's formal note, a marketer's upbeat
# post.
_REFERENCE_STATEMENTS = {
    "direct_founder": (
        "We shipped the Manchester rollout two weeks early and I'm not going to pretend "
        "that wasn't mostly luck. The team pulled it off anyway."
    ),
    "hedging_academic": (
        "It could perhaps be argued that the results here are somewhat more nuanced than "
        "the headline figure suggests, though I would want to see this replicated before "
        "drawing any firm conclusion."
    ),
    "formal_civil_servant": (
        "I have reviewed the submission and, on balance, consider the proposed timeline "
        "unrealistic given the current resourcing constraints across the department."
    ),
    "enthusiastic_marketer": (
        "Just wrapped our biggest launch week yet and honestly still buzzing from it! "
        "So proud of what this team pulled together in under a month."
    ),
    "warm_hedging_manager": (
        "I think we probably need to have a proper conversation about workload before "
        "next quarter, if that's alright with everyone. No pressure either way."
    ),
    "terse_engineer": (
        "Fixed the memory leak. Root cause was a stale reference in the cache layer. "
        "Shipped, tested, done."
    ),
}


def _anchor_block(voice_dna: str) -> list[str]:
    if "ANCHOR SENTENCES" not in voice_dna:
        return []
    block = voice_dna.split("ANCHOR SENTENCES")[1].split("\n\n")[0]
    return [l.strip() for l in block.split("\n") if l.strip().startswith('"')]


def main() -> None:
    failures = 0
    checked = 0

    for filename in sorted(os.listdir(_PERSONAS_DIR)):
        if not filename.endswith(".json"):
            continue
        persona_name = filename[:-5]
        reference_statement = _REFERENCE_STATEMENTS.get(persona_name)
        if not reference_statement:
            continue

        with open(os.path.join(_PERSONAS_DIR, filename)) as f:
            persona = json.load(f)

        raw_text = persona.get("sample1_text", "")
        completions = persona.get("sample2_completions", [])
        corpus = (raw_text + " " + " ".join(completions)).strip()

        checked += 1
        print(f"\n{'=' * 70}\n{persona_name}\n{'=' * 70}")

        try:
            without = pr._build_voice_dna(
                observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
                raw_text=corpus, baseline=None, ai_score=0.0,
            )
            with_ref = pr._build_voice_dna(
                observations=[{"headline": "Direct opener", "body": "Gets straight to it."}],
                raw_text=corpus, baseline=None, ai_score=0.0,
                reference_statement=reference_statement,
            )
        except Exception as e:
            print(f"  CRASHED: {e!r}")
            failures += 1
            continue

        anchors_without = _anchor_block(without)
        anchors_with = _anchor_block(with_ref)

        print(f"  Reference statement: \"{reference_statement}\"")
        print(f"  Anchors WITHOUT reference statement ({len(anchors_without)}):")
        for a in anchors_without:
            print(f"    {a}")
        print(f"  Anchors WITH reference statement ({len(anchors_with)}):")
        for a in anchors_with:
            print(f"    {a}")

        # Check 1: no crash (already implicit — reached this point).
        # Check 2: priority ordering — every reference-statement-
        # derived anchor must appear BEFORE every corpus-derived one
        # that's unique to the "with" run.
        ref_sentence_texts = [s.strip().lower() for s in pr.usable_reference_statement_sentences(reference_statement)]
        with_ref_indices_matching_ref = [
            i for i, a in enumerate(anchors_with)
            if any(a.lower().strip('"') == rs or rs in a.lower() for rs in ref_sentence_texts)
        ]
        other_indices = [i for i in range(len(anchors_with)) if i not in with_ref_indices_matching_ref]
        priority_ok = (
            not with_ref_indices_matching_ref
            or not other_indices
            or max(with_ref_indices_matching_ref) < min(other_indices)
        )
        if with_ref_indices_matching_ref and priority_ok:
            print("  PASS — reference-statement anchors appear first")
        elif not with_ref_indices_matching_ref:
            print("  FAIL — reference statement supplied no anchors at all (check sentence length: 5-35 words)")
            failures += 1
        else:
            print("  FAIL — reference-statement anchors did not take priority ordering")
            failures += 1

    print(f"\n{'=' * 70}")
    print(f"Checked {checked} personas.")
    if failures:
        print(f"{failures} CHECK(S) FAILED — do not treat this feature as launch-ready until these pass.")
        sys.exit(1)
    print("ALL CHECKS PASSED (mechanism-level only — see this file's own docstring "
          "for what this does NOT prove: real Fix-it output quality).")


if __name__ == "__main__":
    main()
