# voicova-webhooks (Cloudflare Worker)

Source-controlled copy of the `voicova-webhooks` Worker's code, for
reference and backup only — **this file is not what's deployed.**
Deployment is manual, via the Cloudflare dashboard's inline editor
(Quick Edit), same as CLEARANCE's `clearance-monitor` Worker. There is
no CI/CD link between this repo and the live Worker: editing
`worker.js` here does **not** update what Cloudflare is running, and
editing the Worker in the dashboard does **not** update this file. If
the dashboard version is ever hand-edited again, update this copy to
match afterwards so the two don't silently drift apart.

## What it does

Solves Section 15.2 item 3 (`VOICOVA_Product_2.0_Consolidated.docx`):
`stripe_subscription.py` only ever checks subscription status once, at
checkout (`Session.retrieve()`). This Worker is the missing piece —
it receives Stripe's `customer.subscription.updated`,
`customer.subscription.deleted`, and `invoice.payment_failed` webhook
events, verifies the signature by hand (Web Crypto HMAC-SHA256, no
Stripe SDK — the dashboard editor has no bundler), and writes the
resulting status straight to the same `lifetime_render_cap` Supabase
table `stripe_subscription.py` already reads for entitlement. The
Streamlit app needs zero code changes to consume this.

Full rationale, event-by-event handling, and env var docs are in the
file's own header comment — read that first, this README doesn't
repeat it.

## Live deployment

- **URL:** `https://voicova-webhooks.ajoakinmade.workers.dev`
- **Deployed via:** Cloudflare dashboard → Workers & Pages →
  `voicova-webhooks` → Quick Edit
- **Secrets set on the live Worker** (Settings → Variables and
  Secrets, all as Secret type): `STRIPE_WEBHOOK_SECRET`,
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

## Status as of 21 Aug 2026 — NOT yet confirmed working

Worker deployed, all three secrets set, Stripe test-mode webhook
endpoint created with the three events above selected. Three resent
test events all came back `400` with a "Signature verification
failed" body from the Worker, even after `STRIPE_WEBHOOK_SECRET` was
set. Not yet diagnosed further — next step is checking the Worker's
live log stream during a resend, to tell whether it's seeing no
secret at all (`voicova_webhook_no_signing_secret_configured` in the
logs) versus a secret that's present but doesn't match Stripe's
`whsec_...` value (e.g. a copy-paste corruption, same failure shape as
CLEARANCE's June 2026 SendGrid key issue).

**Do not consider Section 15.2 item 3 closed until three real test
events come back 200 and a row in `lifetime_render_cap` visibly
updates in Supabase.**
