# audio-ident-service

FastAPI backend for the audio-ident project.

## Stack

- **Python 3.12** with **uv** for package management
- **FastAPI** with Pydantic v2
- **SQLAlchemy 2.x** async ORM with asyncpg
- **Alembic** for database migrations
- **PyJWT** + **argon2-cffi** for auth
- **Ruff** for linting/formatting, **Pyright** for type checking
- **pytest** + pytest-asyncio + httpx for testing

## Quick start

```bash
cd audio-ident-service
uv sync --all-extras          # install deps + dev extras
cp .env.example .env          # configure environment
uv run alembic upgrade head   # run migrations
uv run uvicorn app.main:app --port 17010 --reload
```

## Project layout

```
app/
  main.py          # FastAPI app factory, CORS, router mount
  settings.py      # pydantic-settings (reads .env)
  routers/         # endpoint modules (health, version)
  db/              # async engine + session dependency
  models/          # SQLAlchemy ORM models
  schemas/         # Pydantic request/response schemas
  auth/            # JWT, OAuth2, password hashing stubs
  audio/          # Audio decode, metadata, dedup, fingerprint, embedding, storage
  search/         # Exact match, vibe match, orchestrator, aggregation
  ingest/         # Batch ingestion pipeline and CLI
alembic/           # migration scripts
tests/             # pytest test suite
```

## Key commands

| Task | Command |
|------|---------|
| Run dev server | `uv run uvicorn app.main:app --port 17010 --reload` |
| Run tests | `uv run pytest` |
| Lint | `uv run ruff check app tests` |
| Format | `uv run ruff format app tests` |
| Type check | `uv run pyright app` |
| New migration | `uv run alembic revision --autogenerate -m "description"` |
| Apply migrations | `uv run alembic upgrade head` |

## Conventions

- All endpoints return JSON. Errors use `{"error": {"code": "...", "message": "...", "details": ...}}`.
- Router modules live in `app/routers/` and are registered in `app/main.py`.
- Config is loaded from environment via `app/settings.py` (pydantic-settings).
- Service port: **17010**. UI port (CORS): **17000**.
- Python version pinned to 3.12 in `.tool-versions` at repo root.
- Pin critical dependencies to compatible ranges:
  - `qdrant-client>=1.12,<2.0` (must match Qdrant server version)
  - HuggingFace Transformers CLAP (`clap-htsat-large` backbone)
  - ffmpeg >= 5.0 (verified at startup)

## Audio Processing Conventions

### Sample Rates and PCM Formats

The system uses a dual sample-rate pipeline. Using the wrong rate or format will produce garbage results.

| Consumer | Sample Rate | Format | ffmpeg flags |
|----------|-------------|--------|-------------|
| Olaf (fingerprint) | 16,000 Hz | f32le (32-bit float) | `-ar 16000 -ac 1 -f f32le -acodec pcm_f32le` |
| Chromaprint (dedup) | 16,000 Hz | s16le (16-bit signed int) | `-ar 16000 -ac 1 -f s16le -acodec pcm_s16le` |
| CLAP (embedding) | 48,000 Hz | f32le (32-bit float) | `-ar 48000 -ac 1 -f f32le -acodec pcm_f32le` |

- CLAP **requires** 48kHz input. 16kHz produces significantly degraded embeddings (cosine sim < 0.85).
- Olaf **requires** 32-bit float. Do NOT pass s16le to Olaf.
- Chromaprint s16le can be derived from the 16kHz f32le stream via numpy dtype cast (avoids a third ffmpeg call).

### CFFI / GIL Blocking (Critical)

Olaf uses a C library via CFFI. All CFFI calls are synchronous and hold the Python GIL. If called directly in an async function, they block the entire asyncio event loop and prevent parallel execution.

**Always** wrap Olaf CFFI calls in `loop.run_in_executor(None, ...)`:

```python
# CORRECT
result = await loop.run_in_executor(None, functools.partial(olaf_query, pcm_data))

# WRONG - blocks the event loop
result = olaf_query(pcm_data)  # Never do this in async code
```

### CLAP Model Lifecycle

- Pre-load in FastAPI lifespan handler (avoids 5-15s cold start on first request)
- Run warm-up inference with 5s silence during startup
- CLAP model must be a **singleton**. Use `app.state.clap_model` (set in lifespan handler). Both ingestion and search code must access the same instance.
- Do NOT call `load_clap_model()` in module-level code. Always access via FastAPI's application state or dependency injection.
- Consider `asyncio.Semaphore(1)` to prevent concurrent CPU-bound CLAP inferences from degrading latency

### Data Stores

The system writes to three data stores. All three must be consistent for correct behavior.

| Store | What | Managed By |
|-------|------|-----------|
| PostgreSQL | Track metadata, Chromaprint fingerprints | SQLAlchemy/Alembic |
| Olaf LMDB | Fingerprint inverted index | Olaf C library (single-writer, multi-reader) |
| Qdrant | CLAP embedding vectors (~47 chunks per track) | qdrant-client |

- Olaf LMDB has a **single-writer constraint**. Do NOT run concurrent ingestion processes.
- If PostgreSQL succeeds but Qdrant fails, the system is in an inconsistent state. Use `make rebuild-index` to recover.
- `make rebuild-index` recomputes Olaf and Qdrant data from raw audio files. It does NOT touch PostgreSQL.

## Environment Variables

All configuration is loaded via `app/settings.py` (pydantic-settings). Copy `.env.example` to `.env` for local development.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident` | PostgreSQL connection |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector database |
| `QDRANT_COLLECTION_NAME` | `audio_embeddings` | Qdrant collection for CLAP vectors |
| `OLAF_LMDB_PATH` | `./data/olaf_db` | Olaf fingerprint index directory |
| `AUDIO_STORAGE_ROOT` | `./data` | Root directory for raw audio file storage |
| `EMBEDDING_MODEL` | `clap-htsat-large` | CLAP model identifier |
| `EMBEDDING_DIM` | `512` | CLAP embedding dimension |
| `SERVICE_PORT` | `17010` | Uvicorn listen port |

## Startup Behavior

The service performs health checks during startup and will **refuse to start** if dependencies are unavailable:

1. PostgreSQL connectivity check (`SELECT 1`)
2. Qdrant connectivity check (`get_collections()`)
3. CLAP model loading (~5-15s on first run, downloads ~600MB model weights)
4. CLAP warm-up inference (~1-3s)

If startup fails, check:
- `make docker-up` to ensure Docker services are running
- `curl http://localhost:6333/healthz` for Qdrant health
- `docker compose exec -T postgres pg_isready -U audio_ident` for Postgres health

**Note**: The Qdrant collection (`audio_embeddings`) is NOT created at startup. It is created lazily during the first ingestion. Running a search before any ingestion will return empty results, not an error.

## Testing Conventions

- Test files live in `tests/` and mirror the `app/` module structure
- Use `pytest-asyncio` for async tests
- Use `httpx.AsyncClient` for integration tests against the FastAPI app
- Mock external services (Qdrant, Olaf LMDB) in unit tests; use real services in integration tests
- Audio test fixtures: small WAV/MP3 files (~1-5s) stored in `tests/fixtures/audio/`
- For CLAP tests: mock the model to avoid 600MB download and 5s+ inference in CI
- For Olaf tests: use a temporary LMDB directory per test (clean up in teardown)
- Run all tests: `uv run pytest` (from service directory) or `make test` (from root)
