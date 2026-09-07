/**
 * dev_tools/verify_externally_connectable.js — proves, offline and
 * for real (not by reading Chrome's docs), that chrome.runtime is
 * only injected into a page whose origin is listed in the extension's
 * externally_connectable.matches — the exact root cause diagnosed for
 * the recovery landing page (served from the API's own Railway
 * domain, which wasn't in that list until this fix).
 *
 * Tests BOTH cases against the real, unmodified onMessageExternal
 * listener in background/service_worker.js:
 *   1. A page at an ALLOWED origin — chrome.runtime must exist, and
 *      sendMessage(EXTENSION_ID, {type:"LINK",...}) must succeed.
 *   2. A page at a NON-allowed origin — chrome.runtime must be
 *      undefined, reproducing the original bug exactly (not just an
 *      assumption about why it happened).
 *
 * USAGE
 *   node dev_tools/verify_externally_connectable.js
 */
const { execSync, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const REPO_ROOT = path.resolve(__dirname, "..");
const EXTENSION_SRC = path.resolve(REPO_ROOT, "voicova-extension");
const CHROME_BINARY = "/home/claude/.cache/puppeteer/chrome/linux-131.0.6778.204/chrome-linux64/chrome";
const puppeteer = require("/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer");

const ALLOWED_PORT = 8201;
const NOT_ALLOWED_PORT = 8202;
const DEBUG_PORT = 9335;

let procs = [];
let extensionCopyDir = null;
let userDataDir = null;
let allowedPageDir = null;
let notAllowedPageDir = null;

function log(...args) { console.log("[connectable-check]", ...args); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

function makePageDir(port) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-ec-page-"));
  fs.writeFileSync(
    path.join(dir, "index.html"),
    `<!DOCTYPE html><html><body><h1>origin test on port ${port}</h1></body></html>`
  );
  return dir;
}

function prepareExtensionCopy() {
  extensionCopyDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-ext-ec-"));
  execSync(`cp -r "${EXTENSION_SRC}"/. "${extensionCopyDir}"`);

  // Add ONLY the ALLOWED test origin to externally_connectable — the
  // real manifest's own two production entries (voicova.com, the live
  // Railway API domain) stay untouched and are not reachable from
  // this sandbox anyway; this copy-only addition is what lets the
  // "allowed origin" case be tested locally without touching prod.
  const manifestPath = path.join(extensionCopyDir, "manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  manifest.externally_connectable.matches.push(`http://127.0.0.1:${ALLOWED_PORT}/*`);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  log("extension copied — added http://127.0.0.1:" + ALLOWED_PORT + " to externally_connectable.matches (test-only)");
}

function startPageServer(dir, port) {
  const proc = spawn("python3", ["-m", "http.server", String(port), "--directory", dir], { stdio: "ignore" });
  procs.push(proc);
  return proc;
}

function startChrome() {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-chrome-ec-profile-"));
  const proc = spawn(
    "xvfb-run",
    [
      "-a", "--server-args=-screen 0 1280x1024x24",
      CHROME_BINARY,
      "--no-sandbox", "--disable-setuid-sandbox", "--disable-gpu",
      `--disable-extensions-except=${extensionCopyDir}`,
      `--load-extension=${extensionCopyDir}`,
      `--remote-debugging-port=${DEBUG_PORT}`,
      "--remote-debugging-address=0.0.0.0",
      `--user-data-dir=${userDataDir}`,
      "about:blank",
    ],
    { stdio: "ignore" }
  );
  procs.push(proc);
  return proc;
}

function cleanup() {
  for (const proc of procs) { try { proc.kill("SIGKILL"); } catch {} }
  try { execSync("pkill -9 -f chrome-linux64/chrome || true"); } catch {}
  try { execSync("pkill -9 -f Xvfb || true"); } catch {}
  for (const d of [extensionCopyDir, userDataDir, allowedPageDir, notAllowedPageDir]) {
    if (d) fs.rmSync(d, { recursive: true, force: true });
  }
}

async function findServiceWorkerTarget(browser, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const targets = await browser.targets();
    const sw = targets.find((t) => t.type() === "service_worker" && t.url().includes("service_worker.js"));
    if (sw) return sw;
    await sleep(300);
  }
  throw new Error("Extension service worker never registered");
}

async function main() {
  let failures = 0;
  const check = (label, condition) => {
    if (condition) { log("PASS —", label); } else { log("FAIL —", label); failures += 1; }
  };

  allowedPageDir = makePageDir(ALLOWED_PORT);
  notAllowedPageDir = makePageDir(NOT_ALLOWED_PORT);
  prepareExtensionCopy();
  startPageServer(allowedPageDir, ALLOWED_PORT);
  startPageServer(notAllowedPageDir, NOT_ALLOWED_PORT);
  startChrome();

  for (let i = 0; i < 20; i++) {
    try { await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`); break; } catch { await sleep(300); }
  }
  const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${DEBUG_PORT}` });
  log("connected to Chrome");
  const swTarget = await findServiceWorkerTarget(browser, 10000);
  const extensionId = new URL(swTarget.url()).host;
  log("extension loaded, id =", extensionId);

  // Case 1: NOT-allowed origin — reproduces the original bug exactly.
  const page1 = await browser.newPage();
  await page1.goto(`http://127.0.0.1:${NOT_ALLOWED_PORT}/`, { waitUntil: "networkidle0" });
  const runtimeOnNotAllowed = await page1.evaluate(() => {
    return !!(window.chrome && window.chrome.runtime && window.chrome.runtime.sendMessage);
  });
  check(
    "chrome.runtime is undefined on a NON-allowed origin (reproduces the diagnosed bug)",
    runtimeOnNotAllowed === false
  );

  // Case 2: allowed origin — the fix.
  const page2 = await browser.newPage();
  await page2.goto(`http://127.0.0.1:${ALLOWED_PORT}/`, { waitUntil: "networkidle0" });
  const runtimeOnAllowed = await page2.evaluate(() => {
    return !!(window.chrome && window.chrome.runtime && window.chrome.runtime.sendMessage);
  });
  check("chrome.runtime IS available on an allowed origin", runtimeOnAllowed === true);

  if (runtimeOnAllowed) {
    const linkResult = await page2.evaluate(
      (extId) =>
        new Promise((resolve) => {
          chrome.runtime.sendMessage(
            extId,
            { type: "LINK", access_token: "test-token", refresh_handle: "test-refresh", installation_id: "test-install-id" },
            (response) => resolve({ lastError: chrome.runtime.lastError?.message, response })
          );
        }),
      extensionId
    );
    check(
      "real LINK message handoff succeeds end-to-end from an allowed-origin page",
      !linkResult.lastError && linkResult.response && linkResult.response.linked === true
    );
    log("LINK handoff result:", JSON.stringify(linkResult));
  }

  await browser.disconnect();
  console.log("\n" + (failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`));
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((err) => { console.error("[connectable-check] ERROR", err); process.exitCode = 1; })
  .finally(cleanup);
