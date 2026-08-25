-- Migration: restore-by-email magic-link columns (25 Aug 2026).
--
-- WHY: VOICOVA's identity model is device-cookie-only, documented in
-- stripe_subscription.py's module docstring as an accepted limitation
-- for Tier 1 — "Clear cookies or switch browsers and VOICOVA can't
-- find the subscription again without a manual support fix." This
-- migration is that fix, made self-serve: two nullable columns on the
-- SAME lifetime_render_cap row already keyed by device_id (same
-- pattern as stripe_customer_id / subscription_status added in an
-- earlier migration for the same reason — extend the existing row,
-- don't create a new table).
--
-- restore_token: a single-use, short-lived token minted when someone
-- requests a restore link (see request_subscription_restore() in
-- stripe_subscription.py). Looked up directly on ?restore=<token>,
-- so it needs to be fast to query — indexed below.
-- restore_token_expires_at: 15-minute expiry, the industry-converged
-- window for magic links (Supertokens, Slack). Checked in Python
-- (_is_expired), not enforced by the database — consistent with how
-- subscription_status itself is already just an application-level
-- convention on this table, not a DB constraint.
--
-- Apply once via Supabase MCP apply_migration against the live
-- project. Idempotent — safe to re-run (IF NOT EXISTS / OR REPLACE).

alter table lifetime_render_cap
    add column if not exists restore_token text,
    add column if not exists restore_token_expires_at timestamptz;

-- Restore confirmation (confirm_subscription_restore) looks up a row
-- by restore_token alone — index it so that lookup stays a single
-- fast index scan rather than a full table scan as the table grows.
-- Partial index (where restore_token is not null) since the vast
-- majority of rows will never have an active restore token at any
-- given moment — no reason to index every null.
create index if not exists idx_lifetime_render_cap_restore_token
    on lifetime_render_cap (restore_token)
    where restore_token is not null;
