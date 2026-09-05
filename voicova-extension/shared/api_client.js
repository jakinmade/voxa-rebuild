/**
 * shared/api_client.js — the only module that constructs a request to
 * the backend (Architecture Section 6.3: "each content script only
 * ever says 'check this text' or 'fix this text'... it never
 * constructs a request itself"). Background-service-worker-only,
 * imported via importScripts() in service_worker.js — not loaded by
 * manifest.json's content_scripts.
 *
 * refreshAccessToken is exported (not private) because it's called
 * from two places that must never drift into two implementations:
 * service_worker.js's own proactive refresh alarm, and this module's
 * own reactive retry-once-on-token_expired path (Architecture Section
 * 6.4's diagram). One function, two callers.
 *
 * Wrapped in an IIFE for the same reason state_machine.js is (see
 * that file's own comment) — this loads alongside storage.js via
 * importScripts() in service_worker.js, sharing one global scope.
 */
(function () {
// V1 pilot value (Architecture Section 7): the API service's own
// Railway domain, not voicova.com/api — see that section's own note
// on why the latter is a later option only. Change this one line to
// point at a local dev server instead.
//
// Coupled to manifest.json's host_permissions: api/main.py's own
// docstring skips CORS middleware because "a Manifest V3 extension
// with host_permissions for this service's domain is exempt from
// CORS" — but Architecture Section 6.1's own suggested manifest
// doesn't actually list the API domain in host_permissions (only
// linkedin.com, gmail.com, voicova.com). Confirmed the hard way: a
// real browser sent a CORS preflight OPTIONS to /api/check-draft and
// got 405, because host_permissions didn't cover this origin.
// manifest.json here has been corrected to include it — if this
// constant ever changes, that permission must change with it.
const API_BASE_URL = "https://voicova-api.up.railway.app";

async function refreshAccessToken() {
  const installation = await VoicovaStorage.getInstallation();
  if (!installation) return false;

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/extension/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        installation_id: installation.installationId,
        refresh_handle: installation.refreshHandle,
      }),
    });
  } catch {
    return false; // network failure — caller treats this the same as any other refresh failure
  }

  if (!response.ok) {
    // token_revoked here means the refresh handle itself is dead
    // (expired, or a reuse was detected server-side) — clear
    // everything so the panel goes to auth_required cleanly rather
    // than retrying against credentials that will never work again.
    await VoicovaStorage.clearAll();
    return false;
  }

  const body = await response.json();
  await VoicovaStorage.setAccessToken(body.access_token);
  await VoicovaStorage.setInstallation({
    refreshHandle: body.refresh_handle,
    installationId: installation.installationId,
  });
  return true;
}

async function _authedRequest(path, payload) {
  const accessToken = await VoicovaStorage.getAccessToken();
  if (!accessToken) return { ok: false, status: 401, errorCode: "token_revoked" };

  const attempt = async (token) => {
    let response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify(payload),
      });
    } catch {
      return { ok: false, status: 0, errorCode: "error_offline" };
    }
    const body = await response.json().catch(() => ({}));
    if (response.ok) return { ok: true, data: body };
    return { ok: false, status: response.status, errorCode: body?.detail?.error_code };
  };

  const first = await attempt(accessToken);
  if (first.ok || first.errorCode !== "token_expired") return first;

  // Retry once after a silent refresh — Architecture Section 6.4's
  // diagram exactly. A second failure is returned as-is; this never
  // loops more than once.
  const refreshed = await refreshAccessToken();
  if (!refreshed) return { ok: false, status: 401, errorCode: "token_revoked" };
  const newToken = await VoicovaStorage.getAccessToken();
  return attempt(newToken);
}

function checkDraft(draftText, surface) {
  return _authedRequest("/api/check-draft", { draft_text: draftText, surface });
}

function fixDraft(originalDraft, surface) {
  return _authedRequest("/api/fix", { original_draft: originalDraft, surface });
}

function disconnect() {
  // Same _authedRequest path as check/fix, not a one-off — the
  // backend route takes no request body (Depends(resolve_identity)
  // only), so an empty payload is simply ignored server-side.
  return _authedRequest("/api/extension/disconnect", {});
}

self.VoicovaApiClient = { checkDraft, fixDraft, disconnect, refreshAccessToken, API_BASE_URL };
})();
