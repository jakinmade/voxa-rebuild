# VOICOVA — baseline ahead of 2.0 (5 Sept 2026)

Marks the state of `jakinmade/voxa-rebuild` at commit `fda9ae6`
(PR #38 merged) as the reference point before starting the VOICOVA 2.0
Chrome-First build (agreed 2 Sept 2026, starting point: Section 3,
the API + identity bridge).

## Test suite

Full suite green: **1156 passed, 4 skipped, 0 failed.**

## Production

Confirmed via Railway MCP tools (not assumed):
- `inspiring-mercy` project, `production` environment: both services
  (`web`, `healthcheck`) reporting **Online**, 0 issues, 0 recent
  failures.
- Latest live deployment (`4c431610`) is built from commit `fda9ae6`
  — production is running the exact code on `main`, no stale-deploy
  gap.

## Known open items carried into 2.0

### 1. Content fabrication in generation — open, positioning changed

Three separate guard placements (render-prompt branch fix, dedicated
correction pass at two pipeline points, general-correction-prompt
guard) were tried and live-tested this session. Each measurably
softened fabrication; none eliminated it. Root blocker precisely
diagnosed: the fabrication-detector's diff collapses to one
whole-document block once the general correction pass paraphrases
heavily, so even a genuinely successful fix can't always be verified
and trusted (confirmed directly — a fix that worked was rejected by
the strict-improvement gate because the verifier couldn't see it).

A same-day competitor benchmark (`docs/competitor-fabrication-
benchmark-protocol.md` / `-results.md` / `VOICOVA_Competitor_
Fabrication_Benchmark.docx`) found at least one direct competitor
(VoiceMoat) exhibits the same fabrication failure with zero gate or
warning; ContentIn's free tier didn't fabricate but couldn't test the
real paywalled engine; a scorer-type tool (standing in for River)
confirmed that category of tool checks tone/rhythm only, never
factual fidelity. VOICOVA's `has_content_integrity_hard_fail` gate
looks like a genuine differentiator on this evidence, not a gap.

**Decision for 2.0, based on this evidence:** current gated behavior
(confirm-before-ship on suspect renders) is the accepted position
going into 2.0 — not a bug to hide, a stated product strength ("tells
you when it isn't sure"). No further fabrication-elimination work is
required before starting 2.0. The deeper fix (a semantic/word-overlap
re-verification to replace the sentence-count-based one that hit the
whole-document-collapse blind spot) remains open, tracked, and
deliberately NOT attempted again without a dedicated design session —
three prompt-based attempts this session each failed to eliminate it,
so a fourth quick attempt is not a good use of time.

### 2. Scaffolding-density auto-fixer — closed

Confirmed working correctly on real generation this session (it had
never fired live before). A genuine reintroduction bug it exposed
(the general correction pass undoing its work) was found and fixed,
live-confirmed twice. No outstanding work.

### 3. `conclusion_opener_ratio` has no auto-fixer — deferred, design notes only

Not built. Correcting it needs sentence reordering — a materially
riskier transformation than every other fixer in this codebase (all
deletion-only), and this repo has direct history of a reordering-
adjacent fix breaking a real render before. Design notes captured in
`docs/conclusion-opener-ratio-fixer-design-notes.md`: what the
dimension measures, why it's harder, two candidate approaches, and a
recommended first step (read `score_restructure_fidelity` and the
`platform_format` reordering instructions first).

### 4. Post-deploy smoke test CI — found broken, needs investigation

**New finding, this baselining pass:** `.github/workflows/smoke-
test.yml` (built 2 Sept 2026, Foundation Hardening item 4) has failed
on **every one of 28 runs** since it was created — it has never once
passed. This was not previously known; Foundation Hardening's item 4
was recorded as complete based on the workflow existing and running,
not on it passing.

Not fully diagnosed — this sandbox can't reach voicova.com or
GitHub Actions' log-storage host to confirm the exact cause — but two
concrete leads were found via Railway/GitHub tooling and are worth
checking first next session, before guessing further:

- **WebSocket lead:** Railway HTTP logs show only one `/_stcore/
  stream` request logged in the last 11 days (traffic is very low),
  and it returned status `0` rather than a normal `101 Switching
  Protocols`. Suggestive of exactly what `check_websocket` tests for,
  but with only one data point ever logged there's no historical
  baseline to confirm `0` isn't just how Railway logs a successful
  upgrade. Needs a live `curl`/websocket test or a fresh smoke-test
  run compared against Railway logs at the same timestamp.
- **Stripe lead:** `gh secret list` confirms `STRIPE_API_KEY` is
  configured (set 2 Sept 2026) but secret values aren't readable —
  can't confirm whether it's VOICOVA's own current live key or a
  stale one left over from before the VOICOVA/CLEARANCE Stripe
  account split (see that split's own history, 1 Sept 2026).

Fastest resolution: run `python scripts/smoke_test.py` against
production locally — it prints exactly which of the three checks
fails and why, definitively, in seconds.

### Housekeeping still open

- Rotate `ANTHROPIC_API_KEY` in Railway again — the key used for this
  session's live testing was pasted into chat and should be treated
  as exposed, same as the reminder at the start of this session.
- The 24-item benchmark corpus (flagged unfinished 29 Aug 2026) is
  still unfinished — unrelated to this session's work.
