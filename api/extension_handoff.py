"""
api/extension_handoff.py — the browser-facing LINK handoff page,
shared by both places that need it: profile recovery (GET /api/
profile/recover) and the normal "connect this device" flow (GET
/api/extension/link-page, added 7 Sept 2026 to close the gap that a
device's *first* connection had no working frontend at all — see
routes/extension_link_page.py's own module docstring).

Pulled out of profile_recovery.py into its own module rather than
duplicated, once a second caller needed the identical mechanism —
same LINK message shape service_worker.js's onMessageExternal
listener already handles for both callers, same externally_connectable
requirement (the page must be served from an origin listed there;
manifest.json currently lists voicova.com and this API's own domain,
not a Streamlit-embedded iframe — see extension_link_page.py's
docstring for why that distinction matters), same fallback UI for
"couldn't reach the extension automatically".
"""
from __future__ import annotations

import json as _json
import os

from fastapi.responses import HTMLResponse

_EXTENSION_ID_ENV = "VOICOVA_EXTENSION_ID"
# CORRECTED 8 Sept 2026 — this ID is a DEV-ONLY placeholder, not a
# stable published one. It's derived from the openssl-generated public
# key currently pinned in voicova-extension/manifest.json's "key"
# field, which keeps unpacked/dev-mode installs on the same ID across
# reloads, but Chrome Web Store rejects a *first-time* upload whose
# manifest contains a "key" field at all ("key field is not allowed in
# manifest") — so this ID will NOT survive Store publish unchanged.
# See scripts/build_store_zip.py for the submission build (strips
# "key") and the correct 6-step sequence for locking in the real,
# Chrome-issued ID afterward. Do not treat this default as the
# production ID until VOICOVA_EXTENSION_ID has been updated to the
# real Item ID shown on the Developer Dashboard post-publish.
_DEFAULT_EXTENSION_ID = "dkgmcpdgfgcoeneodjhkdgmdmjmghbog"


def link_handoff_html(link_result: dict, *, connecting_verb: str = "Reconnecting") -> HTMLResponse:
    """link_result must have installation_id/access_token/refresh_handle
    (the exact LinkResponse shape both callers already produce).
    connecting_verb lets the two callers say "Connecting" (first-time)
    vs "Reconnecting" (recovery) without a second near-duplicate
    template — the mechanism and fallback behavior are identical
    either way."""
    extension_id = os.environ.get(_EXTENSION_ID_ENV, _DEFAULT_EXTENSION_ID)
    payload = _json.dumps({
        "type": "LINK",
        "access_token": link_result["access_token"],
        "refresh_handle": link_result["refresh_handle"],
        "installation_id": link_result["installation_id"],
    })

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{connecting_verb} your VOICOVA profile</title>
<style>
  body {{ font-family: -apple-system, Arial, sans-serif; max-width: 480px; margin: 4rem auto; padding: 0 1.5rem; color: #111827; text-align: center; }}
  .status {{ font-size: 1.1rem; margin-top: 1.5rem; }}
  .fallback {{ display: none; margin-top: 2rem; padding: 1rem; background: #f3f4f6; border-radius: 8px; font-size: 0.85rem; word-break: break-all; text-align: left; }}
  .fallback.show {{ display: block; }}
</style>
</head>
<body>
  <h2>VOICOVA</h2>
  <p class="status" id="status">{connecting_verb} your voice profile…</p>
  <div class="fallback" id="fallback">
    <p>Couldn't reach the extension automatically. If it's installed, open the VOICOVA popup and it should reconnect shortly — or contact support with this reference:</p>
    <code id="installation-id"></code>
  </div>
  <script>
    const EXTENSION_ID = "{extension_id}";
    const payload = {payload};
    document.getElementById("installation-id").textContent = payload.installation_id;

    function showFallback(message) {{
      document.getElementById("status").textContent = message;
      document.getElementById("fallback").classList.add("show");
    }}

    if (!window.chrome || !chrome.runtime || !chrome.runtime.sendMessage) {{
      showFallback("Open this link in Chrome with the VOICOVA extension installed.");
    }} else {{
      chrome.runtime.sendMessage(EXTENSION_ID, payload, (response) => {{
        if (chrome.runtime.lastError || !response || !response.linked) {{
          showFallback("Almost there — this is taking longer than expected.");
          return;
        }}
        document.getElementById("status").textContent = "Connected. You can close this tab and go back to LinkedIn.";
      }});
      // externally_connectable calls with no listening extension (not
      // installed, or a mismatched ID) fail silently with no callback
      // at all in some Chrome versions — a timeout fallback covers
      // that case, not just an explicit lastError.
      setTimeout(() => {{
        if (document.getElementById("status").textContent.includes("{connecting_verb}")) {{
          showFallback("Couldn't reach the extension automatically.");
        }}
      }}, 4000);
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})
