-- Migration: Stripe webhook event idempotency (27 Aug 2026).
--
-- WHY: part of the webhook reconciliation redesign
-- (docs/stripe-webhook-reconciliation-design.md, findings 2b/2d/2e).
-- Once every webhook write reconciles against Stripe's CURRENT
-- subscription state rather than trusting the event payload,
-- processing the same event twice is harmless by construction (you'd
-- just fetch and write the same current state again) — this table is
-- a cost/hygiene measure (skip redundant Stripe API calls on Stripe's
-- own automatic retries), not a correctness requirement. Standard
-- Stripe-recommended idempotency pattern: insert event.id before
-- processing, skip on conflict.
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent — safe to
-- re-run (CREATE TABLE IF NOT EXISTS).

create table if not exists stripe_webhook_events (
    event_id text primary key,
    received_at timestamptz not null default now()
);

-- No index needed beyond the primary key — every lookup is a direct
-- event_id equality check, which the primary key already serves.
