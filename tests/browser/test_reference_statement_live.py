"""
Live-browser verification of the Reference Statement panel
(_reference_statement_panel, screen_my_voice — 8 Sept 2026,
VOICOVA_Reference_Statement_Design.docx).

SKIPPED BY DEFAULT, same convention and same reason as
test_paste_guard_live.py: a real Streamlit subprocess plus a real
Chromium browser, slow and fundamentally different from the rest of
this suite. Run explicitly with:

    RUN_BROWSER_TESTS=1 pytest tests/browser/test_reference_statement_live.py -v

WHY THIS EXISTS

Every other test covering this panel (tests/integration/
test_my_voice_screen.py) drives it via Streamlit's AppTest framework,
which — as test_streamlit_app_flow.py's own comment already documents
for Screen 3 — cannot drive paste_guard's actual custom JS component
directly. Those tests work around it by setting
reference_statement_draft in session_state directly, which proves the
Python-side save/validation logic is correct but proves nothing about
whether a real person, in a real browser, can actually type into this
panel and have paste genuinely blocked — the exact thing
test_paste_guard_live.py already proved once for Screen 3's starters,
but never for this new panel specifically. This file closes that gap
for the second, independent instance of paste_guard this feature adds.

Reuses the SAME executable_path workaround this session's own
extension e2e tests needed: Playwright's browser-download CDN isn't
in this sandbox's network allowlist, but a cached Puppeteer Chrome
binary is — Playwright can launch an arbitrary Chrome executable
directly via executable_path, so no download is needed either way.

Three claims checked, mirroring test_paste_guard_live.py's own three:
  1. The full real flow (Screen 1 -> 2 -> 3 -> 4 -> sidebar -> My
     Voice) actually reaches the panel without crashing.
  2. Typed input into the panel's real paste_guard component
     round-trips to the Streamlit backend and a real Save click
     produces the real success message.
  3. A genuine Ctrl+V paste into this panel's textarea is hard-blocked,
     identically to Screen 3's.
"""
import os
import subprocess
import time

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Live-browser test — spins up a subprocess Streamlit server "
           "and a real Chromium instance. Run explicitly with "
           "RUN_BROWSER_TESTS=1, not as part of the default suite.",
)

_PORT = 8798  # distinct from test_paste_guard_live.py's 8799, safe to run either independently
_APP_URL = f"http://localhost:{_PORT}"
_CHROME_BINARY = "/home/claude/.cache/puppeteer/chrome/linux-131.0.6778.204/chrome-linux64/chrome"

_LONG_SAMPLE = """I think we should move fast on this project. I want the team to focus on
the core problem first, and then look at the edges later once we have something working.
I believe the data backs this up clearly, and I think it is the right call for now given
everything we know. We need to move quickly and stay focused on what matters most here,
rather than getting distracted by side issues that do not actually change the outcome.

Last week I sat down with the whole team and walked through the numbers again. What struck
me was how consistent the pattern has been across every region we looked at. I want us to
act on this before the quarter closes, because waiting any longer just means we lose the
advantage we currently have. I have said this before and I will say it again: speed matters
more than perfection at this stage of the project, and I think everyone on the team actually
agrees with that, even if nobody has said it out loud in the meeting yet.

My honest view is that we have been overthinking the rollout plan. I believe the simplest
version of this will outperform anything more complicated, and I think we should ship it
this week rather than next month. I want to be clear that I am not dismissing the risks,
I just think the risks of moving slowly are bigger than the risks of moving fast here."""

_STARTER_0_TEXT = (
    "This completely misses what I actually asked for, and I need to say so plainly "
    "before this goes any further, because reworking it later costs everyone more time."
)
_STARTER_3_TEXT = (
    "Honestly this has been bothering me all afternoon and I cannot quite let it go, "
    "even though I know it is a small thing in the grand scheme of everything else."
)


@pytest.fixture(scope="module")
def live_app():
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = "test-key-not-real-for-browser-test"

    proc = subprocess.Popen(
        [
            "streamlit", "run", "app.py",
            "--server.port", str(_PORT),
            "--server.headless", "true",
            "--server.address", "0.0.0.0",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    time.sleep(10)
    yield
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def my_voice_page(live_app):
    """Drives a real browser through the ENTIRE real flow — Screen 1
    paste, Screen 2 continue, both Screen 3 required starters (typed,
    matching the real word floor), Screen 4, then a real sidebar click
    to My Voice — landing exactly where a real person would after
    finishing onboarding and choosing to check their profile. No step
    here is faked or bypassed; this is the actual onboarding path
    every real user goes through."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=_CHROME_BINARY, args=["--no-sandbox"])
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.goto(_APP_URL, timeout=30000)
        page.wait_for_timeout(3000)
        page.get_by_role("button", name="Get started \u2192").click()
        page.wait_for_timeout(2000)

        textarea = page.locator("textarea").first
        textarea.click()
        textarea.fill(_LONG_SAMPLE)
        page.keyboard.press("Tab")
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Show me my fingerprint \u2192").click()
        page.wait_for_timeout(5000)
        page.get_by_role("button", name="Continue \u2192").click()
        page.wait_for_timeout(4000)

        # Screen 3 — both required starters, real typed input via each
        # one's own real paste_guard iframe (same component this
        # panel reuses).
        guard_frames = [f for f in page.frames if "paste_guard" in f.url]
        assert len(guard_frames) >= 2, "expected at least the two required Screen 3 starters"
        guard_frames[0].locator("#ta").click()
        guard_frames[0].locator("#ta").type(_STARTER_0_TEXT, delay=5)
        page.wait_for_timeout(800)
        guard_frames[1].locator("#ta").click()
        guard_frames[1].locator("#ta").type(_STARTER_3_TEXT, delay=5)
        page.wait_for_timeout(800)

        page.get_by_role("button", name="Continue \u2192").click()
        page.wait_for_timeout(4000)

        # Real sidebar click, not a session_state shortcut.
        page.get_by_role("button", name="My Voice").click()
        page.wait_for_timeout(2500)

        yield page
        browser.close()


def test_reaches_my_voice_screen_via_the_real_flow(my_voice_page):
    headlines = my_voice_page.locator(".headline").all_inner_texts()
    assert "Your voice." in headlines


def test_reference_statement_panel_is_present(my_voice_page):
    expander = my_voice_page.get_by_text("Give Fix-it one real example to aim for")
    assert expander.count() > 0


def test_typed_reference_statement_round_trips_saves_and_blocks_paste(my_voice_page):
    """Combines the round-trip-save and paste-block checks into one
    continuous session — a fresh page reload has no real persistence
    backend in this test (no live Supabase credentials configured),
    so it correctly falls back to the landing screen exactly as a
    real browser with no device-cookie match would; that's expected
    behaviour, not something to route around with a second full
    onboarding pass just for this check."""
    my_voice_page.get_by_text("Give Fix-it one real example to aim for").click()
    my_voice_page.wait_for_timeout(1000)

    guard_frames = [f for f in my_voice_page.frames if "paste_guard" in f.url]
    assert len(guard_frames) >= 1
    ref_frame = guard_frames[-1]
    ref_textarea = ref_frame.locator("#ta")

    typed_text = (
        "We closed our biggest quarter yet and the whole team pulled together to make it happen "
        "under real pressure, and I could not be more proud of how everyone showed up for it."
    )
    ref_textarea.click()
    ref_textarea.type(typed_text, delay=5)
    my_voice_page.wait_for_timeout(1500)

    my_voice_page.get_by_role("button", name="Save").last.click()
    my_voice_page.wait_for_timeout(2500)

    success = my_voice_page.get_by_text("Saved. Fix-it will use this as a priority example.")
    assert success.count() > 0

    # Paste-block check, same session, same panel now labelled "Update".
    my_voice_page.get_by_text("Update your reference statement").click()
    my_voice_page.wait_for_timeout(2500)
    guard_frames = [f for f in my_voice_page.frames if "paste_guard" in f.url]
    ref_textarea = guard_frames[-1].locator("#ta")
    ref_textarea.wait_for(state="visible", timeout=10000)

    ref_textarea.click()
    my_voice_page.keyboard.type("Original typed content here.", delay=10)
    my_voice_page.wait_for_timeout(500)
    my_voice_page.evaluate("() => navigator.clipboard.writeText('PASTED CONTENT SHOULD BE BLOCKED')")
    my_voice_page.keyboard.press("Control+A")
    my_voice_page.keyboard.press("Control+V")
    my_voice_page.wait_for_timeout(1000)

    value_after = ref_textarea.input_value()
    assert "PASTED CONTENT" not in value_after
