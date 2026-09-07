"""
api/privacy_policy.py — static content for GET /privacy (see main.py).

Every claim below was checked against the actual persistence code
before being written, not drafted generically and hoped to be true:
- No account/email/signup by default: persistence.py's own docstring
  ("no accounts, no email, no signup screen") — a device-cookie UUID
  only, until/unless the user opts into email-based recovery
  (api/routes/profile_recovery.py).
- Draft text sent for a Voice Check or Fix is NOT stored raw
  server-side — confirmed via api/evidence/seal.py (seal payload
  hashes match_pct/dimension_scores, not draft text) and
  api/telemetry/events.py (stores draft_length as an int, never the
  text itself).
- The onboarding writing sample IS stored raw — persistence.py's own
  docstring: "the raw writing sample, the starter completions, the
  derived baselines" are the durable, expensive-to-rebuild data this
  product exists to keep.
- Draft text is sent to Anthropic's API for LLM-based analysis
  (render_pipeline.py) — a genuine third-party subprocessor, named
  here rather than left implicit.
"""

PRIVACY_POLICY_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>VOICOVA Privacy Policy</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    max-width: 720px;
    margin: 3rem auto;
    padding: 0 1.5rem 4rem;
    color: #1C1B29;
    background: #FBF9F6;
    line-height: 1.6;
  }
  h1 { color: #7A2632; }
  h2 { color: #1C1B29; margin-top: 2.5rem; border-bottom: 1px solid #E4DBCC; padding-bottom: 0.4rem; }
  .updated { color: #7A7488; font-size: 0.9rem; }
  code { background: #F3EEE6; padding: 0.1rem 0.35rem; border-radius: 4px; font-size: 0.9em; }
  ul { padding-left: 1.3rem; }
  a { color: #7A2632; }
</style>
</head>
<body>
  <h1>VOICOVA Privacy Policy</h1>
  <p class="updated">Last updated: 7 September 2026</p>

  <p>VOICOVA (voicova.com and the VOICOVA Chrome extension) helps you check
  that a draft you're about to post or send still sounds like you, before
  you publish it. This page describes what data that involves.</p>

  <h2>What we collect</h2>
  <ul>
    <li><strong>A device identifier.</strong> When you use voicova.com, a
    random identifier is stored in a browser cookie on your device. It is
    not tied to your name, email, or any other identity unless you choose
    to add one (see Account recovery below).</li>
    <li><strong>Your writing sample.</strong> To build your voice profile,
    you provide writing samples during setup. These are stored so your
    profile persists across visits — this is the core data the product
    exists to keep.</li>
    <li><strong>Draft text you check or fix.</strong> When you use the
    Chrome extension's Voice Check or Fix feature on LinkedIn or Gmail, the
    draft text in that composer is sent to our backend for analysis. It is
    processed and returned to you; it is <strong>not stored</strong> on our
    servers afterward — only a numeric match score, a hash of that score,
    and the draft's length (a number, not the text) are retained, for
    product reliability and evidence purposes.</li>
    <li><strong>Usage metadata.</strong> Timestamps, which surface
    (LinkedIn or Gmail) a check happened on, whether a fix was requested
    or accepted, and similar operational data, used to keep the product
    working and to understand product usage. This does not include your
    draft text.</li>
    <li><strong>An email address, only if you provide one.</strong>
    Account recovery is optional. If you choose to add a recovery email,
    we store it solely to send you a one-time, time-limited recovery link
    if you lose access to your device.</li>
  </ul>

  <h2>How your data is used</h2>
  <p>Draft text you submit for checking is sent to Anthropic's API for
  language analysis as part of producing your result. It is not used to
  train models, sold, or shared for advertising. We do not run or serve
  advertising in this product.</p>

  <h2>Who else sees it</h2>
  <p>The following third-party services process data on our behalf, strictly
  to run the product:</p>
  <ul>
    <li><strong>Anthropic</strong> — processes draft text you submit for
    checking, to produce the voice-match analysis.</li>
    <li><strong>Supabase</strong> — hosts our database (writing samples,
    voice profiles, usage metadata).</li>
    <li><strong>SendGrid</strong> — sends the one-time recovery email, only
    if you've added a recovery email address.</li>
    <li><strong>Railway</strong> — hosts the backend service itself.</li>
  </ul>
  <p>None of these providers use your data for their own purposes beyond
  providing the service we've contracted them for.</p>

  <h2>What the Chrome extension can access</h2>
  <p>The extension only reads the text of the composer box on LinkedIn and
  Gmail when you explicitly click Check or Fix — it does not read your
  inbox, your connections, or any other page content, and does not run on
  any site other than LinkedIn and Gmail.</p>

  <h2>Data retention and deletion</h2>
  <p>Disconnecting the extension (via its popup) revokes its access
  immediately, both locally and on our server, but does not delete your
  underlying voice profile — that persists so you can reconnect later
  without redoing setup. To delete your voice profile and all associated
  data entirely, contact us at the address below.</p>

  <h2>Contact</h2>
  <p>Questions about this policy or a deletion request: <a href="mailto:hello@voicova.com">hello@voicova.com</a></p>
</body>
</html>"""
