# CRIBSHEET — Voxa test harness

Quick reference. If you need the full explanation, read README.md.
If you just need to run something, this page is enough.

## What this is

A headless copy of Voxa's real pipeline. No browser, no Streamlit.
Feed it a fake user ("persona"), get back what the real product would
have produced for that person.

## Setup — one time

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # skip this if you're only using --dry-run
```

## The three commands you need

```bash
# Free. No API key. Fingerprint + fitness score only.
python3 harness.py personas/direct_founder.json --dry-run

# Costs tokens. Full pipeline: fingerprint + actual rewrite + Voice Report.
python3 harness.py personas/direct_founder.json

# Run everything in personas/ at once, save to a file.
python3 harness.py personas/ --batch --out results.json
```

## Making a new persona — copy this, fill it in

```json
{
  "persona_name": "whatever you want to call them",
  "sample1_text": "a realistic paste of writing — email, message, note. 10+ words minimum.",
  "sample2_completions": [
    "finishes: The thing I'd tell someone new to this is...",
    "finishes: What actually annoys me about...",
    "finishes: If I'm honest, the hardest part was...",
    "finishes: The way I'd explain this to a friend is..."
  ],
  "render_input": "an AI-sounding draft to rewrite — verbose, em dashes, filler words work well as a test",
  "refinement": { "tags": ["Too formal"], "freetext": "" }
}
```

Rules:
- `sample1_text` — 10 words minimum, or it errors.
- `sample2_completions` — all four together need 40+ words total, or `sample2_met_floor` comes back `false`.
- `refinement` — optional. Delete it or set to `null` if you don't want it.
- Save it as a `.json` file anywhere, point the command at it.

Tag options for `refinement.tags`: `"Too formal"`, `"Too blunt"`,
`"Doesn't sound like me"`, `"Too long"`, `"Missing my directness"`.

## Reading what comes back — the numbers that matter

| Field | What it means | Good sign |
|---|---|---|
| `fitness.tier` | How usable Sample 1 was for fingerprinting | `gold` or `strong` |
| `voice_report.voice_match` | % — does the rewrite hold the person's style | Higher = better, 80+ is solid |
| `voice_report.semantic_match` | % — did the rewrite keep the actual meaning | Higher = better, 80+ is solid |
| `voice_report.confidence` | How much to trust the two numbers above | `High` |
| `voice_report.risk` | How far this rewrite drifted | `Low` |
| `voice_report.ai_tell_clean` | Any em dash / AI phrase survived? | `true` |
| `status` | Did it actually run | `complete` or `dry_run_complete` |

If `status` says `skipped_render_no_api_key` — you forgot to export the
key, or you're expecting a full run but only did `--dry-run`.

## What "good" looks like

Run 3+ personas that genuinely differ from each other (already have 3
in `personas/` — direct, hedging, contraction-heavy). Check:
1. Does `fingerprint_stage.observations` actually describe how that
   persona writes, not generic filler.
2. Does `voice_match` stay high even when `render_input` is deliberately
   written in a totally different, AI-sounding style.
3. Does `ai_tell_clean` come back `true` even when you feed it input
   packed with em dashes and phrases like "furthermore," "leverage,"
   "it is important to note."

If any of those fail, that's a real product bug, not a test-harness
issue — the harness calls the exact same functions the live app does.

## What this does NOT test

The paste-blocking on the sentence-starter screen. That's a browser
thing (`components/paste_guard` in the real repo) and needs a live
person clicking the actual Railway URL — this harness can't touch it.
