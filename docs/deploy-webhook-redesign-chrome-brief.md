# VOICOVA — Deploy the Stripe Webhook Reconciliation Redesign

Code is done and pushed (commit 17c2702). Nothing is live yet. This is a
deployment + verification checklist, not a code-writing task.

## 1. Apply the migration (Supabase)

- Open the Supabase dashboard → SQL Editor for the VOICOVA project
- Run the contents of `migrations/2026_08_27_stripe_webhook_event_idempotency.sql`
  (from the repo — creates one table, `stripe_webhook_events`)
- Confirm: table exists, no errors

## 2. Deploy the Worker (Cloudflare)

- Open the Cloudflare dashboard → Workers → `voicova-webhooks`
- Open the Quick Edit / inline editor
- Replace the entire contents with `cloudflare/voicova-webhooks/worker.js`
  from the repo (the whole file — this is a full rewrite, not a patch)
- **Add one new secret**: `STRIPE_API_KEY` — same value
  `stripe_subscription.py` already uses (test-mode key for now, matching
  the existing `STRIPE_WEBHOOK_SECRET`'s test-mode status). Settings →
  Variables and Secrets → Add.
- Confirm existing secrets are still present and unchanged:
  `STRIPE_WEBHOOK_SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- Save / Deploy

## 3. Test in Stripe test mode BEFORE touching production traffic

Using the Stripe CLI (`stripe trigger ...`) or the Dashboard's "Send test
webhook" on the endpoint:

- Trigger `customer.subscription.updated` → check the Worker's live logs
  (Cloudflare dashboard → Worker → Logs) show it fetching FROM Stripe
  (a reconciliation GET), not just writing the event's own payload
- Trigger `customer.subscription.deleted` → same check
- Trigger `invoice.payment_failed` → same check
- Trigger the same event twice in a row → second one should log as
  "duplicate, skipped" (idempotency working)

## 4. Real end-to-end test

- Subscribe with a Stripe test card (4242 4242 4242 4242)
- Confirm the app shows unlimited renders
- Cancel via the Stripe customer portal
- Confirm the app's `lifetime_render_cap` row for that customer flips to
  a non-active status, and the app re-enforces the free-tier limit
- This is the exact scenario 2b/2c/2d/2e all exist to get right — the
  single most important check on this whole list

## 5. Only once 1-4 all check out

- Note in the repo/team channel that the Worker is deployed and verified
- Do NOT swap to live-mode secrets until VOICOVA's own Stripe integration
  is otherwise ready to go fully live — that's a separate decision, not
  part of this checklist

## If anything looks wrong

Stop, don't debug live — the Worker's error handling already returns a
500 on any failure (Supabase write, Stripe lookup, zero-row match), which
makes Stripe retry safely on its own schedule. A failure here fails safe,
not open. Screenshot the Cloudflare logs and bring them back for review.
