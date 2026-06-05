# Voxa — Development Setup

## Quick start (clean checkout)

```bash
git clone https://github.com/jakinmade/voxa-rebuild.git
cd voxa-rebuild
pip install -e .
pytest
```

Or with Make:

```bash
make install
make test
```

Both commands install all seven packages in editable mode and run the full test suite. No PYTHONPATH manipulation required.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VOXA_REPOSITORY` | `memory` | `memory` or `supabase` |
| `ANTHROPIC_API_KEY` | — | Required for LLM rendering |
| `SUPABASE_URL` | — | Required when `VOXA_REPOSITORY=supabase` |
| `SUPABASE_SERVICE_KEY` | — | Required when `VOXA_REPOSITORY=supabase` |
| `VOXA_RATE_LIMIT_RENDER` | `60` | Requests per minute per user on `/render` |
| `VOXA_RATE_LIMIT_HUMANISE` | `20` | Requests per minute per user on `/humanise` |
| `VOXA_RATE_LIMIT_CALIBRATE` | `120` | Requests per minute per user on `/calibrate` |
| `VOXA_API_KEY_REQUIRED` | `false` | Set `true` to require `Authorization: Bearer <key>` |
| `VOXA_API_KEYS` | — | Comma-separated valid API keys |
| `VOXA_PATTERN_CONFIG` | `config/change_vector_patterns_v1.yaml` | Path to pattern config |

## Running the API

```bash
uvicorn voxa_api.main:app --reload
```

## Package structure

```
packages/
  voxa-core/          Shared entities, enums, bootstrap, defaults
  voxa-humanisation/  Layer 1 — Humanisation Engine
  voxa-profile/       Layer 2 — Canonical Voice Profile
  voxa-rendering/     Layer 3 — Voice Rendering Engine
  voxa-calibration/   Layer 4 — Calibration Engine
  voxa-governance/    Layer 5 — Voice Governance Engine
  voxa-api/           FastAPI application + repositories + middleware
config/
  change_vector_patterns_v1.yaml  Edit classification patterns (tune without deployment)
tests/
  unit/               Per-layer and per-feature unit tests
  integration/        Cross-layer pipeline tests + API tests
```
