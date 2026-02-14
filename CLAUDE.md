# audio-ident

Audio identification and fingerprinting application. FastAPI backend + SvelteKit frontend monorepo.

## Bootstrap

```bash
# Prerequisites: asdf (nodejs, python, pnpm, uv), Docker
asdf install            # installs tool versions from .tool-versions
make install            # installs Python + Node dependencies
make dev                # starts postgres, qdrant, service (port 17010), UI (port 17000)
```

## System Dependencies

The following system libraries must be installed before `make install`. They are NOT managed by uv or pnpm.

### macOS (Homebrew)

```bash
brew install ffmpeg fftw lmdb chromaprint libmagic
```

### Ubuntu/Debian

```bash
apt-get update && apt-get install -y \
  ffmpeg libfftw3-dev liblmdb-dev libchromaprint-dev libmagic1 build-essential
```

### Verification

```bash
ffmpeg -version | head -1        # >= 5.0 required
pkg-config --libs fftw3f         # Olaf dependency
pkg-config --libs lmdb           # Olaf dependency
which fpcalc                     # Chromaprint CLI (used by pyacoustid)
python -c "import magic"         # python-magic (requires libmagic)
```

### Docker

All system dependencies are bundled in the service Docker image. Use Docker if host installation is problematic.

## How to Run

```bash
make dev                # starts everything (postgres + qdrant + service + UI)
make test               # runs pytest + vitest
make lint               # ruff check + eslint
make fmt                # ruff format + prettier
make typecheck          # pyright + svelte-check
make docker-up          # starts only Docker services (postgres + qdrant)
make docker-down        # stops all Docker services
```

## Data Management

```bash
make ingest AUDIO_DIR=/path/to/mp3s   # ingest audio files into all stores
make rebuild-index                     # drop Olaf + Qdrant, re-index from raw audio
```

## Project Layout

```
audio-ident-service/    # FastAPI backend (Python, port 17010)
audio-ident-ui/         # SvelteKit frontend (TypeScript, port 17000)
docs/                   # Shared documentation
docker-compose.yml      # PostgreSQL + Qdrant
Makefile                # Developer UX entry point
```

## Conventions

- All developer commands go through `make` targets
- Service uses `uv` for Python dependency management
- UI uses `pnpm` for Node dependency management
- Ports are configurable via environment variables (`SERVICE_PORT`, `UI_PORT`)
- Database credentials: `audio_ident` / `audio_ident` / `audio_ident` (dev only)
- API routes: `/health` (no prefix), `/api/v1/*` (versioned)
- Docker services require profiles: `make dev` handles this automatically. Do NOT use `docker compose up -d` directly (nothing will start). Use `make docker-up` or `make dev`.
- To start infrastructure without the service: `docker compose --profile postgres --profile qdrant up -d`
- Do NOT run multiple `make ingest` processes simultaneously (Olaf LMDB is single-writer)
- Do NOT manually modify Olaf LMDB files or Qdrant collections — use `make rebuild-index` for recovery
- Do NOT ingest files shorter than 3 seconds or longer than 30 minutes
- If ingestion crashes mid-batch, re-run `make ingest` — SHA-256 dedup will skip already-ingested files

## Guard-rails / Non-goals

- Do NOT manually write TypeScript types for API responses — always generate from OpenAPI
- Do NOT add endpoints without updating `docs/api-contract.md` first
- Do NOT deploy frontend without regenerating types after backend changes
- Do NOT modify generated files in `audio-ident-ui/src/lib/api/generated.ts`
- No microservices — single service, single UI
- No server-side rendering that depends on service availability at build time
- `make gen-client` requires the backend to be running (`make dev`). If the backend cannot start, fix backend issues first before attempting frontend type generation.
- As a fallback, commit the generated `openapi.json` to the repo so types can be regenerated from the static file.

## How to Add a New Endpoint

1. Define the endpoint in `docs/api-contract.md`
2. Implement the route in `audio-ident-service/app/routers/`
3. Add Pydantic schemas in `audio-ident-service/app/schemas/`
4. Write tests in `audio-ident-service/tests/`
5. Start the service: `make dev`
6. Regenerate the client: `make gen-client`
7. Copy the contract: `cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/`
8. Use generated types in UI code
9. Write UI tests

## Implementation Sequencing

When adding new endpoints:
1. Update the API contract FIRST (this is a blocking prerequisite)
2. Copy contract to all three locations
3. THEN implement backend schemas and routes
4. THEN regenerate frontend types

Never implement code before the contract is updated and copied.

## The Golden Rule: API Contract is FROZEN

**Contract Location**: `docs/api-contract.md` exists in BOTH subprojects and the repo root (all three must be identical).

Once an API version is published, its contract is frozen. Any change requires:
- A version bump in the contract
- Updating both copies of the contract
- Regenerating frontend types

### Contract Synchronization Workflow (ONE WAY ONLY)

Direction: **service → UI** (never the reverse)

1. **Update service contract**: Edit `audio-ident-service/docs/api-contract.md`
2. **Update service models**: Update Pydantic request/response schemas
3. **Run service tests**: `cd audio-ident-service && uv run pytest`
4. **Copy contract to UI**: `cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/`
5. **Copy contract to root**: `cp audio-ident-service/docs/api-contract.md docs/`
6. **Regenerate frontend types**: `make gen-client`
7. **Update frontend code**: Use new generated types
8. **Run frontend tests**: `cd audio-ident-ui && pnpm test`

**NEVER**:
- Manually edit generated type files in the frontend
- Change API without updating contract in ALL three locations
- Deploy frontend without regenerating types after backend changes
- Break backward compatibility without version bump

### Contract Change Checklist

- [ ] Update `audio-ident-service/docs/api-contract.md`
- [ ] Update backend Pydantic schemas/models
- [ ] Backend tests pass
- [ ] Copy contract to `audio-ident-ui/docs/api-contract.md`
- [ ] Copy contract to `docs/api-contract.md`
- [ ] Regenerate types (`make gen-client`)
- [ ] Update frontend code using new types
- [ ] Frontend tests pass
