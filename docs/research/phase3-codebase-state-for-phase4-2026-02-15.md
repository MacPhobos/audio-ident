# Phase 3 Codebase State: Foundation for Phase 4 (Search Lanes)

> **Date**: 2026-02-15
> **Purpose**: Comprehensive inventory of Phase 3 (Ingestion Pipeline) deliverables to inform Phase 4 (Search Lanes) implementation
> **Classification**: Actionable -- Phase 4 implementation depends on these findings

---

## 1. Directory Structure

```
audio-ident-service/app/
  __init__.py
  main.py               # FastAPI app factory, lifespan, CORS, router mount
  settings.py            # pydantic-settings (all config)
  audio/
    __init__.py          # empty
    decode.py            # FFmpeg subprocess: dual-rate PCM decode
    dedup.py             # SHA-256 + Chromaprint duplicate detection
    embedding.py         # CLAP model loading + chunked embedding generation
    fingerprint.py       # Olaf CLI subprocess wrapper (index/query/delete)
    metadata.py          # mutagen-based metadata extraction
    qdrant_setup.py      # Qdrant collection management + point upsert/delete
    storage.py           # Raw audio file storage with hash-based fan-out
  auth/
    __init__.py
    jwt.py               # JWT stubs
    oauth2.py            # OAuth2 stubs
    password.py          # argon2 password stubs
  db/
    __init__.py          # empty
    engine.py            # AsyncEngine creation from settings
    session.py           # async_session_factory + get_db() dependency
  ingest/
    __init__.py          # empty
    __main__.py          # python -m app.ingest support
    cli.py               # CLI entry point for batch ingestion
    pipeline.py          # Full ingestion orchestration
  models/
    __init__.py          # Base DeclarativeBase + Track import
    track.py             # Track ORM model
  routers/
    __init__.py          # empty
    health.py            # GET /health
    version.py           # GET /api/v1/version
  schemas/
    __init__.py          # empty
    errors.py            # ErrorDetail, ErrorResponse
    health.py            # HealthResponse
    ingest.py            # IngestStatus, IngestResponse, IngestError, IngestReport
    search.py            # SearchMode, TrackInfo, ExactMatch, VibeMatch, SearchResponse
    track.py             # TrackDetail (extends TrackInfo)
    version.py           # VersionResponse
  search/               # DOES NOT EXIST -- must be created for Phase 4
```

### Key Observation: `app/search/` Does NOT Exist

The `search/` directory is referenced in the service-level `CLAUDE.md` project layout but has not been created yet. Phase 4 must create this directory with the search lane modules. The CLAUDE.md lists planned contents: "Exact match, vibe match, orchestrator, aggregation".

---

## 2. Olaf Fingerprint Module (`app/audio/fingerprint.py`)

### Architecture: CLI Subprocess Wrapper (NOT CFFI)

Despite the service CLAUDE.md mentioning "CFFI / GIL Blocking" concerns, the actual implementation wraps the `olaf_c` **command-line binary** via `asyncio.create_subprocess_exec()`. There is NO CFFI wrapper in the codebase. The subprocess approach inherently avoids GIL issues.

### Public API

```python
class OlafError(Exception): ...

@dataclass
class OlafMatch:
    match_count: int
    query_start: float
    query_stop: float
    reference_path: str      # track_id string used during indexing
    reference_id: int        # internal Olaf reference ID
    reference_start: float
    reference_stop: float

async def olaf_index_track(pcm_16k_f32le: bytes, track_id: uuid.UUID) -> bool
async def olaf_query(pcm_16k_f32le: bytes) -> list[OlafMatch]
async def olaf_delete_track(track_id: uuid.UUID) -> bool
```

### Critical Details for Phase 4

- **Input format**: 16kHz mono float32 PCM (`f32le`). MUST be decoded first via `decode_to_pcm()`.
- **Query flow**: Writes PCM to temp file, runs `olaf_c query <tmpfile> query`, parses stdout CSV.
- **Output parsing**: CSV line format: `match_count, query_start, query_stop, ref_path, ref_id, ref_start, ref_stop`. Also supports semicolon-separated fallback.
- **Track identification**: `reference_path` contains the UUID string that was passed during `olaf_index_track()`. This is how you map Olaf results back to Track UUIDs.
- **Error handling**: Returns empty list on query failure (non-fatal). Raises `OlafError` only for missing binary.
- **Environment**: Sets `OLAF_DB` env var pointing to `settings.olaf_lmdb_path`.
- **Temp file cleanup**: Always cleans up in `finally` block.

---

## 3. CLAP Embedding Module (`app/audio/embedding.py`)

### Architecture

Uses HuggingFace Transformers CLAP (`laion/larger_clap_music_and_speech`) to generate 512-dim embeddings from 48kHz audio, chunked into 10s windows with 5s hop.

### Public API

```python
class EmbeddingError(Exception): ...

@dataclass
class AudioChunk:
    embedding: list[float]   # 512-dim vector
    offset_sec: float        # chunk start time
    chunk_index: int         # sequential index
    duration_sec: float      # actual chunk duration

# Constants
CHUNK_WINDOW_SEC = 10.0
CHUNK_HOP_SEC = 5.0
MIN_CHUNK_SEC = 1.0
SAMPLE_RATE = 48000
MODEL_NAME = "laion/larger_clap_music_and_speech"

def load_clap_model() -> tuple[Any, Any]    # Returns (model, processor)
def generate_embedding(audio_48k: np.ndarray, model, processor) -> np.ndarray  # (512,)
def chunk_audio(pcm_48k_f32le: bytes) -> list[tuple[np.ndarray, float, int, float]]
def generate_chunked_embeddings(pcm_48k_f32le: bytes, model, processor) -> list[AudioChunk]
```

### Critical Details for Phase 4

- **Input format**: 48kHz mono float32 PCM (`f32le`). Using 16kHz input produces degraded embeddings.
- **Model loading**: `load_clap_model()` should be called ONCE during startup and stored in `app.state`. The current `main.py` does NOT load CLAP at startup -- this is only done in the ingestion CLI.
- **Chunking**: A 30s track produces 6 chunks (at offsets 0, 5, 10, 15, 20, 25s). Each chunk is zero-padded to 10s.
- **Synchronous inference**: `generate_embedding()` and `generate_chunked_embeddings()` are synchronous (CPU-bound). The ingestion pipeline wraps them in `loop.run_in_executor()`. Phase 4 search must do the same.
- **Query embedding generation**: For search, you generate embeddings from the query clip the same way. The query audio will be shorter (likely 3-10s), producing 1-2 chunks.

---

## 4. Database Models (`app/models/track.py`)

### Track Model

```python
class Track(Base):
    __tablename__ = "tracks"

    id: UUID                          # PK, default uuid4
    title: str                        # max 500, NOT NULL
    artist: str | None                # max 500
    album: str | None                 # max 500
    duration_seconds: float           # NOT NULL
    sample_rate: int | None
    channels: int | None
    bitrate: int | None
    format: str | None                # max 20 (e.g. "mp3")
    file_hash_sha256: str             # UNIQUE, 64 chars, NOT NULL
    file_size_bytes: int              # BigInteger, NOT NULL
    file_path: str                    # Text, NOT NULL
    chromaprint_fingerprint: str | None  # Text
    chromaprint_duration: float | None
    olaf_indexed: bool                # default False
    embedding_model: str | None       # max 100
    embedding_dim: int | None
    ingested_at: datetime             # server_default=now(), with timezone
    updated_at: datetime              # server_default=now(), onupdate=now()

    # Indexes:
    # ix_tracks_file_hash (unique)
    # ix_tracks_artist_title (composite)
    # ix_tracks_ingested_at
```

### Base Class

```python
# app/models/__init__.py
from sqlalchemy.orm import DeclarativeBase
class Base(DeclarativeBase): pass
from app.models.track import Track  # re-exported
```

---

## 5. Schemas (`app/schemas/`)

### Search Schemas (`search.py`) -- Already Defined

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
    confidence: float          # Field(ge=0.0, le=1.0)
    offset_seconds: float | None = None
    aligned_hashes: int

class VibeMatch(BaseModel):
    track: TrackInfo
    similarity: float          # Field(ge=0.0, le=1.0)
    embedding_model: str

class SearchResponse(BaseModel):
    request_id: uuid.UUID
    query_duration_ms: float
    exact_matches: list[ExactMatch] = Field(default_factory=list)
    vibe_matches: list[VibeMatch] = Field(default_factory=list)
    mode_used: SearchMode
```

### Track Detail Schema (`track.py`)

```python
class TrackDetail(TrackInfo):
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    format: str | None = None
    file_hash_sha256: str
    file_size_bytes: int
    olaf_indexed: bool
    embedding_model: str | None = None
    embedding_dim: int | None = None
    updated_at: datetime
```

### Ingest Schemas (`ingest.py`)

```python
class IngestStatus(StrEnum): INGESTED, DUPLICATE, ERROR
class IngestResponse(BaseModel): track_id, title, artist, status
class IngestError(BaseModel): file, error
class IngestReport(BaseModel): total, ingested, duplicates, errors
```

### Error Schemas (`errors.py`)

```python
class ErrorDetail(BaseModel): code, message, details
class ErrorResponse(BaseModel): error: ErrorDetail
```

---

## 6. Qdrant Integration (`app/audio/qdrant_setup.py`)

### Client Factory

```python
def get_qdrant_client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)
```

Note: This creates a **synchronous** `QdrantClient`. The `main.py` lifespan creates an `AsyncQdrantClient` for health checks and stores it on `app.state.qdrant`. Phase 4 search should use `AsyncQdrantClient` for query operations.

### Collection Schema

```
Collection: audio_embeddings (from settings.qdrant_collection_name)
Vectors: 512-dim, cosine distance
HNSW: m=16, ef_construct=200
Quantization: INT8, quantile=0.99, always_ram=True
Payload indexes: track_id (keyword), genre (keyword)
```

### Point Structure (Payload)

```python
{
    "track_id": str,       # UUID as string
    "offset_sec": float,   # chunk start time
    "chunk_index": int,    # sequential index
    "duration_sec": float, # chunk duration
    "artist": str,         # optional (from metadata)
    "title": str,          # optional (from metadata)
    "genre": str,          # optional (from metadata)
}
```

### Upsert API

```python
def upsert_track_embeddings(
    client: QdrantClient,
    track_id: uuid.UUID,
    chunks: list[AudioChunk],
    metadata: dict[str, str] | None = None,
) -> int  # returns count of upserted points
```

### Delete API

```python
def delete_track_embeddings(client: QdrantClient, track_id: uuid.UUID) -> None
```

Uses filter-based deletion on `track_id` field.

### Batch Size

`BATCH_SIZE = 100` -- upserts are batched to avoid oversized requests.

---

## 7. Database Session Management

### Engine (`app/db/engine.py`)

```python
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
)
```

### Session Factory (`app/db/session.py`)

```python
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
```

`get_db()` is designed as a FastAPI dependency (yields an AsyncSession). The `async_session_factory` is also used directly by the ingestion pipeline.

---

## 8. Settings/Config (`app/settings.py`)

```python
class Settings(BaseSettings):
    # Service
    service_port: int = 17010
    service_host: str = "0.0.0.0"

    # CORS
    cors_origins: str = "http://localhost:17000"

    # Database
    database_url: str = "postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident"

    # JWT (stubs)
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30

    # App metadata
    app_name: str = "audio-ident-service"
    app_version: str = "0.1.0"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection_name: str = "audio_embeddings"

    # Audio storage
    audio_storage_root: str = "./data"

    # Olaf
    olaf_lmdb_path: str = "./data/olaf_db"
    olaf_bin_path: str = "olaf_c"

    # Embedding
    embedding_model: str = "clap-htsat-large"
    embedding_dim: int = 512

settings = Settings()  # module-level singleton
```

---

## 9. Existing Tests

```
tests/
  conftest.py                   # httpx AsyncClient fixture
  test_health.py                # GET /health endpoint test
  test_audio_decode.py          # FFmpeg decode tests
  test_audio_dedup.py           # SHA-256 + Chromaprint dedup tests
  test_audio_embedding.py       # CLAP chunk/embed tests (mocked model)
  test_audio_fingerprint.py     # Olaf index/query/delete tests (mocked subprocess)
  test_audio_metadata.py        # mutagen metadata extraction tests
  test_audio_qdrant_setup.py    # Qdrant collection/upsert/delete tests (mocked client)
  test_audio_storage.py         # Raw file storage tests
  test_ingest_pipeline.py       # Full pipeline orchestration tests (all deps mocked)
```

All tests mock external dependencies (Olaf binary, CLAP model, Qdrant client, FFmpeg). No integration tests with real services.

---

## 10. API Contract (`docs/api-contract.md`)

### Version: 1.1.0 (FROZEN)

### Search Endpoint (Defined but NOT Implemented)

```
POST /api/v1/search
Content-Type: multipart/form-data

Fields:
  audio: file (required, max 10 MB)
  mode: string (optional, default "both") -- "exact" | "vibe" | "both"
  max_results: integer (optional, default 10, range 1-50)

Response 200: SearchResponse
Error codes: FILE_TOO_LARGE, UNSUPPORTED_FORMAT, AUDIO_TOO_SHORT,
             SEARCH_TIMEOUT, SERVICE_UNAVAILABLE
```

### Other Defined but Unimplemented Endpoints

- `POST /api/v1/ingest` -- Ingest via HTTP (CLI exists, HTTP endpoint does not)
- `GET /api/v1/tracks` -- List tracks with pagination
- `GET /api/v1/tracks/{id}` -- Track detail

### Implemented Endpoints

- `GET /health` -- Working
- `GET /api/v1/version` -- Working

---

## 11. Current State of `app/main.py` (Lifespan)

The lifespan handler currently:
1. Checks PostgreSQL connectivity
2. Checks Qdrant connectivity
3. Stores `AsyncQdrantClient` on `app.state.qdrant`

It does NOT:
- Load the CLAP model (needed for search)
- Run CLAP warm-up inference
- Create Qdrant collection (done lazily during ingestion)

Phase 4 must add CLAP model loading to the lifespan handler (or a startup event) for search to work.

---

## 12. Gaps Phase 4 Must Fill

1. **Create `app/search/` directory** with modules for:
   - Exact match lane (Olaf query + track lookup)
   - Vibe match lane (CLAP embed query audio + Qdrant similarity search + track lookup)
   - Search orchestrator (parallel execution, result aggregation, timeout handling)

2. **Create `app/routers/search.py`** implementing `POST /api/v1/search` per the frozen API contract.

3. **Add CLAP model to lifespan** in `app/main.py` -- store on `app.state.clap_model` and `app.state.clap_processor`.

4. **Create async Qdrant search function** -- the existing `qdrant_setup.py` uses synchronous `QdrantClient`. Search needs `AsyncQdrantClient` (already available on `app.state.qdrant`).

5. **Map Olaf results to Track records** -- `OlafMatch.reference_path` contains the UUID string. Need to query PostgreSQL to get `TrackInfo` for each matched UUID.

6. **Compute confidence from Olaf match_count** -- Need a normalization function to convert raw `match_count` to 0.0-1.0 confidence score.

7. **Handle query audio decode** -- Accept multipart file upload, decode to both 16kHz and 48kHz PCM for the two search lanes.

8. **Implement `max_results` parameter** -- Limit results from both Olaf and Qdrant.

9. **Implement `mode` parameter** -- Run only exact, only vibe, or both lanes based on request.

10. **Add search router to `app/main.py`** -- Register new router.

11. **Write tests for search modules** -- Following the existing test patterns with mocked dependencies.
