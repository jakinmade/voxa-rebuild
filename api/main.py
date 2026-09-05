"""
api/main.py — FastAPI application entrypoint, deployed as its own
standalone Railway service (Engineering Architecture Section 4.1),
NOT mounted inside the existing Streamlit process.

No CORS middleware: the Chrome extension's background service worker
makes these calls (Section 2.2 — content scripts never hold tokens or
call the API directly; only the background worker does, per message
passing in Section 6.4), and a Manifest V3 extension with
host_permissions for this service's domain is exempt from CORS on
requests it makes itself — that's the standard, documented Chrome
extension mechanism for exactly this case, not an oversight. Revisit
only if a future caller needs to hit this API directly from a web
page's own JS context (not true for the pilot: Section 4.1 confirms
the extension is the only caller, "not via voicova.com").
"""
from __future__ import annotations

from fastapi import FastAPI

from logging_config import configure_logging, get_logger
from api.routes.check_draft import router as check_draft_router
from api.routes.extension_auth import router as extension_auth_router

configure_logging()
log = get_logger(__name__)

app = FastAPI(title="VOICOVA Chrome-First API")

app.include_router(check_draft_router)
app.include_router(extension_auth_router)


@app.get("/")
def health():
    # Railway healthcheckPath target (Section 4.1) — deliberately
    # trivial: this must never depend on Supabase or any other
    # downstream service being up, or a transient Supabase blip would
    # get misread as this service being down and trigger an
    # unnecessary restart.
    return {"status": "ok"}
