"""
api/schemas/extension_auth.py — pydantic models for the identity/
lifecycle endpoints (Full Spec Section 3.3.3, Engineering Architecture
Section 4.3): link, refresh, disconnect.

link and refresh are deliberately NOT behind resolve_identity (Section
4.2) — link is how an access token comes to exist in the first place,
and refresh exists specifically for when the access token has expired.
Each carries its own, different proof of identity instead: link trusts
the existing voicova.com device-cookie value (Section 4.3's "the web
session itself is the proof of identity here"); refresh trusts
possession of the current refresh handle, checked via the atomic
rotation in tokens.py.
"""
from __future__ import annotations

from pydantic import BaseModel


class LinkRequest(BaseModel):
    # The existing voicova.com device-cookie value (persistence.py's
    # get_or_create_device_id) — a random UUIDv4, already the sole
    # credential this entire product trusts everywhere else (Section
    # 4.3 device_identity note). Not a new, weaker trust boundary:
    # the same bearer credential, extended to a second surface.
    device_identity: str


class LinkResponse(BaseModel):
    installation_id: str
    access_token: str
    refresh_handle: str


class RefreshRequest(BaseModel):
    installation_id: str
    refresh_handle: str


class RefreshResponse(BaseModel):
    access_token: str
    refresh_handle: str


class DisconnectResponse(BaseModel):
    disconnected: bool
