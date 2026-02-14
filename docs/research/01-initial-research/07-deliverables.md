# Section 7: Concrete Deliverables

> **Status**: Research Complete
> **Date**: 2026-02-14
> **Scope**: v1 stack, data model, config defaults, pseudocode, docker-compose, .env.example, risks, sizing, dependencies

---

## 7.1 — Recommended v1 Stack

### Python Backend

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | >=0.115,<1 | Web framework (already installed) |
| `uvicorn[standard]` | >=0.34,<1 | ASGI server (already installed) |
| `pydantic` | >=2,<3 | Data validation (already installed) |
| `pydantic-settings` | >=2,<3 | Configuration (already installed) |
| `sqlalchemy[asyncio]` | >=2,<3 | ORM + async (already installed) |
| `asyncpg` | >=0.30,<1 | PostgreSQL async driver (already installed) |
| `alembic` | >=1.14,<2 | Database migrations (already installed) |
| `python-multipart` | >=0.0.18,<1 | File upload parsing (already installed) |
| `qdrant-client` | >=1.13,<2 | Qdrant vector DB client — use latest compatible with v1.16.x server (**NEW**) |
| `pyacoustid` | >=1.3,<2 | Chromaprint Python bindings — ingestion-time content dedup only (**NEW**) |
| `mutagen` | >=1.47,<2 | Audio metadata extraction (**NEW**) |
| `python-magic` | >=0.4.27,<1 | MIME type detection (**NEW**) |

**Embedding model package** (depends on Section 1-3 research): one of:
| Option | Package | Version |
|--------|---------|---------|
| CLAP (LAION) | `laion-clap` | >=1.1,<2 |
| CLAP (MS) | `msclap` | >=1.0,<2 |
| Essentia TensorFlow | `essentia-tensorflow` | >=2.1b6 |

### System Dependencies

| Dependency | Version | Purpose | Install |
|------------|---------|---------|---------|
| **ffmpeg** | >=5.0 | Audio decoding | `brew install ffmpeg` / `apt install ffmpeg` |
| **chromaprint** (libchromaprint) | >=1.5 | Content dedup fingerprinting (C library) — ingestion-time only | `brew install chromaprint` / `apt install libchromaprint-dev` |
| **Olaf** (C + CFFI) | latest | Query-time acoustic fingerprinting (LMDB inverted index) | Compile from source + CFFI wrapper ([build guide](https://0110.be/posts/A_Python_wrapper_for_Olaf_-_Acoustic_fingerprinting_in_Python)) |
| **libmagic** | >=5.0 | MIME detection (for python-magic) | `brew install libmagic` / `apt install libmagic1` |
| **PostgreSQL** | 16 | Relational database | Docker or native |
| **Qdrant** | >=1.16 | Vector database | Docker or native |

### Frontend (Unchanged)

| Package | Version | Purpose |
|---------|---------|---------|
| `@sveltejs/kit` | ^2.51 | SvelteKit framework (already installed) |
| `svelte` | ^5.51 | Svelte 5 with Runes (already installed) |
| `@tanstack/svelte-query` | ^6.0 | Server state management (already installed) |
| `tailwindcss` | ^4.1 | CSS framework (already installed) |
| `zod` | ^4.3 | Schema validation (already installed) |
| `openapi-typescript` | ^7.13 | Type generation (already installed) |

---

## 7.2 — Data Model

### PostgreSQL Tables

#### `tracks` table

```sql
CREATE TABLE tracks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title                   VARCHAR(500) NOT NULL,
    artist                  VARCHAR(500),
    album                   VARCHAR(500),
    duration_seconds        DOUBLE PRECISION NOT NULL,
    sample_rate             INTEGER,
    channels                INTEGER,
    bitrate                 INTEGER,
    format                  VARCHAR(20),
    file_hash_sha256        VARCHAR(64) NOT NULL,
    file_size_bytes         INTEGER NOT NULL,
    file_path               TEXT NOT NULL,
    chromaprint_fingerprint TEXT,              -- ingestion-time content dedup only
    chromaprint_duration    DOUBLE PRECISION,
    olaf_indexed            BOOLEAN NOT NULL DEFAULT false,  -- true once indexed in Olaf LMDB
    embedding_model         VARCHAR(100),
    embedding_dim           INTEGER,
    ingested_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE UNIQUE INDEX ix_tracks_file_hash ON tracks (file_hash_sha256);
CREATE INDEX ix_tracks_artist_title ON tracks (artist, title);
CREATE INDEX ix_tracks_ingested_at ON tracks (ingested_at);
CREATE INDEX ix_tracks_duration ON tracks (chromaprint_duration);
```

#### `search_logs` table (optional, for analytics)

```sql
CREATE TABLE search_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_hash      VARCHAR(64) NOT NULL,
    mode            VARCHAR(10) NOT NULL,  -- 'exact', 'vibe', 'both'
    query_duration_ms DOUBLE PRECISION NOT NULL,
    exact_count     INTEGER NOT NULL DEFAULT 0,
    vibe_count      INTEGER NOT NULL DEFAULT 0,
    top_exact_id    UUID REFERENCES tracks(id),
    top_exact_score DOUBLE PRECISION,
    top_vibe_id     UUID REFERENCES tracks(id),
    top_vibe_score  DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_search_logs_created ON search_logs (created_at);
```

### Qdrant Collection

```
Collection: audio_embeddings
├── Vector dimension: 512 (CLAP)
├── Distance metric: Cosine
├── HNSW index:
│   ├── m: 16
│   ├── ef_construct: 200
│   └── full_scan_threshold: 10000
├── Quantization: Scalar (int8), always_ram=true
└── Payload schema (per CHUNK, not per track):
    ├── track_id: keyword (UUID string, indexed) — FK to PostgreSQL
    ├── offset_sec: float — start time of this chunk in the track
    ├── chunk_index: integer — sequential chunk number
    ├── duration_sec: float — chunk duration (typically 10s)
    ├── artist: keyword (indexed, for filtering)
    ├── title: text (not indexed, for display)
    └── genre: keyword (indexed, for filtering)

Expected scale: ~47 chunks per track × 20K tracks = ~940K points
```

**Qdrant collection creation (Python):**

```python
from qdrant_client import AsyncQdrantClient, models

async def create_audio_collection(client: AsyncQdrantClient, name: str, dim: int) -> None:
    await client.create_collection(
        collection_name=name,
        vectors_config=models.VectorParams(
            size=dim,
            distance=models.Distance.COSINE,
        ),
        hnsw_config=models.HnswConfigDiff(
            m=16,
            ef_construct=200,
            full_scan_threshold=10000,
        ),
        quantization_config=models.ScalarQuantization(
            scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8,
                quantile=0.99,
                always_ram=True,
            ),
        ),
        optimizers_config=models.OptimizersConfigDiff(
            indexing_threshold=20000,  # Start HNSW indexing after 20k vectors
        ),
    )
    # Create payload indexes for filtering (aligned with Section 03)
    await client.create_payload_index(
        collection_name=name,
        field_name="track_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
    await client.create_payload_index(
        collection_name=name,
        field_name="genre",
        field_schema=models.PayloadSchemaType.KEYWORD,
    )
```

---

## 7.3 — Config Defaults

```python
# Audio processing (dual sample-rate pipeline)
PCM_SAMPLE_RATE_FINGERPRINT = 16_000  # Hz — for Olaf and Chromaprint
PCM_SAMPLE_RATE_EMBEDDING = 48_000    # Hz — for CLAP (required by model)
PCM_CHANNELS = 1              # Mono
PCM_BIT_DEPTH_FINGERPRINT = 32  # f32le for Olaf (32-bit float required)
PCM_BIT_DEPTH_CHROMAPRINT = 16  # s16le for Chromaprint (16-bit signed integer)

# Fingerprinting (Olaf — query-time search)
OLAF_SAMPLE_RATE = 16_000     # Olaf expects 16kHz mono 32-bit float
OLAF_LMDB_PATH = "./data/olaf_db"  # Path to Olaf's LMDB index
MIN_FINGERPRINT_DURATION = 3.0  # Seconds — minimum for reliable fingerprint

# Content dedup (Chromaprint — ingestion-time only)
CHROMAPRINT_ALGORITHM = 2     # Chromaprint algorithm version
FINGERPRINT_CHUNK_SECONDS = 120  # Fingerprint the first 2 minutes of long tracks

# Embedding
EMBEDDING_CHUNK_SECONDS = 10  # Process audio in 10-second chunks for embedding
EMBEDDING_HOP_SECONDS = 5     # 5-second hop between chunks (50% overlap with 10s window)
EMBEDDING_DIM = 512           # CLAP output dimension (or 128 for VGGish)
EMBEDDING_AGGREGATION = "top_k_avg"  # How to aggregate chunk scores: top-K average + diversity bonus (Section 03)

# Search
# Olaf exact match thresholds (based on aligned hash count)
MIN_ALIGNED_HASHES = 8         # Minimum aligned hashes to consider a match (Section 02)
STRONG_MATCH_HASHES = 20       # Strong match threshold for high confidence
EXACT_TRUST_THRESHOLD = 0.85   # Normalized confidence above which UI highlights as "exact match"
VIBE_MATCH_THRESHOLD = 0.60    # Minimum cosine similarity for vibe results
MAX_RESULTS_PER_LANE = 10      # Default max results
EXACT_TIMEOUT_SECONDS = 3.0    # Fingerprint lane timeout (Olaf is fast, <500ms typical)
VIBE_TIMEOUT_SECONDS = 4.0     # Embedding lane timeout (CLAP inference + Qdrant query)
TOTAL_REQUEST_TIMEOUT = 5.0    # Hard cap: preprocessing (1s) + max(exact, vibe) (4s)

# Duplicate detection
FILE_HASH_ALGORITHM = "sha256"
CONTENT_DUPLICATE_THRESHOLD = 0.85  # Fingerprint similarity above this = same content
DURATION_TOLERANCE_RATIO = 0.10     # ±10% duration for content dedup pre-filter

# Storage
STORAGE_FAN_OUT_PREFIX_LEN = 2  # First N chars of hash for directory fan-out

# Upload limits
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
MIN_QUERY_DURATION = 3.0     # Minimum query audio duration (seconds)
MAX_QUERY_DURATION = 30.0    # Maximum query audio duration (seconds)
MAX_INGEST_DURATION = 1800.0  # Maximum ingestion audio duration (30 minutes)

# Qdrant HNSW tuning
HNSW_M = 16                  # Number of edges per node
HNSW_EF_CONSTRUCT = 200      # Construction-time search width
HNSW_EF_SEARCH = 128         # Query-time search width (N)
QDRANT_SEARCH_LIMIT = 50     # Max k for nearest neighbor query
```

---

## 7.4 — Pseudocode

### Ingestion Loop

```python
async def ingest_directory(directory: Path) -> IngestReport:
    """Ingest all audio files from a directory."""
    audio_extensions = {".mp3", ".wav", ".webm", ".ogg", ".mp4", ".m4a", ".flac"}
    files = [f for f in directory.rglob("*") if f.suffix.lower() in audio_extensions]

    report = IngestReport(total=len(files))

    async with async_session_factory() as session:
        qdrant = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key)

        for file_path in files:
            try:
                track = await ingest_file(file_path, session, qdrant)
                if track:
                    report.ingested += 1
                else:
                    report.duplicates += 1
            except Exception as e:
                report.errors.append(IngestError(file=str(file_path), error=str(e)))

        await qdrant.close()

    return report
```

### Embedding Chunking (10s window, 5s hop)

The ingestion pipeline chunks each track into overlapping 10-second windows and stores
**one Qdrant point per chunk** (~47 chunks per 4-minute track, ~940K total points for 20K tracks).
This is required for the ranking algorithm in Section 03 (aggregate_chunk_hits).

```python
import numpy as np
import uuid as uuid_mod

CHUNK_WINDOW_SEC = 10     # CLAP's native input length
CHUNK_HOP_SEC = 5         # 50% overlap
CLAP_SAMPLE_RATE = 48000  # CLAP requires 48kHz

async def generate_chunked_embeddings(
    pcm_48k: bytes,
    track_id: uuid_mod.UUID,
    metadata: dict,
    qdrant: AsyncQdrantClient,
) -> int:
    """
    Chunk audio into 10s windows with 5s hop, embed each chunk via CLAP,
    and upsert all chunks to Qdrant. Returns number of chunks stored.
    """
    # Convert bytes to numpy array (f32le — already 32-bit float from ffmpeg)
    audio = np.frombuffer(pcm_48k, dtype=np.float32)
    total_samples = len(audio)
    window_samples = CHUNK_WINDOW_SEC * CLAP_SAMPLE_RATE
    hop_samples = CHUNK_HOP_SEC * CLAP_SAMPLE_RATE

    points = []
    chunk_index = 0
    offset = 0

    while offset < total_samples:
        chunk = audio[offset:offset + window_samples]

        # Skip chunks shorter than 1 second
        if len(chunk) < CLAP_SAMPLE_RATE:
            break

        # Pad short final chunk to window length
        if len(chunk) < window_samples:
            chunk = np.pad(chunk, (0, window_samples - len(chunk)))

        # Generate CLAP embedding for this chunk
        embedding = clap_model.get_audio_embedding_from_data(
            x=chunk, use_tensor=False
        )  # shape: (1, 512)

        offset_sec = offset / CLAP_SAMPLE_RATE

        point = models.PointStruct(
            id=str(uuid_mod.uuid4()),  # Unique ID per chunk
            vector=embedding[0].tolist(),
            payload={
                "track_id": str(track_id),
                "offset_sec": offset_sec,
                "chunk_index": chunk_index,
                "duration_sec": min(CHUNK_WINDOW_SEC, len(chunk) / CLAP_SAMPLE_RATE),
                "artist": metadata.get("artist", ""),
                "title": metadata.get("title", ""),
                "genre": metadata.get("genre", ""),
            },
        )
        points.append(point)
        chunk_index += 1
        offset += hop_samples

    # Batch upsert all chunks
    if points:
        await ensure_collection(qdrant, settings.qdrant_collection_name, 512)
        # Upsert in batches of 100 to avoid oversized requests
        for i in range(0, len(points), 100):
            await qdrant.upsert(
                collection_name=settings.qdrant_collection_name,
                points=points[i:i + 100],
            )

    return len(points)
```

### Exact Query (Fingerprint Lane — Olaf)

```python
import asyncio
import functools

async def run_exact_lane(pcm_data: bytes, max_results: int) -> list[ExactMatch]:
    """Search by audio fingerprint using Olaf's LMDB inverted index."""
    loop = asyncio.get_event_loop()

    # 1. Extract Olaf fingerprint hashes from query PCM (16kHz mono)
    #    IMPORTANT: CFFI calls hold the GIL — must run in executor to avoid
    #    blocking the asyncio event loop and defeating gather() parallelism.
    query_hashes = await loop.run_in_executor(
        None, functools.partial(olaf_extract_hashes, pcm_data, sample_rate=16000)
    )

    # 2. Query Olaf's LMDB index for matching hashes
    #    Returns list of (track_id, offset_sec, aligned_hash_count) tuples
    raw_matches = await loop.run_in_executor(
        None, functools.partial(olaf_query, query_hashes)
    )

    # 3. Apply consensus scoring (overlapping sub-windows for 5s clips)
    #    See Section 02 §2.3 for sub-window strategy
    scored_matches = consensus_score(raw_matches)

    # 4. Filter by minimum aligned hash threshold
    matches = []
    for match in scored_matches:
        if match.aligned_hashes >= MIN_ALIGNED_HASHES:  # Default: 8
            # Look up track metadata from PostgreSQL
            track = await get_track_by_id(match.track_id)
            if track:
                confidence = min(match.aligned_hashes / STRONG_MATCH_HASHES, 1.0)  # Normalize to 0-1
                matches.append(ExactMatch(
                    track=TrackInfo.model_validate(track),
                    confidence=confidence,
                    offset_seconds=match.offset_sec,
                    aligned_hashes=match.aligned_hashes,
                ))

    # 5. Sort by confidence, take top N
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches[:max_results]
```

**Key differences from Chromaprint approach:**
- No O(n) linear scan against PostgreSQL candidates -- Olaf uses an inverted index (hash -> track_id, offset)
- Query time is O(matches), not O(n_tracks) -- fundamentally faster at scale
- Returns time offset -- Chromaprint cannot do this
- Designed for short fragments (5s mic recordings) -- Chromaprint requires near-complete tracks

### Vibe Query (Embedding Lane) + Chunk Aggregation

```python
from collections import defaultdict
from uuid import UUID

async def run_vibe_lane(pcm_48k: bytes, max_results: int) -> list[VibeMatch]:
    """
    Search by audio embedding (vibe/similarity).
    Uses chunk-level search in Qdrant + track-level aggregation (Section 03 algorithm).
    """
    # 1. Generate query embedding from 48kHz PCM (CLAP's required input rate)
    #    PCM is already f32le from ffmpeg — no conversion needed
    audio = np.frombuffer(pcm_48k, dtype=np.float32)
    embedding = clap_model.get_audio_embedding_from_data(
        x=audio, use_tensor=False
    )  # shape: (1, 512)

    # 2. Query Qdrant for nearest CHUNKS (not tracks)
    #    Fetch more chunks than final results, since multiple chunks may belong to same track
    qdrant = app.state.qdrant
    search_results = await qdrant.query_points(
        collection_name=settings.qdrant_collection_name,
        query=embedding[0].tolist(),
        limit=QDRANT_SEARCH_LIMIT,  # Default: 50 chunks
        with_payload=True,
        search_params=models.SearchParams(hnsw_ef=HNSW_EF_SEARCH),
    )

    # 3. Aggregate chunk hits to track-level scores
    #    Uses the Top-K Average + Diversity Bonus algorithm from Section 03
    chunk_hits = [
        ChunkHit(
            track_id=UUID(point.payload["track_id"]),
            chunk_index=point.payload["chunk_index"],
            offset_sec=point.payload["offset_sec"],
            score=point.score,
        )
        for point in search_results.points
    ]
    track_results = aggregate_chunk_hits(chunk_hits)  # Section 03 algorithm

    # 4. Map track IDs back to PostgreSQL metadata
    track_ids = [r.track_id for r in track_results[:max_results]]
    async with async_session_factory() as session:
        stmt = select(Track).where(Track.id.in_(track_ids))
        results = await session.execute(stmt)
        tracks_by_id = {t.id: t for t in results.scalars().all()}

    # 5. Build response maintaining aggregated ranking order
    matches = []
    for result in track_results[:max_results]:
        track = tracks_by_id.get(result.track_id)
        if track:
            matches.append(VibeMatch(
                track=TrackInfo.model_validate(track),
                similarity=result.score,
                embedding_model=track.embedding_model or "unknown",
            ))

    return matches
```

**Key difference from previous version:** This now queries for individual chunks (not whole-track embeddings),
then aggregates using the Top-K Average + Diversity Bonus algorithm from Section 03. Without chunking,
the ranking algorithm has nothing to aggregate and track-level similarity degrades.

### Browser Audio Preprocessing

```typescript
// src/lib/audio/preprocess.ts

/**
 * Preprocess a recorded audio Blob before uploading.
 * Browser-side validation only — server does full decoding.
 */
export async function preprocessAudioBlob(blob: Blob): Promise<{
  blob: Blob;
  duration: number;
  mimeType: string;
}> {
  // 1. Validate MIME type
  const mimeType = blob.type || 'audio/webm';
  const allowedTypes = ['audio/webm', 'audio/ogg', 'audio/mpeg', 'audio/mp4', 'audio/wav'];
  if (!allowedTypes.some(t => mimeType.startsWith(t))) {
    throw new Error(`Unsupported audio format: ${mimeType}`);
  }

  // 2. Check file size
  const MAX_SIZE = 10 * 1024 * 1024; // 10 MB
  if (blob.size > MAX_SIZE) {
    throw new Error(`File too large: ${(blob.size / 1024 / 1024).toFixed(1)} MB (max 10 MB)`);
  }

  // 3. Measure duration using Web Audio API
  const audioContext = new AudioContext();
  const arrayBuffer = await blob.arrayBuffer();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
  const duration = audioBuffer.duration;
  await audioContext.close();

  // 4. Validate duration
  if (duration < 3) {
    throw new Error(`Recording too short: ${duration.toFixed(1)}s (minimum 3s)`);
  }

  return { blob, duration, mimeType };
}
```

---

## 7.5 — Docker Compose Additions

Updated `docker-compose.yml` with Qdrant and compose profiles:

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
    environment:
      QDRANT__SERVICE__GRPC_PORT: 6334
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:6333/healthz || exit 1"]
      interval: 5s
      timeout: 3s
      retries: 5

volumes:
  pgdata:
  qdrant_data:
```

**Key points:**
- Both services use `profiles:` — they only start when explicitly activated
- `QDRANT_MODE=docker` activates the `qdrant` profile in the Makefile
- `POSTGRES_MODE=docker` activates the `postgres` profile (existing behavior, now conditional)
- Qdrant exposes both HTTP (6333) and gRPC (6334) ports
- Persistent volume `qdrant_data` survives container restarts
- Health check uses the `/healthz` endpoint built into Qdrant

---

## 7.6 — `.env.example`

```bash
# =============================================================================
# audio-ident Configuration
# =============================================================================
# Copy this file to .env and modify as needed.
# All values shown are defaults.

# --- Deployment Modes ---
# "docker" = managed by docker-compose; "external" = pre-existing instance
POSTGRES_MODE=docker
QDRANT_MODE=docker

# --- Service ---
SERVICE_PORT=17010
SERVICE_HOST=0.0.0.0

# --- UI ---
UI_PORT=17000
VITE_API_BASE_URL=http://localhost:17010

# --- CORS ---
CORS_ORIGINS=http://localhost:17000

# --- PostgreSQL ---
# Used by both the app and Alembic migrations.
# For external mode: change to your managed PostgreSQL URL.
DATABASE_URL=postgresql+asyncpg://audio_ident:audio_ident@localhost:5432/audio_ident
POSTGRES_PORT=5432

# --- Qdrant ---
# For external mode: change to your Qdrant Cloud URL + API key.
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=
QDRANT_COLLECTION_NAME=audio_embeddings
QDRANT_HTTP_PORT=6333
QDRANT_GRPC_PORT=6334

# --- Audio Storage ---
AUDIO_STORAGE_ROOT=./data
STORE_RAW_AUDIO=true

# --- JWT (stubs — not enforced in v1) ---
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30

# --- Fingerprinting (Olaf) ---
OLAF_LMDB_PATH=./data/olaf_db

# --- Embedding Model ---
# Name of the audio embedding model to use
EMBEDDING_MODEL=clap-laion-music
# Dimension of the embedding vectors (must match model output)
EMBEDDING_DIM=512
# CLAP requires 48kHz audio input (separate from Olaf's 16kHz)
CLAP_SAMPLE_RATE=48000

# --- Search Tuning ---
EXACT_MATCH_THRESHOLD=0.45
EXACT_TRUST_THRESHOLD=0.85
VIBE_MATCH_THRESHOLD=0.60
MAX_RESULTS_PER_LANE=10
EXACT_TIMEOUT_SECONDS=3.0
VIBE_TIMEOUT_SECONDS=4.0
TOTAL_REQUEST_TIMEOUT=5.0

# --- Upload Limits ---
MAX_UPLOAD_BYTES=10485760
MIN_QUERY_DURATION_SECONDS=3.0
MAX_QUERY_DURATION_SECONDS=30.0

# --- App Metadata ---
APP_NAME=audio-ident-service
APP_VERSION=0.1.0
```

---

## 7.7 — Risks & Mitigations (Top 10)

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | **Embedding model too slow on CPU** | High | High | Start with CLAP's smallest variant. Profile early. Budget for GPU if needed. Degrade gracefully: return only exact matches if embedding times out. |
| 2 | **Olaf CFFI compilation / platform compatibility** | Medium | High | Olaf requires C compilation + CFFI wrapper. Test in Docker and CI early. Dejavu (pure Python) as Plan B. Pin Python version in Docker to avoid CFFI breakage. |
| 3 | **ffmpeg subprocess overhead** | Low | Medium | ffmpeg is fast for single-file decode (~50ms for 30s audio). Only a concern at >100 concurrent requests — at that scale, consider a persistent ffmpeg process or native Python decoder. |
| 4 | **Qdrant collection corruption / data loss** | Low | High | Use persistent Docker volume. Embeddings are regenerable from raw audio (`make rebuild-index`). Back up Qdrant snapshots periodically. |
| 5 | **Browser MediaRecorder codec inconsistency** | Medium | Medium | Use `audio/webm;codecs=opus` which is supported in all major browsers (Chrome, Firefox, Edge, Safari 14.1+). Fall back to `audio/webm` if Opus not available. Server-side ffmpeg handles any codec. |
| 6 | **Content dedup false positives** (different songs with similar fingerprints) | Low | Medium | Use conservative threshold (0.85). Add duration check. In v2, add metadata comparison (different title = not a duplicate even if similar fingerprint). |
| 7 | **Database migration conflicts** between docker and external mode | Low | Medium | Alembic reads `DATABASE_URL` from env — mode-agnostic. Document that external mode users must have the role/database pre-created. |
| 8 | **Storage disk exhaustion** for raw audio files | Medium | Medium | Monitor disk usage. 20k tracks × 5.5 MB avg = ~110 GB. Add size warning to `make ingest`. PCM is never cached — all derived data is regenerable from raw audio. |
| 9 | **Qdrant nearest neighbor recall degradation** as collection grows | Low (at 20k) | Medium | HNSW with m=16, ef_construct=200 provides >95% recall at 20k vectors. Re-evaluate HNSW params at 100k+. |
| 10 | **CORS issues in production deployment** | Medium | Low | CORS is already configurable via `CORS_ORIGINS` env var. Document production CORS setup in deployment guide. |

---

## 7.8 — Sizing for 20k Tracks

### Assumptions

- Average track: 4 minutes, 192kbps MP3 → ~5.5 MB raw file
- PCM is decoded on-the-fly from raw audio (never cached to disk)
- Chromaprint fingerprint: ~4 KB per track (text representation)
- Audio embedding: 512 floats × 4 bytes = ~2 KB per vector × ~47 chunks per track = ~94 KB per track
- Olaf LMDB index: ~200 bytes per track (compact inverted index)

### Storage Estimates

| Component | Per Track | 20k Tracks | Notes |
|-----------|-----------|-----------|-------|
| Raw audio files | ~5.5 MB | **~110 GB** | Immutable archive — single source of truth |
| PostgreSQL (tracks table) | ~2 KB | **~40 MB** | Metadata + dedup fingerprint text |
| Olaf LMDB index | ~0.2 KB | **~4 MB** | Compact inverted index for fingerprints |
| Qdrant vectors (~47 chunks/track) | ~94 KB | **~1.9 GB** | Plus HNSW index overhead |
| Qdrant total (with HNSW + quantization) | ~150 KB | **~3 GB** | Index + quantized vectors in RAM (~0.8 GB) |
| **Total** | | **~113 GB** | PCM decoded on-the-fly from raw audio (never cached to disk) |

### Compute Estimates

| Resource | Minimum | Recommended | Notes |
|----------|---------|------------|-------|
| **CPU** | 2 cores | 4 cores | Ingestion is CPU-heavy (ffmpeg + fingerprint). Search is lighter. |
| **RAM** | 4 GB | 8 GB | Qdrant needs ~0.8-1 GB for ~940K chunked vectors. CLAP model needs ~600 MB-1 GB. Olaf LMDB ~4 MB. |
| **GPU** | None | Optional (NVIDIA, 4+ GB VRAM) | Only for embedding inference. CPU works but ~10x slower. |
| **Disk** | 120 GB SSD | 150 GB SSD | SSD strongly recommended for Qdrant and PostgreSQL I/O. No PCM cache — derived data is ~3 GB. |

### Performance Estimates (20k tracks, CPU-only)

| Operation | Expected Latency | Notes |
|-----------|-----------------|-------|
| Search (exact only) | 100-300 ms | Olaf hash extraction + LMDB index lookup |
| Search (vibe only) | 500-2000 ms | Embedding inference + Qdrant query |
| Search (both, parallel) | 500-2000 ms | Wall-clock = max(exact, vibe) |
| Ingestion (per track) | 3-10 s | Decode + fingerprint + embed + DB insert |
| Full 20k ingestion | 17-56 hours (CPU) | Highly parallel — can run N workers |
| Full 20k ingestion (GPU) | 2-8 hours | Embedding inference is the bottleneck |

---

## 7.9 — Dependency List

### Python Packages (via `uv add`)

```bash
# Core (already in pyproject.toml)
# fastapi, uvicorn[standard], pydantic, pydantic-settings,
# sqlalchemy[asyncio], asyncpg, alembic, python-multipart

# New dependencies for audio-ident features
uv add qdrant-client      # Vector database client (latest compatible with Qdrant v1.16.3)
uv add pyacoustid          # Chromaprint fingerprinting (1.3.x)
uv add mutagen             # Audio metadata extraction (1.47.x)
uv add python-magic        # MIME type detection (0.4.x)
uv add numpy               # Numerical operations for embeddings

# Embedding model (choose one based on Section 1-3 research)
uv add laion-clap          # CLAP audio-text embeddings
# OR
uv add msclap              # Microsoft CLAP
# OR
uv add essentia-tensorflow  # Essentia with TF models

# Dev dependencies (already installed)
# pytest, pytest-asyncio, httpx, ruff, mypy, aiosqlite
```

### System Dependencies

**macOS (Homebrew):**
```bash
brew install ffmpeg chromaprint libmagic
# Olaf: compile from source (see build guide below)
```

**Ubuntu/Debian:**
```bash
apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-dev \
    libchromaprint-tools \
    libmagic1 \
    build-essential \
    libfftw3-dev \
    liblmdb-dev
# Then compile Olaf: see https://github.com/JorenSix/Olaf
```

**Docker (for CI or deployment):**
```dockerfile
# Add to service Dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libchromaprint-dev \
    libchromaprint-tools \
    libmagic1 \
    build-essential \
    libfftw3-dev \
    liblmdb-dev \
    && rm -rf /var/lib/apt/lists/*

# Compile Olaf C library and CFFI wrapper
# (Steps documented in Milestone 0 validation prototype)
```

### Node.js Packages (no new dependencies)

The frontend doesn't need new npm packages — MediaRecorder API is native browser functionality. The existing stack (SvelteKit + TanStack Query + Tailwind) is sufficient for the search UI.

---

## Summary

| Deliverable | Status |
|-------------|--------|
| v1 stack (exact lib names + versions) | Defined above |
| Data model (PostgreSQL + Qdrant) | tracks table + audio_embeddings collection |
| Config defaults | 30+ tunable parameters with sensible defaults |
| Pseudocode (ingestion, exact query, vibe query, browser preprocessing) | Complete |
| Docker-compose additions (Qdrant + profiles) | Complete YAML |
| `.env.example` | Complete template covering both modes |
| Risks & mitigations (top 10) | Documented with likelihood/impact |
| Sizing for 20k tracks | Storage (~110-265 GB), RAM (~2-4 GB), CPU (2-4 cores) |
| Dependency list (Python + system) | Complete with install commands |
