/**
 * shared/state_machine.js — the panel's states, in one place, per Full
 * Spec Section 3.4 and Engineering Architecture Section 6.5: "keeping
 * this in one shared module (rather than duplicated per site) is what
 * makes the panel behave identically on LinkedIn and Gmail."
 *
 * Pure data and pure functions only — no chrome.* APIs, no DOM. That's
 * what makes it testable with plain Node, and it's also just what a
 * state machine should be: this module answers "what state comes
 * next", nothing about how that state gets drawn (panel.js) or how
 * the trigger happened (linkedin.js).
 *
 * Loaded as a classic (non-module) script by manifest.json's
 * content_scripts, so it exports itself as one global, matching
 * api_client.js's own approach in the background worker context —
 * same convention on both sides, not two different loading styles.
 *
 * Wrapped in an IIFE: classic scripts loaded together (this file,
 * panel.js, linkedin.js) share ONE global scope, so any top-level
 * const/let name used in more than one file collides — confirmed the
 * hard way (STATES declared here AND, unwrapped, in panel.js's own
 * destructuring, threw "Identifier already declared" the first time
 * this ran for real in a browser). The IIFE keeps every internal name
 * private; only the explicit self.VoicovaStateMachine assignment
 * below is actually shared.
 */
(function () {
const STATES = Object.freeze({
  IDLE: "idle",
  CHECKING: "checking",
  RESULT_GOOD: "result_good",
  RESULT_BORDERLINE: "result_borderline",
  RESULT_FAILED_CONTENT_LOCK: "result_failed_content_lock",
  FIX_IT: "fix_it",
  ACCEPTED: "accepted",
  AUTH_REQUIRED: "auth_required",
  CREDITS_EXHAUSTED: "credits_exhausted",
  ERROR_OFFLINE: "error_offline",
});

// check-draft's response has no content-lock concept at all —
// score_draft_check deliberately never computes it (see
// api/routes/check_draft.py's own docstring: Content Lock only
// applies to a REWRITE, and a Voice Check has none). Only /api/fix's
// response carries content_lock_result. So a plain check can only
// ever justify two outcomes — "good" or "not good" — never the
// distinct Content Lock warning state; that state exists in this
// module (and panel.js can render it) because the *state machine*
// has 11 documented states, but nothing in the current check-draft
// contract can produce the 11th until Content Lock is added to
// score_draft_check itself. Flagged here rather than faking the
// signal from verdict alone, which would have been wrong, not just
// incomplete.
function stateForCheckResult(verdict) {
  return verdict === "good" ? STATES.RESULT_GOOD : STATES.RESULT_BORDERLINE;
}

function stateForErrorCode(errorCode) {
  if (errorCode === "token_revoked" || errorCode === "installation_mismatch") {
    return STATES.AUTH_REQUIRED;
  }
  if (errorCode === "render_cap_exhausted") return STATES.CREDITS_EXHAUSTED;
  return STATES.ERROR_OFFLINE; // engine_error, rate_limited, network failure, anything else
}

// Every result state EXCEPT a good one can transition to fix_it —
// "Result — good" is explicitly "no action required" per Section 3.4's
// own table, so it's deliberately excluded here, not an oversight.
const FIX_IT_ELIGIBLE_STATES = Object.freeze([
  STATES.RESULT_BORDERLINE,
  STATES.RESULT_FAILED_CONTENT_LOCK,
]);

function canOfferFixIt(state) {
  return FIX_IT_ELIGIBLE_STATES.includes(state);
}

const VoicovaStateMachine = { STATES, stateForCheckResult, stateForErrorCode, canOfferFixIt };

if (typeof module !== "undefined" && module.exports) {
  module.exports = VoicovaStateMachine; // Node, for plain-JS unit tests
} else {
  self.VoicovaStateMachine = VoicovaStateMachine; // classic content-script global
}
})();
