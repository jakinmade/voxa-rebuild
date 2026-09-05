/**
 * background/service_worker.js — owns everything token-related
 * (Architecture Section 6.3): the proactive refresh alarm, the
 * externally_connectable handoff from voicova.com (Flow A), message
 * routing between content scripts and api_client.js, and Disconnect.
 *
 * Classic (non-module) service worker, using importScripts() rather
 * than ES module imports — simpler, no bundler needed for two small
 * files, and Manifest V3 supports both equally well.
 */
importScripts("../shared/storage.js", "../shared/api_client.js");

// 80% of the server's ACCESS_TOKEN_TTL_SECONDS default (Architecture
// Section 7: recommend 3600 / 1 hour), picked from the documented
// "80-85%" window's conservative end. This is a real client/server
// coupling with no dynamic signal between them today — neither
// LinkResponse nor RefreshResponse carries an expiry field the
// extension could read instead. If the server's TTL ever changes,
// this constant needs updating to match; flagged here rather than
// silently assumed to always be true.
const _REFRESH_ALARM = "voicova_refresh";
const _REFRESH_INTERVAL_MINUTES = (3600 * 0.8) / 60;

function _scheduleRefresh() {
  chrome.alarms.create(_REFRESH_ALARM, { periodInMinutes: _REFRESH_INTERVAL_MINUTES });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === _REFRESH_ALARM) VoicovaApiClient.refreshAccessToken();
});

// Re-arm the alarm on every service-worker startup (browser restart,
// or MV3 suspending and waking the worker) — chrome.alarms persists
// across worker restarts already, but create() is idempotent (same
// name replaces the existing schedule), so this is a safe no-op when
// one is already registered and a correct recovery when it isn't.
chrome.runtime.onStartup.addListener(async () => {
  const installation = await VoicovaStorage.getInstallation();
  if (installation) _scheduleRefresh();
});

// Flow A completion (Section 3.3.3 step 2): voicova.com sends the
// newly-minted token directly to the extension. matches in
// manifest.json's externally_connectable restricts this to
// https://voicova.com/* — no other site can reach this listener.
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  if (message?.type !== "LINK") return;
  (async () => {
    await VoicovaStorage.setAccessToken(message.access_token);
    await VoicovaStorage.setInstallation({
      refreshHandle: message.refresh_handle,
      installationId: message.installation_id,
    });
    _scheduleRefresh();
    sendResponse({ linked: true });
  })();
  return true; // keep the message channel open for the async response
});

// Message routing (Architecture Section 6.4): content scripts only
// ever say "check this text" or "fix this text" and get a result —
// they never see a token or construct a request themselves.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "CHECK_DRAFT") {
    VoicovaApiClient.checkDraft(message.text, message.surface).then((response) => {
      if (response.ok) VoicovaStorage.setLastKnownAllowance(response.data.remaining_allowance);
      sendResponse(response);
    });
    return true;
  }
  if (message?.type === "FIX_DRAFT") {
    VoicovaApiClient.fixDraft(message.text, message.surface).then(sendResponse);
    return true;
  }
  if (message?.type === "DISCONNECT") {
    (async () => {
      await VoicovaApiClient.disconnect(); // best-effort; clear locally either way, per below
      await VoicovaStorage.clearAll();
      chrome.alarms.clear(_REFRESH_ALARM);
      // Broadcast to any open LinkedIn tabs so an already-rendered
      // panel doesn't keep showing Connected until its next API call
      // fails (Section 6.3: "a stale-state bug, not just a delayed
      // one").
      const tabs = await chrome.tabs.query({ url: "https://www.linkedin.com/*" });
      for (const tab of tabs) {
        chrome.tabs.sendMessage(tab.id, { type: "TOKEN_INVALIDATED" }).catch(() => {});
      }
      sendResponse({ disconnected: true });
    })();
    return true;
  }
});
