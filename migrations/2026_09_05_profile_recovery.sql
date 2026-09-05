-- Migration: profile-recovery tables (Full Spec Section 2.5, Engineering
-- Architecture Section 5.2), plus one table beyond what either document
-- specifies — see the note on profile_recovery_emails below.
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent — safe to
-- re-run (create table if not exists / create or replace function).

-- profile_recovery_requests — exactly Architecture Section 5.2's
-- documented columns (request_id, profile_id, email, token_hash,
-- expires_at, used_at), plus created_at, which that section doesn't
-- list but which every other table in this codebase has — harmless,
-- additive, and consistent with the rest of the schema rather than a
-- documented decision to omit it.
create table if not exists profile_recovery_requests (
    request_id uuid primary key default gen_random_uuid(),
    profile_id uuid not null,
    email text not null,
    token_hash text not null,
    expires_at timestamptz not null,
    used_at timestamptz,
    created_at timestamptz not null default now()
);

create index if not exists profile_recovery_requests_token_hash_idx
    on profile_recovery_requests (token_hash);

-- profile_recovery_emails — NOT documented in either the Full Spec or
-- the Engineering Architecture. Both describe WHAT profile recovery
-- does ("a user can request a one-time recovery link sent to an email
-- address they provide at will") but neither specifies WHERE that
-- email is captured or stored before a recovery request needs it —
-- profile_recovery_requests only records a request's email at the
-- moment the request is made, which doesn't help resolve a bare
-- incoming email to a profile_id on a cold request from a lost
-- device. This is the minimal table that closes that gap: one row
-- per profile, holding the email to search on, deliberately NOT an
-- accounts table (no password, no session, nothing else attached —
-- see the hard boundary in Full Spec Section 2.5/6.2: "email is
-- recovery-only, never a primary identity or login"). Populated by
-- the new POST /api/profile/recovery-email endpoint, which is itself
-- an addition beyond Section 11.6's two documented steps, for the
-- same reason — see api/routes/profile_recovery.py's module
-- docstring for the full account of this decision.
create table if not exists profile_recovery_emails (
    profile_id uuid primary key,
    email text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- Atomic single-use consumption of a recovery token, same reasoning
-- and same shape as reserve_lifetime_render/rotate_refresh_handle
-- (migrations/2026_08_23_atomic_lifetime_render_cap.sql and
-- api/db/extension_installations.py's rotate_refresh_handle): a plain
-- read-then-write from Python has the same TOCTOU race those already
-- solved with a single atomic UPDATE ... WHERE ... RETURNING inside
-- the database — two near-simultaneous clicks on the same emailed
-- link could otherwise both read "unused" and both succeed. This
-- closes that the same way, not a new technique.
create or replace function consume_recovery_request(p_token_hash text)
returns table(profile_id uuid, email text)
language plpgsql
-- Same search_path hardening applied to reserve_lifetime_render/
-- release_lifetime_render (migrations/2026_08_23_..._search_path.sql)
-- — a plpgsql function's unqualified table references resolve
-- against whatever search_path is in effect at CALL time, not
-- definition time, so pinning it here closes the standard Postgres
-- search_path-hijack vector regardless of caller. Checked against the
-- live project before writing this: rotate_refresh_handle (also
-- created this session) does not have this set — confirmed via
-- get_advisors after applying this migration, which still flags it
-- as a WARN (function_search_path_mutable). Flagged here, not fixed
-- here — that function lives in an already-merged PR, out of scope
-- for this one, but worth a follow-up.
set search_path = public
as $$
declare
  v_profile_id uuid;
  v_email text;
begin
  update profile_recovery_requests
  set used_at = now()
  where token_hash = p_token_hash
    and used_at is null
    and expires_at > now()
  returning profile_recovery_requests.profile_id, profile_recovery_requests.email
  into v_profile_id, v_email;

  if v_profile_id is null then
    return;  -- empty result set: no such token, already used, or expired —
             -- callers must not distinguish between these (Section 2.5's
             -- non-enumeration posture, same as request_subscription_
             -- restore's own "never reveals whether the email matched").
  end if;

  return query select v_profile_id, v_email;
end;
$$;

-- Closes an ERROR-level Supabase linter finding (rls_disabled_in_public)
-- surfaced by get_advisors right after the two tables above were first
-- applied: five Chrome-First tables were exposed to PostgREST with RLS
-- disabled. All backend access uses SUPABASE_SERVICE_KEY (service_role),
-- which bypasses RLS regardless, so this never affected current
-- functionality — but with RLS off, the project's anon key (designed to
-- be public, safe only when paired with RLS policies) would have had
-- full read/write access to these tables via direct PostgREST calls,
-- bypassing the FastAPI service's auth entirely, if that key is ever
-- embedded anywhere (e.g. a future client-side use).
--
-- Same remediation this exact project already applied once before
-- (migrations/2026_08_30_enable_rls_stripe_webhook_events.sql): enable
-- RLS with zero policies. Since service_role always bypasses RLS, this
-- is a complete no-op for the backend's own access and a complete
-- lockout for anon/authenticated — deny-by-default, not a partial fix.
-- Three of these five tables predate this PR (extension_installations,
-- evidence_seals, telemetry_events — created earlier the same day,
-- evidently before this check was run); two are new in this PR
-- (profile_recovery_requests, profile_recovery_emails).
alter table extension_installations enable row level security;
alter table evidence_seals enable row level security;
alter table telemetry_events enable row level security;
alter table profile_recovery_requests enable row level security;
alter table profile_recovery_emails enable row level security;
