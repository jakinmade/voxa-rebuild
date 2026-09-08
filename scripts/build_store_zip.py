"""
scripts/build_store_zip.py — builds the Chrome Web Store submission ZIP
from voicova-extension/, per the Go-Live Playbook Section 2.1 sequence.

WHY THIS EXISTS: voicova-extension/manifest.json currently carries a
self-generated "key" field (openssl, not Chrome's own keypair flow).
Chrome Web Store rejects a *first-time* upload that contains a manifest
"key" field outright ("key field is not allowed in manifest"). The key
stays in the source manifest because it's still useful for local
unpacked/dev-mode installs (keeps the dev extension ID stable across
reloads) — this script produces a submission-only copy with that field
stripped, leaving the source file untouched.

CORRECT SEQUENCE (do not skip steps or reorder):
  1. Run this script -> dist/voicova-store-submission.zip (no "key").
  2. Upload that ZIP to the Chrome Web Store Developer Dashboard as a
     NEW item. Do not publish yet.
  3. Dashboard -> Package tab -> "View public key" -> copy it.
  4. Paste that real Chrome-issued key into voicova-extension/
     manifest.json's "key" field, REPLACING the current openssl one.
  5. Update VOICOVA_EXTENSION_ID (or _DEFAULT_EXTENSION_ID in
     api/extension_handoff.py) to the real Item ID shown on the
     dashboard.
  6. Redeploy the API. Confirm chrome://extensions shows the same ID
     as the dashboard's "Item ID" before telling anyone to install it.

Until step 4 is done, the ID baked into api/extension_handoff.py
(_DEFAULT_EXTENSION_ID) is a DEV-ONLY placeholder, not the real
published ID — see the correction in that file's own docstring.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "voicova-extension"
DIST_DIR = Path(__file__).resolve().parent.parent / "dist"
OUTPUT_ZIP = DIST_DIR / "voicova-store-submission.zip"

# Files/dirs that must never end up inside the submission ZIP even if
# someone later adds a .git, dev_tools/, or editor-swap file under
# voicova-extension/ — Section 2.2's "confirm the package is clean".
EXCLUDE_NAMES = {".git", ".gitignore", "dev_tools", "__pycache__", ".DS_Store"}


def _should_include(path: Path) -> bool:
    return not any(part in EXCLUDE_NAMES for part in path.parts)


def build() -> Path:
    if not SRC_DIR.is_dir():
        raise SystemExit(f"Extension source not found at {SRC_DIR}")

    manifest_path = SRC_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    had_key = "key" in manifest
    manifest.pop("key", None)  # <-- the actual Section 2.1 fix

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_DIR.mkdir(parents=True)

    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(SRC_DIR.rglob("*")):
            if path.is_dir() or not _should_include(path.relative_to(SRC_DIR)):
                continue
            arcname = path.relative_to(SRC_DIR)
            if arcname.name == "manifest.json":
                zf.writestr(str(arcname), json.dumps(manifest, indent=2))
            else:
                zf.write(path, arcname)

    print(f"Wrote {OUTPUT_ZIP}")
    print(f"Manifest 'key' field present in source: {had_key}")
    print("Stripped from submission ZIP: True")
    print("manifest.json sits at the ZIP root, not inside a subfolder: verified by construction.")
    return OUTPUT_ZIP


if __name__ == "__main__":
    build()
