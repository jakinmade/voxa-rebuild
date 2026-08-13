"""
Live-browser verification of components/paste_guard — the one piece of
this codebase the original author explicitly flagged as unable to
verify without a real browser session (see the note that used to be in
index.html, and __init__.py's docstring, both updated August 2026 once
this file closed that gap).

SKIPPED BY DEFAULT. This spins up a real Streamlit subprocess and a
real headless Chromium browser via Playwright — slow (tens of seconds),
requires chromium to be installed (`python -m playwright install
chromium`), and is a fundamentally different kind of test from
everything else in tests/unit and tests/integration (which are all
fast, in-process, no subprocess, no browser). Bundling this into the
default `pytest tests/` run would slow down and could flake every
ordinary test run for a check that only needs to happen when
paste_guard's actual JS changes, not on every commit.

Run explicitly with:
    RUN_BROWSER_TESTS=1 pytest tests/browser/test_paste_guard_live.py -v

What this proves that no other test in this codebase can: the actual
JS behaviour of paste_guard's index.html running in a real browser
against a real running instance of app.py — not the Python side (no
Python test can exercise a paste event, a clipboard, or a drop event,
since none of that exists on the Python side of a Streamlit custom
component). Three specific claims from paste_guard's own code comments
are checked directly:
  1. Typed input round-trips to the Streamlit backend (word counter,
     which is rendered server-side from the component's returned
     value, actually updates).
  2. A genuine Ctrl+V paste with real clipboard content is hard-blocked
     (preventDefault fires; zero pasted text reaches the field).
  3. A synthetic drop event is defaultPrevented; zero dropped text
     reaches the field.
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

_PORT = 8799
_APP_URL = f"http://localhost:{_PORT}"

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


@pytest.fixture(scope="module")
def live_app():
    """Starts a real app.py subprocess on a fixed local port, tears it
    down after the module's tests finish. Uses a dummy API key — no
    test in this file triggers an actual render/API call, only the
    Screen 1 -> 2 -> 3 navigation and the paste_guard component itself."""
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
    time.sleep(6)  # Streamlit's own startup, not negotiable lower
    yield
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def guard_page(live_app):
    """Navigates a real browser through Screen 1 -> 2 -> 3, landing on
    the required-starters screen where paste_guard actually renders.
    Returns the Playwright page positioned there, ready for the tests
    below to interact with the component directly."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        # clipboard-write/-read needed for test_real_paste_is_hard_blocked,
        # which writes real content to the clipboard via navigator.clipboard
        # before firing a genuine Ctrl+V - without this grant, the write
        # itself fails with NotAllowedError before the actual paste-block
        # behaviour is ever exercised. Found by actually running this file,
        # not assumed from the ad hoc script it was written up from.
        context = browser.new_context(permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.goto(_APP_URL, timeout=30000)
        page.wait_for_timeout(3000)

        textarea = page.locator("textarea").first
        textarea.click()
        textarea.fill(_LONG_SAMPLE)
        page.keyboard.press("Tab")
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Show me my fingerprint \u2192").click()
        page.wait_for_timeout(5000)
        page.get_by_role("button", name="Continue \u2192").click()
        page.wait_for_timeout(4000)

        yield page
        browser.close()


def test_reaches_screen3_with_paste_guard_iframes(guard_page):
    headlines = guard_page.locator(".headline").all_inner_texts()
    assert headlines == ["Two more samples."]

    guard_frames = [f for f in guard_page.frames if "paste_guard" in f.url]
    assert len(guard_frames) >= 1


def test_typed_input_round_trips_to_streamlit_backend(guard_page):
    guard_frames = [f for f in guard_page.frames if "paste_guard" in f.url]
    guard_textarea = guard_frames[0].locator("#ta")

    typed_text = "This is a real typed reply about pushing back on moving too fast."
    guard_textarea.click()
    guard_textarea.type(typed_text, delay=15)
    guard_page.wait_for_timeout(2500)

    # The word counter is rendered server-side from the component's
    # returned value (completions[idx] in app.py) - if this updates,
    # the value genuinely reached the Python backend, not just the
    # browser's local DOM.
    counter = guard_page.locator("text=/\\d+ \\/ 10 words/").first.inner_text()
    word_count = int(counter.split(" / ")[0])
    assert word_count >= 10


def test_real_paste_is_hard_blocked(guard_page):
    guard_frames = [f for f in guard_page.frames if "paste_guard" in f.url]
    guard_textarea = guard_frames[0].locator("#ta")

    guard_textarea.click()
    guard_page.keyboard.type("Original typed content here.", delay=10)
    guard_page.wait_for_timeout(500)

    guard_page.evaluate("() => navigator.clipboard.writeText('PASTED CONTENT SHOULD BE BLOCKED')")
    guard_page.keyboard.press("Control+A")
    guard_page.keyboard.press("Control+V")
    guard_page.wait_for_timeout(1000)

    value_after = guard_textarea.input_value()
    assert "PASTED CONTENT" not in value_after
    assert "Original typed content" in value_after

    # Visual confirmation the component's own warning state fired - not
    # just that the paste silently failed for an unrelated reason. The
    # hint text lives inside the component's own iframe DOM, not the
    # top-level page - searching guard_page directly (as an earlier
    # version of this test did) finds nothing, since Playwright's
    # locator only searches the frame it's called on, not descendant
    # iframes. Caught by actually running this file.
    warning = guard_frames[0].locator("text=Pasting is disabled here on purpose")
    assert warning.count() > 0


def test_drag_and_drop_insertion_is_blocked(guard_page):
    guard_frames = [f for f in guard_page.frames if "paste_guard" in f.url]
    first_guard = guard_frames[0]

    result = first_guard.evaluate("""() => {
        const ta = document.getElementById('ta');
        const dt = new DataTransfer();
        dt.setData('text/plain', 'DROPPED CONTENT SHOULD BE BLOCKED');
        const dropEvent = new DragEvent('drop', {
            bubbles: true, cancelable: true, dataTransfer: dt,
        });
        const wasDefaultPrevented = !ta.dispatchEvent(dropEvent);
        return { wasDefaultPrevented, valueAfter: ta.value };
    }""")

    assert result["wasDefaultPrevented"] is True
    assert "DROPPED CONTENT" not in result["valueAfter"]
