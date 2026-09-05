-- Migration: /api/fix idempotency (5 Sept 2026).
--
-- WHY: independent architecture review, finding #5 — /api/fix has no
-- protection against a single user action producing more than one
-- paid render (double-click, two tabs, a browser-level retry, an
-- extension messaging bug duplicating one message, a service-worker
-- restart replaying an in-flight call). Unlike
-- stripe_webhook_events (migrations/2026_08_27_..._idempotency.sql),
-- reprocessing here is NOT harmless by construction — a duplicate
-- means a second real Anthropic API call and a second spent render
-- credit for the same user intention — so this table stores the
-- original response and returns it on a repeat, rather than just
-- deduplicating a side effect.
--
-- Same atomicity reasoning and technique as
-- rotate_refresh_handle/consume_recovery_request (api/db/
-- extension_installations.py, migrations/2026_09_05_profile_
-- recovery.sql): a plain Python read-then-insert has a TOCTOU race
-- two near-simultaneous requests with the same key could both pass —
-- closed here the same way, with a single atomic statement inside
-- the database rather than a new technique.
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent — safe to
-- re-run (create table if not exists / create or replace function).

-- idempotency_key is client-generated (crypto.randomUUID() in the
-- extension, one per actual Fix button click — see shared/panel.js's
-- own comment on why a fresh key per click, not a reused
-- correlation id, is the right scope for this). profile_id is stored
-- alongside it only for auditability/defense-in-depth (a key is
-- globally unique by construction; nothing currently queries by
-- profile_id on this table) — not a composite key, since the whole
-- point is one key can never resolve to two different outcomes for
-- anyone.
create table if not exists fix_idempotency_keys (
    idempotency_key text primary key,
    profile_id uuid not null,
    status text not null default 'pending' check (status in ('pending', 'completed')),
    response_json jsonb,
    created_at timestamptz not null default now()
);

-- No cleanup job included in this migration — same fail-open-on-
-- storage-growth posture this codebase already accepts for
-- stripe_webhook_events and evidence_seals (both unbounded, no
-- scheduled purge). Flagged, not solved here: a scheduled delete of
-- rows older than e.g. 24-48h (well past any plausible legitimate
-- retry window) would be the follow-up if row count ever becomes a
-- real concern at pilot volume.

-- Atomic reserve-or-return: one round trip, one atomic statement.
-- FOUND after INSERT ... ON CONFLICT DO NOTHING is true only when a
-- row was actually inserted by THIS call — false means the key
-- already existed (either a genuine still-in-flight duplicate, or a
-- prior call that already completed), which is exactly the signal
-- the caller needs to decide whether to run a fresh render or return
-- a stored result.
create or replace function reserve_fix_idempotency_key(p_key text, p_profile_id uuid)
returns table(is_new boolean, status text, response_json jsonb)
language plpgsql
-- Same search_path hardening as consume_recovery_request/
-- reserve_lifetime_render (migrations/2026_09_05_profile_recovery.sql,
-- 2026_08_23_atomic_lifetime_render_cap.sql) — closes the standard
-- Postgres search_path-hijack vector for this function's unqualified
-- table reference.
set search_path = public
as $$
declare
  v_status text;
  v_response jsonb;
begin
  insert into fix_idempotency_keys (idempotency_key, profile_id)
  values (p_key, p_profile_id)
  on conflict (idempotency_key) do nothing;

  if found then
    return query select true, 'pending'::text, null::jsonb;
    return;
  end if;

  select fix_idempotency_keys.status, fix_idempotency_keys.response_json
  into v_status, v_response
  from fix_idempotency_keys
  where idempotency_key = p_key;

  return query select false, v_status, v_response;
end;
$$;

-- Marks a reservation complete once a render has actually succeeded,
-- storing the exact response a repeat request should be handed back.
create or replace function complete_fix_idempotency_key(p_key text, p_response jsonb)
returns void
language plpgsql
set search_path = public
as $$
begin
  update fix_idempotency_keys
  set status = 'completed', response_json = p_response
  where idempotency_key = p_key;
end;
$$;

-- Releases a reservation after a failed render, so a legitimate retry
-- with the SAME key (the client's own retry-on-failure, not a new
-- user action) gets a genuinely fresh attempt rather than being
-- permanently stuck behind a 'pending' row with no response that will
-- ever arrive. Guarded to 'pending' only, so a stray/duplicate call to
-- this function can never undo an already-completed, already-returned
-- result.
create or replace function release_fix_idempotency_key(p_key text)
returns void
language plpgsql
set search_path = public
as $$
begin
  delete from fix_idempotency_keys
  where idempotency_key = p_key and status = 'pending';
end;
$$;

-- Same RLS remediation applied to every other Chrome-First table
-- (migrations/2026_09_05_profile_recovery.sql) — deny-by-default for
-- anon/authenticated via PostgREST; the backend's own
-- SUPABASE_SERVICE_KEY (service_role) bypasses RLS regardless, so
-- this is a no-op for current functionality and a complete lockout
-- for anyone else.
alter table fix_idempotency_keys enable row level security;
