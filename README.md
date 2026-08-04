# Voicova

**Governed Communication Identity System**

Architecture Specification v9.2.0 | Build v9.5.0

Voicova is not an AI humaniser. Voicova is a governed communication identity system.

> Package names below still read `voxa-*` — the `packages/` monorepo isn't renamed yet, deliberately. See build plan.

## Monorepo Structure

```
packages/
  voxa-core/          # Shared data entities, schemas, base types
  voxa-humanisation/  # Layer 1 — Humanisation Engine
  voxa-profile/       # Layer 2 — Canonical Voice Profile
  voxa-rendering/     # Layer 3 — Voice Rendering Engine
  voxa-calibration/   # Layer 4 — Calibration Engine
  voxa-governance/    # Layer 5 — Voice Governance Engine
  voxa-api/           # FastAPI — mounts all layers, exposes endpoints
```

## Architecture Principles

- Voice is constraints, preferences, tendencies, and confidence-weighted rules — not writing samples
- Every input passes through a meaning layer before a voice layer
- LLMs operate inside the rendering layer only
- Every rule carries full metadata — no bare values
- Deterministic engine generates decisions; LLM handles expression only

## Sprints

- **Sprint 1** — Pipeline Foundation: raw input → governed output → first rule candidate
- **Sprint 2** — Calibration Depth: confidence formula, full promotion lifecycle, negative evidence
- **Sprint 3** — Governance and Enterprise: context overrides, drift monitor, org policy

## Stack

- Python 3.12 + FastAPI
- Pydantic v2 (schema enforcement at every boundary)
- PostgreSQL via Supabase
- Redis (session state, rendering cache)
- Claude API (rendering layer only — LLM boundary contract enforced)
- Next.js 14 (frontend)
- structlog (instrumentation)
- pytest (test suite)

---

akinmade.co.uk
