# QA Log — Voicova build sprint

Running list. Add as found, mark fixed, don't delete history.

## Wednesday

- [x] Rename sweep: app.py, prompts.py, voice_engine.py, README.md — verified zero remaining "Voxa"/"VOXA" in live-surface files
- [x] packages/, setup.py, pyproject.toml left untouched, deliberately — dormant monorepo, not live, rename would be wasted work before Friday's port
- [x] Profile export: export_profile() in storage.py, wired to a download button in app.py — syntax-checked, import-checked
- [x] Positioning copy ("The engine wrote as you. Not for you.") now always visible on render, not gated behind a mode
- [x] App boots clean under `streamlit run` — HTTP 200, no import/runtime errors
- [x] Real functional walkthrough: tests/integration/test_streamlit_app_flow.py — paste → fingerprint → screen transitions → export button → positioning copy, all verified via Streamlit's AppTest framework plus a direct unit test of export_profile(). Both pass under pytest. Render step (screen 4's live Anthropic call) deliberately excluded — not spending API tokens on an automated test.
- [ ] Still owed: one real render, manually, in a browser, with a live API key. Confirms the actual Claude call path end to end, which the test suite correctly doesn't touch.

## Thursday — cost guardrail check

Audited every Anthropic API call site in the live app (_run_render in app.py, _grammar_fix_pass in prompts.py). Findings, not yet acted on — flagging before touching anything:

- **Up to 3 API calls per single render**, not 1: main render call, an unconditional grammar-fix pass that runs on every render regardless of need, and a conditional correction pass that only fires if drift is detected.
- **max_tokens=4096 on all three calls**, uniformly. No caching anywhere in the render path.
- **No auto-retry** — the one call wrapped in try/except (the correction pass) fails silently and keeps the original render rather than retrying. That part's already correct.
- This is a different pattern from the "2 calls max, 1000-1500 tokens, 24hr cache" standard already established elsewhere (SEAM). Worth a decision on whether the render path should match that standard, or whether 3 calls / 4096 tokens is deliberate given what each pass does. Not changing this without direction — it's existing, shipped logic, and max_tokens changes could affect output quality on longer renders.

**Decision (4 August):** deferred to Friday's port, deliberately, not dropped. Rationale: Streamlit's session_state has no persistence to cache against, so real session-level caching only becomes possible once Supabase is in place — fixing the call count/token size now on Streamlit means doing the work twice. Exposure between now and Friday is low (unreleased, manual QA traffic only, no real users). Real risk window is post-launch, which is after this fix lands anyway. Locked into the Friday iteration of the build plan as an explicit task, not an assumption.

