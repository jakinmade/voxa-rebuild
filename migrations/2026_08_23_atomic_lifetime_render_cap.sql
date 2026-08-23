-- Migration: atomic lifetime-render reservation (Phase 2, hardening
-- build order, 23 Aug 2026).
--
-- WHY: the previous check_and_reserve_lifetime_render() implementation
-- read `count`, checked it in Python, then wrote `count + 1` in a
-- separate call. Two concurrent renders that both read count=14 could
-- both pass the check and both write 15, letting a device exceed its
-- 15-render lifetime cap under concurrency (confirmed as a real race
-- in the Aug 2026 independent codebase review). A single
-- UPDATE ... WHERE ... RETURNING statement is atomic in Postgres —
-- concurrent callers serialize on the row lock, and each one
-- re-evaluates the WHERE clause against the post-commit value of the
-- previous caller, so two callers can never both succeed past the
-- limit. This migration moves the check-and-increment into the
-- database as a single statement inside a Postgres function, callable
-- via Supabase's .rpc().
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent — safe to
-- re-run (CREATE OR REPLACE).

create or replace function reserve_lifetime_render(p_device_id uuid, p_limit integer)
returns table(allowed boolean, used_count integer, subscription_status text)
language plpgsql
as $$
declare
  v_status text;
  v_new_count integer;
begin
  -- Ensure a row exists for this device. ON CONFLICT DO NOTHING makes
  -- this safe under concurrency too — if two callers race to create
  -- the same device's first row, one succeeds and the other silently
  -- no-ops rather than erroring.
  insert into lifetime_render_cap (device_id, count)
  values (p_device_id, 0)
  on conflict (device_id) do nothing;

  -- Subscription status is written only by the Stripe webhook path
  -- (stripe_subscription.py), never by a render call — so there is no
  -- race between reading it here and the atomic increment below; it
  -- cannot change out from under this function mid-call.
  select lifetime_render_cap.subscription_status into v_status
  from lifetime_render_cap
  where device_id = p_device_id;

  if v_status = 'active' then
    select lifetime_render_cap.count into v_new_count
    from lifetime_render_cap where device_id = p_device_id;
    return query select true, v_new_count, v_status;
    return;
  end if;

  -- The atomic reservation itself. This single UPDATE is indivisible:
  -- Postgres locks the row for the statement's duration, so a second
  -- concurrent call for the same device_id blocks until the first
  -- commits, then re-checks count < p_limit against the NEW value —
  -- eliminating the read-then-write race entirely for this device.
  -- Different devices use different rows and never block each other.
  update lifetime_render_cap
  set count = count + 1
  where device_id = p_device_id and count < p_limit
  returning count into v_new_count;

  if v_new_count is null then
    -- WHERE clause matched no row: at or over the limit. Read the
    -- current value back for the caller's (allowed, used, limit) tuple.
    select lifetime_render_cap.count into v_new_count
    from lifetime_render_cap where device_id = p_device_id;
    return query select false, v_new_count, v_status;
  else
    return query select true, v_new_count, v_status;
  end if;
end;
$$;


-- Symmetric atomic release, for the release-on-render-failure path
-- (a render that failed after the cap already counted it shouldn't
-- cost the person a free slot). Lower-stakes than the reservation
-- above — this already fails open/silently in Python on any error,
-- same as before — but making the decrement itself atomic removes an
-- unnecessary race even though the consequence of losing that race is
-- minor (an extra free render slips through, the same accepted
-- soft-ceiling trade-off already documented elsewhere in this module).
create or replace function release_lifetime_render(p_device_id uuid)
returns integer
language plpgsql
as $$
declare
  v_status text;
  v_new_count integer;
begin
  select lifetime_render_cap.subscription_status into v_status
  from lifetime_render_cap
  where device_id = p_device_id;

  if v_status = 'active' then
    return null;  -- never incremented for subscribers, never decrement
  end if;

  update lifetime_render_cap
  set count = greatest(count - 1, 0)
  where device_id = p_device_id
  returning count into v_new_count;

  return v_new_count;
end;
$$;
