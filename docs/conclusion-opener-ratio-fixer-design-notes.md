# conclusion_opener_ratio auto-fixer — design notes (not yet built)

Captured 5 Sept 2026, end of session. Deliberately deferred here rather
than coded under end-of-session time pressure — this is a genuinely
riskier transformation than the other fixers and deserves a fresh
start, not a rushed one.

## What the dimension measures

`conclusion_opener_ratio` (voice_engine.py, `compute_baseline_metrics`
and `score_render_delta`) is:

    (average word length of the first 3 sentences) / (average sentence
    length across the whole piece)

Lower ratio = the opener is short relative to the piece as a whole =
the writer states their point first, then elaborates. Higher ratio =
a long, throat-clearing opener before the point arrives. It's the
numeric, re-checkable version of `score_conclusion_position`'s
onboarding-time observation (same heuristic, continuous instead of a
threshold boolean) — added 4 Sept 2026 specifically so a *rewrite*
could be checked against this target the way hedge_density etc.
already are.

## Why this fixer is harder than the other five

Every existing fixer in `deterministic_fixers.py` is deletion-only —
remove a hedge word, remove a scaffolding phrase, strip a polite
wrapper off an imperative. None of them ever move a sentence relative
to another sentence. Fixing conclusion_opener_ratio fundamentally
requires **reordering**: if the piece buries its point in sentence 4,
the fix is either (a) moving that sentence earlier, or (b)
restructuring the opener to state the point directly and pushing the
throat-clearing material later. Both are reordering operations, not
deletions.

This repo has direct, painful history with reordering-adjacent
sentence-alignment logic going wrong on real renders (see
`_check_uncorrected_insertions`'s own KNOWN LIMITATION docstring and
its "greedily pairing off near-identical sentences" reversion, 29 Aug
2026) — a cleverer attempt fixed the case it was built for and broke a
different real render. Any conclusion_opener_ratio fixer needs to take
that history seriously rather than relearn it.

## Constraints any design needs to satisfy

- **Must not change what any sentence claims.** Reordering is
  permitted; rewriting content is not — same boundary every other
  fixer/correction pass in this codebase already respects (see
  `build_correction_prompt`'s "never move sentences relative to each
  other" line for the *general* correction pass, which explicitly
  carves out `conclusion_opener_ratio`-style reordering as **not
  currently attempted** anywhere in the pipeline).
- **Must handle the platform-format precedent carefully.** The one
  place this codebase currently *does* permit reordering is the
  `platform_format == "social"` correction instruction (hook-first
  restructuring for LinkedIn/X/Threads) — worth reading as a precedent
  for how the "you may reorder, but every word must trace back to the
  input" contract is worded, but that's an LLM-driven reorder inside a
  forced-tool-call correction pass, not a deterministic function. A
  deterministic reordering fixer is a materially different (harder to
  get right, but more inspectable) approach.
- **Two directions to decide between:**
  1. **Deterministic sentence-move fixer** — mechanically detect which
     early sentence is disproportionately long relative to the rest,
     and if a later short, self-contained sentence carries the "point"
     (per whatever heuristic identifies a conclusion/point sentence),
     swap their positions. Fully inspectable, no LLM call, but a real
     design question: how do you detect confidently which sentence
     *is* "the point" without an LLM? Getting this wrong silently
     produces a worse, not better, opener.
  2. **LLM-driven correction pass, scoped narrowly** — similar shape to
     the new fabrication correction pass (dedicated prompt, forced
     tool call, re-check-and-only-adopt-if-improved). Loses the
     "fully deterministic" property, but the codebase already accepts
     one narrow reordering carve-out for platform_format, so there's
     precedent for this being an acceptable trade when reordering is
     unavoidable. Verification would need the same care the
     fabrication pass's re-check needed — a simple word-count-based
     re-check would need to confirm sentences were only *moved*, not
     rewritten (there's already a function for this shape of check:
     `score_restructure_fidelity`, built for the platform_format
     restructuring case — likely reusable or adaptable here rather
     than building new verification logic from scratch).

## What to test against

No live-confirmed persona exists yet for this dimension specifically
(unlike scaffolding_density, which now has
`dev_tools/personas/scaffolding_test.json`). Whatever gets built here
needs its own deliberately-constructed test case: a calibration voice
with a low (point-first) `conclusion_opener_ratio` baseline, paired
with a render_input that leads with throat-clearing before its actual
point — mirroring exactly how `scaffolding_test.json` was built to
reliably exercise its target dimension.

## Recommended first step next session

Read `score_restructure_fidelity` (voice_engine.py) and the
`platform_format` reordering instructions in `build_correction_prompt`
(prompts.py) closely before designing anything new — both already
solve adjacent halves of this problem (permitting reordering safely,
verifying only reordering happened) and the real design work here may
be adapting them rather than starting from zero.
