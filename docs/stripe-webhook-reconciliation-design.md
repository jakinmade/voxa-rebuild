# VOICOVA — Stripe Webhook Reconciliation Redesign

**Scope:** independent codebase review findings 2b, 2d, 2e.
**Status:** design only — not yet implemented. Deliberately held back from
today's hardening batch to get its own careful pass, not a rush job under
launch pressure.
**Answer to "do we need to reinvent this?": no.** The core fix below —
*treat a webhook event as a trigger to fetch current state, never as the
state itself* — is Stripe's own documented recommendation for exactly this
failure class (at-least-once delivery, no ordering guarantee). We're
applying a known pattern, not inventing one.

## The three findings, and why one fix addresses all three

| # | Finding | Root cause |
|---|---|---|
| 2b | A webhook PATCH can silently match zero rows (device row doesn't exist yet) — Stripe still considers the event delivered | The write trusts the event alone, with no fallback if the target row isn't there |
| 2d | No event-ordering protection — a stale event can overwrite a newer one | The write copies `event.data.object.status` verbatim, whichever event arrives last wins, regardless of which is *actually* current |
| 2e | `invoice.payment_failed` → `past_due` is a blunt, hardcoded policy call | Same root cause: the code is guessing at Stripe's state from one event type instead of asking Stripe what the state actually is |

All three come from the same design choice: **the worker trusts each
event's payload as truth.** The fix is one architectural change, not three
separate patches: on any relevant event, **fetch the subscription's
current state directly from Stripe's API and write that** — never the
event payload's own fields.

This resolves 2d automatically, with no event-ordering logic needed at
all: if two events arrive out of order, each one still triggers a fresh
fetch of Stripe's *current* state at the moment it's processed, so the
final write always reflects reality — you're never comparing which event
is "newer," you're just always asking Stripe "what's true right now."

It resolves 2e the same way: no more hardcoded `invoice.payment_failed →
past_due`. Every event type becomes the same three steps — get the
customer ID, fetch current subscription state from Stripe, write it.

2b needs one additional, explicit piece: detecting a zero-row match and
retrying, rather than silently succeeding.

## Design

### 1. Reconcile, don't trust (resolves 2d, 2e)

```js
async function handleEvent(event, env) {
  const customerId = extractCustomerId(event); // works for both
                                                 // customer.subscription.*
                                                 // and invoice.* events
  if (!customerId) {
    console.error("voicova_webhook_no_customer_id", event.type);
    return; // nothing to reconcile
  }

  // Ignore event-type-specific fields entirely from this point on -
  // event.type only tells us WHEN to look, never WHAT to write.
  const currentStatus = await fetchCurrentSubscriptionStatus(env, customerId);
  await writeSubscriptionStatus(env, customerId, currentStatus);
}

async function fetchCurrentSubscriptionStatus(env, customerId) {
  const subs = await stripeGet(
    env, `/v1/subscriptions?customer=${customerId}&status=all&limit=1`
  );
  if (!subs.data.length) return "canceled"; // no subscription at all
  return subs.data[0].status; // Stripe's own current truth
}
```

`stripeGet` is a small new helper — a plain authenticated `fetch()` to
Stripe's REST API using the existing secret, no SDK needed, same
zero-dependency approach the signature verification already uses.

**Which events to still listen for:** unchanged (`customer.subscription.
updated`, `customer.subscription.deleted`, `invoice.payment_failed`) —
these are still the right *triggers* to reconcile on, they just no longer
supply the value being written.

### 2. Zero-row detection (resolves 2b)

```js
async function writeSubscriptionStatus(env, stripeCustomerId, status) {
  const url =
    `${env.SUPABASE_URL}/rest/v1/${SUPABASE_TABLE}` +
    `?stripe_customer_id=eq.${encodeURIComponent(stripeCustomerId)}`;

  const response = await fetch(url, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      Prefer: "return=representation", // CHANGED from return=minimal -
                                        // need the affected rows back
                                        // to detect a zero-row match
    },
    body: JSON.stringify({ subscription_status: status }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`Supabase write failed (${response.status}): ${text}`);
  }

  const affectedRows = await response.json();
  if (affectedRows.length === 0) {
    // The device row doesn't exist yet - most likely the checkout
    // page's own write (stripe_subscription.py's verify_and_record_
    // subscription) hasn't landed yet. Throwing here triggers the
    // existing 500-response path in fetch(), which makes Stripe retry
    // this event on its own backoff schedule - the correct response
    // to "not ready yet," not a silent no-op.
    throw new Error(
      `No row found for stripe_customer_id=${stripeCustomerId} - retrying`
    );
  }
}
```

This is a small, surgical change (one header value, one length check) —
not a schema change, not a new table for this specific finding.

### 3. Idempotency (defense in depth, not strictly required for correctness anymore)

Once every write reconciles against Stripe's current state, processing the
same event twice is harmless by construction — you'd just fetch and write
the same current state again. Idempotency tracking is still worth adding,
but as a cost/hygiene measure (avoid redundant Stripe API calls on Stripe's
automatic retries) rather than a correctness requirement:

```sql
create table if not exists stripe_webhook_events (
  event_id text primary key,
  received_at timestamptz not null default now()
);
```

```js
// Before handleEvent(): insert event.id, skip processing on conflict.
const seen = await markEventSeen(env, event.id);
if (seen) return; // already processed, ack and stop
```

One new table, one migration, standard Stripe-recommended idempotency
pattern.

## What this does NOT change

- **No change to the device-cookie identity model.** Device rows are
  still looked up by `stripe_customer_id`, same as today. This design
  doesn't touch the bigger "should subscription state be keyed by device
  or by customer" question — that's a separate, larger decision this doc
  deliberately doesn't take on.
- **No change to `stripe_subscription.py` or the Streamlit app at all.**
  `lifetime_cap.py` already reads `subscription_status` off the same row
  the same way; it has no idea whether that column was last written by a
  trusted-payload write or a reconciled one.
- **No change to which three event types trigger reconciliation.**

## Rollout plan

1. Apply the `stripe_webhook_events` migration.
2. Update `worker.js` with the reconcile-then-write logic above.
3. Test against Stripe's test-mode webhook endpoint first — trigger each
   of the three event types via the Stripe CLI (`stripe trigger customer.
   subscription.updated`, etc.) and confirm the Worker's logs show a
   reconciliation fetch happening, not a direct payload write.
4. Specifically test the 2c-adjacent race this whole thing traces back
   to: cancel a real test subscription and confirm the row updates
   correctly even if the webhook fires before some other write.
5. Only then point the live Stripe webhook endpoint at the updated
   Worker.

## Effort estimate

Small-to-medium: the core logic change is under 40 lines of Worker code;
the migration is a 3-line table. The bulk of the real time cost is in
testing (step 3-4 above), not implementation — worth doing carefully
given this is money-adjacent code, not worth rushing.
