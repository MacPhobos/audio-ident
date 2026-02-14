# audio-ident

Audio identification and fingerprinting application. FastAPI backend + SvelteKit frontend monorepo.

## Bootstrap

```bash
# Prerequisites: asdf (nodejs, python, pnpm, uv), Docker
asdf install            # installs tool versions from .tool-versions
make install            # installs Python + Node dependencies
make dev                # starts postgres, service (port 17010), UI (port 17000)
```

## How to Run

```bash
make dev                # starts everything (postgres + service + UI)
make test               # runs pytest + vitest
make lint               # ruff check + eslint
make fmt                # ruff format + prettier
make typecheck          # pyright + svelte-check
```

## Project Layout

```
audio-ident-service/    # FastAPI backend (Python, port 17010)
audio-ident-ui/         # SvelteKit frontend (TypeScript, port 17000)
docs/                   # Shared documentation
docker-compose.yml      # PostgreSQL
Makefile                # Developer UX entry point
```

## Conventions

- All developer commands go through `make` targets
- Service uses `uv` for Python dependency management
- UI uses `pnpm` for Node dependency management
- Ports are configurable via environment variables (`SERVICE_PORT`, `UI_PORT`)
- Database credentials: `audio_ident` / `audio_ident` / `audio_ident` (dev only)
- API routes: `/health` (no prefix), `/api/v1/*` (versioned)

## Guard-rails / Non-goals

- Do NOT manually write TypeScript types for API responses — always generate from OpenAPI
- Do NOT add endpoints without updating `docs/api-contract.md` first
- Do NOT deploy frontend without regenerating types after backend changes
- Do NOT modify generated files in `audio-ident-ui/src/lib/api/generated.ts`
- No microservices — single service, single UI
- No server-side rendering that depends on service availability at build time

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
