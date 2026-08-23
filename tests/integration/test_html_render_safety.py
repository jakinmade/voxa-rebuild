"""
Regression coverage for the copy-to-clipboard rendering bug (23 Aug
2026, reported live): Streamlit's AppTest framework — everything else
in this test suite — never runs a real browser. It executes the
Python script and checks for exceptions and session-state correctness,
but it does not parse the HTML string, does not run a markdown-to-HTML
renderer, and does not execute JavaScript. Two real bugs shipped
because of that gap:

1. A multi-line f-string passed to st.markdown(unsafe_allow_html=True)
   with each line indented to match the surrounding Python code gets
   treated as an indented code block by Streamlit's frontend markdown
   parser and rendered as literal visible text instead of parsed HTML.
2. json.dumps(output) (a double-quoted JSON string) embedded directly
   inside a double-quoted onclick="..." HTML attribute: any apostrophe
   in the actual rendered text breaks the attribute early and
   corrupts the element.

Neither is a Python exception, a session-state bug, or anything the
rest of this suite's AppTest-based approach can see. This file closes
that specific gap with two techniques that don't require a real
browser:

- A static source scan (test_no_indented_html_in_markdown_calls) that
  fails if this exact anti-pattern reappears anywhere in app.py, for
  ANY st.markdown(unsafe_allow_html=True) call, not just the ones
  fixed this session — including the Voice Report card, which still
  uses the same indented pattern and was deliberately left unfixed
  pending a live browser confirmation (see the commit that introduced
  this test). If that block is later touched, this test will still
  correctly flag it as using the risky pattern.
- An HTML-well-formedness + attribute-safety check
  (test_copy_button_html_is_well_formed_and_has_no_quote_collision)
  that drives a real render through AppTest with apostrophe-containing
  text and parses the actual emitted markdown string with Python's
  html.parser, catching malformed/unclosed tags and confirming the
  raw text never appears unescaped inside an HTML attribute.

This does not replace a real browser smoke test — it cannot execute
CSS or JavaScript, and cannot confirm click behavior. It closes the
specific, mechanical gap that let these two bugs through undetected.
"""
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch

from streamlit.testing.v1 import AppTest
from voice_engine import compute_baseline_metrics, analyse_writing, _score_sample_fitness

_APP_PATH = Path(__file__).resolve().parents[2] / "app.py"


def test_no_indented_html_in_markdown_calls():
    """Static scan: every st.markdown(f\"\"\"...\"\"\", unsafe_allow_html=True)
    call (or plain \"\"\"...\"\"\") must not have its HTML content indented,
    since Streamlit's frontend markdown parser treats 4+ leading spaces
    as an indented code block and renders the content as literal text
    instead of parsing it as HTML. Scans the raw source directly —
    doesn't execute the app — so it also flags blocks nobody has
    manually re-tested in a live browser yet, like the Voice Report
    card, rather than silently trusting they're fine."""
    source = _APP_PATH.read_text()

    # Matches an f-string or plain triple-quoted string starting right
    # after st.markdown(, capturing the content up to the closing """.
    pattern = re.compile(r'st\.markdown\(\s*f?"""(.*?)"""', re.DOTALL)
    offenders = []
    for match in pattern.finditer(source):
        body = match.group(1)
        lines = body.split("\n")
        # Only the FIRST non-blank content line matters for whether
        # Streamlit's markdown parser recognises this as an HTML block
        # vs an indented code block - once it's correctly recognised
        # as HTML (first line unindented and starting with a tag),
        # content further inside (e.g. CSS rules inside <style>, or
        # deliberately nested HTML for readability) is fine indented.
        first_content_line = next((l for l in lines if l.strip()), "")
        if first_content_line.startswith("    "):
            line_no = source[: match.start()].count("\n") + 1
            offenders.append(f"line ~{line_no}: {first_content_line.strip()[:60]!r}")

    assert not offenders, (
        "Found st.markdown(...) call(s) with indented multi-line HTML, "
        "which Streamlit's frontend renders as a literal code block "
        "instead of parsed HTML (confirmed live, 23 Aug 2026 copy-"
        "button bug). Flatten to a zero-indent single-line string, or "
        "build the HTML via string concatenation without embedded "
        "newlines, same pattern used elsewhere in this file:\n"
        + "\n".join(offenders)
    )


class _StrictHTMLValidator(HTMLParser):
    """Fails on any tag that never closes - the specific failure mode
    of the quote-collision bug, where a corrupted attribute value
    swallows the rest of the markup and the button/textarea tag is
    left permanently open."""

    def __init__(self):
        super().__init__()
        self.open_tags = []

    def handle_starttag(self, tag, attrs):
        if tag not in ("br", "hr", "img", "input", "meta"):
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if self.open_tags and self.open_tags[-1] == tag:
            self.open_tags.pop()


def _fake_anthropic_response(text: str):
    block = MagicMock()
    block.text = text
    resp = MagicMock()
    resp.content = [block]
    return resp


def _clear_review_gate_if_present(at: AppTest):
    """If this render's flagged risk gated the output behind the
    review-confirmation checkbox, clear it so show_output becomes
    True and the copy button can render - same helper pattern as
    test_render_accounting_fix.py, mirrors what a real user does."""
    checkbox = next((c for c in at.checkbox if c.key and c.key.startswith("confirm_checkbox_")), None)
    if checkbox is not None:
        checkbox.check().run()
        confirm_button = next((b for b in at.button if b.key and b.key.startswith("confirm_button_")), None)
        if confirm_button is not None:
            confirm_button.click().run()


def test_copy_button_html_is_well_formed_and_has_no_quote_collision():
    """Drives a real render, with output text containing an apostrophe
    (the exact trigger for the quote-collision bug - json.dumps only
    escapes double quotes, not single quotes/apostrophes), and checks
    the actual copy-button markdown string that gets sent to the
    frontend. Two checks, either of which alone would have caught the
    live bug:
    1. HTMLParser can walk the string with no tags left open at the
       end - an unescaped quote inside an attribute truncates the
       attribute early, which either breaks the parse or leaves the
       button/textarea tag unclosed.
    2. The raw apostrophe-containing text never appears directly
       inside an onclick="..." attribute - it must only appear inside
       the escaped <textarea> content, which is what the fix requires.
    """
    fake_output = "It's the plan we discussed. It wasn't finished, but it's close."
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key-not-real"}):
        with patch("anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = _fake_anthropic_response(fake_output)

            at = AppTest.from_file(str(_APP_PATH))
            at.session_state["screen"] = 1
            at.run()

            sample = (
                "I think we should move fast on this. I want the team to focus "
                "on the core problem first, and then look at the edges of it."
            )
            at.session_state["screen"] = 4
            at.session_state["raw_text"] = sample
            at.session_state["baseline_fingerprint"] = compute_baseline_metrics(sample)
            at.session_state["observations"] = analyse_writing(sample)
            at.session_state["sample_fitness"] = _score_sample_fitness(sample)
            at.session_state["fingerprint_samples"] = [compute_baseline_metrics(sample)]
            at.session_state["fingerprint_sample_texts"] = [sample]
            at.session_state["sample2_completions"] = ["", "", "", ""]
            at.session_state["_device_id"] = "test-device-copybtn"
            at.run()
            assert not at.exception

            at.text_area[0].input("Please write a short note about the launch plan.")
            at.button[0].click()
            at.run()
            assert not at.exception
            _clear_review_gate_if_present(at)

    copy_markdown = None
    for md in at.markdown:
        if "copybtn_" in md.value and "<button" in md.value:
            copy_markdown = md.value
            break

    assert copy_markdown is not None, (
        "No copy-button markdown found on the rendered output screen - "
        "either the render failed to reach the output stage, or the "
        "copy button's key/marker changed and this test needs updating."
    )

    validator = _StrictHTMLValidator()
    validator.feed(copy_markdown)
    assert not validator.open_tags, (
        f"Copy-button HTML left tag(s) unclosed after parsing: "
        f"{validator.open_tags} - a truncated attribute (the quote-"
        f"collision bug) is the most likely cause. Raw markup:\n{copy_markdown}"
    )

    onclick_match = re.search(r'onclick="([^"]*)"', copy_markdown)
    assert onclick_match, "No onclick=\"...\" attribute found on the copy button."
    assert "It's" not in onclick_match.group(1) and "wasn't" not in onclick_match.group(1), (
        "The apostrophe-containing render text leaked directly into the "
        "onclick attribute instead of staying inside the escaped hidden "
        "textarea - this is exactly the quote-collision bug reintroduced."
    )


# ---------------------------------------------------------------------------
# HTML injection regression tests (added alongside the html.escape() fixes
# to every dynamic-content unsafe_allow_html call site — Phase 1 of the
# hardening build order). Each test pastes a payload containing an
# HTML/script-shaped string into a field that is known to reach
# st.markdown(..., unsafe_allow_html=True) unescaped-content risk, and
# asserts the payload never survives as a live, parseable tag in the
# rendered markdown — only as inert, escaped text.
# ---------------------------------------------------------------------------

_HTML_PAYLOAD = '<img src=x onerror="alert(1)">'
_SCRIPT_PAYLOAD = "<script>alert(1)</script>"


def _assert_payload_neutralised(markdown_values: list[str], payload: str, context: str):
    """A payload is neutralised if it never appears as a live tag - i.e.
    it's either absent, or present only in its html.escape()'d form
    (&lt;...&gt;). Presence of the raw '<img' / '<script' substring
    means it would parse as a real element in a browser."""
    raw_hits = [md for md in markdown_values if payload in md]
    assert not raw_hits, (
        f"Unescaped HTML payload survived into rendered markdown ({context}). "
        f"This means user- or model-derived text is reaching "
        f"st.markdown(..., unsafe_allow_html=True) without html.escape() - "
        f"a stored HTML/script injection path. Offending markdown:\n"
        + "\n---\n".join(raw_hits)
    )


def test_starter_anchor_sentences_are_escaped():
    """_build_starters() anchors Screen 3 starter prompts to sentences
    pulled directly from the user's Screen 1 paste (see _ANCHOR_TEMPLATES
    in app.py), then screen_sample2() interpolates the resulting starter
    string into an f-string passed to st.markdown(unsafe_allow_html=True).
    If a user pastes an HTML/script-shaped sentence as their Screen 1
    sample, it becomes an anchor sentence and must render as inert text,
    not a live tag."""
    payload_sentence = (
        f'Before we start, one more thing to check. {_HTML_PAYLOAD} is what I '
        f"meant by that earlier comment. Anyway let's move forward with the plan."
    )

    at = AppTest.from_file(str(_APP_PATH))
    at.session_state["screen"] = 3
    at.session_state["raw_text"] = payload_sentence
    at.session_state["sample2_completions"] = ["", "", "", ""]
    at.run()
    assert not at.exception

    markdown_values = [md.value for md in at.markdown]
    _assert_payload_neutralised(markdown_values, _HTML_PAYLOAD, "Screen 3 starter anchor")


def test_observation_evidence_quotes_are_escaped():
    """screen_reveal() extracts a quoted fragment out of each
    observation's body text via regex (quote_match) and interpolates it
    into a 'voice-check-evidence' div. Since observation bodies are
    themselves built from the user's own writing, a quoted HTML/script
    fragment inside the user's paste must not survive as live markup."""
    sample = (
        f"I do not know if this will work. {_SCRIPT_PAYLOAD} is what I "
        f"actually meant by that comment. I want to be direct about it."
    )

    at = AppTest.from_file(str(_APP_PATH))
    at.session_state["screen"] = 2
    at.session_state["raw_text"] = sample
    at.session_state["observations"] = analyse_writing(sample)
    at.run()
    assert not at.exception

    markdown_values = [md.value for md in at.markdown]
    _assert_payload_neutralised(markdown_values, _SCRIPT_PAYLOAD, "observation evidence quote")
