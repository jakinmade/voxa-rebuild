# Voxa — Development Setup

## Quick start (clean checkout)

```bash
git clone https://github.com/jakinmade/voxa-rebuild.git
cd voxa-rebuild
pip install -r requirements.txt
pytest
```

Or with Make:

```bash
make install
make test
```

That is all that is required. No PYTHONPATH manipulation. No manual path setup.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VOXA_REPOSITORY` | `memory` | `memory` (dev) or `supabase` (production) |
| `ANTHROPIC_API_KEY` | — | Required for LLM rendering. Tests pass without it (passthrough). |
| `SUPABASE_URL` | — | Required when `VOXA_REPOSITORY=supabase` |
| `SUPABASE_SERVICE_KEY` | — | Required when `VOXA_REPOSITORY=supabase` |
| `VOXA_RATE_LIMIT_BACKEND` | `memory` | `memory` (dev) or `redis` (production, multi-instance safe) |
| `VOXA_REDIS_URL` | `redis://localhost:6379` | Redis URL when backend is `redis` |
| `VOXA_RATE_LIMIT_RENDER` | `60` | Requests/min per user on `/render` |
| `VOXA_RATE_LIMIT_HUMANISE` | `20` | Requests/min per user on `/humanise` |
| `VOXA_RATE_LIMIT_CALIBRATE` | `120` | Requests/min per user on `/calibrate` |
| `VOXA_API_KEY_REQUIRED` | `false` | Set `true` to require `Authorization: Bearer <key>` |
| `VOXA_API_KEYS` | — | Comma-separated valid API keys |
| `VOXA_PATTERN_CONFIG` | `config/change_vector_patterns_v1.yaml` | Pattern config path |

## Running the API

```bash
uvicorn voxa_api.main:app --reload
```

## Production checklist

- Set `VOXA_REPOSITORY=supabase` and configure Supabase credentials
- Set `VOXA_RATE_LIMIT_BACKEND=redis` and configure `VOXA_REDIS_URL`
- Set `VOXA_API_KEY_REQUIRED=true` and populate `VOXA_API_KEYS`
- Set `ANTHROPIC_API_KEY`

## Package structure

```
packages/
  voxa-core/          Shared entities, enums, bootstrap, defaults
  voxa-humanisation/  Layer 1 — Humanisation Engine
  voxa-profile/       Layer 2 — Canonical Voice Profile
  voxa-rendering/     Layer 3 — Voice Rendering Engine (LLM boundary lives here)
  voxa-calibration/   Layer 4 — Calibration Engine (change vector classifier)
  voxa-governance/    Layer 5 — Voice Governance Engine
  voxa-api/           FastAPI + repositories + middleware
config/
  change_vector_patterns_v1.yaml  — tune patterns without code deployment
tests/
  unit/               Per-layer unit tests + failure mode tests
  integration/        Cross-layer pipeline tests + full API tests
```

## Test suite

201 tests across unit and integration. All pass without external services.

```bash
pytest                          # Full suite
pytest tests/unit/              # Unit tests only
pytest tests/integration/       # Integration tests only
pytest -k "change_vector"       # Specific test group
```
