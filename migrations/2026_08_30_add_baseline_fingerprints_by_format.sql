-- Migration: per-register compounding baselines (30 Aug 2026).
--
-- WHY: VOICOVA's existing baseline_fingerprint is a single blended
-- profile across every register (email, social, general writing).
-- A person's LinkedIn voice and their internal Slack voice are
-- legitimately different fingerprints -- forcing them into one merged
-- baseline flattens exactly the kind of register-specific signal the
-- voice-drift audit (30 Aug 2026) was already protecting at the
-- sentence level. This adds a second, independent compounding
-- baseline keyed by platform_format, additive alongside the existing
-- column -- every existing read of baseline_fingerprint is completely
-- unaffected; this is a new, nullable column, not a change to an
-- existing one.
--
-- Shape: {"email": {...same shape as baseline_fingerprint...},
--          "social": {...}, "general": {...}} -- one merged baseline
-- per platform_format value, built with the same _merge_baseline
-- logic already used for the single blended baseline. A row saved
-- before this feature existed simply won't have it (NULL), and the
-- app falls back to the existing single-baseline behaviour exactly
-- as it does today for voice_profile_summary (see persistence.py's
-- restore_profile_if_available -- same optional-field pattern).
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent -- safe to
-- re-run (ADD COLUMN IF NOT EXISTS).

alter table public.voice_profiles
    add column if not exists baseline_fingerprints_by_format jsonb;
