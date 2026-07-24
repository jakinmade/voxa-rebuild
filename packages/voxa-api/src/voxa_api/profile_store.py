"""
Voxa — Checker Profile Store
Iteration 2: real persistence for the checker, keyed by email.

Disk-backed JSON, matching the CLEARANCE precedent of save/load run data
to disk rather than relying on in-memory session state. No external
service (Supabase, etc.) required to get this live today. Swappable
for a real database later without changing the endpoints below it.

A profile's baseline for each dimension is a majority vote across all
samples given so far. Each additional sample strengthens the baseline
rather than replacing it — the more you give it, the more confident
the baseline is, per the original v1 design.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from threading import Lock

_DATA_DIR = Path(os.environ.get("VOXA_DATA_DIR", "/tmp/voxa_data"))
_STORE_PATH = _DATA_DIR / "checker_profiles.json"
_lock = Lock()


def _normalise_email(email: str) -> str:
    return email.strip().lower()


def _load_all() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(json.dumps(data, indent=2))


def get_profile(email: str) -> dict | None:
    with _lock:
        return _load_all().get(_normalise_email(email))


def save_profile(email: str, profile: dict) -> None:
    with _lock:
        data = _load_all()
        data[_normalise_email(email)] = profile
        _save_all(data)


def delete_profile(email: str) -> bool:
    with _lock:
        data = _load_all()
        key = _normalise_email(email)
        if key in data:
            del data[key]
            _save_all(data)
            return True
        return False
