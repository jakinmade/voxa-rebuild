/**
 * content_scripts/linkedin.js — finds the composer, injects the
 * control, reads draft text on demand, hands off to panel.js for all
 * rendering (Architecture Section 6.2: "no scoring or business logic
 * lives in the content script"). Depends on shared/state_machine.js
 * and shared/panel.js, both loaded before this file by manifest.json.
 *
 * Wrapped in an IIFE for the same reason state_machine.js is (see
 * that file's own comment) — this loads alongside state_machine.js
 * and panel.js in one shared content-script scope.
 */
(function () {
// Selectors isolated in one object, not scattered through the logic
// below (Architecture Section 6.2's explicit maintenance requirement:
// "the known maintenance risk... is that LinkedIn/Gmail DOM changes
// break these; isolating them keeps a fix to a one-file change").
//
// Best-effort current values, not hand-verified against a live
// session (this sandbox has no LinkedIn login) — sourced from public
// research on LinkedIn's own early-2026 composer migration (Quill,
// replacing ProseMirror — .ql-editor is the real current contenteditable
// class as of that migration) and a field-tested browser-automation
// reference confirming .share-creation-state as the composer's
// scoping container. Treat these as a strong starting point requiring
// live verification, not a guarantee — exactly the risk Full Spec
// Section 4.3's own risk table already accepts ("keep selectors
// isolated and versioned; treat as expected maintenance, not a
// blocker").
const CONFIG = {
  composerSelector: ".share-creation-state",
  textAreaSelector: '.ql-editor[contenteditable="true"]',
  controlContainerId: "voicova-control-container",
};

const _attachedComposers = new WeakSet();

function _findComposers() {
  return document.querySelectorAll(CONFIG.composerSelector);
}

function _getDraftText(composer) {
  const textArea = composer.querySelector(CONFIG.textAreaSelector);
  return textArea ? textArea.innerText.trim() : "";
}

// Replaces a rich-text editor's content the way a real user's typing
// or paste would, rather than by writing to the DOM directly.
//
// Quill (like ProseMirror, Draft.js, Lexical) keeps its own internal
// model and only stays in sync with the DOM by reacting to native
// browser edit events. Setting `.innerText`/`.innerHTML` changes what
// the screen shows but never reaches that internal model — so what
// the user sees and what the editor will actually submit on Post can
// silently diverge. This content script also has no access to
// LinkedIn's own Quill instance to call its API directly: that object
// lives in the page's own JS world, not the extension's isolated one.
//
// document.execCommand("insertText", ...) sidesteps both problems: it
// performs a real native text edit, which fires the same
// beforeinput/input events a live keystroke or paste would — the
// exact events Quill's own change-tracking is built to handle. This
// is the same approach production extensions (e.g. Grammarly) use to
// edit third-party rich-text editors they don't control.
//
// Selecting the full contents first makes the insertion a replacement
// rather than an append. Deprecated-but-universally-supported in
// Chrome; a readback afterwards confirms the DOM actually changed
// rather than trusting execCommand's own (unreliable) return value.
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

function _injectControl(composer) {
  if (_attachedComposers.has(composer)) return;
  _attachedComposers.add(composer);

  const container = document.createElement("div");
  container.id = CONFIG.controlContainerId;
  composer.appendChild(container);

  const panel = VoicovaPanel.mount(container, {
    onCheck: () => {
      const text = _getDraftText(composer);
      return chrome.runtime.sendMessage({
        type: "CHECK_DRAFT",
        text,
        surface: "linkedin",
      });
    },
    onFix: () => {
      const text = _getDraftText(composer);
      return chrome.runtime.sendMessage({
        type: "FIX_DRAFT",
        text,
        surface: "linkedin",
        // Generated HERE, not in service_worker.js where the request
        // is actually built (Architecture Section 6.4's own "content
        // scripts never construct a request themselves" principle,
        // which this deliberately steps outside of, for one reason):
        // idempotency only works if a genuinely duplicated MESSAGE —
        // the exact failure mode this key exists to guard against —
        // carries the same key both times. Generating it downstream,
        // after a duplicate message has already been sent twice,
        // would produce two different keys and dedupe nothing. This
        // is per-action input data, the same category as `text` and
        // `surface` above, not a protocol/auth decision — those still
        // belong to api_client.js alone.
        idempotencyKey: crypto.randomUUID(),
      });
    },
    onAcceptFix: (correctedText) => {
      const textArea = composer.querySelector(CONFIG.textAreaSelector);
      if (!textArea) return false;
      // never auto-inserted before this point (Section 3.4) — see
      // _replaceEditorText's own comment for why this isn't a plain
      // innerText assignment.
      return _replaceEditorText(textArea, correctedText);
    },
  });

  // Section 3.4: "editing the draft after a result invalidates the
  // prior result rather than showing a stale score" — one listener,
  // panel.js owns the actual state reset.
  const textArea = composer.querySelector(CONFIG.textAreaSelector);
  if (textArea) textArea.addEventListener("input", () => panel.invalidateResult());
}

// LinkedIn renders its composer dynamically and re-creates it on SPA
// navigation (Architecture Section 6.2) — a MutationObserver, not a
// one-time querySelector at load, is what "re-attach if the node is
// removed and re-created" requires.
const observer = new MutationObserver(() => {
  _findComposers().forEach(_injectControl);
});
observer.observe(document.body, { childList: true, subtree: true });
_findComposers().forEach(_injectControl); // in case the composer is already present on script load

// Section 6.3: a Disconnect elsewhere must not leave an
// already-rendered panel showing Connected.
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "TOKEN_INVALIDATED") VoicovaPanel.notifyAuthRequired();
});

// Exposed for automated testing only (tests/js/test_linkedin_editor.js)
// — same convention as VoicovaStorage/VoicovaApiClient/VoicovaStateMachine
// exporting their own internals via `self.X`. Not consumed by any other
// runtime file; the content script itself only calls these privately
// above.
self.VoicovaLinkedInInternal = { _replaceEditorText, _getDraftText };
})();
