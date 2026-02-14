# CLAUDE.md Improvement Analysis

> **Date**: 2026-02-14
> **Scope**: All three CLAUDE.md files analyzed against 8 implementation plan files
> **Output**: Actionable improvements to make implementation more likely to succeed
> **Methodology**: Cross-referenced each CLAUDE.md instruction against every phase plan, identifying gaps where an AI agent or developer following only CLAUDE.md would make mistakes, miss conventions, or create inconsistencies.

---

## Executive Summary

The three CLAUDE.md files (root, service, UI) provide a solid foundation for API contract management and basic development workflows. However, they were written during scaffolding and have not been updated to reflect the audio fingerprinting domain that the implementation plans describe in detail. The result is a significant **context gap**: an agent following CLAUDE.md has no awareness of audio processing conventions, system dependencies, multi-service coordination (PostgreSQL + Qdrant + Olaf LMDB), memory constraints, or the specific error handling patterns the system requires.

**The five most critical gaps are:**

1. **No system dependency documentation** -- ffmpeg, libmagic, fftw3, lmdb, chromaprint are all required but nowhere mentioned in any CLAUDE.md
2. **No audio processing conventions** -- dual sample-rate pipeline, CFFI/GIL concerns, CLAP model lifecycle are all domain-critical but absent
3. **No multi-store coordination guidance** -- the system writes to three data stores (PostgreSQL, Olaf LMDB, Qdrant) with no documented consistency model
4. **Service CLAUDE.md lists mypy but plans use pyright** -- type checker mismatch will cause confusion
5. **No environment variable documentation** -- Qdrant, Olaf, CLAP, and audio storage settings are essential but undocumented in CLAUDE.md

Addressing these gaps before Phase 2 begins will prevent significant rework and debugging time during Phases 3-6.

---

## Critical Improvements (Must-Fix Before Implementation)

### CRITICAL-1: Add System Dependencies Section to Root CLAUDE.md

**Why it matters**: Every implementation phase (1-6) depends on external system libraries. An agent running `make install` will install Python/Node packages but will NOT install ffmpeg, libmagic, fftw3, lmdb, or chromaprint. Builds will fail with cryptic errors. Phase 1 (Olaf compilation), Phase 3 (ffmpeg decoding, Chromaprint), and Phase 5 (python-magic) all require system libraries.

**Evidence from plans**:
- Phase 1 Step 1: `brew install fftw lmdb` (macOS) / `apt-get install build-essential libfftw3-dev liblmdb-dev` (Linux)
- Phase 3 Step 3.2: `brew install chromaprint` (macOS) / `apt install libchromaprint-dev` (Linux)
- Phase 5 Step 1.5: `brew install libmagic` (macOS) / `apt install libmagic1` (Linux)
- Phase 3 Step 1.1: ffmpeg >= 5.0 is required for all audio decoding
- Phase 3 devil's advocate: `fpcalc` binary may be needed separately for pyacoustid
- Phase 1 devil's advocate: `pkg-config --libs fftw3f` and `pkg-config --libs lmdb` for verification

**Recommended addition to root CLAUDE.md** (add after Bootstrap section):

```markdown
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
```

---

### CRITICAL-2: Fix Type Checker Inconsistency (mypy vs pyright)

**Why it matters**: The service CLAUDE.md lists `mypy` in the stack and key commands (`uv run mypy app`), but the root CLAUDE.md uses `pyright` (`make typecheck` runs `pyright + svelte-check`). The implementation plans reference `pyright` in Phase 2 Step 5.3 (`uv run pyright app/schemas/search.py`). Using two different type checkers creates conflicting error behavior and false confidence in type safety.

**Evidence**:
- Service CLAUDE.md line 8: "**Ruff** for linting/formatting, **mypy** for type checking"
- Service CLAUDE.md key commands: "Type check | `uv run mypy app`"
- Root CLAUDE.md line 21: "`make typecheck          # pyright + svelte-check`"
- Phase 2 Step 5.3: "`cd audio-ident-service && uv run pyright app/schemas/search.py`"

**Recommended fix**: Update service CLAUDE.md to use pyright consistently:

In the Stack section, change:
```
- **Ruff** for linting/formatting, **mypy** for type checking
```
to:
```
- **Ruff** for linting/formatting, **Pyright** for type checking
```

In the Key commands table, change:
```
| Type check | `uv run mypy app` |
```
to:
```
| Type check | `uv run pyright app` |
```

---

### CRITICAL-3: Add Audio Processing Conventions to Service CLAUDE.md

**Why it matters**: The service's core domain is audio processing, but the CLAUDE.md contains zero domain-specific conventions. An agent implementing Phase 3-5 without these conventions will make critical mistakes: wrong sample rates, wrong PCM formats, blocking the asyncio event loop with CFFI calls, or loading duplicate CLAP models.

**Evidence from plans**:
- Phase 3 Step 1.1: Dual sample-rate pipeline (16kHz f32le for Olaf, 16kHz s16le for Chromaprint, 48kHz f32le for CLAP)
- Phase 4a Step 1 edge case #6: "All Olaf CFFI calls are synchronous C code that holds the Python GIL. Always wrap CFFI calls in `loop.run_in_executor(None, ...)`"
- Phase 5 Step 3: CLAP model pre-loaded in lifespan handler; warm-up inference required
- Phase 3 devil's advocate #3: CLAP inference for 20K tracks is ~522 CPU hours; must address corpus size
- Phase 5 devil's advocate #5: No CLAP inference semaphore for concurrent requests
- Phase 7 cross-plan notes: CLAP model potentially loaded twice (ingestion + search)

**Recommended addition to service CLAUDE.md** (new section after Conventions):

```markdown
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
- Use a **single model instance** shared between ingestion and search (do NOT load twice)
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
```

---

### CRITICAL-4: Add Missing Environment Variables to Service CLAUDE.md

**Why it matters**: Phase 2 introduces Qdrant, Olaf, CLAP, and audio storage settings that are essential for the service to function. The current service CLAUDE.md mentions only the database and service port. An agent configuring the service will miss critical settings.

**Evidence from plans**:
- Phase 2 Step 2.2: .env.example with QDRANT_URL, QDRANT_COLLECTION_NAME, OLAF_LMDB_PATH, EMBEDDING_MODEL, EMBEDDING_DIM, CLAP_SAMPLE_RATE, AUDIO_STORAGE_ROOT
- Phase 2 Step 4.1: Settings class additions for qdrant_url, qdrant_api_key, qdrant_collection_name, audio_storage_root, olaf_lmdb_path, embedding_model, embedding_dim
- Phase 5 Step 1.2: MAX_UPLOAD_BYTES = 10MB

**Recommended addition to service CLAUDE.md** (new section or expand existing Conventions):

```markdown
## Environment Variables

All configuration is loaded via `app/settings.py` (pydantic-settings). Copy `.env.example` to `.env` for local development.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident` | PostgreSQL connection |
| `QDRANT_URL` | `http://localhost:6333` | Qdrant vector database |
| `QDRANT_COLLECTION_NAME` | `audio_embeddings` | Qdrant collection for CLAP vectors |
| `OLAF_LMDB_PATH` | `./data/olaf_db` | Olaf fingerprint index directory |
| `AUDIO_STORAGE_ROOT` | `./data` | Root directory for raw audio file storage |
| `EMBEDDING_MODEL` | `clap-laion-music` | CLAP model identifier |
| `EMBEDDING_DIM` | `512` | CLAP embedding dimension |
| `SERVICE_PORT` | `17010` | Uvicorn listen port |
```

---

### CRITICAL-5: Document Docker Compose Profile Requirement

**Why it matters**: Phase 2 introduces Docker Compose profiles for PostgreSQL and Qdrant. After this change, running `docker compose up -d` without profiles will start NOTHING. This is a breaking change from the existing workflow that the current CLAUDE.md does not warn about.

**Evidence from plans**:
- Phase 2 Step 2.1: Both postgres and qdrant services have `profiles:` set
- Phase 2 devil's advocate #1: "If a developer runs `docker compose up -d` without specifying profiles, NEITHER Postgres NOR Qdrant will start"
- Root CLAUDE.md Bootstrap: "make dev # starts postgres, service (port 17010), UI (port 17000)"

**Recommended addition to root CLAUDE.md** (update the Project Layout section and add to Conventions):

Add to Conventions:
```markdown
- Docker services require profiles: `make dev` handles this automatically. Do NOT use `docker compose up -d` directly (nothing will start). Use `make docker-up` or `make dev`.
- To start infrastructure without the service: `docker compose --profile postgres --profile qdrant up -d`
- To stop infrastructure: `make docker-down`
```

---

## High-Impact Improvements (Strongly Recommended)

### HIGH-1: Add Search Endpoint Error Codes to API Contract and CLAUDE.md

**Why it matters**: The current API contract (docs/api-contract.md) defines only 4 error codes (VALIDATION_ERROR, NOT_FOUND, RATE_LIMITED, INTERNAL_ERROR). Phase 5 introduces 6 additional search-specific error codes. Without updating the contract first, this violates the "frozen contract" golden rule.

**Evidence**:
- Phase 2 Step 1.1: FILE_TOO_LARGE (400), UNSUPPORTED_FORMAT (400), AUDIO_TOO_SHORT (400), SEARCH_TIMEOUT (504), SERVICE_UNAVAILABLE (503)
- Phase 5 Step 4: DECODE_FAILED (422), SEARCH_UNAVAILABLE (503)
- Phase 5 devil's advocate #4: Status code inconsistency between 400 and 422 for UNSUPPORTED_FORMAT
- Root CLAUDE.md: "Do NOT add endpoints without updating docs/api-contract.md first"

**Recommended action**: Before Phase 2 implementation begins, update the API contract to include search-specific error codes and harmonize status codes (recommend 422 for format validation, 400 for size/duration limits). Then update all three copies of the contract.

```markdown
## Search-Specific Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `FILE_TOO_LARGE` | 400 | Upload exceeds 10 MB |
| `AUDIO_TOO_SHORT` | 400 | Audio duration < 3 seconds |
| `UNSUPPORTED_FORMAT` | 422 | Content type not in allowed audio formats |
| `DECODE_FAILED` | 422 | ffmpeg cannot decode the audio file |
| `SEARCH_TIMEOUT` | 504 | Search exceeded 5-second timeout |
| `SEARCH_UNAVAILABLE` | 503 | Both search lanes failed |
```

---

### HIGH-2: Add Make Targets Documentation to Root CLAUDE.md

**Why it matters**: Phases 2-7 introduce critical Make targets (ingest, rebuild-index, eval-*) that are the primary interface for data management. The current CLAUDE.md only documents dev/test/lint/fmt/typecheck. An agent implementing later phases will not know these targets exist.

**Evidence from plans**:
- Phase 3 Step 6.4: `make ingest AUDIO_DIR=/path/to/mp3s`, `make rebuild-index`
- Phase 7: `make eval-corpus`, `make eval-exact`, `make eval-vibe`, `make eval-latency`, `make eval-report`
- Phase 2 Step 2.3: `make docker-up`, `make docker-down`

**Recommended addition to root CLAUDE.md** (expand "How to Run"):

```markdown
## How to Run

```bash
make dev                # starts everything (postgres + qdrant + service + UI)
make docker-up          # starts only Docker services (postgres + qdrant)
make docker-down        # stops all Docker services
make test               # runs pytest + vitest
make lint               # ruff check + eslint
make fmt                # ruff format + prettier
make typecheck          # pyright + svelte-check
```

## Data Management

```bash
make ingest AUDIO_DIR=/path/to/mp3s   # ingest audio files into all stores
make rebuild-index                     # drop Olaf + Qdrant, re-index from raw audio
```
```

---

### HIGH-3: Add Startup Health Check Documentation

**Why it matters**: Phase 2 adds fail-fast health checks to the lifespan handler. If PostgreSQL or Qdrant is down, the service will not start with a SystemExit. This is intentional behavior but will surprise developers who are used to the service starting regardless. The error message includes connection URLs that should not contain credentials.

**Evidence from plans**:
- Phase 2 Step 4.3: SystemExit with error messages for Postgres/Qdrant failures
- Phase 2 devil's advocate #3: "Error message includes settings.database_url which may contain credentials"
- Phase 2 devil's advocate #4: "No Qdrant collection auto-creation at startup"
- Phase 5 Step 3: CLAP model loading during startup adds 5-15s

**Recommended addition to service CLAUDE.md**:

```markdown
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
```

---

### HIGH-4: Add Frontend API Configuration to UI CLAUDE.md

**Why it matters**: Phase 6 uses `API_BASE_URL` in the TanStack Query mutation but this variable is never defined. The UI CLAUDE.md mentions `VITE_API_BASE_URL` for the port but does not explain how it connects to the API client. CORS configuration is mentioned in the API contract but not in either CLAUDE.md.

**Evidence from plans**:
- Phase 6 Step 5: `${API_BASE_URL}/api/v1/search` used but not defined
- Phase 6 devil's advocate #1: `API_BASE_URL` configuration missing
- Phase 6 devil's advocate #2: CORS not mentioned in frontend plan
- UI CLAUDE.md line 48: "API: 17010 (configurable via VITE_API_BASE_URL env var)"
- API contract: CORS configured for `http://localhost:17000`

**Recommended addition to UI CLAUDE.md** (expand Conventions):

```markdown
## API Configuration

- API base URL is configured via `VITE_API_BASE_URL` environment variable (default: `http://localhost:17010`)
- Access in code via: `import.meta.env.VITE_API_BASE_URL` or `$env/static/public`
- All API calls go through `src/lib/api/client.ts` which reads this variable
- CORS is configured on the backend to allow `http://localhost:17000` in development
- The backend must be running for `make gen-client` to work (it reads `/openapi.json` from the live server)
```

---

### HIGH-5: Add Testing Conventions for Audio Domain

**Why it matters**: The implementation plans define 15+ test files across 7 phases, but neither the service nor root CLAUDE.md documents testing patterns for audio-specific concerns: test fixtures for audio files, mocking CLAP/Olaf, testing async code with CFFI executors, or managing test database state across three stores.

**Evidence from plans**:
- Phase 3: tests/test_audio_decode.py, test_audio_metadata.py, test_audio_dedup.py, test_audio_fingerprint.py, test_audio_embedding.py
- Phase 4: tests/test_search_exact.py, test_search_vibe.py
- Phase 5: tests/test_search_integration.py (httpx.AsyncClient with FastAPI test client)
- Phase 2 risks: "Existing tests break due to lifespan change" -- "Update test fixtures to mock Qdrant client"

**Recommended addition to service CLAUDE.md**:

```markdown
## Testing Conventions

- Test files live in `tests/` and mirror the `app/` module structure
- Use `pytest-asyncio` for async tests
- Use `httpx.AsyncClient` for integration tests against the FastAPI app
- Mock external services (Qdrant, Olaf LMDB) in unit tests; use real services in integration tests
- Audio test fixtures: small WAV/MP3 files (~1-5s) stored in `tests/fixtures/audio/`
- For CLAP tests: mock the model to avoid 600MB download and 5s+ inference in CI
- For Olaf tests: use a temporary LMDB directory per test (clean up in teardown)
- Run all tests: `uv run pytest` (from service directory) or `make test` (from root)
```

---

### HIGH-6: Add Ingestion Workflow Guard-rail

**Why it matters**: The ingestion pipeline writes to three data stores in parallel. If it crashes mid-batch, partial state is left across stores. The plans acknowledge this problem but provide no guard-rail in CLAUDE.md to prevent it or guide recovery.

**Recommended addition to root CLAUDE.md**:

```markdown
## Guard-rails / Non-goals

(add to existing list)

- Do NOT run multiple `make ingest` processes simultaneously (Olaf LMDB is single-writer)
- Do NOT manually modify Olaf LMDB files or Qdrant collections -- use `make rebuild-index` for recovery
- Do NOT ingest files shorter than 3 seconds or longer than 30 minutes
- If ingestion crashes mid-batch, re-run `make ingest` -- SHA-256 dedup will skip already-ingested files
```

---

## Per-File Analysis

### Root CLAUDE.md (`/Users/mac/workspace/audio-ident/CLAUDE.md`)

**Current state**: Comprehensive for contract management and basic developer workflow. Strong "Golden Rule" section. Good "How to Add a New Endpoint" checklist.

**Gaps identified**:

| Category | Gap | Severity | Plan Reference |
|----------|-----|----------|---------------|
| Missing guard-rail | No mention of system dependencies (ffmpeg, fftw, etc.) | Critical | Phase 1, 3, 5 |
| Missing guard-rail | Docker profiles will break `docker compose up -d` | Critical | Phase 2 |
| Missing convention | No Make targets for data management (ingest, rebuild-index) | High | Phase 3, 7 |
| Missing guard-rail | No warning about concurrent ingestion (LMDB single-writer) | High | Phase 3 DA |
| Missing convention | No audio file format/duration constraints documented | Medium | Phase 3, 5 |
| Contradiction | `make dev` description says "starts postgres" but will now start postgres + qdrant | Medium | Phase 2 |
| Missing convention | No mention of Qdrant as an infrastructure dependency | High | Phase 2-7 |
| Missing coordination | Contract change checklist does not mention search endpoint | Medium | Phase 2 |

**Contradictions found**:
- `make dev` comment says "starts postgres, service (port 17010), UI (port 17000)" but Phase 2 adds Qdrant to this target. Update the comment.
- The "How to Add a New Endpoint" workflow step 7 says `cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/` but there is also a root copy. Step 5 of the Contract Synchronization Workflow handles the root copy, but the "How to Add" section is missing this step.

---

### Service CLAUDE.md (`/Users/mac/workspace/audio-ident/audio-ident-service/CLAUDE.md`)

**Current state**: Basic scaffolding information. Lists stack, project layout, key commands, and conventions. Written before any audio-domain code existed.

**Gaps identified**:

| Category | Gap | Severity | Plan Reference |
|----------|-----|----------|---------------|
| Contradiction | Lists mypy but project uses pyright | Critical | Phase 2 Step 5.3 |
| Missing convention | No audio processing conventions (sample rates, formats) | Critical | Phase 3 Step 1 |
| Missing convention | No CFFI/GIL guidance for async code | Critical | Phase 4a edge case 6 |
| Missing convention | No environment variable documentation | Critical | Phase 2 Step 4.1 |
| Missing convention | No multi-store consistency guidance | High | Phase 3 DA #2 |
| Missing section | No startup behavior documentation | High | Phase 2 Step 4, Phase 5 Step 3 |
| Missing section | No testing conventions for audio domain | High | Phase 3-5 tests |
| Missing convention | No CLAP model lifecycle documentation | High | Phase 5 Step 3, Phase 7 cross-plan |
| Missing layout | Project layout doesn't include `app/audio/` or `app/search/` or `app/ingest/` | Medium | Phase 3-5 |
| Missing convention | No memory management guidance for batch processing | Medium | Phase 3 memory section |
| Ambiguity | "PyJWT + argon2-cffi for auth" listed in stack but auth is stub-only | Low | API contract auth section |

**Project layout update needed**: The current layout shows:
```
app/
  main.py, settings.py, routers/, db/, models/, schemas/, auth/
```
After Phase 3-5, it will also include:
```
app/
  audio/           # decode, metadata, dedup, fingerprint, embedding, storage, qdrant_setup
  search/          # exact, vibe, orchestrator, aggregation
  ingest/          # pipeline, cli
```

---

### UI CLAUDE.md (`/Users/mac/workspace/audio-ident/audio-ident-ui/CLAUDE.md`)

**Current state**: Clean and well-structured. Good Svelte 5 Runes conventions. Clear on TanStack Query usage.

**Gaps identified**:

| Category | Gap | Severity | Plan Reference |
|----------|-----|----------|---------------|
| Missing convention | No API_BASE_URL / VITE_API_BASE_URL configuration | High | Phase 6 Step 5, DA #1 |
| Missing convention | No CORS documentation | High | Phase 6 DA #2 |
| Missing convention | No accessibility (a11y) requirements | Medium | Phase 6 DA #2 |
| Missing guard-rail | No AbortController convention for mutations | Medium | Phase 6 DA #4 |
| Missing convention | No MediaRecorder/Web Audio API patterns | Medium | Phase 6 Step 2 |
| Missing convention | No file extension handling for recorded audio | Medium | Phase 6 DA edge case #3 |
| Missing guard-rail | No guidance on TanStack Query provider setup | Low | Phase 6 DA #3 |
| Missing layout | Architecture section doesn't include components/ or routes/search/ | Low | Phase 6 |

**Specific improvements**:

Add to UI Conventions:
```markdown
- Use `AbortController` to cancel in-flight API requests when a new request starts
- Recorded audio file extension must match the MIME type (`.webm` for WebM/Opus, `.mp4` for MP4/AAC) -- do NOT hardcode `.webm`
- For MediaRecorder: disable `echoCancellation`, `noiseSuppression`, and `autoGainControl` (these degrade fingerprint quality)
- Add ARIA attributes to all interactive components (`aria-live`, `aria-disabled`, `role` where appropriate)
```

---

## Cross-Project Coordination Issues

### COORD-1: Contract Must Be Updated Before Phase 2 Implementation

The root CLAUDE.md's "Golden Rule" states endpoints must be documented in the contract before implementation. Phase 2 adds 4 new endpoints (search, ingest, tracks, tracks/{id}) but the current contract only defines health and version. This creates a sequencing problem: the contract must be updated and frozen before the Phase 2 Pydantic schemas are written.

**Recommendation**: Add a note to Phase 2 Step 1 that the contract update is the FIRST task, and add this note to root CLAUDE.md:

```markdown
## Implementation Sequencing

When adding new endpoints:
1. Update the API contract FIRST (this is a blocking prerequisite)
2. Copy contract to all three locations
3. THEN implement backend schemas and routes
4. THEN regenerate frontend types

Never implement code before the contract is updated and copied.
```

### COORD-2: gen-client Requires Running Backend

The `make gen-client` target calls the service's `/openapi.json` endpoint. If the backend cannot start (CLAP model fails to load, Qdrant is down, etc.), type generation fails. This creates a chicken-and-egg problem during Phase 6.

**Evidence**: Phase 6 DA #1: "If the backend has errors (e.g., CLAP model fails to load during lifespan), the service won't start and types can't be generated."

**Recommendation**: Add to root CLAUDE.md:

```markdown
## Guard-rails / Non-goals

(add to existing list)

- `make gen-client` requires the backend to be running (`make dev`). If the backend cannot start, fix backend issues first before attempting frontend type generation.
- As a fallback, commit the generated `openapi.json` to the repo so types can be regenerated from the static file.
```

### COORD-3: Effort Estimate Discrepancy

The plan overview claims 26-38 developer-days. The sum of individual phase effort breakdowns is 228 hours = 28.5 days minimum. The low end (26d) is unreachable.

**Evidence**: Phase 7 cross-plan notes: "Sum of individual phase efforts: 32h + 28h + 40h + 32h + 28h + 36h + 32h = 228h = ~28.5 days"

**Recommendation**: This is not a CLAUDE.md issue per se, but the root CLAUDE.md should reference the actual implementation timeline if it is used for project planning. More importantly, several phase-level devil's advocate reviews recommend reducing some estimates (Phase 2: 28h to 20-24h) and increasing others (Phase 7: 32h to 48-64h). The net effect is roughly 29-40 developer-days.

### COORD-4: CLAP Model Instance Sharing

Phase 3 (ingestion) and Phase 5 (search) both load the CLAP model. If both are active simultaneously, two copies of the model consume ~1.2-2GB of memory.

**Evidence**: Phase 7 cross-plan notes: "CLAP model loading appears in Phase 3 (ingestion) and Phase 5 (search). This could mean two copies of CLAP in memory."

**Recommendation**: Add to service CLAUDE.md under Audio Processing Conventions:

```markdown
- CLAP model must be a **singleton**. Use `app.state.clap_model` (set in lifespan handler). Both ingestion and search code must access the same instance.
- Do NOT call `load_clap_model()` in module-level code. Always access via FastAPI's application state or dependency injection.
```

### COORD-5: Version Pinning Inconsistency

Several dependencies are pinned in some places but not others.

**Evidence**:
- Phase 7 cross-plan notes: "CLAP: >=1.1 in overview, not pinned in Phase 3" and "qdrant-client: Not version-pinned anywhere"
- Phase 2 DA: "qdrant-client version not pinned"

**Recommendation**: Add to service CLAUDE.md Conventions:

```markdown
- Pin critical dependencies to compatible ranges:
  - `qdrant-client>=1.12,<2.0` (must match Qdrant server v1.16.3)
  - `laion-clap>=1.1` (larger_clap_music checkpoint)
  - ffmpeg >= 5.0 (verified at startup)
```

---

## Summary of Recommended Changes

### By Priority

| Priority | Count | Description |
|----------|-------|-------------|
| Critical (must-fix) | 5 | System deps, type checker, audio conventions, env vars, Docker profiles |
| High (strongly recommended) | 6 | Error codes, Make targets, startup docs, API config, test conventions, ingestion guard-rail |
| Coordination fixes | 5 | Contract sequencing, gen-client fallback, effort estimates, CLAP singleton, version pinning |

### By File

| File | Changes Needed |
|------|---------------|
| Root CLAUDE.md | System deps section, Docker profiles note, expanded Make targets, ingestion guard-rails, implementation sequencing note, gen-client fallback |
| Service CLAUDE.md | Fix mypy->pyright, audio processing conventions section, env vars section, startup behavior section, testing conventions, expanded project layout, CLAP singleton note, version pinning |
| UI CLAUDE.md | API config section, CORS note, a11y convention, AbortController convention, file extension convention, MediaRecorder settings |
| API Contract | Search endpoint definition, new error codes with harmonized status codes |

### Estimated Effort to Implement

All CLAUDE.md improvements can be completed in approximately 2-4 hours. The API contract update (HIGH-1) is part of Phase 2 Step 1 and should be done as the first task of that phase.

---

## Appendix: Devil's Advocate Findings Not Covered Elsewhere

The following findings from phase-level devil's advocate reviews are important but do not map directly to CLAUDE.md changes. They should be tracked as implementation considerations:

1. **Phase 1 DA**: Prototype 1 clean-clip Go threshold (>=80%) is too low relative to Phase 7 target (>=98%). Recommend raising to >=90%.
2. **Phase 1 DA**: Prototype 2 uses random noise, not real audio. Real audio exercises different code paths.
3. **Phase 3 DA**: No progress persistence for batch ingestion. Crashing at track 5000 of 20K requires re-checking all previous tracks.
4. **Phase 3 DA**: Disk space requirement for raw audio storage (~100GB for 20K tracks) is undocumented.
5. **Phase 4 DA**: `exact_match_track_id` parameter in vibe lane creates implicit dependency on exact lane completing first, contradicting parallel execution.
6. **Phase 4 DA**: `VIBE_MATCH_THRESHOLD=0.60` is unvalidated. Real CLAP cosine similarity for "similar" music may be much lower.
7. **Phase 5 DA**: `asyncio.gather` does not cancel the surviving task when one times out. Response time is `max(both_tasks)`, not `timeout`.
8. **Phase 5 DA**: Upload validation reads entire file into memory (up to 10MB per request per concurrent user).
9. **Phase 6 DA**: Safari MP4/AAC recordings saved with `.webm` extension confuse server-side format detection.
10. **Phase 7 DA**: 200 mic recordings across 4 environments is ~6.7 hours of manual recording. Budget 12-16h, not 8h.
11. **Phase 7 DA**: 3 human raters for vibe evaluation may not be available for a solo project.
12. **Phase 7 cross-plan**: Effort totals (228h = 28.5d minimum) exceed the overview's low estimate (26d).
