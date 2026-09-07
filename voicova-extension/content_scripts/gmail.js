/**
 * content_scripts/gmail.js — Gmail's half of the surface-agnostic
 * design shared/panel.js and shared/state_machine.js were already
 * built for (see those files' own comments: "makes the panel behave
 * identically on LinkedIn and Gmail"). Backend already accepts
 * surface="gmail" (api/schemas/check_draft.py, api/schemas/fix.py) —
 * this file and the manifest.json/service_worker.js wiring below were
 * the only missing pieces.
 *
 * Structured identically to linkedin.js (same IIFE-in-shared-scope
 * convention, same _findComposers/_getDraftText/_replaceEditorText/
 * _injectControl shape) so a future reader of one understands the
 * other immediately. Two real differences from LinkedIn, both forced
 * by Gmail's DOM rather than chosen:
 *
 * 1. TIERED SELECTOR FALLBACK for the text box. LinkedIn has exactly
 *    one semantic, non-obfuscated hook (componentkey="..."). Gmail
 *    does not — its accessibility attributes (role, aria-label,
 *    g_editable) are the stable layer, obfuscated classes are not,
 *    and even the accessible-attribute combination has shifted before
 *    (aria-label="Message Body" was dropped from some compose
 *    surfaces; g_editable="true" is the attribute that has held up in
 *    current real-world extension maintenance). A fallback list, tried
 *    in order, is the documented mitigation for exactly this
 *    volatility — not a guess unique to this file.
 *
 * 2. NO COMPOSER CONTAINER. _findComposers here returns text boxes
 *    directly, not an enclosing dialog. Gmail's compose window is not
 *    reliably exposed as role="dialog" (unlike LinkedIn's
 *    dialog[data-testid="dialog"]), so walking up to one would be the
 *    same class of fragile assumption this file is trying to avoid.
 *    The text box itself is therefore both the attachment key and the
 *    insertion anchor (control is inserted as its previous sibling via
 *    insertAdjacentElement, never as a child — writing into the
 *    contenteditable body itself would corrupt the message the same
 *    way a raw innerText write would, see _replaceEditorText below).
 *
 * LIVE-DOM VERIFICATION STATUS (7 Sept 2026): selectors are sourced
 * from current real-world Gmail extension maintenance (a maintainer's
 * own selector-fallback writeup, dated within the last several months,
 * plus a same-week compatibility-fix commit moving off aria-label
 * entirely) — not yet hand-verified against a live Gmail compose
 * window from this session. This is the same category of gap
 * linkedin.js had before its own 6 Sept live-DOM correction, named
 * explicitly rather than assumed away.
 */
(function () {
const CONFIG = {
  // Tried in order; first match wins. Primary matches current
  // real-world extension-maintenance guidance; fallbacks cover the
  // two attribute shapes Gmail has actually shipped.
  textAreaSelectors: [
    '[role="textbox"][g_editable="true"]',
    '[aria-label="Message Body"][role="textbox"]',
    'div[contenteditable="true"][g_editable="true"]',
  ],
  controlContainerId: "voicova-control-container",
};

const _attachedTextAreas = new WeakSet();

function _queryTextAreas() {
  for (const selector of CONFIG.textAreaSelectors) {
    const matches = document.querySelectorAll(selector);
    if (matches.length > 0) return Array.from(matches);
  }
  return [];
}

// Named _findComposers (not _findTextAreas) to keep this file
// structurally parallel to linkedin.js even though, per this file's
// own header comment, there is no separate composer container here —
// the text box IS the composer unit for Gmail.
function _findComposers() {
  return _queryTextAreas();
}

function _getDraftText(composer) {
  return composer ? composer.innerText.trim() : "";
}

// Identical technique to linkedin.js's _replaceEditorText, and for
// the identical reason: Gmail's compose body is also a contenteditable
// the extension has no internal-model access to, so the write must go
// through native execCommand("insertText", ...) to fire the same
// beforeinput/input events a real keystroke would, with a DOM readback
// to verify the write actually landed rather than trusting
// execCommand's own return value. See linkedin.js's own comment for
// the full rationale — duplicated here rather than shared, matching
// this codebase's existing convention of each content script owning
// its own copy (e.g. compare the two files directly rather than
// tracing a shared import).
function _replaceEditorText(editableEl, newText) {
  editableEl.focus();

  const range = document.createRange();
  range.selectNodeContents(editableEl);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);

  document.execCommand("insertText", false, newText);

  return editableEl.innerText.trim() === newText.trim();
}

function _injectControl(textArea) {
  if (_attachedTextAreas.has(textArea)) return;
  _attachedTextAreas.add(textArea);

  const container = document.createElement("div");
  container.id = CONFIG.controlContainerId;
  // Inserted as a sibling BEFORE the text box, never inside it —
  // appending into the contenteditable body itself would inject the
  // control's own markup into the user's draft the same way a raw
  // innerText write would corrupt it (see _replaceEditorText above).
  textArea.insertAdjacentElement("beforebegin", container);

  const panel = VoicovaPanel.mount(container, {
    onCheck: () => {
      const text = _getDraftText(textArea);
      return chrome.runtime.sendMessage({
        type: "CHECK_DRAFT",
        text,
        surface: "gmail",
      });
    },
    onFix: () => {
      const text = _getDraftText(textArea);
      return chrome.runtime.sendMessage({
        type: "FIX_DRAFT",
        text,
        surface: "gmail",
        // Same reasoning as linkedin.js: generated here so a genuinely
        // duplicated message carries the same idempotency key both
        // times, not regenerated downstream where a duplicate would
        // already be two different keys.
        idempotencyKey: crypto.randomUUID(),
      });
    },
    onAcceptFix: (correctedText) => {
      return _replaceEditorText(textArea, correctedText);
    },
  });

  textArea.addEventListener("input", () => panel.invalidateResult());
}

// Gmail's DOM churns constantly (compose windows open/close/minimize,
// inline reply boxes mount/unmount) — same MutationObserver approach
// as linkedin.js, for the same reason: a one-time querySelector at
// load cannot see composers created later.
const observer = new MutationObserver(() => {
  _findComposers().forEach(_injectControl);
});
observer.observe(document.body, { childList: true, subtree: true });
_findComposers().forEach(_injectControl); // in case a composer is already open on script load

chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "TOKEN_INVALIDATED") VoicovaPanel.notifyAuthRequired();
});

// Exposed for automated testing only, same convention as
// VoicovaLinkedInInternal in linkedin.js.
self.VoicovaGmailInternal = { _replaceEditorText, _getDraftText, _queryTextAreas };
})();
