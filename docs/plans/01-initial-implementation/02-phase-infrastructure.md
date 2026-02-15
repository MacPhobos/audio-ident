# Phase 2: Infrastructure (~3-4 days)

> **Depends on**: Phase 1 (validation prototypes must pass)
> **Blocks**: Phase 3 (ingestion pipeline)
> **Goal**: API contract, database schema, Docker services, health checks, Pydantic schemas

---

## Overview

This phase establishes the foundational infrastructure: the API contract (frozen once approved), PostgreSQL schema for track metadata, Qdrant service in Docker, startup health checks, and Pydantic schemas matching the contract.

**Corresponds to**: 06-implementation-plan.md Milestones 1-2

---

## Step 1: API Contract (~4 hours)

**Reference**: 04-architecture-and-api.md §4.1, CLAUDE.md "How to Add a New Endpoint"

### 1.1 Define Search Endpoint

Edit `audio-ident-service/docs/api-contract.md` to add:

**`POST /api/v1/search`** — Multipart form upload
- Request: `audio` (UploadFile, max 10MB) + `mode` (Form: "exact"|"vibe"|"both") + `max_results` (Form: 1-50, default 10)
- Response: `SearchResponse` with `request_id`, `query_duration_ms`, `exact_matches[]`, `vibe_matches[]`, `mode_used`
- Error codes: `FILE_TOO_LARGE` (400), `UNSUPPORTED_FORMAT` (400), `AUDIO_TOO_SHORT` (400), `SEARCH_TIMEOUT` (504), `SERVICE_UNAVAILABLE` (503)

**`POST /api/v1/ingest`** — Admin/CLI endpoint for batch ingestion
- Request: `audio` (UploadFile) or `directory` (Form: path string)
- Response: `IngestResponse` with `track_id`, `title`, `artist`, `status`

**`GET /api/v1/tracks`** — List ingested tracks
- Query params: `page`, `per_page`, `search` (title/artist)
- Response: Paginated list of `TrackInfo`

**`GET /api/v1/tracks/{id}`** — Track detail
- Response: Full `TrackDetail` with metadata, fingerprint status, embedding status

### 1.2 Define Response Schemas (in contract)

```typescript
// TypeScript interfaces for the contract document
interface SearchResponse {
  request_id: string;       // UUID
  query_duration_ms: number;
  exact_matches: ExactMatch[];
  vibe_matches: VibeMatch[];
  mode_used: "exact" | "vibe" | "both";
}

interface ExactMatch {
  track: TrackInfo;
  confidence: number;        // 0.0-1.0
  offset_seconds: number | null;
  aligned_hashes: number;
}

interface VibeMatch {
  track: TrackInfo;
  similarity: number;        // 0.0-1.0
  embedding_model: string;
}

interface TrackInfo {
  id: string;               // UUID
  title: string;
  artist: string | null;
  album: string | null;
  duration_seconds: number;
  ingested_at: string;      // ISO 8601
}
```

### 1.3 Copy Contract (Golden Rule)

Per CLAUDE.md, the contract must exist in all three locations:

```bash
# Edit the source of truth
vim audio-ident-service/docs/api-contract.md

# Copy to UI and root
cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/api-contract.md
cp audio-ident-service/docs/api-contract.md docs/api-contract.md
```

### Acceptance Criteria
- [ ] Contract document includes all 4 endpoints with full request/response schemas
- [ ] Contract exists in all 3 locations (service, UI, root) and is identical
- [ ] Contract version bumped (e.g., v1.1.0)

### Commands to Verify
```bash
diff audio-ident-service/docs/api-contract.md audio-ident-ui/docs/api-contract.md
diff audio-ident-service/docs/api-contract.md docs/api-contract.md
# Both should show no differences
```

---

## Step 2: Docker Compose + .env (~4 hours)

**Reference**: 04-architecture-and-api.md §0.4, 03-embeddings-and-qdrant.md §3.4

### 2.1 Update docker-compose.yml

**File**: `docker-compose.yml`

Add Qdrant service alongside existing PostgreSQL. Add compose profiles to both services for conditional startup.

```yaml
services:
  postgres:
    image: postgres:16
    profiles: ["postgres"]           # NEW: only starts when profile activated
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    environment:
      POSTGRES_DB: audio_ident
      POSTGRES_USER: audio_ident
      POSTGRES_PASSWORD: audio_ident
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U audio_ident"]
      interval: 5s
      timeout: 3s
      retries: 5

  qdrant:                            # NEW
    image: qdrant/qdrant:v1.16.3
    profiles: ["qdrant"]
    ports:
      - "${QDRANT_HTTP_PORT:-6333}:6333"
      - "${QDRANT_GRPC_PORT:-6334}:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      QDRANT__SERVICE__GRPC_PORT: 6334
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:6333/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
  qdrant_data:                       # NEW
```

### 2.2 Create .env.example

**File**: `.env.example` (in repo root, alongside docker-compose.yml)

See 07-deliverables.md §7.6 for the full template. Key additions:

```bash
# Deployment modes: "docker" or "external"
POSTGRES_MODE=docker
QDRANT_MODE=docker

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=audio_embeddings
QDRANT_HTTP_PORT=6333
QDRANT_GRPC_PORT=6334

# Audio storage
AUDIO_STORAGE_ROOT=./data

# Olaf
OLAF_LMDB_PATH=./data/olaf_db

# Embedding
EMBEDDING_MODEL=clap-htsat-large
EMBEDDING_DIM=512
CLAP_SAMPLE_RATE=48000
```

### 2.3 Update Makefile

**File**: `Makefile`

Replace the existing `db-up` target with mode-aware logic:

```makefile
POSTGRES_MODE ?= docker
QDRANT_MODE ?= docker

docker-up: ## Start Docker-managed infra services based on MODE flags
	@if [ "$(POSTGRES_MODE)" = "docker" ]; then \
		docker compose --profile postgres up -d && \
		echo "Waiting for Postgres..." && \
		until docker compose exec -T postgres pg_isready -U audio_ident > /dev/null 2>&1; do sleep 0.5; done && \
		echo "Postgres ready."; \
	else \
		echo "Postgres mode=external, skipping Docker."; \
	fi
	@if [ "$(QDRANT_MODE)" = "docker" ]; then \
		docker compose --profile qdrant up -d && \
		echo "Waiting for Qdrant..." && \
		until curl -sf http://localhost:$${QDRANT_HTTP_PORT:-6333}/healthz > /dev/null 2>&1; do sleep 0.5; done && \
		echo "Qdrant ready."; \
	else \
		echo "Qdrant mode=external, skipping Docker."; \
	fi

docker-down: ## Stop all Docker infra services
	docker compose --profile postgres --profile qdrant down

dev: ## Start postgres, qdrant, service, and UI
	@trap 'echo "Shutting down..."; kill 0; exit 0' INT TERM; \
	$(MAKE) docker-up; \
	cd $(SERVICE_DIR) && uv run alembic upgrade head; \
	cd $(SERVICE_DIR) && uv run uvicorn app.main:app --host 0.0.0.0 --port $(SERVICE_PORT) --reload & \
	cd $(UI_DIR) && pnpm dev --port $(UI_PORT) & \
	wait
```

### Acceptance Criteria
- [ ] `make dev` starts Postgres + Qdrant + service + UI
- [ ] `QDRANT_MODE=external make dev` skips Qdrant Docker, starts rest
- [ ] `POSTGRES_MODE=external make dev` skips Postgres Docker, starts rest
- [ ] Qdrant health check passes: `curl http://localhost:6333/healthz`
- [ ] `make docker-down` stops all services cleanly

### Commands to Verify
```bash
make dev
curl http://localhost:6333/healthz        # Should return OK
curl http://localhost:17010/health         # Service health
make docker-down
```

### Rollback
```bash
# Revert docker-compose.yml to remove Qdrant service
git checkout docker-compose.yml
# Revert Makefile changes
git checkout Makefile
```

---

## Step 3: Database Schema (~8 hours)

**Reference**: 05-ingestion-pipeline.md §5.2, 07-deliverables.md §7.2

### 3.1 Create Track Model

**File**: `audio-ident-service/app/models/track.py` (NEW)

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Core metadata
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    artist: Mapped[str | None] = mapped_column(String(500), nullable=True)
    album: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Audio properties
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bitrate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    format: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # File identity
    file_hash_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)

    # Chromaprint (ingestion-time content dedup ONLY)
    chromaprint_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    chromaprint_duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Olaf fingerprint status (fingerprints stored in Olaf LMDB, not PG)
    olaf_indexed: Mapped[bool] = mapped_column(default=False)

    # Embedding reference (vectors stored in Qdrant, referenced by track_id)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Indexes
    __table_args__ = (
        Index("ix_tracks_file_hash", "file_hash_sha256", unique=True),
        Index("ix_tracks_artist_title", "artist", "title"),
        Index("ix_tracks_ingested_at", "ingested_at"),
    )
```

### 3.2 Register Model

**File**: `audio-ident-service/app/models/__init__.py`

Add import so Alembic detects the model:

```python
from app.models.track import Track  # noqa: F401
```

### 3.3 Generate Migration

```bash
cd audio-ident-service
uv run alembic revision --autogenerate -m "add tracks table"
```

**Review the generated migration** — verify:
- All columns are present with correct types
- Indexes are created (ix_tracks_file_hash, ix_tracks_artist_title, ix_tracks_ingested_at)
- UNIQUE constraint on file_hash_sha256
- UUID primary key with gen_random_uuid() default

### 3.4 Test Migration Forward and Backward

```bash
# Forward (create table)
cd audio-ident-service && uv run alembic upgrade head

# Verify schema
docker compose exec -T postgres psql -U audio_ident -c "\d tracks"
docker compose exec -T postgres psql -U audio_ident -c "\di" | grep tracks

# Backward (drop table)
cd audio-ident-service && uv run alembic downgrade -1

# Forward again (should be clean)
cd audio-ident-service && uv run alembic upgrade head
```

### Acceptance Criteria
- [ ] Migration runs forward cleanly on Docker Postgres
- [ ] Migration runs backward cleanly (drops table)
- [ ] Forward again works (idempotent)
- [ ] All indexes verified via `\di`
- [ ] UUID primary key generates valid UUIDs
- [ ] `make db-reset` works with the new migration

### Rollback
```bash
cd audio-ident-service && uv run alembic downgrade -1
rm app/models/track.py
# Remove the generated migration file from alembic/versions/
```

---

## Step 4: Startup Health Checks (~4 hours)

**Reference**: 04-architecture-and-api.md §0.4 (Startup Health Checks)

### 4.1 Add Qdrant Settings

**File**: `audio-ident-service/app/settings.py`

Add Qdrant and storage settings to the existing Settings class:

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "audio_embeddings"

    # Audio storage
    audio_storage_root: str = "./data"

    # Olaf
    olaf_lmdb_path: str = "./data/olaf_db"

    # Embedding
    embedding_model: str = "clap-htsat-large"
    embedding_dim: int = 512
```

### 4.2 Add Qdrant Dependency

```bash
cd audio-ident-service && uv add qdrant-client
```

### 4.3 Add Lifespan Handler with Health Checks

**File**: `audio-ident-service/app/main.py`

Update `create_app()` to use a lifespan context manager:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
import logging

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.db.engine import engine
from app.settings import settings

logger = logging.getLogger(__name__)


async def _check_postgres() -> None:
    """Verify PostgreSQL is reachable."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_qdrant(client: AsyncQdrantClient) -> None:
    """Verify Qdrant is reachable."""
    await client.get_collections()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- Startup ---
    # 1. Check Postgres
    try:
        await _check_postgres()
        logger.info("PostgreSQL connection verified")
    except Exception as exc:
        raise SystemExit(
            f"FATAL: Cannot reach PostgreSQL at {settings.database_url}. Error: {exc}"
        ) from exc

    # 2. Check Qdrant
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key if settings.qdrant_api_key else None,
    )
    try:
        await _check_qdrant(qdrant)
        logger.info("Qdrant connection verified")
    except Exception as exc:
        raise SystemExit(
            f"FATAL: Cannot reach Qdrant at {settings.qdrant_url}. Error: {exc}"
        ) from exc

    app.state.qdrant = qdrant

    yield

    # --- Shutdown ---
    await qdrant.close()
    await engine.dispose()
```

Update `create_app()` to pass the lifespan:

```python
def create_app() -> FastAPI:
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    # ... rest unchanged ...
```

### Acceptance Criteria
- [ ] Service starts successfully when both Postgres and Qdrant are running
- [ ] Service refuses to start with clear error when Postgres is down
- [ ] Service refuses to start with clear error when Qdrant is down
- [ ] Error messages include the connection URL for debugging

### Commands to Verify
```bash
# Test success case
make dev
curl http://localhost:17010/health  # Should return OK

# Test Postgres failure
make docker-down
docker compose --profile qdrant up -d  # Only Qdrant
cd audio-ident-service && uv run uvicorn app.main:app --port 17010
# Should fail with "FATAL: Cannot reach PostgreSQL..."

# Test Qdrant failure
make docker-down
docker compose --profile postgres up -d  # Only Postgres
cd audio-ident-service && uv run uvicorn app.main:app --port 17010
# Should fail with "FATAL: Cannot reach Qdrant..."
```

### Rollback
```bash
# Revert main.py to remove lifespan
git checkout audio-ident-service/app/main.py
# Remove qdrant-client from dependencies
cd audio-ident-service && uv remove qdrant-client
```

---

## Step 5: Pydantic Schemas (~4 hours)

**Reference**: 04-architecture-and-api.md §4.1, 00-reconciliation-summary.md

### 5.1 Create Search Schemas

**File**: `audio-ident-service/app/schemas/search.py` (NEW)

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SearchMode(StrEnum):
    EXACT = "exact"
    VIBE = "vibe"
    BOTH = "both"


class TrackInfo(BaseModel):
    """Minimal track metadata returned in search results."""
    id: uuid.UUID
    title: str
    artist: str | None = None
    album: str | None = None
    duration_seconds: float
    ingested_at: datetime


class ExactMatch(BaseModel):
    """Result from the fingerprint (exact identification) lane."""
    track: TrackInfo
    confidence: float = Field(ge=0.0, le=1.0)
    offset_seconds: float | None = None
    aligned_hashes: int


class VibeMatch(BaseModel):
    """Result from the embedding (vibe/similarity) lane."""
    track: TrackInfo
    similarity: float = Field(ge=0.0, le=1.0)
    embedding_model: str


class SearchResponse(BaseModel):
    """Combined response from both search lanes."""
    request_id: uuid.UUID
    query_duration_ms: float
    exact_matches: list[ExactMatch] = Field(default_factory=list)
    vibe_matches: list[VibeMatch] = Field(default_factory=list)
    mode_used: SearchMode
```

### 5.2 Create Ingest Schemas

**File**: `audio-ident-service/app/schemas/ingest.py` (NEW)

```python
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class IngestResponse(BaseModel):
    track_id: uuid.UUID
    title: str
    artist: str | None = None
    status: str  # "ingested", "duplicate", "error"


class IngestReport(BaseModel):
    total: int
    ingested: int = 0
    duplicates: int = 0
    errors: list[IngestError] = []


class IngestError(BaseModel):
    file: str
    error: str
```

### 5.3 Verify OpenAPI Generation

```bash
cd audio-ident-service
uv run uvicorn app.main:app --port 17010 &
sleep 2
curl http://localhost:17010/openapi.json | python -m json.tool | grep -i "search"
kill %1
```

Schemas should appear in the OpenAPI spec under `components.schemas`.

### Acceptance Criteria
- [ ] All schemas defined in `app/schemas/search.py` and `app/schemas/ingest.py`
- [ ] Schemas match the API contract exactly (field names, types, constraints)
- [ ] OpenAPI spec generates correctly with all schema types visible
- [ ] `SearchMode` enum has exactly 3 values: exact, vibe, both
- [ ] `ExactMatch` includes `aligned_hashes` and `offset_seconds` (Olaf fields, not Chromaprint)

### Commands to Verify
```bash
# Type check
cd audio-ident-service && uv run pyright app/schemas/search.py
cd audio-ident-service && uv run pyright app/schemas/ingest.py

# Test schema validation
cd audio-ident-service && uv run python -c "
from app.schemas.search import SearchResponse, SearchMode
import uuid
resp = SearchResponse(
    request_id=uuid.uuid4(),
    query_duration_ms=123.45,
    exact_matches=[],
    vibe_matches=[],
    mode_used=SearchMode.BOTH,
)
print(resp.model_dump_json(indent=2))
"
```

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Docker Compose profiles require Compose v2.1+ | Low | Medium | `docker compose version` — most installs are v2+ now |
| Alembic migration generates incorrect types for UUID | Low | Medium | Review generated migration manually before running |
| Qdrant v1.16.3 image not yet on Docker Hub | Very Low | Low | Pin to v1.16.2 if v1.16.3 is unavailable |
| [Updated] Qdrant client `vectors_count` removed in v1.16.2 | Medium | Medium | Use `indexed_vectors_count` instead; update all health checks and collection status queries accordingly |
| Existing tests break due to lifespan change | Medium | Low | Update test fixtures to mock Qdrant client |

## Edge Cases & Gotchas

1. **Alembic migration order**: If other migrations were added between scaffold and now, ensure the tracks migration depends on the correct head.
2. **Docker volume persistence**: `qdrant_data` volume persists across `docker compose down`. Use `docker compose down -v` to fully reset.
3. **CORS origins**: Adding Qdrant doesn't affect CORS, but ensure the service port hasn't changed.
4. **`.env` vs `.env.example`**: Never commit `.env`. Only commit `.env.example`. Developers copy and customize.
5. **[Updated] Olaf bundles its own dependencies**: Olaf includes its own pffft and LMDB libraries. Homebrew `fftw` and `lmdb` are NOT required as Olaf dependencies. Do not list them as system prerequisites for Olaf.
6. **[Updated] Qdrant `vectors_count` field removed**: As of Qdrant v1.16.2, the `vectors_count` field was removed from collection info responses. Use `indexed_vectors_count` in all health checks, status queries, and monitoring code.

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| API contract definition + review | 4h |
| docker-compose.yml + .env.example | 4h |
| Track model + Alembic migration | 8h |
| Startup health checks (lifespan) | 4h |
| Pydantic schemas | 4h |
| Testing + verification | 4h |
| **Total** | **~28h (3.5 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: HIGH** — Infrastructure phases are the most predictable. The Docker Compose profiles approach correctly matches the research (04-architecture-and-api.md §0.4). Pydantic schemas match the contract. The main concerns are minor but worth flagging.

### Gaps Identified

1. **Docker Compose profiles break `docker compose up -d` (no profiles).** If a developer runs `docker compose up -d` without specifying profiles, NEITHER Postgres NOR Qdrant will start (profiles require explicit activation). This is a breaking change from the existing `make dev` workflow. The plan should document this clearly and consider adding a default profile or a "dev" profile that includes both.

2. **Qdrant health check uses `curl` inside the container**, but the Qdrant Docker image may not include `curl`. The official `qdrant/qdrant` image is minimal. Verify that `curl` is available, or use a different health check method (e.g., `wget` or a custom script). Research doc 04-architecture-and-api.md §0.4 recommends `curl -sf http://localhost:6333/healthz` but this assumes curl exists in the container.

3. **The lifespan handler raises `SystemExit` on connection failure.** This is correct behavior (fail-fast), but the error message includes `settings.database_url` which may contain credentials. If logs are shipped to a centralized system, this leaks the database password. Consider sanitizing the URL in error messages.

4. **No Qdrant collection auto-creation at startup.** The lifespan handler checks Qdrant connectivity (`get_collections()`) but doesn't create the `audio_embeddings` collection. Phase 3 (Step 5.3) handles this with `ensure_collection()`, but if someone runs the service before ingestion, Qdrant queries will fail with "collection not found." Consider adding lazy collection creation to the lifespan or documenting the expected bootstrap order.

5. **Contract version strategy is undefined.** Step 1.3 says "Contract version bumped (e.g., v1.1.0)" but the initial contract version isn't specified. Is the existing scaffold at v1.0.0? What's the versioning scheme (semver for APIs)? This should be explicit.

### Edge Cases Not Addressed

1. **Alembic migration conflicts.** If the scaffold already created migrations (the `initial` migration from scaffolding), adding the tracks table must build on that head. The plan mentions this in "Edge Cases & Gotchas" #1 but doesn't provide the specific command to check: `uv run alembic heads` should show exactly one head.

2. **Qdrant port collision.** If another service uses port 6333/6334, Qdrant won't start. The plan uses configurable ports (`QDRANT_HTTP_PORT`, `QDRANT_GRPC_PORT`) which is good, but the health check in `docker-up` hardcodes `localhost:${QDRANT_HTTP_PORT:-6333}` — ensure this works when ports are overridden.

3. **`.env` file loading order.** The plan adds `.env.example` but doesn't specify how the service loads environment variables. FastAPI's `BaseSettings` can use `.env` files via `model_config = SettingsConfigDict(env_file=".env")`, but the Settings class may need this configured. If settings come only from environment variables (not .env files), developers must `source .env` or use `direnv`.

### Feasibility Concerns

1. **28h (3.5 days) for infrastructure seems high.** API contract definition (4h), docker-compose edits (4h), one SQLAlchemy model + migration (8h), a lifespan handler (4h), and Pydantic schemas (4h) — the 8h for the Track model seems generous for a single table with no relationships. Could likely be done in 4h with testing. Total is probably closer to 20-24h (2.5-3 days).

2. **The `IngestError` schema is defined here but the ingest endpoint isn't implemented until Phase 3.** This creates a forward reference. It's not harmful but may confuse developers who see schemas for endpoints that don't exist yet. Consider adding a comment or deferring `ingest.py` schemas to Phase 3.

### Missing Dependencies

1. **`qdrant-client` version not pinned.** Step 4.2 adds `uv add qdrant-client` but doesn't specify a version. The Qdrant server is pinned to v1.16.3 — the client library should be compatible. Pin to a specific range (e.g., `>=1.12,<2.0`).

2. **No mention of `asyncpg` or database driver.** SQLAlchemy async requires an async database driver like `asyncpg`. If the scaffold already installed it, fine — but the plan should verify this dependency exists.

### Recommended Changes

1. **Add a "dev" profile to docker-compose.yml** that includes both postgres and qdrant, so `docker compose --profile dev up -d` starts everything. Or document the profile requirement prominently.
2. **Verify `curl` availability in Qdrant container** for health checks; if not available, switch to `wget -q --spider http://localhost:6333/healthz`.
3. **Sanitize database URL in error messages** (mask password).
4. **Pin `qdrant-client` version** to a compatible range.
5. **Reduce effort estimate for Track model** from 8h to 4-6h.
6. **Add a note about collection bootstrap order** — service requires ingestion before vibe search works.
