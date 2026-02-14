# audio-ident Developer Guide

## Prerequisites

- [asdf](https://asdf-vm.com/) with plugins: `nodejs`, `python`, `pnpm`, `uv`
- [Docker](https://docs.docker.com/get-docker/) (for PostgreSQL)

## Quick Start

```bash
asdf install            # install tool versions
make install            # install all dependencies
make dev                # start postgres + service + UI
```

Open http://localhost:17000 for the UI. Service API is at http://localhost:17010.

## Running Tests

```bash
make test               # all tests (pytest + vitest)
cd audio-ident-service && uv run pytest       # service only
cd audio-ident-ui && pnpm test                # UI only
```

## Linting and Formatting

```bash
make lint               # check both projects
make fmt                # auto-format both projects
make typecheck          # type-check both projects
```

## Database

```bash
make db-up              # start postgres
make db-reset           # drop + recreate + run migrations
```

Connection: `postgresql://audio_ident:audio_ident@localhost:5432/audio_ident`

## Generating the TypeScript API Client

The UI uses a generated TypeScript client derived from the service's OpenAPI spec.

### From a running service

```bash
make dev                # ensure service is running
make gen-client         # fetches /openapi.json and generates types
```

### From a saved spec file

```bash
make gen-client-from-file SPEC=docs/openapi.json
```

The generated client is placed at `audio-ident-ui/src/lib/api/generated.ts`.

## Ports

| Service   | Default Port | Override                     |
|-----------|-------------|------------------------------|
| UI        | 17000       | `UI_PORT=18000 make dev`     |
| Service   | 17010       | `SERVICE_PORT=18010 make dev`|
| Postgres  | 5432        | `POSTGRES_PORT=5433` in env  |

## API Contract

The API contract lives in `docs/api-contract.md` and is copied to both subprojects. See the root `CLAUDE.md` for the contract synchronization workflow.
