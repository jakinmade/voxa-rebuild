# Experiment A — Onboarding Condition Comparison

## Why this exists

Three independent reviews converged on: the current two-scenario-prompt
onboarding is performative (the "observer's paradox" — people write
differently when they know they're being tested). Stimulus-response
tasks (react to something concrete) were proposed as a fix. Review 3
argued this shouldn't be assumed and shipped — it should be tested
head-to-head against the alternatives *before* Phase 2 (fingerprint
expansion) gets built on top of whichever one wins.

This is that test. It gates Phase 2. Nothing in the build sequence
past this point proceeds until it's run.

## What's being compared

Three onboarding conditions, each producing an independent voice
fingerprint from a comparable amount of text, run through the *same*
`render_input` for a given participant:

- **A — Open sample.** Participant pastes ~150-250 words of their own
  existing writing (email, Slack message, doc — whatever they have).
- **B — Current scenario prompts.** The two live-product starters
  (register contrast: professional/defensive vs unfiltered/emotional).
- **C — Stimulus-response (new).** Two concrete messages to react to
  naturally, replacing "tell us how you'd write" with "here's something,
  reply to it." See `stimuli.md` for the exact text.

Each condition's fingerprint runs independently through the *existing,
unmodified* fingerprint → render → Voice Report pipeline
(`voice_engine.py`, `prompts.py`) via `experiment_a.py`, which is a
thin wrapper — no engine logic changes, no fingerprint-scoring changes.
This experiment tests input, not the pipeline.

## Participants

- **15-20 people.** Below 15, no realistic chance of a clean majority
  reading; above 20 is spending recruitment budget the question
  doesn't need.
- Recruit from JA's existing network / prospect list where a real
  writing sample is available (email, LinkedIn post, Slack export) —
  don't recruit blind, since Condition A needs 150-250 words of
  genuine prior writing per participant.
- No screening on writing style, register, or industry beyond having
  a real sample. A skew toward business-professional writing is
  expected and fine — that's the primary use case.

## Procedure per participant

1. Collect Condition A text (their own pasted sample, ~150-250 words).
2. Collect Condition B text (their completions of the two live scenario
   starters, same word-count floor as production: 10 words each).
3. Collect Condition C text (their natural replies to the two stimuli
   in `stimuli.md`).
4. Run `experiment_a.py` for that participant — produces three
   rewrites of the same `render_input`, each from a fingerprint built
   on ONLY that condition's text (conditions are not combined).
5. Present the three outputs to the participant **blind and
   randomised** (labelled X / Y / Z, order randomised per participant —
   the harness does this, not the researcher, to avoid order bias).
6. Ask one question: **"Which of these three sounds most like you?"**
   Forced choice, no ties. Optionally capture one line on why.
7. Record the mapping (kept separate from what the participant sees)
   and log the result.

## What counts as a result

- Tally wins per condition across all participants.
- A condition needs a **clear plurality** (not just "won more often
  than chance for one condition by one vote") to be declared the
  winner — treat anything within 2-3 participants of even at n=15-20
  as inconclusive, not as a tiebreak-and-ship situation.
- If inconclusive: don't average or split the difference in the
  product. Re-run with a larger n on the leading 2 conditions, or
  fall back to B (current) as the safe default while flagging that
  the question is still open.
- Record *why* where participants gave a reason — even a small n of
  qualitative reasons ("B felt like I was performing," "C's reply
  sounded like something I'd actually send") is signal worth keeping
  regardless of the numeric result.

## Data handling

- Participant writing samples and replies are personal, sometimes
  work-related, content. Don't commit raw participant text to the
  repo. `participants/` is gitignored — each participant's file stays
  local or in a private store, not on GitHub.
- Only the tallied results and anonymised reasons go in the final
  write-up.

## What happens after

Once a condition wins clearly:
- Ship that onboarding flow, replacing Screen 3 as needed.
- Phase 2 (behavioural fingerprint expansion) proceeds, built on the
  winning condition's input shape.
- If C wins: reposition per the "provable, not performed" language —
  this becomes part of the pitch, not just a UX change.
