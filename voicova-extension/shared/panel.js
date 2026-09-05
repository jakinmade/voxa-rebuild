/**
 * shared/panel.js — renders VoicovaStateMachine's states. One shared
 * module so LinkedIn and (later) Gmail panels behave identically
 * (Architecture 6.5's "cockpit, not dashboard" principle) — no
 * framework, just direct DOM updates per state, which is all a small,
 * docked control needs.
 *
 * mount(container, {onCheck, onFix, onAcceptFix}) returns
 * { invalidateResult() } — the content script's own hook for Section
 * 3.4's "editing the draft after a result invalidates the prior
 * result" rule. onCheck/onFix return the Promise a chrome.runtime
 * message send already yields (linkedin.js), so this module never
 * imports api_client.js or touches a token — matches every other
 * content-script-adjacent piece of this extension.
 *
 * Wrapped in an IIFE for the same reason state_machine.js is — see
 * that file's own comment on the real collision this prevents.
 */
(function () {
const { STATES, stateForCheckResult, stateForErrorCode, canOfferFixIt } = VoicovaStateMachine;

// Every mounted panel, so a Disconnect broadcast (Section 6.3) can
// update all of them — LinkedIn only ever shows one composer at a
// time in practice, but nothing here assumes that.
const _instances = [];

function mount(container, { onCheck, onFix, onAcceptFix }) {
  let state = STATES.IDLE;
  let lastResult = null;

  function render() {
    container.setAttribute("aria-live", "polite");
    container.innerHTML = "";
    const build = _RENDERERS[state] || _RENDERERS[STATES.IDLE];
    container.appendChild(build());
  }

  function setState(next, result) {
    state = next;
    lastResult = result ?? lastResult;
    render();
  }

  async function handleCheck() {
    setState(STATES.CHECKING);
    const response = await onCheck();
    if (!response.ok) {
      setState(stateForErrorCode(response.errorCode));
      return;
    }
    setState(stateForCheckResult(response.data.verdict), response.data);
  }

  async function handleFix() {
    setState(STATES.CHECKING);
    const response = await onFix();
    if (!response.ok) {
      setState(stateForErrorCode(response.errorCode));
      return;
    }
    setState(STATES.FIX_IT, response.data);
  }

  function handleAccept() {
    // onAcceptFix now reports whether the DOM edit actually verified
    // (see linkedin.js's _replaceEditorText) — showing "Applied."
    // unconditionally would tell the user their post was corrected
    // when the editor's own model might not have changed at all.
    const applied = onAcceptFix(lastResult.corrected_text);
    if (!applied) {
      setState(STATES.ERROR_OFFLINE);
      return;
    }
    setState(STATES.ACCEPTED);
    setTimeout(() => setState(STATES.IDLE), 1500); // brief confirmation, then back to idle (Section 3.4)
  }

  function handleDiscard() {
    setState(STATES.IDLE);
  }

  const _RESULT_LABEL = { good: "Sounds like you", borderline: "Worth a look", failed: "Needs your eyes" };

  const _RENDERERS = {
    [STATES.IDLE]: () => {
      const button = document.createElement("button");
      button.className = "voicova-btn voicova-btn-idle";
      button.type = "button";
      button.setAttribute("aria-label", "Check my voice");
      button.textContent = "Check my voice";
      button.addEventListener("click", handleCheck);
      return button;
    },

    [STATES.CHECKING]: () => {
      const el = document.createElement("div");
      el.className = "voicova-btn voicova-checking";
      el.setAttribute("role", "status");
      el.textContent = "Checking…";
      return el;
    },

    [STATES.RESULT_GOOD]: () => _resultPanel("good"),
    [STATES.RESULT_BORDERLINE]: () => _resultPanel("borderline"),
    [STATES.RESULT_FAILED_CONTENT_LOCK]: () => _resultPanel("failed"),

    [STATES.FIX_IT]: () => {
      const el = document.createElement("div");
      el.className = "voicova-panel voicova-fixit";
      const p = document.createElement("p");
      p.className = "voicova-fixit-text";
      p.textContent = lastResult.corrected_text;
      el.appendChild(p);
      el.appendChild(_actionRow([
        ["Accept", handleAccept, "voicova-btn-primary"],
        ["Discard", handleDiscard, "voicova-btn-secondary"],
      ]));
      return el;
    },

    [STATES.ACCEPTED]: () => {
      const el = document.createElement("div");
      el.className = "voicova-panel voicova-accepted";
      el.setAttribute("role", "status");
      el.textContent = "Applied.";
      return el;
    },

    [STATES.AUTH_REQUIRED]: () => {
      const el = document.createElement("div");
      el.className = "voicova-panel voicova-auth";
      el.textContent = "Reconnect this extension on voicova.com.";
      return el;
    },

    [STATES.CREDITS_EXHAUSTED]: () => {
      const el = document.createElement("div");
      el.className = "voicova-panel voicova-credits";
      const p = document.createElement("p");
      p.textContent = "You've used all your free renders.";
      const link = document.createElement("a");
      link.href = "https://voicova.com/upgrade";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Upgrade to Solo";
      el.append(p, link);
      return el;
    },

    [STATES.ERROR_OFFLINE]: () => {
      const el = document.createElement("div");
      el.className = "voicova-panel voicova-error";
      const p = document.createElement("p");
      p.textContent = "That didn't go through.";
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "voicova-btn-secondary";
      retry.textContent = "Retry";
      retry.addEventListener("click", handleCheck);
      el.append(p, retry);
      return el;
    },
  };

  function _resultPanel(tier) {
    const el = document.createElement("div");
    el.className = `voicova-panel voicova-result voicova-result-${tier}`;
    const headline = document.createElement("div");
    headline.className = "voicova-result-headline";
    headline.textContent = `${lastResult.overall_match}% — ${_RESULT_LABEL[tier]}`;
    el.appendChild(headline);

    const flagged = Object.entries(lastResult.dimension_explanations || {}).slice(0, 3);
    if (flagged.length) {
      const list = document.createElement("ul");
      list.className = "voicova-result-flags";
      for (const [, explanation] of flagged) {
        const li = document.createElement("li");
        li.textContent = explanation;
        list.appendChild(li);
      }
      el.appendChild(list);
    }

    if (canOfferFixIt(state)) {
      el.appendChild(_actionRow([["Fix it", handleFix, "voicova-btn-primary"]]));
    }
    return el;
  }

  function _actionRow(buttons) {
    const row = document.createElement("div");
    row.className = "voicova-action-row";
    for (const [label, handler, className] of buttons) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = label;
      button.addEventListener("click", handler);
      row.appendChild(button);
    }
    return row;
  }

  // Escape collapses back to idle (Section 3.4's "Panel dismissal"
  // state) — keyboard parity with a close/dismiss action, not just a
  // mouse one.
  container.addEventListener("keydown", (event) => {
    if (event.key === "Escape") setState(STATES.IDLE);
  });

  render();

  const instance = {
    invalidateResult() {
      // Only a shown result (or its fix-it/accepted follow-on) goes
      // stale on an edit — checking/idle/auth/error states are
      // untouched, since there's no result to invalidate yet.
      if ([STATES.RESULT_GOOD, STATES.RESULT_BORDERLINE, STATES.RESULT_FAILED_CONTENT_LOCK, STATES.FIX_IT].includes(state)) {
        setState(STATES.IDLE);
      }
    },
    notifyAuthRequired() {
      setState(STATES.AUTH_REQUIRED);
    },
  };
  _instances.push(instance);
  return instance;
}

function notifyAuthRequired() {
  _instances.forEach((instance) => instance.notifyAuthRequired());
}

self.VoicovaPanel = { mount, notifyAuthRequired };
})();
