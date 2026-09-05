/**
 * shared/storage.js — chrome.storage wrappers, per Full Spec Section
 * 3.3.2's storage split: access token in chrome.storage.session
 * (cleared on browser close, never touches disk), refresh handle in
 * chrome.storage.local (survives a service-worker restart, which is
 * why it's the one that's opaque/single-use/rotated — see
 * background/service_worker.js's own comment on this trade-off).
 *
 * Background-service-worker-only module — content scripts never
 * import this (Architecture Section 6.3: "content scripts never
 * touch tokens directly"). Not loaded by manifest.json's
 * content_scripts entry; imported via importScripts() in
 * service_worker.js, and loaded directly by popup.html's own script
 * tag (a privileged extension page, not a content script — see
 * popup.js's own comment on why that's fine).
 *
 * Wrapped in an IIFE for the same reason state_machine.js is (that
 * file's own comment has the real collision this prevents) — this
 * file and api_client.js load together via importScripts() in
 * service_worker.js, so they'd share a global scope exactly the same
 * way the content-script files do.
 */
(function () {
const _SESSION_KEY = "voicova_access_token";
const _LOCAL_KEYS = {
  refreshHandle: "voicova_refresh_handle",
  installationId: "voicova_installation_id",
};

async function getAccessToken() {
  const { [_SESSION_KEY]: token } = await chrome.storage.session.get(_SESSION_KEY);
  return token || null;
}

async function setAccessToken(token) {
  await chrome.storage.session.set({ [_SESSION_KEY]: token });
}

async function getInstallation() {
  const stored = await chrome.storage.local.get([
    _LOCAL_KEYS.refreshHandle,
    _LOCAL_KEYS.installationId,
  ]);
  const refreshHandle = stored[_LOCAL_KEYS.refreshHandle] || null;
  const installationId = stored[_LOCAL_KEYS.installationId] || null;
  return refreshHandle && installationId ? { refreshHandle, installationId } : null;
}

async function setInstallation({ refreshHandle, installationId }) {
  await chrome.storage.local.set({
    [_LOCAL_KEYS.refreshHandle]: refreshHandle,
    [_LOCAL_KEYS.installationId]: installationId,
  });
}

// Clears everything — called on Disconnect (Section 6.3: "clears
// local token storage immediately... in addition to the server-side
// revoke call") and on a detected refresh-handle reuse (Section 4.3's
// compromise signal), so this is one function both call, not two
// slightly different clear-outs.
async function clearAll() {
  await chrome.storage.session.remove(_SESSION_KEY);
  await chrome.storage.local.remove([_LOCAL_KEYS.refreshHandle, _LOCAL_KEYS.installationId]);
}

// No dedicated "get usage" endpoint exists yet — the simplest honest
// way to show a remaining-allowance number in the popup (Architecture
// Section 6.6) is to cache the last one a real check/fix response
// actually carried, not to invent a new API call just for this.
// "Last known", not "current" — flagged in popup.js's own display.
const _ALLOWANCE_KEY = "voicova_last_known_allowance";

async function setLastKnownAllowance(remaining) {
  if (remaining === undefined || remaining === null) return;
  await chrome.storage.session.set({ [_ALLOWANCE_KEY]: remaining });
}

async function getLastKnownAllowance() {
  const { [_ALLOWANCE_KEY]: remaining } = await chrome.storage.session.get(_ALLOWANCE_KEY);
  return remaining ?? null;
}

self.VoicovaStorage = {
  getAccessToken, setAccessToken, getInstallation, setInstallation, clearAll,
  setLastKnownAllowance, getLastKnownAllowance,
};
})();
