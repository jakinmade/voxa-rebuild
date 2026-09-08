/**
 * dev_tools/extension_refresh_e2e_test.js — real-Chrome regression
 * test for the two fixes made to shared/api_client.js on 8 Sept 2026
 * (the live installation-revocation bug). Same harness pattern as
 * extension_e2e_test.js (real unmodified extension, real service
 * worker, real api.main:app over a local port) — this file exists
 * separately because it targets the refresh path specifically,
 * evaluating directly inside the service worker rather than driving
 * the mock LinkedIn page, which is unrelated to what's being proven
 * here.
 *
 * What this proves, with real chrome.storage and a real network
 * round trip (no vm mocks):
 *   1. Clearing only chrome.storage.session (simulating a browser
 *      restart per Full Spec Section 3.3.2 — access token gone,
 *      refresh handle still in chrome.storage.local) no longer
 *      reports token_revoked outright. checkDraft() transparently
 *      refreshes first, then succeeds.
 *   2. A real refresh leaves chrome.storage.local's refresh handle
 *      AND chrome.storage.installationId correctly updated, and
 *      chrome.storage.session's access token correctly updated —
 *      end state correctness, using the real extension APIs rather
 *      than the unit tests' in-memory storage mock.
 *
 * What this does NOT prove: that the MV3 service worker can never be
 * suspended between the two storage writes in _doRefresh() — Chrome
 * exposes no public API to force that at an exact point, so that part
 * of the fix (write ORDER minimizing the damage if it happens) stays
 * argued from code inspection + the real production incident data,
 * not from a reproduced-on-demand race here.
 *
 * USAGE
 *   node dev_tools/extension_refresh_e2e_test.js
 */
const { execSync, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const REPO_ROOT = path.resolve(__dirname, "..");
const EXTENSION_SRC = path.resolve(REPO_ROOT, "voicova-extension");
const CHROME_BINARY = "/home/claude/.cache/puppeteer/chrome/linux-131.0.6778.204/chrome-linux64/chrome";
const puppeteer = require("/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer");

const API_PORT = 8001; // distinct from extension_e2e_test.js's 8000, safe to run either independently
const DEBUG_PORT = 9334;

let procs = [];
let extensionCopyDir = null;
let userDataDir = null;

function log(...args) {
  console.log("[refresh-e2e]", ...args);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForPort(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res) return true;
    } catch {
      // not up yet
    }
    await sleep(300);
  }
  throw new Error(`Nothing answered on port ${port} within ${timeoutMs}ms`);
}

function prepareExtensionCopy() {
  extensionCopyDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-ext-refresh-e2e-"));
  execSync(`cp -r "${EXTENSION_SRC}"/. "${extensionCopyDir}"`);

  const apiClientPath = path.join(extensionCopyDir, "shared", "api_client.js");
  let jsContent = fs.readFileSync(apiClientPath, "utf8");
  const jsBefore = jsContent;
  jsContent = jsContent.replace(
    /const API_BASE_URL = ".*";/,
    `const API_BASE_URL = "http://127.0.0.1:${API_PORT}";`
  );
  if (jsContent === jsBefore) throw new Error("Could not find API_BASE_URL line to replace");
  fs.writeFileSync(apiClientPath, jsContent);

  // manifest.json's host_permissions must cover the local test
  // server's origin, or the browser sends a real CORS preflight that
  // api.main:app (deliberately, per its own docstring) has no CORS
  // middleware to answer — same requirement extension_e2e_test.js's
  // own prepareExtensionCopy already documents and applies.
  const manifestPath = path.join(extensionCopyDir, "manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  manifest.host_permissions.push(`http://127.0.0.1:${API_PORT}/*`);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  log("extension copied to", extensionCopyDir, "— API_BASE_URL and host_permissions pointed at local test server");
}

function startBackend() {
  const proc = spawn("python3", ["dev_tools/e2e_server.py", "--port", String(API_PORT)], {
    cwd: REPO_ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  procs.push(proc);
  proc.stdout.on("data", (d) => process.stdout.write(`[backend] ${d}`));
  proc.stderr.on("data", (d) => process.stderr.write(`[backend] ${d}`));
  return proc;
}

function startChrome() {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-chrome-refresh-profile-"));
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
  for (const proc of procs) {
    try { proc.kill("SIGKILL"); } catch {}
  }
  try { execSync("pkill -9 -f chrome-linux64/chrome || true"); } catch {}
  try { execSync("pkill -9 -f Xvfb || true"); } catch {}
  if (extensionCopyDir) fs.rmSync(extensionCopyDir, { recursive: true, force: true });
  if (userDataDir) fs.rmSync(userDataDir, { recursive: true, force: true });
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
    if (condition) {
      log("PASS —", label);
    } else {
      log("FAIL —", label);
      failures += 1;
    }
  };

  prepareExtensionCopy();
  startBackend();
  await waitForPort(API_PORT, 15000);
  log("backend up");

  startChrome();
  for (let i = 0; i < 20; i++) {
    try {
      await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/version`);
      break;
    } catch {
      await sleep(300);
    }
  }
  const browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${DEBUG_PORT}` });
  log("connected to Chrome");

  const swTarget = await findServiceWorkerTarget(browser, 10000);
  const worker = await swTarget.worker();

  // Real link against the real backend — same as extension_e2e_test.js.
  const linkResponse = await fetch(`http://127.0.0.1:${API_PORT}/api/extension/link`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_identity: "device-abc" }),
  });
  const linkBody = await linkResponse.json();
  check("real /api/extension/link returned a token", !!linkBody.access_token);

  await worker.evaluate(
    async (installationId, accessToken, refreshHandle) => {
      await VoicovaStorage.setAccessToken(accessToken);
      await VoicovaStorage.setInstallation({ installationId, refreshHandle });
    },
    linkBody.installation_id, linkBody.access_token, linkBody.refresh_handle
  );
  log("initial token installed into the extension's real storage");

  // TEST 1: simulate a browser restart — clear ONLY session storage
  // (access token), leave local storage (refresh handle) untouched,
  // exactly as a real Chrome restart would per Full Spec 3.3.2. Then
  // call checkDraft() and confirm it transparently refreshes instead
  // of reporting token_revoked outright (the bug this fix targets).
  await worker.evaluate(async () => {
    await chrome.storage.session.clear();
  });
  const sessionAfterClear = await worker.evaluate(async () => {
    return await chrome.storage.session.get(null);
  });
  check("session storage genuinely cleared (access token gone)", Object.keys(sessionAfterClear).length === 0);

  const checkResult = await worker.evaluate(async () => {
    return await VoicovaApiClient.checkDraft("It could perhaps be argued that this needs review.", "linkedin");
  });
  check(
    "checkDraft succeeds via transparent refresh after simulated restart (not token_revoked)",
    checkResult.ok === true
  );
  log("checkDraft result after simulated restart:", JSON.stringify(checkResult));

  // TEST 2: end-state correctness after that real refresh — both
  // storage areas should now hold fresh, self-consistent values.
  const storageAfterRefresh = await worker.evaluate(async () => {
    const installation = await VoicovaStorage.getInstallation();
    const accessToken = await VoicovaStorage.getAccessToken();
    return { installation, accessToken };
  });
  check("access token repopulated after transparent refresh", !!storageAfterRefresh.accessToken);
  check(
    "refresh handle rotated (no longer the original link-time handle)",
    !!storageAfterRefresh.installation &&
      storageAfterRefresh.installation.refreshHandle !== linkBody.refresh_handle
  );
  check(
    "installation_id preserved across the refresh",
    storageAfterRefresh.installation && storageAfterRefresh.installation.installationId === linkBody.installation_id
  );

  // TEST 3: sanity check the OTHER path still works — a genuinely
  // dead installation (no record at all) must still report
  // token_revoked, not silently hang or misreport.
  await worker.evaluate(async () => {
    await VoicovaStorage.clearAll();
  });
  const deadResult = await worker.evaluate(async () => {
    return await VoicovaApiClient.checkDraft("Another draft.", "linkedin");
  });
  check(
    "no installation at all still reports token_revoked (fix didn't mask real revocation)",
    deadResult.ok === false && deadResult.errorCode === "token_revoked"
  );

  await browser.disconnect();

  console.log("\n" + (failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`));
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((err) => {
    console.error("[refresh-e2e] ERROR", err);
    process.exitCode = 1;
  })
  .finally(cleanup);
