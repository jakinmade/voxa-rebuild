/**
 * tests/js/test_linkedin_editor.js — real, executed coverage for
 * content_scripts/linkedin.js's _replaceEditorText fix: replacing a
 * rich-text editor's content via document.execCommand("insertText",
 * ...) after selecting its contents, instead of writing to
 * `.innerText` directly (which never reaches Quill's own internal
 * model — see linkedin.js's own comment on why).
 *
 * This is not a substitute for a real, live LinkedIn click-through
 * (there is no actual Quill instance here to diverge from, which is
 * exactly the limitation dev_tools/extension_e2e_test.js's own mock
 * has). What this DOES prove, for real: the selection is built
 * correctly, execCommand is invoked with the right arguments in the
 * right order, the function's own readback-verification return value
 * is honest (true only when the DOM actually changed, false when
 * execCommand silently no-ops), and no regression can slip into this
 * function unnoticed in the future the way the original innerText bug
 * did. The remaining live-LinkedIn risk is a real-Quill-model
 * question this harness cannot answer and does not claim to.
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

// A DOM mock narrow enough to be honest about what it proves: it
// faithfully drives the same call sequence a real browser's Selection
// + execCommand APIs expose (createRange -> selectNodeContents,
// getSelection -> removeAllRanges/addRange, execCommand("insertText",
// ...)), and lets a test script exactly how execCommand behaves —
// including the "it returned true but nothing actually changed" case
// a real flaky/locked-down browser could produce, which is precisely
// why the function under test reads the DOM back afterwards instead
// of trusting execCommand's own return value.
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
      return true; // browsers often report success even when nothing changed
    },
  };

  const window = {
    getSelection: () => ({
      removeAllRanges: () => {
        selectionCalls.removeAllRanges += 1;
      },
      addRange: (range) => {
        selectionCalls.addRange += 1;
      },
    }),
  };

  return { document, window, selectionCalls, getRangeTarget: () => rangeTarget };
}

function loadLinkedinContentScript(domMocks) {
  return loadClassicScript("content_scripts/linkedin.js", {
    document: {
      ...domMocks.document,
      querySelectorAll: () => [], // no composers present at "page load" in this test
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
}

test("replaces editor text and reports success when the DOM actually changes", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadLinkedinContentScript(domMocks);
  const editor = makeFakeEditor("original draft text");

  const result = sandbox.VoicovaLinkedInInternal._replaceEditorText(editor, "corrected draft text");

  assert.equal(result, true);
  assert.equal(editor.innerText, "corrected draft text");
  assert.equal(editor.focused, true, "the editor must be focused before selection/insertion");
});

test("selects the editor's full contents before inserting (replace, not append)", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadLinkedinContentScript(domMocks);
  const editor = makeFakeEditor("original");

  sandbox.VoicovaLinkedInInternal._replaceEditorText(editor, "corrected");

  assert.equal(domMocks.getRangeTarget(), editor, "the range must target the editor element itself");
  assert.equal(domMocks.selectionCalls.removeAllRanges, 1);
  assert.equal(domMocks.selectionCalls.addRange, 1);
});

test("returns false when execCommand reports success but the DOM did not actually change", () => {
  // The exact failure mode a plain boolean check on execCommand's own
  // return value would miss — a locked-down/unusual browser state
  // that reports success without writing anything. This is why the
  // function reads the DOM back instead of trusting that return value.
  const domMocks = makeDomMocks({ execCommandWrites: false });
  const sandbox = loadLinkedinContentScript(domMocks);
  const editor = makeFakeEditor("original draft text");

  const result = sandbox.VoicovaLinkedInInternal._replaceEditorText(editor, "corrected draft text");

  assert.equal(result, false);
  assert.equal(editor.innerText, "original draft text", "unchanged text proves the false return is honest");
});

test("trims whitespace when comparing the readback, matching _getDraftText's own trim", () => {
  const domMocks = makeDomMocks({ execCommandWrites: true });
  const sandbox = loadLinkedinContentScript(domMocks);
  const editor = makeFakeEditor("");
  // Simulate an editor that pads the inserted text with trailing
  // whitespace (Quill and other rich editors commonly do) — the
  // comparison must not fail purely on that.
  const originalSetText = editor._setText.bind(editor);
  editor._setText = (t) => originalSetText(`${t}\n`);

  const result = sandbox.VoicovaLinkedInInternal._replaceEditorText(editor, "corrected text");

  assert.equal(result, true);
});
