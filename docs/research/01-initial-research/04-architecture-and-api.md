# Section 0.4 + 4: Architecture, Infrastructure Deployment Modes & API Design

> **Status**: Research Complete
> **Date**: 2026-02-14
> **Scope**: Infrastructure dual-mode deployment, API design for search endpoint, orchestration strategy, browser upload handling

---

## 0.4 — Infrastructure Deployment Modes

### Problem Statement

Both PostgreSQL and Qdrant must support two deployment modes — **docker** (managed by docker-compose) and **external** (pre-existing instance, e.g., managed cloud service or locally installed). The mode is selected via `.env` flags:

```env
POSTGRES_MODE=docker    # or "external"
QDRANT_MODE=docker      # or "external"
```

### Decision: Docker Compose Profiles (CHOSEN)

**Compared approaches:**

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Compose profiles** | Native Docker feature, single file, `--profile` flag | Requires Compose v2.1+, slightly less discoverable | **CHOSEN** |
| `--scale=0` | Works, hacky | Semantically wrong — scale=0 means "scale down", not "don't run" | Rejected |
| Separate compose files | Clear separation | Two files to maintain, `-f` flag error-prone | Rejected |
| Conditional YAML anchors | DRY | YAML has no real conditionals; anchors don't help here | Rejected |

**Compose profiles** are the correct mechanism. Services assigned to a profile only start when that profile is explicitly activated. Unassigned services (none in our case — all infra services get a profile) start by default. This maps perfectly to our docker vs external toggle.

References:
- [Docker Docs: Service Profiles](https://docs.docker.com/compose/how-tos/profiles/)
- [Docker Compose Profiles Guide](https://nickjanetakis.com/blog/docker-tip-94-docker-compose-v2-and-profiles-are-the-best-thing-ever)

### docker-compose.yml — Recommended Configuration

```yaml
services:
  postgres:
    image: postgres:16
    profiles: ["postgres"]
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

  qdrant:
    image: qdrant/qdrant:v1.16.3
    profiles: ["qdrant"]
    ports:
      - "${QDRANT_HTTP_PORT:-6333}:6333"
      - "${QDRANT_GRPC_PORT:-6334}:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:6333/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
  qdrant_data:
```

**Profile activation** in the Makefile:

```makefile
# Build the --profile flags dynamically from POSTGRES_MODE / QDRANT_MODE
COMPOSE_PROFILES :=
ifeq ($(POSTGRES_MODE),docker)
  COMPOSE_PROFILES := $(COMPOSE_PROFILES),postgres
endif
ifeq ($(QDRANT_MODE),docker)
  COMPOSE_PROFILES := $(COMPOSE_PROFILES),qdrant
endif
# Strip leading comma
COMPOSE_PROFILES := $(shell echo "$(COMPOSE_PROFILES)" | sed 's/^,//')

dev: ## Start services based on MODE flags
	@if [ -n "$(COMPOSE_PROFILES)" ]; then \
		COMPOSE_PROFILES=$(COMPOSE_PROFILES) docker compose up -d; \
		echo "Waiting for Docker services..."; \
	fi
	# ... rest of dev target
```

Simpler alternative (and recommended for clarity):

```makefile
POSTGRES_MODE ?= docker
QDRANT_MODE ?= docker

docker-up: ## Start Docker-managed infra services
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
```

### Unified Connection Logic

FastAPI reads the **same environment variables** regardless of mode. The application doesn't know or care whether the database is Docker-managed or external — it just connects to the URL.

```python
# app/settings.py additions
class Settings(BaseSettings):
    # Existing...
    database_url: str = "postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None  # For Qdrant Cloud
    qdrant_collection_name: str = "audio_embeddings"

    # Mode flags (used by Makefile only, not by the app)
    postgres_mode: str = "docker"
    qdrant_mode: str = "docker"
```

**Key insight**: The `POSTGRES_MODE` / `QDRANT_MODE` flags are consumed only by the Makefile / docker-compose orchestration layer. The Python application only ever sees `DATABASE_URL` and `QDRANT_URL`. This decouples deployment topology from application logic.

### Startup Health Checks — Fail Fast

Add a startup probe in `app/main.py` that runs during the FastAPI `lifespan` event:

```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient
from sqlalchemy import text

from app.db.engine import engine
from app.settings import settings


async def _check_postgres() -> None:
    """Verify PostgreSQL is reachable. Raises on failure."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_qdrant(client: AsyncQdrantClient) -> None:
    """Verify Qdrant is reachable. Raises on failure."""
    # get_collections() is a lightweight RPC that proves connectivity
    await client.get_collections()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # --- Startup ---
    # 1. Check Postgres
    try:
        await _check_postgres()
    except Exception as exc:
        raise SystemExit(
            f"FATAL: Cannot reach PostgreSQL at {settings.database_url}. "
            f"Error: {exc}"
        ) from exc

    # 2. Check Qdrant
    qdrant = AsyncQdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
    )
    try:
        await _check_qdrant(qdrant)
    except Exception as exc:
        raise SystemExit(
            f"FATAL: Cannot reach Qdrant at {settings.qdrant_url}. "
            f"Error: {exc}"
        ) from exc

    # Store client in app state for reuse
    app.state.qdrant = qdrant

    # 3. Pre-load CLAP embedding model (avoids cold-start latency on first request)
    import logging
    import time as _time
    logger = logging.getLogger(__name__)
    t_model = _time.perf_counter()
    logger.info("Loading CLAP embedding model...")
    # import laion_clap
    # clap_model = laion_clap.CLAP_Module(enable_fusion=False, amodel='HTSAT-large')
    # clap_model.load_ckpt(model_id=3)
    # app.state.clap_model = clap_model
    load_time = _time.perf_counter() - t_model
    logger.info(f"CLAP model loaded in {load_time:.1f}s")
    if load_time > 5:
        logger.warning(f"CLAP model load took {load_time:.1f}s — consider caching model weights in Docker image")

    yield

    # --- Shutdown ---
    await qdrant.close()
    await engine.dispose()
```

### Migration Safety Across Both Modes

Alembic already reads `DATABASE_URL` via `app/settings.py` in `alembic/env.py` (line 16: `config.set_main_option("sqlalchemy.url", settings.database_url)`). This means:

- **Docker mode**: `DATABASE_URL` defaults to `postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident` — works with docker-compose Postgres.
- **External mode**: Set `DATABASE_URL` in `.env` to point to the external Postgres instance.

**No changes needed** — migrations work identically in both modes. The only gotcha: if using external mode, ensure the database and role exist before running `alembic upgrade head`.

### Qdrant Collection Initialization — Lazy (CHOSEN)

**Compared approaches:**

| Approach | Pros | Cons |
|----------|------|------|
| **Lazy init (on first use)** | Zero manual steps, self-healing | Slightly slower first request (< 100ms) |
| Explicit `make init-qdrant` | Clear, intentional | One more step to forget; breaks `make dev` flow |

**Decision: Lazy initialization** during ingestion. The first `upsert` call checks if the collection exists and creates it if missing. This avoids a manual step and keeps `make dev` simple.

```python
async def ensure_collection(client: AsyncQdrantClient, name: str, dim: int) -> None:
    """Create collection if it doesn't exist. Idempotent."""
    collections = await client.get_collections()
    existing = {c.name for c in collections.collections}
    if name not in existing:
        await client.create_collection(
            collection_name=name,
            vectors_config=models.VectorParams(
                size=dim,
                distance=models.Distance.COSINE,
            ),
        )
```

For a full **rebuild-index** scenario (e.g., changed embedding model), provide a `make rebuild-index` target:

```makefile
rebuild-index: ## Drop and recreate Qdrant collection + re-ingest
	@echo "Dropping Qdrant collection..."
	curl -X DELETE "http://localhost:$${QDRANT_HTTP_PORT:-6333}/collections/$${QDRANT_COLLECTION_NAME:-audio_embeddings}"
	@echo "Re-ingesting..."
	$(MAKE) ingest
```

---

## 4 — API Design & Orchestration

### 4.1 — Pydantic v2 Schemas for Search

Location: `audio-ident-service/app/schemas/search.py`

```python
from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


# --- Request ---
# Note: The actual endpoint uses Form() + UploadFile, not a JSON body.
# These models are for the JSON metadata portion and the response.

class SearchMode(StrEnum):
    EXACT = "exact"
    VIBE = "vibe"
    BOTH = "both"


class SearchMetadata(BaseModel):
    """JSON metadata sent alongside the audio file."""
    mode: SearchMode = Field(
        default=SearchMode.BOTH,
        description="Search mode: exact (fingerprint only), vibe (embedding only), or both",
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results per lane",
    )


# --- Response models ---

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
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Fingerprint match confidence (0-1)",
    )
    offset_seconds: float | None = Field(
        default=None,
        description="Estimated time offset in the matched track (seconds)",
    )
    aligned_hashes: int = Field(
        description="Number of aligned fingerprint hashes (Olaf)",
    )


class VibeMatch(BaseModel):
    """Result from the embedding (vibe/similarity) lane."""
    track: TrackInfo
    similarity: float = Field(
        ge=0.0, le=1.0,
        description="Cosine similarity score (0-1)",
    )
    embedding_model: str = Field(
        description="Name of the embedding model used",
    )


class SearchResponse(BaseModel):
    """Combined response from both search lanes."""
    request_id: uuid.UUID = Field(
        description="Unique request identifier for tracing",
    )
    query_duration_ms: float = Field(
        description="Total wall-clock time for the search in milliseconds",
    )
    exact_matches: list[ExactMatch] = Field(
        default_factory=list,
        description="Fingerprint-based exact matches, sorted by confidence descending",
    )
    vibe_matches: list[VibeMatch] = Field(
        default_factory=list,
        description="Embedding-based similarity matches, sorted by similarity descending",
    )
    mode_used: SearchMode = Field(
        description="The search mode that was actually executed",
    )


# --- Error models (extend existing) ---

class SearchErrorDetail(BaseModel):
    """Additional detail for search-specific errors."""
    lane: str | None = Field(
        default=None,
        description="Which lane failed: 'exact', 'vibe', or null for general errors",
    )
    audio_duration_seconds: float | None = Field(
        default=None,
        description="Duration of the uploaded audio clip",
    )
```

**Endpoint signature** (in `app/routers/search.py`):

```python
from fastapi import APIRouter, File, Form, UploadFile

from app.schemas.search import SearchMetadata, SearchMode, SearchResponse

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_audio(
    audio: UploadFile = File(
        ...,
        description="Audio file (WebM/Opus, MP3, MP4/AAC, WAV). Max 10 MB.",
    ),
    mode: SearchMode = Form(default=SearchMode.BOTH),
    max_results: int = Form(default=10, ge=1, le=50),
) -> SearchResponse:
    """
    Search for audio matches using fingerprint (exact) and/or
    embedding (vibe) similarity.

    Accepts multipart/form-data with:
    - `audio`: The audio file to search for
    - `mode`: Search mode ("exact", "vibe", or "both")
    - `max_results`: Max results per lane (1-50, default 10)
    """
    ...
```

**Why Form() + UploadFile, not a single Pydantic model?**

FastAPI does not support Pydantic models with `UploadFile` in multipart requests (as of 2025). The workaround is to use `Form()` for metadata fields alongside `File()` for the upload. This is idiomatic FastAPI for file upload endpoints.

### 4.2 — Orchestration: Parallel with asyncio.gather (CHOSEN)

**Compared approaches:**

| Strategy | Latency | Complexity | Error isolation |
|----------|---------|------------|-----------------|
| **Parallel (asyncio.gather)** | max(fingerprint, embedding) | Medium | Excellent — one lane can fail without killing the other |
| Sequential | fingerprint + embedding | Low | Poor — first failure blocks second |

**Decision: Parallel execution with independent error handling.**

```python
import asyncio
import time
import uuid

from app.schemas.search import (
    ExactMatch,
    SearchMode,
    SearchResponse,
    VibeMatch,
)


# Configuration
# Timeout budget: total p95 target is <5s end-to-end.
# Budget: preprocessing (~1s) + max(exact, vibe) (<=4s) = 5s.
EXACT_TIMEOUT_SECONDS = 3.0   # Olaf LMDB lookup is fast (<500ms typical)
VIBE_TIMEOUT_SECONDS = 4.0    # CLAP inference (~1-3s) + Qdrant query (~200ms)
EXACT_TRUST_THRESHOLD = 0.85  # If exact confidence >= this, skip vibe results display


async def run_exact_lane(pcm_data: bytes, max_results: int) -> list[ExactMatch]:
    """Fingerprint the audio and search the Olaf LMDB inverted index."""
    # IMPORTANT: Olaf CFFI calls (olaf_extract_hashes, olaf_query) are synchronous
    # C code that holds the GIL. They MUST be wrapped in
    # loop.run_in_executor(None, ...) to avoid blocking the asyncio event loop.
    # Without this, asyncio.gather parallelism with the vibe lane is defeated.
    #
    # 1. Generate Olaf fingerprint hashes from PCM (16kHz mono) — via run_in_executor
    # 2. Query Olaf's LMDB index for matching hashes — via run_in_executor
    # 3. Apply time-alignment consensus scoring
    # 4. Return matches ranked by aligned hash count
    ...


async def run_vibe_lane(pcm_data: bytes, max_results: int) -> list[VibeMatch]:
    """Generate embedding and search Qdrant for nearest neighbors."""
    # 1. Generate audio embedding from PCM
    # 2. Query Qdrant for nearest neighbors (cosine similarity)
    # 3. Return ranked results
    ...


async def orchestrate_search(
    pcm_data: bytes,
    mode: SearchMode,
    max_results: int,
) -> SearchResponse:
    request_id = uuid.uuid4()
    t0 = time.perf_counter()

    exact_matches: list[ExactMatch] = []
    vibe_matches: list[VibeMatch] = []

    if mode == SearchMode.EXACT:
        exact_matches = await asyncio.wait_for(
            run_exact_lane(pcm_data, max_results),
            timeout=EXACT_TIMEOUT_SECONDS,
        )
    elif mode == SearchMode.VIBE:
        vibe_matches = await asyncio.wait_for(
            run_vibe_lane(pcm_data, max_results),
            timeout=VIBE_TIMEOUT_SECONDS,
        )
    else:
        # BOTH — run in parallel, tolerate individual failures
        exact_task = asyncio.create_task(
            asyncio.wait_for(run_exact_lane(pcm_data, max_results), timeout=EXACT_TIMEOUT_SECONDS)
        )
        vibe_task = asyncio.create_task(
            asyncio.wait_for(run_vibe_lane(pcm_data, max_results), timeout=VIBE_TIMEOUT_SECONDS)
        )

        # Gather with return_exceptions=True so one failure doesn't kill the other
        results = await asyncio.gather(exact_task, vibe_task, return_exceptions=True)

        if not isinstance(results[0], BaseException):
            exact_matches = results[0]
        if not isinstance(results[1], BaseException):
            vibe_matches = results[1]

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return SearchResponse(
        request_id=request_id,
        query_duration_ms=round(elapsed_ms, 2),
        exact_matches=exact_matches,
        vibe_matches=vibe_matches,
        mode_used=mode,
    )
```

**Trust threshold logic**: When `exact_matches[0].confidence >= 0.85`, the UI can highlight the exact match as "high confidence" and de-emphasize vibe results. This is a **presentation concern** — the API always returns both lanes' results when `mode=both`.

**Timeout defaults** (aligned with p95 < 5s end-to-end target):

| Lane | Default Timeout | Rationale |
|------|----------------|-----------|
| Exact (fingerprint) | 3s | Olaf LMDB index lookup is fast (~200ms typical); 3s provides generous headroom |
| Vibe (embedding) | 4s | CLAP inference (~1-3s CPU) + Qdrant query (~200ms); must fit within 5s total budget minus preprocessing |
| Total request | 5s | Hard cap: preprocessing (~1s) + max(exact, vibe) (4s) = 5s |

### 4.3 — Browser Upload Constraints

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| **Max upload size** | 10 MB | Covers ~5 min of WebM/Opus at 128kbps. More than enough for a search clip |
| **Min duration** | 3 seconds | Fingerprinting needs >=3s for reliable results |
| **Max duration** | 30 seconds | Longer clips don't improve accuracy; waste bandwidth |
| **Accepted content types** | `audio/webm`, `audio/ogg`, `audio/mpeg`, `audio/mp4`, `audio/wav` | WebM/Opus (browser recording), MP3/MP4 (file upload), WAV (raw) |

**Upload strategy: Complete-then-send (CHOSEN)**

| Strategy | Pros | Cons |
|----------|------|------|
| **Complete-then-send** | Simple, works everywhere, easier to validate size/duration upfront | User waits for recording to finish before upload starts |
| Streaming (chunked) | Lower latency for long recordings | Complex, requires server-side reassembly, harder to validate, breaks Content-Length |

For search clips of 3-30 seconds, the latency difference is negligible. **Complete-then-send** is the right choice. The browser records the full clip, creates a Blob, and POSTs it as multipart/form-data.

**Server-side validation** (in the endpoint):

```python
import magic  # python-magic for content type detection

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {
    "audio/webm", "audio/ogg", "audio/mpeg",
    "audio/mp4", "audio/wav", "audio/x-wav",
}

async def validate_upload(audio: UploadFile) -> bytes:
    """Read and validate the uploaded audio file."""
    content = await audio.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "FILE_TOO_LARGE", "message": f"Max size is {MAX_UPLOAD_BYTES // (1024*1024)} MB"}},
        )

    # Verify content type via magic bytes, not just the Content-Type header
    detected_type = magic.from_buffer(content, mime=True)
    if detected_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "UNSUPPORTED_FORMAT", "message": f"Unsupported audio format: {detected_type}"}},
        )

    return content
```

---

## Summary of Key Decisions

| Decision | Choice | Alternatives Considered |
|----------|--------|------------------------|
| Conditional Docker services | Compose profiles | `--scale=0`, separate files |
| App deployment mode coupling | None — app reads URLs only | Mode-aware app logic |
| Qdrant collection init | Lazy (on first use) | Explicit `make init-qdrant` |
| Search endpoint format | Form() + File() multipart | JSON body + base64 |
| Orchestration | asyncio.gather parallel | Sequential |
| Upload strategy | Complete-then-send | Chunked streaming |
| Max upload size | 10 MB | 5 MB, 25 MB |
