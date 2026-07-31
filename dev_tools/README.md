# dev_tools — headless test harness and stability testing

Standalone diagnostic tools for `voice_engine.py` / `prompts.py` at
the repo root. Not part of the pytest suite in `tests/` — these need
a live `ANTHROPIC_API_KEY` and are for manual/exploratory testing,
not CI.

## What's here

- **`harness.py`** — runs the real fingerprint → rewrite → Voice
  Report pipeline against a persona, no Streamlit, no browser. Imports
  `voice_engine.py` and `prompts.py` from the repo root directly —
  unmodified, same functions the live app uses.
- **`stability_test.py`** — runs the same persona N times and reports
  variance (mean/stdev) on Voice Match and Semantic Match. The
  scoring layer is deterministic; the LLM rewrite isn't. This measures
  that gap, which nothing else in the repo currently does.
- **`personas/`** — 11 synthetic test personas across distinct voices
  (direct founder, casual/contraction-heavy, hedging academic,
  enthusiastic marketer, terse engineer, warm hedging manager,
  dry/sarcastic, formal civil-service register, narrative storyteller,
  data analyst, reflective essayist). All invented for contrast, not
  real people's writing.

## Setup

```bash
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...   # only needed for full runs, not --dry-run
```

## Running the pipeline harness

```bash
# Free, no API key — fingerprint + fitness score only
python3 dev_tools/harness.py dev_tools/personas/direct_founder.json --dry-run

# Full pipeline, needs API key
python3 dev_tools/harness.py dev_tools/personas/direct_founder.json

# Every persona at once
python3 dev_tools/harness.py dev_tools/personas/ --batch --out results.json
```

## Running stability tests

```bash
# Single persona, 10 runs, display card
python3 dev_tools/stability_test.py dev_tools/personas/direct_founder.json --runs 10

# Every persona, table format
python3 dev_tools/stability_test.py dev_tools/personas/ --batch --runs 10 --out stability.json
```

See `CRIBSHEET.md` for the persona JSON format and a quick-reference
table of what each field in the output means.

## What changed in `voice_engine.py` alongside this addition

Two fixes prompted by external review, plus one consistency fix found
while implementing them:

1. **Sentence splitter** now guards against Mr./Dr./U.K./e.g. style
   abbreviations creating false sentence breaks.
2. **Imperative verb detection** expanded from 20 to 181 words (still
   a fixed deterministic list, not NLP/POS tagging).
3. **`directive_ratio`** (one of the four dimensions in the actual
   Voice Match score) was found to be using a separate, un-updated
   33-word imperative list rather than the one above — the expansion
   in #2 was silently not reaching the real scored metric. Fixed to
   use one shared list. Same root cause affected the baseline's
   sentence splitting, which had its own inline split that bypassed
   fix #1 — also unified to the shared, abbreviation-safe splitter.
4. **`fingerprint_hash()`** — new function, deterministic hash of the
   four scored baseline dimensions. Same input text always produces
   the same hash; different input produces a different one; key order
   never affects it. Demonstrates and regression-checks the engine's
   core determinism claim directly, rather than only asserting it in
   a docstring.

## What this does NOT test

The UI (paste-blocking, the live Streamlit screens) — that needs a
browser against the deployed app, not this harness.
