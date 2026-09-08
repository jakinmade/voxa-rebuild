/**
 * popup/popup.js — the one place the extension shows account-level
 * state outside the in-page panel (Architecture Section 6.6).
 *
 * Reads chrome.storage directly via shared/storage.js — a popup page
 * is a privileged extension context, not a content script, so this
 * doesn't violate "content scripts never touch tokens directly"
 * (Section 6.3); that rule is about linkedin.js, not this file.
 * Disconnect itself still goes through the background worker's own
 * message handler rather than duplicating that logic here.
 */
async function render() {
  const installation = await VoicovaStorage.getInstallation();
  const statusEl = document.getElementById("status");
  const disconnectBtn = document.getElementById("disconnect");
  const connectBtn = document.getElementById("connect");

  // Previously: a disabled Disconnect button was the only thing shown
  // when disconnected — no actual way to connect from the popup at
  // all (outstanding item #2, 8 Sept 2026 fix). Swapping to a real
  // Connect CTA that opens voicova.com, where the existing "Connect
  // Chrome extension" link (app.py, Flow A) completes the handoff —
  // the popup itself can't construct that link, since it needs the
  // visitor's device_identity, which only exists as a cookie on
  // voicova.com, not in extension storage before a connection exists.
  if (installation) {
    statusEl.className = "status status-connected";
    statusEl.innerHTML = '<span class="status-dot"></span>Connected';
    disconnectBtn.hidden = false;
    connectBtn.hidden = true;
  } else {
    statusEl.className = "status status-disconnected";
    statusEl.innerHTML = '<span class="status-dot"></span>Not connected';
    disconnectBtn.hidden = true;
    connectBtn.hidden = false;
  }

  const allowanceEl = document.getElementById("allowance");
  const remaining = await VoicovaStorage.getLastKnownAllowance();
  // "Last known", not live — see storage.js's own comment on why
  // there's no fresher number to show without a dedicated endpoint.
  allowanceEl.textContent = remaining !== null ? `${remaining} renders left (last known)` : "";
}

document.getElementById("disconnect").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "DISCONNECT" });
  render();
});

document.getElementById("connect").addEventListener("click", () => {
  chrome.tabs.create({ url: "https://voicova.com" });
  window.close();
});

render();
