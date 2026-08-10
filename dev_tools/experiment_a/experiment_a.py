#!/usr/bin/env python3
"""
experiment_a.py — the Experiment A gate. See PROTOCOL.md for the why.

Runs THREE independent onboarding conditions for a single participant
(A: open sample, B: current scenario prompts, C: stimulus-response)
through the real, unmodified fingerprint -> render -> Voice Report
pipeline (voice_engine.py, prompts.py — same modules harness.py uses,
same functions the live app uses). Conditions are NOT combined — each
produces its own fingerprint from only its own text, so the comparison
is a fair test of onboarding *input shape*, not a test of more data
beating less data.

Each condition renders the SAME render_input. Output is written blind
and randomised (labels X/Y/Z, order shuffled per participant) to a
result file the participant can be shown directly, plus a separate
answer-key file the researcher keeps that maps X/Y/Z back to A/B/C.
Never show both files to the same participant.

USAGE

  Dry run (fingerprint stage only, no API calls, free):
    python3 experiment_a.py participants/p01.json --dry-run

  Full run (needs ANTHROPIC_API_KEY):
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 experiment_a.py participants/p01.json --out results/p01/

  Batch (every participant file in a folder):
    python3 experiment_a.py participants/ --batch --out results/

  Record a participant's blind judgement after the fact:
    python3 experiment_a.py --record results/p01/answer_key.json --winner Y --reason "felt like something I'd actually send"

  Tally all recorded results:
    python3 experiment_a.py --tally results/

PARTICIPANT FIXTURE FORMAT — see participants/TEMPLATE.json.

  participant_id          str
  condition_a_text        str   — open writing sample, ~150-250 words
  condition_b_completions list[str] — same shape as harness.py's
                                       sample2_completions: indices
                                       [0] and [3] required, >=10 words
                                       each (mirrors production gate)
  condition_c_completions list[str] — exactly 2 entries, the
                                       participant's replies to
                                       stimuli.md's two stimuli,
                                       >=10 words each
  render_input             str   — the AI-ish text to rewrite; SAME
                                    text used across all three
                                    conditions for this participant.
                                    Can vary participant-to-participant.
"""

import os
import sys
import json
import random
import argparse
from collections import Counter

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import voice_engine as ve
import prompts as pr

MIN_WORDS_OPEN_SAMPLE = 150   # floor for Condition A — comparable text volume
MIN_WORDS_COMPLETION = 10     # matches production Screen 3 floor, reused for B and C


def _wc(text: str) -> int:
    return len(text.split())


def build_fingerprint_from_text(label: str, text: str) -> dict:
    """
    Generic, condition-agnostic fingerprint builder. Unlike
    harness.run_fingerprint_stage (which is hard-coded to the
    sample1+sample2 structure), this takes a single block of text and
    builds the same-shaped fingerprint dict run_render_stage expects —
    because Experiment A's whole point is comparing different input
    *shapes* against each other, not assuming the production shape.
    """
    wc = _wc(text)
    floor = MIN_WORDS_OPEN_SAMPLE if label == "A" else (MIN_WORDS_COMPLETION * 2)
    if wc < floor:
        return {"error": f"Condition {label} text is {wc} words, needs >= {floor}."}

    observations = ve.analyse_writing(text)
    baseline = ve.compute_baseline_metrics(text)
    fitness = ve._score_sample_fitness(text)
    locale = pr._detect_locale(text)
    keep_contractions = ve.uses_contractions(text)

    return {
        "observations": observations[:5],
        "baseline": baseline,
        "starter_baseline": None,  # no second independent check outside condition B's own text
        "fingerprint_hash": ve.fingerprint_hash(baseline),
        "fitness": fitness,
        "locale": locale,
        "keep_contractions": keep_contractions,
    }


def condition_text(participant: dict, label: str) -> tuple[str, str | None]:
    """Returns (combined_text, error) for a given condition label."""
    if label == "A":
        text = participant.get("condition_a_text", "")
        wc = _wc(text)
        if wc < MIN_WORDS_OPEN_SAMPLE:
            return "", f"condition_a_text is {wc} words, needs >= {MIN_WORDS_OPEN_SAMPLE}."
        return text, None

    key = "condition_b_completions" if label == "B" else "condition_c_completions"
    completions = participant.get(key, [])
    required_idx = (0, 3) if label == "B" else (0, 1)
    missing = []
    for idx in required_idx:
        t = completions[idx].strip() if idx < len(completions) else ""
        if not t or _wc(t) < MIN_WORDS_COMPLETION:
            missing.append(idx)
    if missing:
        return "", f"{key}{missing} missing or under {MIN_WORDS_COMPLETION} words."
    return " ".join(completions[idx].strip() for idx in required_idx), None


def run_condition(label: str, participant: dict, api_key: str | None,
                   dry_run: bool, max_tokens: int) -> dict:
    text, err = condition_text(participant, label)
    if err:
        return {"condition": label, "status": "error", "error": err}

    fingerprint = build_fingerprint_from_text(label, text)
    if "error" in fingerprint:
        return {"condition": label, "status": "error", "error": fingerprint["error"]}

    if dry_run:
        return {
            "condition": label, "status": "dry_run_complete",
            "input_word_count": _wc(text), "fingerprint_hash": fingerprint["fingerprint_hash"],
        }

    if not api_key:
        return {"condition": label, "status": "skipped_render_no_api_key"}

    import anthropic

    render_input = participant["render_input"]
    observations = fingerprint["observations"]
    baseline = fingerprint["baseline"]
    keep_contractions = fingerprint["keep_contractions"]
    locale = fingerprint["locale"]

    detected_mode = pr._detect_mode(render_input)
    ai_score = ve._score_ai_signal(render_input)
    voice_dna = pr._build_voice_dna(observations, text, baseline, ai_score)
    mode_instruction = pr.apply_intent_mode(render_input, detected_mode)
    word_count_input = len(render_input.split())

    system = pr._build_system_prompt(
        voice_dna=voice_dna, mode_instruction=mode_instruction,
        word_count_input=word_count_input, ai_score=ai_score, baseline=baseline,
    )

    client = anthropic.Anthropic(api_key=api_key, max_retries=0)
    response = client.messages.create(
        model="claude-sonnet-4-6", max_tokens=max_tokens,
        system=system, messages=[{"role": "user", "content": render_input}],
    )
    clean = response.content[0].text
    clean = pr._regex_sweep(clean, keep_contractions=keep_contractions)
    if locale == "uk":
        clean = pr._apply_uk_english(clean)
    clean = pr._grammar_fix_pass(clean, client)
    clean = pr._regex_sweep(clean, keep_contractions=keep_contractions)

    delta = ve.score_render_delta(baseline, clean)
    semantic = ve.score_semantic_drift(render_input, clean)
    ai_tells = ve.score_ai_tells(clean)
    confidence = ve.compute_confidence(fingerprint["fitness"], baseline, len(observations))
    risk = ve.compute_risk(delta, semantic, ai_tells)
    voice_report = ve.build_voice_report(delta, semantic, confidence, risk, ai_tells)

    return {
        "condition": label, "status": "complete",
        "input_word_count": _wc(text),
        "output": clean,
        "voice_report": voice_report,
    }


def run_participant(participant: dict, api_key: str | None, dry_run: bool, max_tokens: int) -> dict:
    pid = participant.get("participant_id", "unnamed")
    results = {label: run_condition(label, participant, api_key, dry_run, max_tokens)
               for label in ("A", "B", "C")}

    # Blind + randomise. Only outputs that reached "complete" (or
    # dry_run_complete) get shown — a condition with bad fixture data
    # doesn't silently get excluded from the tally later, it's just
    # not part of THIS participant's blind set.
    showable = [label for label in ("A", "B", "C")
                if results[label]["status"] in ("complete", "dry_run_complete")]
    shuffled = showable[:]
    random.shuffle(shuffled)
    blind_labels = ["X", "Y", "Z"][:len(shuffled)]
    answer_key = dict(zip(blind_labels, shuffled))

    blind_view = {
        "participant_id": pid,
        "instruction": "Which of these sounds most like you? Pick one.",
        "outputs": {
            blind: (results[real].get("output") if not dry_run else
                    f"[dry run — no output. condition {real}, {results[real].get('input_word_count')} words]")
            for blind, real in answer_key.items()
        },
    }

    return {
        "participant_id": pid,
        "raw_results": results,       # researcher-only — has condition labels
        "answer_key": answer_key,     # researcher-only — X/Y/Z -> A/B/C mapping
        "blind_view": blind_view,     # safe to show the participant
    }


def cmd_run(args):
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    participants = []
    if args.batch:
        for fname in sorted(os.listdir(args.path)):
            if fname.endswith(".json") and fname != "TEMPLATE.json":
                with open(os.path.join(args.path, fname)) as f:
                    participants.append(json.load(f))
    else:
        with open(args.path) as f:
            participants.append(json.load(f))

    for p in participants:
        result = run_participant(p, api_key, args.dry_run, args.max_tokens)
        pid = result["participant_id"]
        if args.out:
            out_dir = os.path.join(args.out, pid) if args.batch else args.out
            os.makedirs(out_dir, exist_ok=True)
            with open(os.path.join(out_dir, "answer_key.json"), "w") as f:
                json.dump({k: result[k] for k in ("participant_id", "raw_results", "answer_key")}, f, indent=2)
            with open(os.path.join(out_dir, "blind_view.json"), "w") as f:
                json.dump(result["blind_view"], f, indent=2)
            print(f"{pid}: wrote answer_key.json + blind_view.json to {out_dir}", file=sys.stderr)
        else:
            print(json.dumps(result, indent=2))


def cmd_record(args):
    with open(args.record) as f:
        answer_key_file = json.load(f)
    mapping = answer_key_file["answer_key"]
    if args.winner not in mapping:
        print(f"'{args.winner}' isn't a valid label for this participant. Options: {list(mapping.keys())}", file=sys.stderr)
        sys.exit(1)
    real_winner = mapping[args.winner]
    record = {
        "participant_id": answer_key_file["participant_id"],
        "blind_choice": args.winner,
        "winning_condition": real_winner,
        "reason": args.reason or "",
    }
    results_dir = os.path.dirname(os.path.abspath(args.record))
    out_path = os.path.join(results_dir, "judgement.json")
    with open(out_path, "w") as f:
        json.dump(record, f, indent=2)
    print(f"Recorded: participant {record['participant_id']} chose {args.winner} -> Condition {real_winner}", file=sys.stderr)
    print(f"Wrote {out_path}", file=sys.stderr)


def cmd_tally(args):
    tally = Counter()
    reasons = {label: [] for label in ("A", "B", "C")}
    n = 0
    for root, _, files in os.walk(args.tally):
        if "judgement.json" in files:
            with open(os.path.join(root, "judgement.json")) as f:
                j = json.load(f)
            tally[j["winning_condition"]] += 1
            n += 1
            if j.get("reason"):
                reasons[j["winning_condition"]].append(j["reason"])

    print(f"\nExperiment A — {n} participant(s) judged\n")
    for label in ("A", "B", "C"):
        name = {"A": "Open sample", "B": "Current scenario prompts", "C": "Stimulus-response"}[label]
        print(f"  {label} ({name}): {tally[label]} win(s)")
    print()

    if n < 15:
        print(f"Below the 15-participant floor from PROTOCOL.md — treat as directional only, not a decision.\n")

    counts = sorted(tally.values(), reverse=True)
    if len(counts) >= 2 and n > 0 and (counts[0] - counts[1]) <= max(2, round(n * 0.15)):
        print("Result within the 2-3 participant margin PROTOCOL.md treats as inconclusive.")
        print("Do not ship on this — re-run with larger n on the leading conditions, or default to B.\n")
    elif n >= 15:
        winner = tally.most_common(1)[0][0]
        print(f"Clear plurality: Condition {winner}. Proceed per PROTOCOL.md's 'What happens after'.\n")

    for label in ("A", "B", "C"):
        if reasons[label]:
            print(f"Reasons given for {label}:")
            for r in reasons[label]:
                print(f"  - {r}")
            print()


def main():
    parser = argparse.ArgumentParser(description="Experiment A — onboarding condition comparison")
    sub = parser.add_mutually_exclusive_group(required=False)

    parser.add_argument("path", nargs="?", help="Participant JSON file, or folder if --batch")
    parser.add_argument("--batch", action="store_true", help="Treat path as a folder of participant JSON files")
    parser.add_argument("--dry-run", action="store_true", help="Fingerprint stage only — no API calls, free")
    parser.add_argument("--out", default=None, help="Write results here instead of stdout")
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--record", default=None, help="Path to an answer_key.json to record a judgement against")
    parser.add_argument("--winner", default=None, help="Blind label (X/Y/Z) the participant picked, with --record")
    parser.add_argument("--reason", default=None, help="Optional one-line reason, with --record")
    parser.add_argument("--tally", default=None, help="Folder to walk for judgement.json files and tally")

    args = parser.parse_args()

    if args.tally:
        cmd_tally(args)
    elif args.record:
        if not args.winner:
            print("--record requires --winner X|Y|Z", file=sys.stderr)
            sys.exit(1)
        cmd_record(args)
    elif args.path:
        cmd_run(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
