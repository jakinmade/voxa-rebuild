/**
 * tests/js/load_classic_script.js — loads one of voicova-extension's
 * classic (non-module) scripts into an isolated vm context for real
 * unit testing, without adding a bundler, a browser test runner, or
 * any npm dependency.
 *
 * Every extension file under voicova-extension/shared and
 * content_scripts is written as `(function () { ... })()` assigning
 * its exports to `self.SomeName` (see e.g. shared/storage.js's own
 * comment on why: it and api_client.js share one global scope via
 * importScripts() in the real service worker, and the content-script
 * files share one global scope the same way via manifest.json). That
 * means the file can't be `require()`d as-is — there's no
 * module.exports, and running it in Node's own global scope would
 * leak `self` assignments into every other test in the same process.
 *
 * loadClassicScript reads the file, builds a fresh sandbox object per
 * call, aliases `sandbox.self = sandbox` so a `self.X = ...` inside
 * the script and a bare `X` read back afterwards refer to the same
 * object, seeds the sandbox with whatever globals that file actually
 * touches (fetch, chrome, document, window, ...) passed in as
 * `extraGlobals`, then runs the script in that one-off vm.Context.
 * The returned sandbox is both "the exports" and "the fake global
 * environment the script saw" — good enough for a plain classic
 * script with no imports of its own.
 */
"use strict";
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const EXTENSION_ROOT = path.join(__dirname, "..", "..", "voicova-extension");

function loadClassicScript(relativePath, extraGlobals = {}) {
  const fullPath = path.join(EXTENSION_ROOT, relativePath);
  const source = fs.readFileSync(fullPath, "utf8");

  const sandbox = { console, ...extraGlobals };
  sandbox.self = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: fullPath });
  return sandbox;
}

module.exports = { loadClassicScript };
