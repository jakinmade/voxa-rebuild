/**
 * tests/js/test_panel.js — real, executed coverage for the two fixes
 * made 8 Sept 2026 (outstanding items #2/#13: the disconnected state
 * was reactive-only, discovered only after a failed Check):
 *
 *   1. state_machine.js gained a CONNECT_REQUIRED state, distinct
 *      from AUTH_REQUIRED (first-run "never connected" vs. "was
 *      connected, token revoked") and correctly excluded from
 *      Fix-it eligibility, same as every other non-result state.
 *   2. panel.js's mount() now asks the background worker up front
 *      (GET_CONNECTION_STATUS) whether an installation exists, and
 *      renders CONNECT_REQUIRED with a real link directly — rather
 *      than always starting idle and only surfacing the gap after a
 *      real CHECK_DRAFT call fails.
 *
 * Run with: node --test tests/js
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadClassicScript } = require("./load_classic_script");

// A narrow, honest DOM mock — same philosophy as test_linkedin_editor.js's
// own mock: it faithfully supports exactly the element/container API
// panel.js actually calls (createElement, append/appendChild,
// setAttribute, addEventListener, innerHTML-as-clear), nothing more.
function makeElement(tag) {
  const el = {
    tagName: tag,
    className: "",
    textContent: "",
    href: "",
    target: "",
    rel: "",
    children: [],
    _listeners: {},
    setAttribute(name, value) {
      this[name] = value;
    },
    appendChild(node) {
      this.children.push(node);
      return node;
    },
    append(...nodes) {
      this.children.push(...nodes);
    },
    addEventListener(evt, fn) {
      this._listeners[evt] = fn;
    },
  };
  return el;
}

function makeDocument() {
  return { createElement: (tag) => makeElement(tag) };
}

function makeContainer() {
  let children = [];
  return {
    get innerHTML() {
      return children.length ? "non-empty" : "";
    },
    set innerHTML(v) {
      if (v === "") children = [];
    },
    appendChild(node) {
      children.push(node);
      return node;
    },
    setAttribute() {},
    addEventListener() {},
    get lastRendered() {
      return children[children.length - 1];
    },
  };
}

function findChild(el, predicate) {
  return (el.children || []).find(predicate);
}

function loadStateMachine() {
  return loadClassicScript("shared/state_machine.js");
}

function loadPanel(VoicovaStateMachine, chrome) {
  return loadClassicScript("shared/panel.js", {
    VoicovaStateMachine,
    document: makeDocument(),
    chrome,
  });
}

const noopCallbacks = {
  onCheck: async () => ({ ok: true, data: {} }),
  onFix: async () => ({ ok: true, data: {} }),
  onAcceptFix: () => true,
};

test("CONNECT_REQUIRED exists and is not Fix-it eligible", () => {
  const sm = loadStateMachine();
  assert.equal(typeof sm.VoicovaStateMachine.STATES.CONNECT_REQUIRED, "string");
  assert.equal(sm.VoicovaStateMachine.canOfferFixIt(sm.VoicovaStateMachine.STATES.CONNECT_REQUIRED), false);
});

test("mount() asks the background worker for connection status on load", async () => {
  const sm = loadStateMachine();
  const sentMessages = [];
  const chrome = {
    runtime: {
      sendMessage: (msg) => {
        sentMessages.push(msg);
        return Promise.resolve({ connected: true });
      },
    },
  };
  const panel = loadPanel(sm.VoicovaStateMachine, chrome);
  panel.VoicovaPanel.mount(makeContainer(), noopCallbacks);

  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(sentMessages.length, 1);
  assert.equal(sentMessages[0].type, "GET_CONNECTION_STATUS");
});

test("not connected -> renders CONNECT_REQUIRED with a real link to voicova.com", async () => {
  const sm = loadStateMachine();
  const chrome = { runtime: { sendMessage: () => Promise.resolve({ connected: false }) } };
  const panel = loadPanel(sm.VoicovaStateMachine, chrome);
  const container = makeContainer();

  panel.VoicovaPanel.mount(container, noopCallbacks);
  await new Promise((resolve) => setImmediate(resolve));

  const rendered = container.lastRendered;
  assert.equal(rendered.className, "voicova-panel voicova-auth");
  const link = findChild(rendered, (c) => c.tagName === "a");
  assert.ok(link, "expected a real <a> link, not plain text");
  assert.equal(link.href, "https://voicova.com");
  assert.match(link.textContent, /Connect on voicova\.com/);
});

test("connected -> renders the normal idle Check button, not CONNECT_REQUIRED", async () => {
  const sm = loadStateMachine();
  const chrome = { runtime: { sendMessage: () => Promise.resolve({ connected: true }) } };
  const panel = loadPanel(sm.VoicovaStateMachine, chrome);
  const container = makeContainer();

  panel.VoicovaPanel.mount(container, noopCallbacks);
  await new Promise((resolve) => setImmediate(resolve));

  const rendered = container.lastRendered;
  assert.equal(rendered.className, "voicova-btn voicova-btn-idle");
  assert.equal(rendered.textContent, "Check my voice");
});

test("background unreachable -> falls back to idle rather than staying blank", async () => {
  const sm = loadStateMachine();
  const chrome = { runtime: { sendMessage: () => Promise.reject(new Error("no receiver")) } };
  const panel = loadPanel(sm.VoicovaStateMachine, chrome);
  const container = makeContainer();

  panel.VoicovaPanel.mount(container, noopCallbacks);
  await new Promise((resolve) => setImmediate(resolve));

  const rendered = container.lastRendered;
  assert.equal(rendered.className, "voicova-btn voicova-btn-idle");
});

test("AUTH_REQUIRED (reactive, was-connected case) also has a real link, not plain text", async () => {
  const sm = loadStateMachine();
  const chrome = { runtime: { sendMessage: () => Promise.resolve({ connected: true }) } };
  const panel = loadPanel(sm.VoicovaStateMachine, chrome);
  const container = makeContainer();

  const instance = panel.VoicovaPanel.mount(container, noopCallbacks);
  await new Promise((resolve) => setImmediate(resolve));

  instance.notifyAuthRequired();
  const rendered = container.lastRendered;
  assert.equal(rendered.className, "voicova-panel voicova-auth");
  const link = findChild(rendered, (c) => c.tagName === "a");
  assert.ok(link, "expected a real <a> link, not plain text");
  assert.equal(link.href, "https://voicova.com");
  assert.match(link.textContent, /Reconnect on voicova\.com/);
});
