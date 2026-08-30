-- Migration: correction evidence persistence (30 Aug 2026).
--
-- WHY: voice-review item #1 -- Learn-from-edit now captures structured
-- predicted-vs-corrected evidence (score_correction_evidence,
-- voice_engine.py), not just a new blended sample. compute_dimension_
-- confidence demotes a dimension's confidence when it's been
-- consistently corrected in the same direction two or more times.
-- Without persistence, that evidence only survives one browser
-- session -- this makes it durable, same additive pattern as
-- 2026_08_30_add_baseline_fingerprints_by_format.sql: a new, nullable
-- column, every existing row and every existing read of the other
-- columns on this table completely unaffected.
--
-- Shape: a JSON array, one entry per Learn-from-edit event:
--   [{"evidence": {"hedge_density": {"predicted": 2.1, "corrected": 0.8,
--                                     "delta": -1.3, "direction": "decreased"}},
--     "platform_format": "email"}, ...]
-- A row saved before this feature existed simply won't have it (NULL),
-- and compute_dimension_confidence's demotion step is a no-op with no
-- evidence -- exactly its behaviour before this feature existed.
--
-- Apply once via the Supabase SQL editor (or Supabase MCP
-- apply_migration) against the live project. Idempotent -- safe to
-- re-run (ADD COLUMN IF NOT EXISTS).

alter table public.voice_profiles
    add column if not exists correction_evidence jsonb;
