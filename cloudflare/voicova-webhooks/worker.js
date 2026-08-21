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
 * stripe.webhooks.constructEvent, zero dependencies.
 *
 * EVENTS HANDLED:
 *   - customer.subscription.updated  → writes event.data.object.status
 *     verbatim ("active", "past_due", "canceled", "unpaid", etc.) so
 *     lifetime_cap.py's existing `== "active"` check stays the single
 *     source of truth for what counts as entitled.
 *   - customer.subscription.deleted  → writes "canceled" explicitly
 *     (Stripe already sends status="canceled" on this event too, but
 *     set directly in case that ever changes upstream).
 *   - invoice.payment_failed         → writes "past_due". Does NOT
 *     write "canceled" — a failed invoice on its own doesn't mean
 *     Stripe has cancelled the subscription yet (retries happen
 *     first); customer.subscription.updated will follow if/when
 *     Stripe's own retry schedule gives up and cancels it.
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
 *   SUPABASE_URL            — e.g. https://txpsphethknujgqvqdzl.supabase.co
 *   SUPABASE_SERVICE_ROLE_KEY — service-role key, NOT the anon key.
 *                             Required because this bypasses row-level
 *                             security to write subscription_status
 *                             for an arbitrary device_id row — the
 *                             same trust level the app's own Supabase
 *                             client already has server-side.
 */

const SUPABASE_TABLE = "lifetime_render_cap";

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
      await handleEvent(event, env);
    } catch (err) {
      // Log-and-500 rather than swallowing: a 500 makes Stripe retry
      // this event on its own backoff schedule, which is the correct
      // behaviour for a transient Supabase write failure. Swallowing
      // it here would silently drop a real entitlement change.
      console.error("voicova_webhook_handler_error", err.message, event?.type);
      return new Response("Internal error", { status: 500 });
    }

    return new Response("ok", { status: 200 });
  },
};

async function handleEvent(event, env) {
  switch (event.type) {
    case "customer.subscription.updated": {
      const sub = event.data.object;
      await writeSubscriptionStatus(env, sub.customer, sub.status);
      break;
    }
    case "customer.subscription.deleted": {
      const sub = event.data.object;
      await writeSubscriptionStatus(env, sub.customer, "canceled");
      break;
    }
    case "invoice.payment_failed": {
      const invoice = event.data.object;
      // invoice.customer is the Stripe Customer ID directly on the
      // invoice object - no need to look up the subscription first.
      await writeSubscriptionStatus(env, invoice.customer, "past_due");
      break;
    }
    default:
      // Acknowledged, ignored - see module docstring.
      break;
  }
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
      Prefer: "return=minimal",
    },
    body: JSON.stringify({ subscription_status: status }),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(
      `Supabase write failed (${response.status}): ${text}`
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
