/**
 * voicova-webhooks — Cloudflare Worker
 *
 * Solves the one gap Section 15.2 item 3 (VOICOVA_Product_2.0_
 * Consolidated.docx) flagged: stripe_subscription.py only ever checks
 * status once, at checkout (Session.retrieve()). It has no way to
 * learn later that a subscription was cancelled or went past_due, so
 * a lapsed subscriber stays entitled forever.
 *
 * Same architecture as CLEARANCE's clearance-monitor Worker: a
 * standalone Cloudflare Worker as the webhook receiver, not the
 * Streamlit app itself — Streamlit has no route Stripe could POST a
 * webhook to. This Worker verifies the Stripe signature, then writes
 * the outcome straight to the SAME Supabase table VOICOVA's own app
 * already reads for entitlement (lifetime_render_cap, looked up by
 * stripe_customer_id — the column stripe_subscription.py's
 * verify_and_record_subscription already populates on checkout). The
 * Streamlit app needs zero changes to consume this: lifetime_cap.py's
 * check_and_reserve_lifetime_render already reads subscription_status
 * off this same row.
 *
 * Deliberately NOT using the stripe npm package here — CLEARANCE's
 * Worker was pasted directly into the dashboard's inline editor with
 * no bundler, so no npm imports. Signature verification is done by
 * hand using the Web Crypto API (HMAC-SHA256), which is what Stripe's
 * own signature scheme actually is under the hood — same result as
 * stripe.webhooks.constructEvent, zero dependencies. Reconciliation
 * fetches (below) use the same no-SDK approach: a plain authenticated
 * fetch() against Stripe's REST API.
 *
 * RECONCILIATION, NOT TRUST (27 Aug 2026 redesign — see
 * docs/stripe-webhook-reconciliation-design.md for the full design
 * doc; independent codebase review findings 2b/2d/2e): the previous
 * version of this Worker wrote event.data.object.status (or a
 * hardcoded "past_due"/"canceled") straight from each event's own
 * payload. That has three problems, all sharing one root cause —
 * trusting the event as truth instead of asking Stripe what's
 * currently true:
 *   - a webhook delivered before this device's own row exists yet
 *     (checkout's own write racing this one) would PATCH zero rows
 *     and Supabase would still report success — the entitlement
 *     change was silently lost, not just delayed;
 *   - Stripe does not guarantee event delivery ORDER, so a stale
 *     event delivered after a newer one could overwrite it, e.g.
 *     canceled -> active if the active event both happened to be
 *     generated earlier but delivered later;
 *   - invoice.payment_failed writing "past_due" unconditionally is a
 *     policy guess this Worker has no business making — Stripe's own
 *     retry schedule determines whether a failed payment has actually
 *     changed the subscription's status yet, and this Worker was
 *     guessing at that instead of asking.
 * Fixed by changing what gets WRITTEN, not which events are listened
 * for: every event type now triggers the same three steps — extract
 * the Stripe customer ID, fetch that customer's CURRENT subscription
 * status directly from Stripe's API, write that. Event type only
 * decides WHEN to reconcile, never WHAT to write — which makes
 * out-of-order delivery irrelevant (every write reflects reality at
 * the moment it runs, not a comparison between events) and removes
 * the need for a separate invoice.payment_failed policy branch
 * entirely.
 *
 * IDEMPOTENCY: once every write reconciles against current Stripe
 * state, processing the same event twice is harmless by construction
 * — this is defense-in-depth (skip redundant Stripe API calls on
 * Stripe's own automatic retries), not a correctness requirement.
 * stripe_webhook_events (new table, see the 27 Aug 2026 migration)
 * tracks processed event IDs; a duplicate is acknowledged and
 * skipped, not reprocessed.
 *
 * EVENTS HANDLED (unchanged from before — still the right triggers,
 * they just no longer supply the value written; see above):
 *   - customer.subscription.updated
 *   - customer.subscription.deleted
 *   - invoice.payment_failed
 * All other event types are acknowledged (200) and ignored — Stripe
 * expects a 200 for any event type it isn't told to filter out
 * server-side, or it will keep retrying.
 *
 * ENV VARS (set as Worker secrets, same convention as clearance-
 * monitor's SENDGRID_API_KEY):
 *   STRIPE_WEBHOOK_SECRET   — from the Stripe Dashboard webhook
 *                             endpoint's "Signing secret", NOT the
 *                             API key. Test-mode secret for now,
 *                             matching VOICOVA's current sandbox
 *                             stage — swap to the live-mode secret
 *                             only when stripe_subscription.py's own
 *                             STRIPE_API_KEY is switched to live.
 *   STRIPE_API_KEY          — NEW as of this redesign: needed to make
 *                             the reconciliation GET request to
 *                             Stripe's REST API (previously this
 *                             Worker never called OUT to Stripe, only
 *                             verified incoming signatures). Same
 *                             secret key stripe_subscription.py
 *                             already uses — test-mode or live-mode
 *                             to match STRIPE_WEBHOOK_SECRET above.
 *   SUPABASE_URL            — e.g. https://txpsphethknujgqvqdzl.supabase.co
 *   SUPABASE_SERVICE_ROLE_KEY — service-role key, NOT the anon key.
 *                             Required because this bypasses row-level
 *                             security to write subscription_status
 *                             for an arbitrary device_id row — the
 *                             same trust level the app's own Supabase
 *                             client already has server-side.
 */

const SUPABASE_TABLE = "lifetime_render_cap";
const SUPABASE_EVENTS_TABLE = "stripe_webhook_events";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const signatureHeader = request.headers.get("stripe-signature");
    const rawBody = await request.text();

    if (!signatureHeader) {
      return new Response("Missing signature", { status: 400 });
    }

    const verified = await verifyStripeSignature(
      rawBody,
      signatureHeader,
      env.STRIPE_WEBHOOK_SECRET
    );
    if (!verified) {
      return new Response("Signature verification failed", { status: 400 });
    }

    let event;
    try {
      event = JSON.parse(rawBody);
    } catch (err) {
      return new Response("Invalid JSON", { status: 400 });
    }

    try {
      const alreadySeen = await markEventSeen(env, event.id);
      if (alreadySeen) {
        return new Response("ok (duplicate, skipped)", { status: 200 });
      }
      await handleEvent(event, env);
    } catch (err) {
      // Log-and-500 rather than swallowing: a 500 makes Stripe retry
      // this event on its own backoff schedule, which is the correct
      // behaviour for a transient Supabase/Stripe API failure, AND
      // for the zero-row-match case below (device row not written yet
      // by checkout's own path — ask Stripe to try again shortly
      // rather than silently dropping the entitlement change).
      console.error("voicova_webhook_handler_error", err.message, event?.type);
      return new Response("Internal error", { status: 500 });
    }

    return new Response("ok", { status: 200 });
  },
};

// Returns true if this event.id has already been processed (skip),
// false if this is the first time (proceed). Insert-then-check-
// conflict, not select-then-insert — avoids a race between two
// concurrent deliveries of the same event both seeing "not yet seen".
async function markEventSeen(env, eventId) {
  if (!eventId) {
    // Malformed event with no id at all - don't block on
    // idempotency tracking for something this broken; let
    // handleEvent's own field extraction fail loudly instead if it's
    // going to.
    return false;
  }
  const url = `${env.SUPABASE_URL}/rest/v1/${SUPABASE_EVENTS_TABLE}`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      Prefer: "return=minimal,resolution=ignore-duplicates",
    },
    body: JSON.stringify({ event_id: eventId }),
  });
  if (!response.ok && response.status !== 409) {
    const text = await response.text();
    throw new Error(
      `Event idempotency write failed (${response.status}): ${text}`
    );
  }
  // Postgrest with resolution=ignore-duplicates returns 201 on a
  // fresh insert and 200 with an empty body on a duplicate it
  // silently ignored - 201 means "first time", anything else on this
  // success path means "already seen".
  return response.status !== 201;
}

async function handleEvent(event, env) {
  const customerId = extractCustomerId(event);
  if (!customerId) {
    console.error("voicova_webhook_no_customer_id", event.type);
    return; // Nothing to reconcile against - not a retryable error.
  }

  switch (event.type) {
    case "customer.subscription.updated":
    case "customer.subscription.deleted":
    case "invoice.payment_failed": {
      // Event type only decides WHEN to reconcile, never WHAT to
      // write - see the module header for why. All three event types
      // funnel into the exact same two calls.
      const currentStatus = await fetchCurrentSubscriptionStatus(env, customerId);
      await writeSubscriptionStatus(env, customerId, currentStatus);
      break;
    }
    default:
      // Acknowledged, ignored - see module header.
      break;
  }
}

// Works for both customer.subscription.* events (customer is on the
// subscription object directly) and invoice.* events (customer is on
// the invoice object directly) - same field name, different parent
// object, so one small helper instead of duplicating this per event
// type.
function extractCustomerId(event) {
  const obj = event?.data?.object;
  return obj?.customer || null;
}

// Fetches this customer's current subscription status DIRECTLY from
// Stripe's API - never trusts the triggering event's own payload
// fields. "status=all" so a canceled subscription is still returned
// (not just active ones) - we need to see it either way to write the
// correct current status.
async function fetchCurrentSubscriptionStatus(env, stripeCustomerId) {
  const apiKey = env.STRIPE_API_KEY;
  if (!apiKey) {
    throw new Error("STRIPE_API_KEY not configured - cannot reconcile");
  }
  const url =
    `https://api.stripe.com/v1/subscriptions` +
    `?customer=${encodeURIComponent(stripeCustomerId)}&status=all&limit=1`;
  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Stripe subscription lookup failed (${response.status}): ${text}`
    );
  }
  const body = await response.json();
  if (!body.data || body.data.length === 0) {
    // No subscription object exists for this customer at all -
    // correct status is "canceled" (never had one, or Stripe has
    // fully removed the record), not left unwritten.
    return "canceled";
  }
  return body.data[0].status;
}

async function writeSubscriptionStatus(env, stripeCustomerId, status) {
  if (!stripeCustomerId) {
    console.error("voicova_webhook_missing_customer_id");
    return;
  }

  const url =
    `${env.SUPABASE_URL}/rest/v1/${SUPABASE_TABLE}` +
    `?stripe_customer_id=eq.${encodeURIComponent(stripeCustomerId)}`;

  const response = await fetch(url, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      apikey: env.SUPABASE_SERVICE_ROLE_KEY,
      Authorization: `Bearer ${env.SUPABASE_SERVICE_ROLE_KEY}`,
      // CHANGED from return=minimal (27 Aug 2026 redesign): need the
      // affected row(s) back to detect a zero-row match below -
      // return=minimal made that silently indistinguishable from a
      // successful one-row update, which was finding 2b.
      Prefer: "return=representation",
    },
    body: JSON.stringify({ subscription_status: status }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Supabase write failed (${response.status}): ${text}`
    );
  }

  const affectedRows = await response.json();
  if (affectedRows.length === 0) {
    // The device row doesn't exist yet for this stripe_customer_id -
    // most likely the checkout page's own write
    // (stripe_subscription.py's verify_and_record_subscription)
    // hasn't landed yet. Throwing here is caught by fetch()'s
    // try/catch above, which returns a 500 - Stripe retries this
    // event on its own backoff schedule, giving the checkout write
    // time to land before the next attempt, rather than this Worker
    // silently succeeding on a write that changed nothing (finding 2b).
    throw new Error(
      `No row found for stripe_customer_id=${stripeCustomerId} - retrying`
    );
  }
}

/**
 * Manual Stripe webhook signature verification, Web Crypto (HMAC-
 * SHA256) — no npm dependency, matches Stripe's documented scheme:
 * https://stripe.com/docs/webhooks/signatures
 *
 * The stripe-signature header looks like:
 *   t=1614556800,v1=5257a869e7...,v0=...
 * We verify against v1 only (v0 is a legacy scheme Stripe no longer
 * recommends checking). Timestamp tolerance of 5 minutes matches
 * Stripe's own default tolerance for constructEvent.
 */
async function verifyStripeSignature(rawBody, signatureHeader, secret) {
  if (!secret) {
    console.error("voicova_webhook_no_signing_secret_configured");
    return false;
  }

  // Cloudflare's Quick Edit secret field has bitten this account before
  // (CLEARANCE's SendGrid key, per this file's header comment) —
  // pasting from the Stripe dashboard can carry a trailing newline,
  // leading/trailing space, or surrounding quotes if the value was
  // copied out of a JSON/env view rather than the raw display. Any of
  // those makes the HMAC key wrong while `secret` still reads as
  // truthy, producing exactly this symptom: "Signature verification
  // failed" on every event even though the secret is "set". Trimming
  // and stripping wrapping quotes here costs nothing when the secret
  // was already clean.
  secret = secret.trim().replace(/^["']|["']$/g, "");

  const parts = Object.fromEntries(
    signatureHeader.split(",").map((part) => {
      const [key, value] = part.split("=");
      return [key, value];
    })
  );
  const timestamp = parts["t"];
  const signature = parts["v1"];
  if (!timestamp || !signature) {
    return false;
  }

  const toleranceSeconds = 300;
  const nowSeconds = Math.floor(Date.now() / 1000);
  if (Math.abs(nowSeconds - Number(timestamp)) > toleranceSeconds) {
    console.error("voicova_webhook_timestamp_out_of_tolerance");
    return false;
  }

  const signedPayload = `${timestamp}.${rawBody}`;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const mac = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(signedPayload)
  );
  const expectedSignature = bufferToHex(mac);

  return timingSafeEqual(expectedSignature, signature);
}

function bufferToHex(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let result = 0;
  for (let i = 0; i < a.length; i++) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
