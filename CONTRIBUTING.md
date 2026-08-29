# Voxa — Development Setup

## Quickest start — zero install required

```bash
git clone https://github.com/jakinmade/voxa-rebuild.git
cd voxa-rebuild
pytest
```

`conftest.py` adds all packages to `sys.path` automatically. Tests run immediately with no install step. This is the path the reviewer should use to verify test execution.

## With dependencies installed (recommended for development)

```bash
git clone https://github.com/jakinmade/voxa-rebuild.git
cd voxa-rebuild
pip install -r requirements.txt
pytest
```

`requirements.txt` installs all seven packages and their runtime dependencies.

## With Make

```bash
make test          # Run tests immediately (no install needed)
make install-deps  # Install runtime dependencies
```

## Running the API

```bash
pip install -r requirements.txt
streamlit run app.py
```
(`voxa_api`/`uvicorn` was the original FastAPI mounting layer — removed
August 2026, unreachable from production. The live app is the Streamlit
entrypoint above; rendering lives in `prompts.py`/`voice_engine.py` at
the repo root, not in `packages/`.)

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `VOXA_REPOSITORY` | `memory` | `memory` (dev) or `supabase` (production) |
| `ANTHROPIC_API_KEY` | — | Required for LLM rendering. Tests pass without it (passthrough). |
| `SUPABASE_URL` | — | Required when `VOXA_REPOSITORY=supabase` |
| `SUPABASE_SERVICE_KEY` | — | Required when `VOXA_REPOSITORY=supabase` |
| `VOXA_RATE_LIMIT_BACKEND` | `memory` | `memory` (dev) or `redis` (production) |
| `VOXA_REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `VOXA_RATE_LIMIT_RENDER` | `60` | Requests/min per user on `/render` |
| `VOXA_RATE_LIMIT_HUMANISE` | `20` | Requests/min per user on `/humanise` |
| `VOXA_RATE_LIMIT_CALIBRATE` | `120` | Requests/min per user on `/calibrate` |
| `VOXA_API_KEY_REQUIRED` | `false` | Set `true` to require `Authorization: Bearer <key>` |
| `VOXA_API_KEYS` | — | Comma-separated valid keys |

## Production checklist

- `VOXA_REPOSITORY=supabase` + Supabase credentials
- `VOXA_RATE_LIMIT_BACKEND=redis` + `VOXA_REDIS_URL`
- `VOXA_API_KEY_REQUIRED=true` + `VOXA_API_KEYS`
- `ANTHROPIC_API_KEY`

## Package structure

```
packages/
  voxa-core/          Shared entities, enums, bootstrap, defaults
  voxa-humanisation/  Layer 1 — Humanisation Engine
  voxa-profile/       Layer 2 — Canonical Voice Profile
  voxa-calibration/   Layer 4 — Calibration Engine (change vector)
  voxa-governance/    Layer 5 — Voice Governance Engine
config/
  change_vector_patterns_v1.yaml
tests/
  unit/
  integration/
conftest.py           — adds packages to sys.path; enables pytest without install
```

## Test suite: 201 tests, all passing

```bash
pytest                    # Full suite
pytest tests/unit/        # Unit only
pytest tests/integration/ # Integration only
pytest -k "change_vector" # Specific group
```
