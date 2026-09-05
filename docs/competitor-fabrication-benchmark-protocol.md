# Competitor fabrication benchmark — protocol

Captured 5 Sept 2026. Purpose: find out whether the content-fabrication
behavior confirmed live in VOICOVA today (see PR #34, #35 history) is
a VOICOVA-specific bug or an industry-wide unsolved limitation of
LLM-based voice rewriting — and if the latter, what product pattern
"great" competitors use to handle it (avoid the feature entirely?
warn the user? gate behind confirmation, same as VOICOVA already
does?).

No public data exists on this for any competitor — it's a subtle
failure mode nobody benchmarks publicly. This has to be run live,
by hand or via a browsing agent (Claude for Chrome).

## Candidates identified

- **VoiceMoat** (voicemoat.com) — closest direct analog. Own "Voice
  DNA" terminology, real-time voice-match score, an assistant
  ("Auden") that suggests rewrites from a seed. Free tier + Pro
  trial available. Twitter/X-focused.
- **ContentIn** — LinkedIn AI ghostwriter, drafts from a trained voice
  profile. 30-day refund, no free plan. Does not show a per-draft
  voice signal (their own limitation, per third-party review) so
  fabrication would be even harder for a USER to catch there than
  in VOICOVA, which does show a signal (Content Lock / hard-fail
  gate) — worth confirming directly.
- **River's Client Voice Match Analyzer** (rivereditor.com) — a
  *scorer*, not a rewriter. Different test: feed it a draft
  containing a KNOWN fabrication (reuse VOICOVA's own fabricated
  output) alongside the real reference voice samples, and see
  whether their scoring flags the fabricated claim at all, or only
  scores tone/rhythm/vocabulary match (their own site copy suggests
  the latter — "sentence length variance, vocabulary overlap,
  formality level, metaphor patterns, emotional tone" — notably
  no mention of factual/claim fidelity).

## The exact test inputs

Reuse the two inputs already proven to trigger fabrication in
VOICOVA, live-confirmed multiple times this session
(`dev_tools/personas/terse_engineer.json`,
`dev_tools/personas/data_analyst.json`):

**Input A:**
> It is worth noting that the deployment pipeline experienced a robust
> failure due to a configuration oversight. This underscores the
> importance of a seamless CI/CD process moving forward, and the team
> leveraged a swift resolution.

**Input B:**
> It is worth noting that customer attrition has demonstrated a
> notable upward trajectory this quarter, which underscores a
> potentially transformative shift in the underlying market
> landscape requiring further robust analysis going forward.

Both are deliberately vague/abstract AI-ish prose with no specific
claim to compress toward — exactly the shape that made VOICOVA invent
a directive or a personal commitment that was never actually said.

## Calibration setup per tool

Each tool needs *some* voice sample to build a profile from before
rewriting. Use the same terse, low-scaffolding calibration text
already in the persona files, so the target voice is held constant
across every tool tested:

> Build broke on merge. Root cause was the env var rename in the last
> PR, nobody updated the CI config. Fixed now. Deploy blocked until QA
> signs off. Will not ship on a Friday again, learned that one twice
> now.

Plus, if the tool wants more samples:
> Not what was asked for. Doesn't match spec. Needs a rewrite, not a
> patch. Will explain what's missing.
>
> I keep systems from breaking, and fix them fast when they do anyway.
>
> Deploy's delayed a week. QA needs more time. Telling the team now,
> no sugar coating it.
>
> Review comments with no reason attached. Just say what's wrong.
> Every single time.

## Steps

1. Sign up for VoiceMoat's free tier (or trial). Build a voice profile
   from the calibration text above.
2. Feed Input A through its rewrite/Auden-suggestion feature. Record
   the output verbatim.
3. Feed Input B the same way. Record the output verbatim.
4. Repeat steps 1–3 for ContentIn.
5. For River: don't feed the vague inputs — instead paste VOICOVA's
   own already-confirmed-fabricated output (e.g. "Audit your CI/CD
   config validation so the process holds up better than this.")
   alongside the calibration text as the "reference voice sample",
   and see whether River's analyzer flags the invented directive at
   all, or only reports on tone/rhythm match.

## Scoring — apply the same test VOICOVA's own harness applies

For each rewrite output, ask exactly one question, same as
`_check_uncorrected_insertions` / `get_fabricated_blocks` in this
codebase: **does any specific claim, directive, or detail appear in
the output that has no basis anywhere in the input?** Not "does it
sound generic" — specifically, is there an invented specific.

Record, per tool, per input:
- Fabricated: yes/no
- If yes, quote the invented specific
- Did the tool's own UI flag/warn/gate this in any way, or ship it
  silently with a confident score?

## What a result would tell us

- **If competitors also fabricate, silently, with no gate at all:**
  VOICOVA's `has_content_integrity_hard_fail` gate — imperfect as the
  underlying generation still is — is already ahead of the field on
  this specific dimension. That reframes the finish line: the product
  differentiator isn't "zero fabrication" (probably not achievable
  with any current hosted LLM at this kind of compression task,
  confirmed independently multiple times this session), it's "honest
  about when it might have fabricated," which VOICOVA already does.
- **If a competitor avoids the failure entirely:** worth learning
  exactly how. Two plausible mechanisms based on their public
  descriptions: (a) VoiceMoat generates original posts from a seed
  topic rather than rewriting existing prose, which sidesteps the
  specific "compress vague input" trigger entirely; (b) a tool might
  refuse or flag when input is too vague to safely compress, which
  maps to the "special-case vague input differently" option
  discussed this session and not yet tried.
- **If River's scorer doesn't catch a known-fabricated claim:** that's
  independent confirmation the whole competitive category treats
  voice/tone match and factual fidelity as separate problems — which
  matches what VOICOVA's own architecture already assumes
  (score_render_delta for voice, `has_content_integrity_hard_fail`
  for integrity, deliberately two different checks).

## Execution

Best run via Claude for Chrome (a browsing agent that can sign up for
trials and operate the actual product UIs) rather than manually, given
the number of tool/input combinations. This protocol is written so it
can be handed to that agent directly as a task list.
