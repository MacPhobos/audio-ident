# Phase 5 Dependency Research: Orchestration Prerequisites

> **Date**: 2026-02-15
> **Purpose**: Comprehensive inventory of all existing Phases 1-4 components that Phase 5 (Orchestration) depends on
> **Status**: Research complete

---

## 1. Search Lane Functions

### 1.1 `run_exact_lane`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/search/exact.py` (lines 70-124)

**Signature**:
```python
async def run_exact_lane(
    pcm_16k: bytes,
    max_results: int = 10,
    *,
    session: AsyncSession | None = None,
) -> list[ExactMatch]:
```

**Behavior**:
- Takes 16kHz mono f32le PCM bytes
- For clips <= 5s: uses overlapping sub-window strategy (3 windows)
- For clips > 5s: queries full clip directly via Olaf
- Applies consensus scoring and min-threshold filtering (MIN_ALIGNED_HASHES=8)
- Normalizes confidence to 0.0-1.0 (STRONG_MATCH_HASHES=20)
- Looks up track metadata from PostgreSQL
- Returns empty list on empty input, no matches, or all below threshold

**Key detail for orchestrator**: The `session` parameter is optional. If `None`, the function creates its own session via `async_session_factory()`. The Phase 5 orchestrator CAN pass a shared session but does NOT have to.

### 1.2 `run_vibe_lane`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/search/vibe.py` (lines 36-161)

**Signature**:
```python
async def run_vibe_lane(
    pcm_48k: bytes,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object,
    clap_processor: object,
    session: AsyncSession,
    exact_match_track_id: uuid.UUID | None = None,
) -> list[VibeMatch]:
```

**Behavior**:
- Takes 48kHz mono f32le PCM bytes
- Generates CLAP embedding using `generate_embedding()` (via `run_in_executor` with semaphore)
- Queries Qdrant for nearest chunks
- Aggregates chunk scores to track-level via `aggregate_chunk_hits()`
- Filters by `settings.vibe_match_threshold` (default 0.60)
- Looks up track metadata from PostgreSQL
- Returns empty list if audio is empty, no Qdrant matches, or all below threshold
- Raises `ValueError` if clap_model or clap_processor is None

**Key detail for orchestrator**: Unlike `run_exact_lane`, this function REQUIRES several dependencies to be explicitly passed:
- `qdrant_client` -- from `app.state.qdrant`
- `clap_model` -- from `app.state.clap_model`
- `clap_processor` -- from `app.state.clap_processor`
- `session` -- an AsyncSession (REQUIRED, not optional)
- `exact_match_track_id` -- optional, to exclude the exact-match track from vibe results

**CRITICAL ASYMMETRY**: `run_exact_lane` can create its own session; `run_vibe_lane` requires one. The orchestrator must create a session and pass it to `run_vibe_lane`. It can optionally pass the same session to `run_exact_lane`.

---

## 2. Audio Decode Function

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/audio/decode.py`

### 2.1 `decode_dual_rate` (line 74)

```python
async def decode_dual_rate(audio_data: bytes) -> tuple[bytes, bytes]:
    """Decode to both 16kHz f32le and 48kHz f32le in parallel.
    Returns: Tuple of (pcm_16k_f32le, pcm_48k_f32le).
    Raises: AudioDecodeError if either decode fails.
    """
```

Runs two ffmpeg subprocesses in parallel via `asyncio.gather`.

### 2.2 `pcm_duration_seconds` (line 90)

```python
def pcm_duration_seconds(
    pcm_data: bytes,
    sample_rate: int,
    sample_width: int = 4,
) -> float:
```

Calculates duration from PCM bytes. For f32le at 16kHz: `pcm_duration_seconds(pcm_16k, sample_rate=16000)`.

### 2.3 `decode_and_validate` (line 108)

```python
async def decode_and_validate(
    audio_data: bytes,
    max_duration: float = 1800.0,
    min_duration: float = 0.0,
) -> tuple[bytes, bytes]:
```

Combines `decode_dual_rate` + duration validation. Could be used directly by the search router instead of calling `decode_dual_rate` + `pcm_duration_seconds` separately.

### 2.4 `AudioDecodeError` (line 13)

Exception class raised on decode failure.

---

## 3. Pydantic Schemas

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/schemas/search.py`

```python
class SearchMode(StrEnum):
    EXACT = "exact"
    VIBE = "vibe"
    BOTH = "both"

class TrackInfo(BaseModel):
    id: uuid.UUID
    title: str
    artist: str | None = None
    album: str | None = None
    duration_seconds: float
    ingested_at: datetime

class ExactMatch(BaseModel):
    track: TrackInfo
    confidence: float = Field(ge=0.0, le=1.0)
    offset_seconds: float | None = None
    aligned_hashes: int

class VibeMatch(BaseModel):
    track: TrackInfo
    similarity: float = Field(ge=0.0, le=1.0)
    embedding_model: str

class SearchResponse(BaseModel):
    request_id: uuid.UUID
    query_duration_ms: float
    exact_matches: list[ExactMatch] = Field(default_factory=list)
    vibe_matches: list[VibeMatch] = Field(default_factory=list)
    mode_used: SearchMode
```

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/schemas/errors.py`

```python
class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any = None

class ErrorResponse(BaseModel):
    error: ErrorDetail
```

All schemas are complete and ready for Phase 5. No modifications needed.

---

## 4. Main App (`app/main.py`)

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/main.py`

### 4.1 Lifespan Handler

Current lifespan checks:
1. PostgreSQL connectivity (`SELECT 1`)
2. Qdrant connectivity (`get_collections()`)
3. Stores `app.state.qdrant = qdrant` (AsyncQdrantClient)

**NOT yet in lifespan** (must be added by Phase 5):
- CLAP model loading (`app.state.clap_model`, `app.state.clap_processor`)
- CLAP warm-up inference
- ffmpeg availability check (recommended by devil's advocate review)

### 4.2 Router Registration

```python
application.include_router(health.router)
application.include_router(version.router, prefix="/api/v1")
```

Pattern: `health.router` has no prefix (registered at root). `version.router` is registered with `prefix="/api/v1"`.

### 4.3 Global Exception Handler

Returns `{"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}}` for unhandled exceptions.

### 4.4 App State Usage

Currently only `app.state.qdrant` is set. Phase 5 needs to add:
- `app.state.clap_model`
- `app.state.clap_processor`

---

## 5. Router Patterns

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/routers/health.py`
```python
router = APIRouter(tags=["health"])

@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="ok", version=settings.app_version)
```

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/routers/version.py`
```python
router = APIRouter(tags=["version"])

@router.get("/version", response_model=VersionResponse)
async def get_version() -> VersionResponse:
    ...
```

**Pattern**: Each router module creates `router = APIRouter(tags=[...])`. Prefix is added at registration time in `main.py`, NOT in the router module itself. The search router should follow this pattern:

```python
# app/routers/search.py
router = APIRouter(tags=["search"])

@router.post("/search", response_model=SearchResponse)
async def search_audio(...):
    ...
```

And register with: `application.include_router(search.router, prefix="/api/v1")`

**Important**: The Phase 5 plan (docs/plans) shows `router = APIRouter(prefix="/api/v1", ...)` but the actual codebase pattern delegates the prefix to `main.py`. Follow the codebase convention, not the plan.

---

## 6. Test Patterns

### 6.1 conftest.py

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/tests/conftest.py`

```python
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app

@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
```

Uses `ASGITransport` (not the older `app=app` pattern). Base URL is `"http://test"`.

### 6.2 pytest configuration

From `pyproject.toml`:
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

`asyncio_mode = "auto"` means ALL async test functions are automatically treated as async tests (no need for `@pytest.mark.asyncio` decorator, though existing tests still use it in some places).

### 6.3 Test patterns used in Phase 4

- Tests use `unittest.mock.patch` to mock internal functions
- `AsyncMock` for async functions, `MagicMock` for sync
- Tests create mock Track objects with standard attributes (`id`, `title`, `artist`, `album`, `duration_seconds`, `ingested_at`)
- Helper functions like `_make_pcm()`, `_make_olaf_match()`, `_make_mock_track()` for test data
- Tests are organized in classes grouped by test scenario
- No database fixtures -- all DB access is mocked

### 6.4 Existing test files

```
tests/conftest.py
tests/test_health.py
tests/test_audio_metadata.py
tests/test_audio_storage.py
tests/test_audio_decode.py
tests/test_audio_embedding.py
tests/test_audio_qdrant_setup.py
tests/test_audio_fingerprint.py
tests/test_audio_dedup.py
tests/test_ingest_pipeline.py
tests/test_search_vibe.py
tests/test_search_exact.py
```

Phase 5 tests should go in `tests/test_search_orchestrator.py` (unit tests for orchestrator) and/or `tests/test_search_endpoint.py` (integration tests for the router).

---

## 7. Dependencies (`pyproject.toml`)

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/pyproject.toml`

### Currently installed:

- `python-multipart>=0.0.18,<1` -- ALREADY present (needed for `UploadFile`)
- `qdrant-client>=1.12,<2.0` -- ALREADY present
- `transformers>=5.1.0` -- ALREADY present (for CLAP)
- `torch>=2.10.0` -- ALREADY present
- `numpy>=2.4.2` -- ALREADY present

### NOT yet installed (needed by Phase 5):

- `python-magic` -- NOT in pyproject.toml. Needed for content-type validation via magic bytes.
  - System dependency: `libmagic` (listed in CLAUDE.md as required via `brew install libmagic`)
  - CLAUDE.md already lists `python -c "import magic"` as a verification step

---

## 8. API Contract

**File**: `/Users/mac/workspace/audio-ident/docs/api-contract.md` (v1.1.0)

### POST /api/v1/search

- **Request**: `multipart/form-data`
  - `audio` (file, required): Max 10 MB
  - `mode` (string, optional): `"exact"`, `"vibe"`, or `"both"` (default: `"both"`)
  - `max_results` (integer, optional): 1-50 (default: 10)
- **Supported formats**: MP3, WAV, FLAC, OGG, WebM, MP4/AAC
- **Response**: `SearchResponse` (200 OK)
- **Error codes**: `FILE_TOO_LARGE` (400), `UNSUPPORTED_FORMAT` (400), `AUDIO_TOO_SHORT` (400), `SEARCH_TIMEOUT` (504), `SERVICE_UNAVAILABLE` (503)

**Note on error status codes**: The contract uses 400 for `UNSUPPORTED_FORMAT`, but the Phase 5 plan's devil's advocate review suggests 422. Follow the contract (400) since it is frozen.

---

## 9. `pcm_duration_seconds`

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/audio/decode.py` (line 90)

```python
def pcm_duration_seconds(
    pcm_data: bytes,
    sample_rate: int,
    sample_width: int = 4,
) -> float:
    return len(pcm_data) / (sample_rate * sample_width)
```

There is ALSO a private `_pcm_duration_sec` in `app/search/exact.py` (line 361) that is hardcoded to 16kHz/f32le. The public version in `decode.py` is more flexible and should be used by the orchestrator.

---

## 10. Existing Phase 5 Files

### Already exists:
- `/Users/mac/workspace/audio-ident/docs/plans/01-initial-implementation/05-phase-orchestration.md` -- detailed plan document

### Does NOT exist yet:
- `app/routers/search.py` -- search endpoint router
- `app/search/orchestrator.py` -- orchestration logic
- `tests/test_search_orchestrator.py` or `tests/test_search_endpoint.py` -- tests

---

## 11. Additional Components

### 11.1 Aggregation Module

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/search/aggregation.py`

Provides `ChunkHit` dataclass and `aggregate_chunk_hits()` function. Already used internally by `run_vibe_lane()`. The orchestrator does NOT need to interact with this directly.

### 11.2 Settings

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/settings.py`

Relevant settings for Phase 5:
- `vibe_match_threshold: float = 0.60` (used internally by vibe lane)
- `qdrant_search_limit: int = 50` (used internally by vibe lane)
- `qdrant_url`, `qdrant_api_key`, `qdrant_collection_name` (used by vibe lane)
- `embedding_model: str = "clap-htsat-large"` (used by vibe lane)

No new settings are strictly required for the orchestrator, though timeout constants could be added here.

### 11.3 DB Session

**File**: `/Users/mac/workspace/audio-ident/audio-ident-service/app/db/session.py`

```python
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
```

The `get_db()` dependency can be used with FastAPI's `Depends()` to inject a session into the search endpoint.

---

## 12. Gaps and Inconsistencies

### 12.1 CLAP Model Not Yet Loaded in Lifespan

The current `main.py` lifespan does NOT load the CLAP model. `app.state.clap_model` and `app.state.clap_processor` are not set. The orchestrator will need these for `run_vibe_lane()`. Either:
- (a) Add CLAP loading to the lifespan handler as part of Phase 5
- (b) Have the search router check and return 503 if not loaded

### 12.2 `run_vibe_lane` Signature vs Plan

The plan shows `run_vibe_lane(pcm_48k, max_results)` with just 2 positional args, but the actual function requires `qdrant_client`, `clap_model`, `clap_processor`, and `session` as keyword args. The orchestrator must supply all of these.

### 12.3 `run_exact_lane` Session Handling

The exact lane can work without a session (creates its own), but for efficiency the orchestrator should pass a shared session to avoid creating multiple DB connections.

### 12.4 Error Code Discrepancy

The plan's devil's advocate review suggests 422 for format errors, but the frozen API contract specifies 400. Follow the contract.

### 12.5 Missing `python-magic` Dependency

`python-magic` is not in `pyproject.toml`. Must be added. The system library `libmagic` is already documented in CLAUDE.md.

### 12.6 Router Prefix Convention

The plan shows `router = APIRouter(prefix="/api/v1", ...)` but existing code puts the prefix in `main.py` at registration time. Follow existing convention.

### 12.7 No ffmpeg Startup Check

The lifespan does not verify ffmpeg availability. Recommended but not strictly required (ffmpeg absence will surface as `AudioDecodeError` on first request).

---

## Summary: What Phase 5 Must Create

| File | Type | Purpose |
|------|------|---------|
| `app/routers/search.py` | NEW | Search endpoint (POST /api/v1/search) |
| `app/search/orchestrator.py` | NEW | Parallel lane execution, timeouts, error handling |
| `tests/test_search_orchestrator.py` | NEW | Unit tests for orchestrator |
| `tests/test_search_endpoint.py` | NEW | Integration tests for router |
| `app/main.py` | MODIFY | Add CLAP model loading to lifespan, register search router |
| `pyproject.toml` | MODIFY | Add `python-magic` dependency |

## Summary: What Phase 5 Can Depend On (Already Working)

| Component | File | Status |
|-----------|------|--------|
| `run_exact_lane()` | `app/search/exact.py` | Complete, tested |
| `run_vibe_lane()` | `app/search/vibe.py` | Complete, tested |
| `decode_dual_rate()` | `app/audio/decode.py` | Complete, tested |
| `pcm_duration_seconds()` | `app/audio/decode.py` | Complete, tested |
| `AudioDecodeError` | `app/audio/decode.py` | Complete |
| `SearchMode`, `SearchResponse`, `ExactMatch`, `VibeMatch`, `TrackInfo` | `app/schemas/search.py` | Complete |
| `ErrorDetail`, `ErrorResponse` | `app/schemas/errors.py` | Complete |
| `async_session_factory`, `get_db` | `app/db/session.py` | Complete |
| `settings` | `app/settings.py` | Complete |
| `aggregate_chunk_hits()` | `app/search/aggregation.py` | Complete (internal to vibe lane) |
