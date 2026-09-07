/**
 * tests/js/test_gmail_editor.js — mirrors test_linkedin_editor.js's
 * structure and its own honesty about scope: this proves
 * _replaceEditorText's selection/execCommand/readback logic is
 * correct (identical technique to linkedin.js, same rationale), and
 * proves _queryTextAreas' fallback-chain ordering is correct. It does
 * NOT prove Gmail's real DOM currently matches any of these selectors
 * — that is a live-verification gap named explicitly in gmail.js's
 * own header comment, not one this harness can close.
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadClassicScript } = require("./load_classic_script");

function makeFakeEditor(initialText = "") {
  let text = initialText;
  let focused = false;
  return {
    get innerText() {
      return text;
    },
    _setText(t) {
      text = t;
    },
    focus() {
      focused = true;
    },
    get focused() {
      return focused;
    },
  };
}

function makeDomMocks({ execCommandWrites = true } = {}) {
  let rangeTarget = null;
  const selectionCalls = { removeAllRanges: 0, addRange: 0 };

  const document = {
    createRange: () => ({
      selectNodeContents: (el) => {
        rangeTarget = el;
      },
    }),
    execCommand: (command, ui, value) => {
      if (command !== "insertText") return false;
      if (execCommandWrites && rangeTarget) rangeTarget._setText(value);
      return true;
    },
  };

  const window = {
    getSelection: () => ({
      removeAllRanges: () => {
        selectionCalls.removeAllRanges += 1;
      },
      addRange: () => {
        selectionCalls.addRange += 1;
      },
    }),
  };

  return { document, window, selectionCalls, getRangeTarget: () => rangeTarget };
}

// gmail.js calls _findComposers().forEach(_injectControl) once at load
// time (same as linkedin.js — "in case a composer is already present
// on script load"). For the _queryTextAreas-focused tests below,
// which need querySelectorAll to return fake, non-DOM-element string
// matches, that load-time call would immediately try to call real DOM
// methods (insertAdjacentElement etc.) on those fake strings and
// throw. Gating the mock to return [] until after the script has
// finished loading isolates the test to _queryTextAreas' own
// selector-fallback logic, which is what these tests are actually
// about — not a change to gmail.js itself.
function loadGmailContentScript(domMocks, { querySelectorAllImpl } = {}) {
  let loaded = false;
  const sandbox = loadClassicScript("content_scripts/gmail.js", {
    document: {
      ...domMocks.document,
      querySelectorAll: (selector) => (loaded && querySelectorAllImpl ? querySelectorAllImpl(selector) : []),
      body: {},
    },
    window: domMocks.window,
    chrome: {
      runtime: {
        onMessage: { addListener: () => {} },
        sendMessage: () => {},
      },
    },
    MutationObserver: class {
      observe() {}
    },
  });
  loaded = true;
  return sandbox;
}

test("replaces editor text and reports success when the DOM actually changes", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadGmailContentScript(domMocks);
  const editor = makeFakeEditor("original draft text");

  const result = sandbox.VoicovaGmailInternal._replaceEditorText(editor, "corrected draft text");

  assert.equal(result, true);
  assert.equal(editor.innerText, "corrected draft text");
  assert.equal(editor.focused, true, "the editor must be focused before selection/insertion");
});

test("selects the editor's full contents before inserting (replace, not append)", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadGmailContentScript(domMocks);
  const editor = makeFakeEditor("original");

  sandbox.VoicovaGmailInternal._replaceEditorText(editor, "corrected");

  assert.equal(domMocks.getRangeTarget(), editor, "the range must target the editor element itself");
  assert.equal(domMocks.selectionCalls.removeAllRanges, 1);
  assert.equal(domMocks.selectionCalls.addRange, 1);
});

test("returns false when execCommand reports success but the DOM did not actually change", () => {
  const domMocks = makeDomMocks({ execCommandWrites: false });
  const sandbox = loadGmailContentScript(domMocks);
  const editor = makeFakeEditor("original draft text");

  const result = sandbox.VoicovaGmailInternal._replaceEditorText(editor, "corrected draft text");

  assert.equal(result, false);
  assert.equal(editor.innerText, "original draft text", "unchanged text proves the false return is honest");
});

test("trims whitespace when comparing the readback, matching _getDraftText's own trim", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadGmailContentScript(domMocks);
  const editor = makeFakeEditor("");
  const originalSetText = editor._setText.bind(editor);
  editor._setText = (t) => originalSetText(`${t}\n`);

  const result = sandbox.VoicovaGmailInternal._replaceEditorText(editor, "corrected text");

  assert.equal(result, true);
});

test("_queryTextAreas uses the primary selector when it matches", () => {
  const domMocks = makeDomMocks();
  const calls = [];
  const sandbox = loadGmailContentScript(domMocks, {
    querySelectorAllImpl: (selector) => {
      calls.push(selector);
      return selector === '[role="textbox"][g_editable="true"]' ? ["fake-primary-match"] : [];
    },
  });

  // Array.from here (outer realm) rather than comparing the sandbox's
  // own Array instance directly — Node's assert.deepEqual treats an
  // array built by a different vm context's Array constructor as not
  // reference-equal even when its contents are identical, since
  // gmail.js's Array.from(...) inside the sandbox uses the sandbox's
  // own Array, not this test file's.
  const result = Array.from(sandbox.VoicovaGmailInternal._queryTextAreas());

  assert.deepEqual(result, ["fake-primary-match"]);
  assert.deepEqual(calls, ['[role="textbox"][g_editable="true"]'], "must not try fallback selectors once the primary one matches");
});

test("_queryTextAreas falls back through the selector list in order when earlier ones find nothing", () => {
  const domMocks = makeDomMocks();
  const calls = [];
  const sandbox = loadGmailContentScript(domMocks, {
    querySelectorAllImpl: (selector) => {
      calls.push(selector);
      return selector === 'div[contenteditable="true"][g_editable="true"]' ? ["fake-fallback-match"] : [];
    },
  });

  const result = Array.from(sandbox.VoicovaGmailInternal._queryTextAreas());

  assert.deepEqual(result, ["fake-fallback-match"]);
  assert.deepEqual(calls, [
    '[role="textbox"][g_editable="true"]',
    '[aria-label="Message Body"][role="textbox"]',
    'div[contenteditable="true"][g_editable="true"]',
  ], "must try every selector in order until one matches");
});

test("_queryTextAreas returns empty when no selector matches anything", () => {
  const domMocks = makeDomMocks();
  const sandbox = loadGmailContentScript(domMocks, { querySelectorAllImpl: () => [] });

  const result = Array.from(sandbox.VoicovaGmailInternal._queryTextAreas());

  assert.deepEqual(result, []);
});
