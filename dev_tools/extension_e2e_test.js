/**
 * dev_tools/extension_e2e_test.js — drives the REAL, unmodified
 * voicova-extension (loaded as an actual Chrome extension, not
 * simulated) against a real running api.main:app (see e2e_server.py
 * for what "real" means there) and a local structural mock of
 * LinkedIn's composer DOM.
 *
 * What this proves: manifest loads, the content script finds the
 * composer and injects the control, clicking it round-trips through
 * the background service worker -> api_client.js -> a real HTTP call
 * -> real FastAPI auth/routing -> real score_draft_check scoring ->
 * back through real chrome.runtime messaging -> panel.js renders the
 * real result. Every one of those hops is the actual shipped code.
 *
 * What this does NOT prove: that LinkedIn's live DOM still matches
 * the mock page's structure (content_scripts/linkedin.js's own
 * CONFIG comment already flags this as accepted, monitored risk —
 * this harness can't remove that, only test around it), or anything
 * about Fix-it (Week 4 scope, needs a real Anthropic key this
 * environment doesn't have).
 *
 * USAGE
 *   node dev_tools/extension_e2e_test.js
 *
 * Requires: the sibling voicova-extension/ directory, a Python env
 * with this repo's dependencies installed (for e2e_server.py), and
 * the cached Puppeteer/Chrome this sandbox already has. Cleans up
 * every process and temp file it creates, on success or failure.
 */
const { execSync, spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const os = require("os");

const REPO_ROOT = path.resolve(__dirname, "..");
const EXTENSION_SRC = path.resolve(REPO_ROOT, "voicova-extension");
const MOCK_PAGE_DIR = path.resolve(REPO_ROOT, "dev_tools", "fixtures", "mock_linkedin");
const CHROME_BINARY = "/home/claude/.cache/puppeteer/chrome/linux-131.0.6778.204/chrome-linux64/chrome";
const puppeteer = require("/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer");

const API_PORT = 8000;
const MOCK_PAGE_PORT = 8123;
const DEBUG_PORT = 9333;

let procs = [];
let extensionCopyDir = null;
let userDataDir = null;

function log(...args) {
  console.log("[e2e]", ...args);
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
  // Copy, don't edit in place — the real source stays byte-for-byte
  // untouched. Two changes on the copy only, both the standard,
  // expected customisation for a local/CI test variant, not a hack:
  extensionCopyDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-ext-e2e-"));
  execSync(`cp -r "${EXTENSION_SRC}"/. "${extensionCopyDir}"`);

  // 1. api_client.js's API_BASE_URL points at the local test server
  //    instead of the production Railway URL — the same one-line
  //    change a real dev/staging build needs anyway.
  const apiClientPath = path.join(extensionCopyDir, "shared", "api_client.js");
  let jsContent = fs.readFileSync(apiClientPath, "utf8");
  const jsBefore = jsContent;
  jsContent = jsContent.replace(
    /const API_BASE_URL = ".*";/,
    `const API_BASE_URL = "http://127.0.0.1:${API_PORT}";`
  );
  if (jsContent === jsBefore) throw new Error("Could not find API_BASE_URL line to replace — check the constant still exists under that name");
  fs.writeFileSync(apiClientPath, jsContent);

  // 2. manifest.json's content_scripts match pattern AND
  //    host_permissions additionally cover the local test server's
  //    own origin — the real manifest only ever grants linkedin.com
  //    and the production API host, correctly; a test fixture needs
  //    both added for its own origins, the standard way to run a
  //    real Manifest V3 extension against local test infrastructure.
  const manifestPath = path.join(extensionCopyDir, "manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  const mockPageOrigin = `http://127.0.0.1:${MOCK_PAGE_PORT}/*`;
  const localApiOrigin = `http://127.0.0.1:${API_PORT}/*`;
  manifest.content_scripts[0].matches.push(mockPageOrigin);
  manifest.host_permissions.push(localApiOrigin);
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));

  log("extension copied to", extensionCopyDir, "— API_BASE_URL and content-script match pattern adjusted for the local test environment only");
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

function startMockPageServer() {
  const proc = spawn("python3", ["-m", "http.server", String(MOCK_PAGE_PORT), "--directory", MOCK_PAGE_DIR], {
    stdio: "ignore",
  });
  procs.push(proc);
  return proc;
}

function startChrome() {
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-chrome-profile-"));
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
  startMockPageServer();
  await waitForPort(API_PORT, 15000);
  await waitForPort(MOCK_PAGE_PORT, 5000);
  log("backend and mock page server both up");

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
  const extensionId = new URL(swTarget.url()).host;
  log("extension loaded, id =", extensionId);
  const worker = await swTarget.worker();

  // Simulate Flow A's outcome (Section 3.3.3): call the real
  // /api/extension/link on the real backend, then hand the result to
  // the extension's own storage the same way the externally_connectable
  // listener would — that listener itself is a five-line message
  // handler already reviewed by hand, not re-tested here; this proves
  // everything downstream of it actually works.
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
  log("token installed into the extension's real storage");

  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${MOCK_PAGE_PORT}/`, { waitUntil: "networkidle0" });

  await page.waitForSelector("#voicova-control-container button", { timeout: 10000 });
  check("content script injected the Check control", true);

  await page.type(".ql-editor", "It could perhaps be argued that further review might be advisable.");
  await page.click("#voicova-control-container button");

  await page.waitForSelector(".voicova-result", { timeout: 15000 }).catch(() => {});
  const resultText = await page.$eval(".voicova-result-headline", (el) => el.textContent).catch(() => null);
  check("a real scored result rendered in the panel", !!resultText && /%/.test(resultText));
  log("rendered result:", resultText);

  // Auth-required path: clear the token the way a real expiry/reuse-
  // revocation would leave things, then check again.
  await worker.evaluate(() => VoicovaStorage.clearAll());
  await page.reload({ waitUntil: "networkidle0" });
  await page.waitForSelector("#voicova-control-container button", { timeout: 10000 });
  await page.type(".ql-editor", "Another draft to check.");
  await page.click("#voicova-control-container button");
  await page.waitForSelector(".voicova-auth", { timeout: 15000 }).catch(() => {});
  const authText = await page.$eval(".voicova-auth", (el) => el.textContent).catch(() => null);
  check("auth_required state rendered after clearing the token", !!authText);

  await browser.disconnect();

  console.log("\n" + (failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`));
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((err) => {
    console.error("[e2e] ERROR", err);
    process.exitCode = 1;
  })
  .finally(cleanup);
