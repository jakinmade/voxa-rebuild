"""
VOICOVA scoring policy — the "policy version" half of a model-version /
policy-version split (same idea used in versioned credit-decisioning
systems: a decision is reproducible from *model version + policy
version + inputs*, and a threshold can be recalibrated same-day
without a retrain or a code change to the scoring logic itself).

WHY THIS FILE EXISTS
Before this, every threshold governing a render's Confidence/Risk
verdict was an inline magic number scattered across voice_engine.py -
0.20 and 0.40 in one function, 70 and 85 in another, 0.25 in
prompts.py. Nothing was wrong with any individual number, but there
was no single place to look to know what "good" currently means, and
no version stamp on any of it - if a threshold changed, there was no
record of which renders were scored under the old value vs the new
one. That's the actual governance gap this closes: not that the
numbers were miscalibrated (no evidence of that yet - see CHANGELOG),
but that the numbers weren't auditable as a set.

WHAT THIS IS NOT
Not a dynamic rule engine, not business-user-editable, not hot-
swappable at runtime. VOICOVA is built and calibrated by one person;
a rule-authoring UI or a rules DSL would be solving a team-scale
problem this product doesn't have. This is deliberately just a
versioned Python module - the smallest thing that gives real
governance value (one place to look, one version stamp per render,
one changelog) without the complexity of a system built for a
scenario that isn't this one.

HOW TO CHANGE A THRESHOLD
1. Change the constant below.
2. Bump SCORING_RULES_VERSION (semver: patch for a number tweak,
   minor for a new rule/dimension, major for a change that could flip
   verdicts on renders that were already scored under the old policy).
3. Add a CHANGELOG entry: version, date, what changed, why, what
   render/incident (if any) prompted it. Every entry so far in this
   file was prompted by a specific, confirmed-live render - keep that
   discipline. A threshold changed on a hunch, with no entry
   explaining why, is exactly the opacity this file exists to prevent.
4. Every render log line already carries this version (see
   scoring_rules_version() call site in app.py's _run_render) - no
   further wiring needed for a threshold-only change.

CHANGELOG
1.4.0 (17 Aug 2026) - Added DELTA_BAND_MIN_ABS_DIFF, an absolute-
    difference floor per dimension in score_render_delta, alongside
    the existing percentage bands. Prompted by a confirmed-live render
    (CLEARANCE outreach to Scott) that scored High risk almost
    entirely off pct_diff blowing up near a baseline close to zero -
    hedge_density moved 0.5 -> 0.0 (half a hedge word per 100 words)
    and registered as pct_diff=1.0 (100%, MISSED), the same failure
    mode as any percentage-of-a-near-zero-denominator calculation.
    Hits hardest exactly on the writers this product is built to
    preserve - direct, low-hedging, low-first-person - since their
    baselines sit closest to zero on the dimensions being measured. A
    verdict now needs BOTH pct_diff over the HIT band AND an absolute
    move past the floor to register as CLOSE/MISSED; a trivial
    absolute move can no longer be inflated into a false drift by a
    small denominator. Semver: minor, not patch - this is a new rule
    added alongside the existing bands, not a retuned number within
    the same rule.
1.3.0 (16 Aug 2026) - Added PERSONAL_EMAIL_DOMAINS, consumed by the
    new firm_signal.py. Governs which domains extract_domain() refuses
    to treat as a firm signal - not a scoring threshold, but the same
    "one place to look, versioned, changelogged" discipline applies:
    this list directly determines what counts as noise vs signal in
    the domain-clustering data, so a change to it is a real behaviour
    change worth a version bump and a reason, same as any threshold
    above. Starting list is the handful of large, unambiguous consumer
    webmail providers - deliberately not exhaustive. Extending it
    (a smaller regional provider, a new consumer webmail entrant)
    is a one-line addition here, not a code change in firm_signal.py.
1.2.0 (16 Aug 2026) - Added REVIEW_REQUIRED_RISK_LEVELS. Not a scoring
    threshold - a business rule consumed by review_gate.py to decide
    which risk verdicts require an explicit human confirmation before
    the rewritten text is shown, rather than a Streamlit text_area
    that's simply always visible the moment a render completes. Exists
    because FINRA's existing guidance on AI-assisted communications
    (Rule 3110/2210/4511) identifies undocumented human-in-the-loop
    review as the most common small-firm compliance gap - this makes
    the review step a real, structural gate instead of something a
    person could skip by scrolling past it. Deliberately still fully
    anonymous: review_gate.py logs that a gated render was reviewed
    and confirmed, with the same risk/semantic_match/scoring_rules_
    version shape as render_events.py, no device_id, no identity. A
    persisted, per-person compliance record (the kind an actual
    supervisor could review by name) is a separate, larger decision -
    it needs an account/identity model this product has explicitly
    not built (see persistence.py's own docstring) - and isn't made
    here. This just builds the gate and the anonymous evidence that
    the gate exists and gets used; whether to attach identity to it is
    for JA to decide deliberately, not something to default into.
1.1.0 (16 Aug 2026) - Added compute_risk_reason() (voice_engine.py),
    logged alongside this version stamp in app.py's render_complete
    log line. Not a threshold change - v1.0.0's own honest gap was
    that its bands (RISK_HIGH/MEDIUM_SEMANTIC_MATCH_BELOW especially)
    were extracted unchanged with no live evidence isolating whether
    they're well-calibrated, because every real render checked that
    day hit a hard-fail (AI tell, dropped entity, sentence growth)
    before the aggregate bands ever got to be the deciding factor.
    This closes that gap the right way: instrumentation before
    recalibration, not a number changed on a hunch. Once render logs
    accumulate enough aggregate_band-driven verdicts, the bands can
    be recalibrated against real data instead of guessed at.
1.0.0 (16 Aug 2026) - Initial extraction. Values unchanged from their
    prior inline locations - this version establishes the baseline,
    it does not recalibrate anything. Prompted by a working session
    that fixed four separate render-quality bugs in one day (lexical
    fidelity, an over-broad AI-tell pattern, a fabricated sentence,
    a hallucinated salutation name) and needed a place to point at
    for "what does the scoring actually check, as of today."

SCOPE BOUNDARY — what's deliberately NOT in this file
_score_ai_signal's internal weights (voice_engine.py) score how
AI-contaminated the INPUT looks, before a render even happens - a
different concern from this file's scope (thresholds governing the
OUTPUT's Confidence/Risk verdict, post-render). AI_CONTAMINATION_
PATH_THRESHOLD lives here because it's the boundary where that input
score gets CONSUMED by a downstream decision (which prompt path to
use); the weights that produce the score in the first place stay
where they are. If input-classification calibration ever needs the
same audit-trail treatment this file gives output-verdict calibration,
that's a parallel file, not an expansion of this one - the two are
different governance questions (is the input AI-written vs was this
particular render trustworthy) and conflating them would make this
file's "every threshold governing a render's Confidence/Risk verdict"
claim inaccurate rather than more complete.
"""

SCORING_RULES_VERSION = "1.4.0"


# ---------------------------------------------------------------------------
# score_render_delta — per-dimension HIT/CLOSE/MISSED bands
#
# pct_diff is the output metric's percentage distance from the
# person's own baseline for that dimension (hedge_density,
# sentence_length_sd, first_person_ratio, directive_ratio). Applied
# identically to all four dimensions - no dimension currently has a
# tighter or looser band than the others. That uniformity is itself a
# calibration choice, not an oversight: no evidence yet that any one
# dimension needs a different tolerance than the others. If that
# changes, this is where a per-dimension override would go.
# ---------------------------------------------------------------------------
DELTA_BAND_HIT_MAX_PCT = 0.20    # within 20% of baseline -> HIT
DELTA_BAND_CLOSE_MAX_PCT = 0.40  # within 40% of baseline -> CLOSE, else MISSED

# pct_diff divides by max(baseline, 0.01) - for a person whose baseline
# on a dimension is already near zero (a direct writer with low
# hedge_density or low first_person_ratio to begin with, exactly the
# style this product is built to preserve), that denominator is tiny,
# so a trivial absolute move reads as a huge relative one. Confirmed
# live: a render with hedge_density baseline ~0.5/100 words moving to
# 0.0 - an absolute change of half a hedge word per 100 words - scored
# pct_diff=1.0 (100%, MISSED), the single largest driver of a High-risk
# verdict on a render that read as a faithful, on-voice rewrite.
# DELTA_BAND_MIN_ABS_DIFF is the floor below which a MISSED can't fire
# regardless of pct_diff: if the absolute move on a dimension is
# smaller than a person could plausibly notice, no percentage math
# should be able to call it a drift. Units match each dimension's own
# native scale (hedge_density and sentence_length_sd are not 0-1
# ratios, so a single shared floor across all four would either be too
# loose for the ratio dimensions or too tight for the other two).
# ---------------------------------------------------------------------------
DELTA_BAND_MIN_ABS_DIFF = {
    "hedge_density": 1.0,        # hedge words per 100 words
    "sentence_length_sd": 2.0,   # words
    "first_person_ratio": 0.10,  # proportion of sentences
    "directive_ratio": 0.10,     # proportion of sentences
}


# ---------------------------------------------------------------------------
# compute_risk — overall Low/Medium/High verdict from aggregate scores
#
# These only apply once none of the hard-fail checks (AI tell present,
# attribution swap, dropped entity, sentence growth) have already
# forced High - see compute_risk's own docstring for why those are
# binary, not banded: a single high-consequence error isn't something
# a percentage threshold should be able to average away.
# ---------------------------------------------------------------------------
RISK_HIGH_SEMANTIC_MATCH_BELOW = 70
RISK_HIGH_MISSED_DIMENSIONS_AT_LEAST = 3
RISK_MEDIUM_SEMANTIC_MATCH_BELOW = 85
RISK_MEDIUM_MISSED_DIMENSIONS_AT_LEAST = 1


# ---------------------------------------------------------------------------
# review_gate — which risk verdicts require an explicit human
# confirmation before the rewritten text is revealed
#
# Consumed by review_gate.py, not by anything in this file - kept here
# rather than in that module because it's a policy decision with the
# same shape as every other constant above (versioned, changelogged,
# one place to look), not implementation detail. Low is excluded
# deliberately: gating every render regardless of risk would train
# people to click through the confirmation without reading it, the
# same failure mode as an over-triggered warning dialog anywhere else.
# Medium and High are exactly the two verdicts compute_risk can
# produce when something concrete already didn't match the baseline or
# the source text - see compute_risk's own docstring for what drives
# each level.
# ---------------------------------------------------------------------------
REVIEW_REQUIRED_RISK_LEVELS = {"Medium", "High"}


# ---------------------------------------------------------------------------
# firm_signal — which domains never count as a firm signal
#
# Consumed by firm_signal.extract_domain(). Multiple people signing up
# from the same large consumer webmail provider isn't evidence of a
# firm relationship - it's the single most common domain in any
# dataset, by construction. Excluding these keeps the domain-
# clustering data meaningful (a repeated domain actually means "the
# same company") rather than dominated by gmail.com showing up
# constantly. Lowercase, exact match only - no wildcard/subdomain
# logic, since none of these providers issue user accounts on
# subdomains in a way that would need it.
# ---------------------------------------------------------------------------
PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "protonmail.com", "live.com", "msn.com", "me.com",
    "mail.com", "gmx.com", "yandex.com",
}


# ---------------------------------------------------------------------------
# score_semantic_drift — how entity preservation and content overlap
# combine into the headline semantic_match number
#
# Entity preservation weighted higher than content-word overlap:
# losing a name or a number is treated as a bigger deal than losing
# some general vocabulary overlap, even before the dropped_entities
# hard-fail (which fires independently of this weighting - see
# compute_risk). This weighting affects the headline number Confidence
# is partly built from; the hard-fail is the actual backstop.
# ---------------------------------------------------------------------------
SEMANTIC_MATCH_ENTITY_WEIGHT = 0.6
SEMANTIC_MATCH_CONTENT_WEIGHT = 0.4


# ---------------------------------------------------------------------------
# _build_system_prompt — AI-contamination path selector
#
# ai_score >= this value routes a render through the "AI-contaminated"
# prompt path (aggressive stripping + restoration targets) instead of
# the "clean human input" path (preservation-first, lexical fidelity
# enforced - see rule 9/10 in base_rules). Getting this wrong in
# either direction has a real cost: too low, and a person's own
# genuine writing gets the aggressive AI-stripping treatment meant for
# actually-AI-generated input; too high, and real AI slop sails
# through the gentler, preservation-first path uncorrected.
# ---------------------------------------------------------------------------
AI_CONTAMINATION_PATH_THRESHOLD = 0.25


def scoring_rules_version() -> str:
    """The version stamp every render log line should carry alongside
    its actual scores, so a render is reproducible later from
    (scoring_rules_version, input_text, baseline) the same way a
    credit decision is reproducible from (model_version, policy_
    version, application_data)."""
    return SCORING_RULES_VERSION
