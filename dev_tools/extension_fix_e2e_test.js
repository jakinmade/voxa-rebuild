/**
 * dev_tools/extension_fix_e2e_test.js — drives the REAL, unmodified
 * voicova-extension's Fix-Accept flow: click Check, click Fix it,
 * click Accept, then verify the composer's real DOM actually changed
 * to the corrected text (not just that the panel says "Applied.").
 *
 * This is the flow the 5 Sept independent architecture review flagged
 * as most severe (P0): a prior bug wrote to editableEl's innerText
 * instead of the editor's real model, which could have let LinkedIn
 * silently repost the UNCORRECTED draft while showing the user a
 * false "Applied." confirmation. PR #45 fixed the write mechanism
 * (execCommand("insertText") + a readback check) — this harness is
 * the first time that fix has been exercised end-to-end through the
 * real extension UI, rather than read as a diff.
 *
 * Uses e2e_server_with_fix.py (real api.main:app, fake Supabase, and
 * a MOCKED Anthropic client) instead of extension_e2e_test.js's own
 * e2e_server.py, specifically so Fix-it can be tested at zero cost —
 * see that script's own docstring. Structurally identical to
 * extension_e2e_test.js otherwise; kept as a separate file rather
 * than merged in, so the already-proven Check-only test is never put
 * at risk by this addition.
 *
 * USAGE
 *   node dev_tools/extension_fix_e2e_test.js
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

const API_PORT = 8001; // different port from extension_e2e_test.js so both can run without colliding
const MOCK_PAGE_PORT = 8124;
const DEBUG_PORT = 9334;

let procs = [];
let extensionCopyDir = null;
let userDataDir = null;

function log(...args) {
  console.log("[fix-e2e]", ...args);
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
  extensionCopyDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-ext-fix-e2e-"));
  execSync(`cp -r "${EXTENSION_SRC}"/. "${extensionCopyDir}"`);

  const apiClientPath = path.join(extensionCopyDir, "shared", "api_client.js");
  let jsContent = fs.readFileSync(apiClientPath, "utf8");
  const jsBefore = jsContent;
  jsContent = jsContent.replace(
    /const API_BASE_URL = ".*";/,
    `const API_BASE_URL = "http://127.0.0.1:${API_PORT}";`
  );
  if (jsContent === jsBefore) throw new Error("Could not find API_BASE_URL line to replace — check the constant still exists under that name");
  fs.writeFileSync(apiClientPath, jsContent);

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
  const proc = spawn("python3", ["dev_tools/e2e_server_with_fix.py", "--port", String(API_PORT)], {
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
  userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "voicova-chrome-fix-profile-"));
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
  log("backend (with mocked Anthropic) and mock page server both up");

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

  const ORIGINAL_DRAFT = "It could perhaps be argued that further review might be advisable.";

  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${MOCK_PAGE_PORT}/`, { waitUntil: "networkidle0" });

  await page.waitForSelector('[componentkey="ShareBox_textEditor"]', { timeout: 10000 });
  await page.type('[componentkey="ShareBox_textEditor"]', ORIGINAL_DRAFT);
  await page.waitForSelector("#voicova-control-container button", { timeout: 10000 });
  await page.click("#voicova-control-container button");

  await page.waitForSelector(".voicova-result", { timeout: 15000 });
  check("Check produced a result panel", true);

  // Fix-eligible tiers (borderline/failed) show a "Fix it" button —
  // canOfferFixIt() in state_machine.js. Our fixed test draft is
  // known to score borderline (same draft api_smoke_test.py itself
  // uses as its own default).
  const fixButton = await page.waitForSelector(".voicova-result .voicova-btn-primary", { timeout: 5000 }).catch(() => null);
  check("Fix it button offered on a borderline result", !!fixButton);
  if (!fixButton) throw new Error("No Fix it button — cannot continue to Fix-Accept flow");

  await fixButton.click();
  await page.waitForSelector(".voicova-fixit", { timeout: 15000 });
  const correctedText = await page.$eval(".voicova-fixit-text", (el) => el.textContent).catch(() => null);
  check("Fix-it call returned corrected text", !!correctedText && correctedText.trim().length > 0);
  log("corrected text from real /api/fix (mocked Anthropic):", correctedText);

  // The actual thing this harness exists to verify: click Accept, then
  // read the COMPOSER'S OWN DOM — not the panel's state — to confirm
  // a real edit happened via execCommand, not merely that the panel
  // claims success.
  await page.click(".voicova-fixit .voicova-btn-primary");
  await page.waitForSelector(".voicova-accepted", { timeout: 5000 });
  check('panel reports "Applied." after Accept', true);

  const editorTextAfterAccept = await page.$eval(
    '[componentkey="ShareBox_textEditor"]',
    (el) => el.innerText.trim()
  );
  check(
    "composer's real DOM actually changed to the corrected text (not the original draft)",
    editorTextAfterAccept === (correctedText || "").trim() && editorTextAfterAccept !== ORIGINAL_DRAFT
  );
  log("composer text after Accept:", editorTextAfterAccept);

  await browser.disconnect();

  console.log("\n" + (failures === 0 ? "ALL CHECKS PASSED" : `${failures} CHECK(S) FAILED`));
  process.exitCode = failures === 0 ? 0 : 1;
}

main()
  .catch((err) => {
    console.error("[fix-e2e] ERROR", err);
    process.exitCode = 1;
  })
  .finally(cleanup);
