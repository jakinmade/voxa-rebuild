# QA Log — Voicova build sprint

Running list. Add as found, mark fixed, don't delete history.

## Wednesday

- [x] Rename sweep: app.py, prompts.py, voice_engine.py, README.md — verified zero remaining "Voxa"/"VOXA" in live-surface files
- [x] packages/, setup.py, pyproject.toml left untouched, deliberately — dormant monorepo, not live, rename would be wasted work before Friday's port
- [x] Profile export: export_profile() in storage.py, wired to a download button in app.py — syntax-checked, import-checked
- [x] Positioning copy ("The engine wrote as you. Not for you.") now always visible on render, not gated behind a mode
- [x] App boots clean under `streamlit run` — HTTP 200, no import/runtime errors
- [ ] Not yet tested: actual end-to-end click-through (paste → fingerprint → render → export) in a real browser. Do this before Thursday starts — headless boot confirms it starts, not that the full flow works.

