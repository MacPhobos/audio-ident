# Section 5: Ingestion Pipeline & Storage

> **Status**: Research Complete
> **Date**: 2026-02-14
> **Scope**: Audio decoding pipelines, metadata extraction, duplicate detection, storage layout

---

## 5.1 — FFmpeg Audio Decoding Pipelines

All audio processing normalizes to canonical PCM formats before downstream consumption. **Important:** the fingerprinting engine (Olaf) and embedding model (CLAP) require **different sample rates**:

**Dual PCM pipeline:**

| Consumer | Sample Rate | Bit Depth | Channels | Format |
|----------|-------------|-----------|----------|--------|
| **Olaf** (fingerprinting) | **16,000 Hz** (16 kHz) | 32-bit float (f32le) | Mono | Raw PCM |
| **CLAP** (embeddings) | **48,000 Hz** (48 kHz) | 32-bit float (f32le) | Mono | Raw PCM |
| **Chromaprint** (content dedup) | **16,000 Hz** (16 kHz) | 16-bit signed (s16le) | Mono | Raw PCM |

The ingestion pipeline decodes each file twice (or decodes at 48kHz and resamples down to 16kHz). The query pipeline does the same for uploaded audio clips.

### FFmpeg Commands

#### MP3 → PCM (16kHz for Olaf — f32le)

```bash
# To raw PCM f32le for Olaf (32-bit float, pipe-friendly, no WAV header)
ffmpeg -i input.mp3 -ar 16000 -ac 1 -f f32le -acodec pcm_f32le pipe:1

# To raw PCM s16le for Chromaprint (16-bit signed integer)
ffmpeg -i input.mp3 -ar 16000 -ac 1 -f s16le -acodec pcm_s16le pipe:1
```

#### MP3 → PCM (48kHz for CLAP)

```bash
ffmpeg -i input.mp3 -ar 48000 -ac 1 -f f32le -acodec pcm_f32le pipe:1
```

#### WebM/Opus → PCM (browser recording, both rates)

```bash
# 16kHz f32le for fingerprinting (Olaf requires 32-bit float)
ffmpeg -f webm -i pipe:0 -ar 16000 -ac 1 -f f32le -acodec pcm_f32le pipe:1

# 48kHz f32le for embeddings (CLAP)
ffmpeg -f webm -i pipe:0 -ar 48000 -ac 1 -f f32le -acodec pcm_f32le pipe:1
```

#### MP4/AAC → PCM

```bash
# 16kHz f32le for fingerprinting (Olaf requires 32-bit float)
ffmpeg -i input.mp4 -ar 16000 -ac 1 -f f32le -acodec pcm_f32le pipe:1

# 48kHz f32le for embeddings (CLAP)
ffmpeg -i input.mp4 -ar 48000 -ac 1 -f f32le -acodec pcm_f32le pipe:1
```

### Python Wrapper

```python
import asyncio
import subprocess
from pathlib import Path


async def decode_to_pcm(
    input_data: bytes,
    input_format: str | None = None,
    target_sample_rate: int = 16000,
    pcm_format: str = "f32le",
) -> bytes:
    """
    Decode audio bytes to mono PCM using ffmpeg.

    Args:
        input_data: Raw audio file bytes (MP3, WebM, MP4, WAV, etc.)
        input_format: Optional format hint (e.g., "webm", "mp3").
                      If None, ffmpeg auto-detects from content.
        target_sample_rate: Output sample rate in Hz.
                           Use 16000 for Olaf/Chromaprint, 48000 for CLAP.
        pcm_format: Output PCM format. Use "f32le" for Olaf and CLAP
                    (both require 32-bit float), "s16le" for Chromaprint.

    Returns:
        Raw PCM bytes at the specified sample rate and format, mono.

    Raises:
        RuntimeError: If ffmpeg fails to decode.
    """
    codec = f"pcm_{pcm_format}"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]

    if input_format:
        cmd.extend(["-f", input_format])

    cmd.extend([
        "-i", "pipe:0",
        "-ar", str(target_sample_rate),
        "-ac", "1",
        "-f", pcm_format,
        "-acodec", codec,
        "pipe:1",
    ])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=input_data)

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg decode failed: {stderr.decode()}")

    return stdout


def pcm_duration_seconds(
    pcm_data: bytes, sample_rate: int = 16000, bytes_per_sample: int = 4,
) -> float:
    """Calculate duration from raw PCM data.

    Args:
        pcm_data: Raw PCM bytes.
        sample_rate: Sample rate in Hz.
        bytes_per_sample: 4 for f32le (default, used by Olaf/CLAP), 2 for s16le (Chromaprint).
    """
    num_samples = len(pcm_data) // bytes_per_sample
    return num_samples / sample_rate


async def decode_dual_rate(
    input_data: bytes,
    input_format: str | None = None,
) -> tuple[bytes, bytes]:
    """
    Decode audio to BOTH 16kHz (for Olaf/Chromaprint) and 48kHz (for CLAP).
    Runs both ffmpeg processes in parallel for efficiency.

    Returns:
        Tuple of (pcm_16k, pcm_48k) — both as raw f32le mono PCM bytes.
        Chromaprint callers should convert 16kHz f32le to s16le via numpy dtype cast.
    """
    pcm_16k, pcm_48k = await asyncio.gather(
        decode_to_pcm(input_data, input_format, target_sample_rate=16000),
        decode_to_pcm(input_data, input_format, target_sample_rate=48000),
    )
    return pcm_16k, pcm_48k
```

### Duration Validation

After decoding, validate the PCM duration:

```python
MIN_DURATION_SECONDS = 3.0
MAX_DURATION_SECONDS = 30.0

async def decode_and_validate(audio_bytes: bytes, format_hint: str | None = None) -> bytes:
    """Decode audio and validate duration constraints."""
    pcm = await decode_to_pcm(audio_bytes, format_hint)
    duration = pcm_duration_seconds(pcm)

    if duration < MIN_DURATION_SECONDS:
        raise ValueError(
            f"Audio too short: {duration:.1f}s (minimum {MIN_DURATION_SECONDS}s)"
        )
    if duration > MAX_DURATION_SECONDS:
        # Truncate rather than reject — take the first MAX_DURATION_SECONDS
        max_bytes = int(MAX_DURATION_SECONDS * 16000 * 4)  # 4 bytes per sample (f32le)
        pcm = pcm[:max_bytes]

    return pcm
```

---

## 5.2 — Metadata Extraction & PostgreSQL Schema

### Metadata Sources

| Source | Fields | Tool |
|--------|--------|------|
| **ID3 tags** (MP3) | title, artist, album, track_number, genre, year | `mutagen` |
| **Vorbis comments** (WebM/Opus/OGG) | title, artist, album | `mutagen` |
| **MP4 tags** | title, artist, album | `mutagen` |
| **File properties** | file size, content hash (SHA-256), format | Python stdlib |
| **Audio analysis** | duration, sample rate, channels, bit rate | `ffprobe` or `mutagen` |
| **Optional enrichment** | loudness (LUFS), tempo (BPM), key | `essentia` or defer to v2 |

### Metadata Extraction (Python)

```python
import hashlib
import json
from pathlib import Path

import mutagen


def extract_metadata(file_path: Path, raw_bytes: bytes) -> dict:
    """Extract metadata from an audio file."""
    # File-level metadata
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Audio metadata via mutagen
    audio = mutagen.File(file_path)
    tags = {}
    if audio is not None:
        if hasattr(audio, "tags") and audio.tags:
            # Normalize tag keys across formats
            tag_mapping = {
                "TIT2": "title",      # ID3
                "TPE1": "artist",     # ID3
                "TALB": "album",      # ID3
                "title": "title",     # Vorbis/MP4
                "artist": "artist",   # Vorbis/MP4
                "album": "album",     # Vorbis/MP4
                "\xa9nam": "title",   # MP4
                "\xa9ART": "artist",  # MP4
                "\xa9alb": "album",   # MP4
            }
            for tag_key, field_name in tag_mapping.items():
                val = audio.tags.get(tag_key)
                if val:
                    tags[field_name] = str(val[0]) if isinstance(val, list) else str(val)

        info = audio.info
        tags["duration_seconds"] = getattr(info, "length", 0.0)
        tags["sample_rate"] = getattr(info, "sample_rate", None)
        tags["channels"] = getattr(info, "channels", None)
        tags["bitrate"] = getattr(info, "bitrate", None)

    return {
        "file_hash_sha256": file_hash,
        "file_size_bytes": len(raw_bytes),
        **tags,
    }
```

### PostgreSQL Schema — Tracks Table

```python
# app/models/track.py
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
    format: Mapped[str | None] = mapped_column(String(20), nullable=True)  # mp3, webm, mp4, wav

    # File identity
    file_hash_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)  # Relative to storage root

    # Chromaprint (used ONLY for ingestion-time content dedup, not query-time search)
    chromaprint_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    chromaprint_duration: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Olaf fingerprint status (fingerprints stored in Olaf's LMDB index, not PG)
    olaf_indexed: Mapped[bool] = mapped_column(default=False)

    # Embedding reference (vector stored in Qdrant, referenced by track ID)
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

### Full SQL (for reference)

```sql
CREATE TABLE tracks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(500) NOT NULL,
    artist VARCHAR(500),
    album VARCHAR(500),
    duration_seconds DOUBLE PRECISION NOT NULL,
    sample_rate INTEGER,
    channels INTEGER,
    bitrate INTEGER,
    format VARCHAR(20),
    file_hash_sha256 VARCHAR(64) NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    chromaprint_fingerprint TEXT,          -- ingestion-time content dedup only
    chromaprint_duration DOUBLE PRECISION,
    olaf_indexed BOOLEAN NOT NULL DEFAULT false,  -- true once Olaf LMDB index has this track
    embedding_model VARCHAR(100),
    embedding_dim INTEGER,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX ix_tracks_file_hash ON tracks (file_hash_sha256);
CREATE INDEX ix_tracks_artist_title ON tracks (artist, title);
CREATE INDEX ix_tracks_ingested_at ON tracks (ingested_at);
```

---

## 5.3 — Duplicate Detection

### Two-Phase Strategy

| Phase | Method | Detects | Speed |
|-------|--------|---------|-------|
| **Phase 1: File hash** | SHA-256 of raw file bytes | Exact duplicate files (byte-for-byte identical) | Instant |
| **Phase 2: Content fingerprint** | Chromaprint comparison | Same audio, different encoding/bitrate/tags | ~50ms per comparison |

#### Phase 1: File Hash (UNIQUE constraint)

The `file_hash_sha256` column has a UNIQUE index. Attempting to insert a duplicate file will raise an IntegrityError, which the ingestion pipeline catches:

```python
from sqlalchemy.exc import IntegrityError


async def ingest_track(session: AsyncSession, track_data: dict, raw_bytes: bytes) -> Track | None:
    """Insert a track, returning None if it's a duplicate."""
    track = Track(**track_data)
    session.add(track)
    try:
        await session.flush()
        return track
    except IntegrityError:
        await session.rollback()
        # Duplicate file hash — exact same file already ingested
        return None
```

#### Phase 2: Chromaprint Content Deduplication

For content-level deduplication (same song, different encoding), use Chromaprint fingerprint comparison. This happens **after** the file hash check passes (i.e., the file bytes are different):

```python
import acoustid
from chromaprint import decode_fingerprint


def chromaprint_similarity(fp1: str, fp2: str) -> float:
    """
    Compare two Chromaprint fingerprints.
    Returns similarity score between 0 and 1.
    """
    return acoustid.compare_fingerprints(fp1, fp2)


CONTENT_DUPLICATE_THRESHOLD = 0.85  # Fingerprints this similar = same audio content

async def check_content_duplicate(
    session: AsyncSession,
    new_fingerprint: str,
    duration: float,
) -> Track | None:
    """
    Check if any existing track has a similar Chromaprint fingerprint.
    Only compares tracks with similar duration (within 10%).
    """
    # Narrow search by duration to avoid O(N) full scan
    min_dur = duration * 0.9
    max_dur = duration * 1.1

    stmt = select(Track).where(
        Track.chromaprint_fingerprint.isnot(None),
        Track.chromaprint_duration.between(min_dur, max_dur),
    )
    results = await session.execute(stmt)
    candidates = results.scalars().all()

    for candidate in candidates:
        score = chromaprint_similarity(new_fingerprint, candidate.chromaprint_fingerprint)
        if score >= CONTENT_DUPLICATE_THRESHOLD:
            return candidate

    return None
```

**Scaling note**: For 20k tracks, the duration filter narrows candidates enough for a linear scan. At 100k+ tracks, consider storing Chromaprint hashes (32-bit) in a separate indexed column for faster pre-filtering.

---

## 5.4 — Storage Layout

### Directory Structure

```
data/                           # AUDIO_STORAGE_ROOT (configurable via .env)
├── raw/                        # Original uploaded files (immutable archive)
│   ├── ab/                     # First 2 chars of SHA-256 hash (fan-out)
│   │   └── abcdef1234...mp3   # Full hash as filename + original extension
│   └── cd/
│       └── cdef5678...webm
├── olaf_db/                    # Olaf LMDB inverted index (regenerable)
│   ├── data.mdb               # LMDB data file
│   └── lock.mdb               # LMDB lock file
└── fingerprints/               # Chromaprint fingerprint cache for dedup (regenerable)
    ├── ab/
    │   └── abcdef1234...json   # {"fingerprint": "...", "duration": 120.5}
    └── ...
```

**PCM is never cached to disk.** Both 16kHz (Olaf/Chromaprint) and 48kHz (CLAP) PCM are decoded on-the-fly from the original audio file via `ffmpeg pipe:1` and held in memory only. This eliminates hundreds of GB of regenerable intermediate data.

**Fan-out by hash prefix**: Using the first 2 characters of SHA-256 gives 256 subdirectories, keeping each directory to ~80 files at 20k tracks. This avoids filesystem performance degradation from large flat directories.

### Storage Configuration

```python
# app/settings.py additions
class Settings(BaseSettings):
    # Storage
    audio_storage_root: str = "./data"
    store_raw_audio: bool = True  # Raw files are the single source of truth
```

### Storage Path Helpers

```python
from pathlib import Path

from app.settings import settings


def raw_audio_path(file_hash: str, extension: str) -> Path:
    """Get the storage path for a raw audio file."""
    prefix = file_hash[:2]
    return Path(settings.audio_storage_root) / "raw" / prefix / f"{file_hash}.{extension}"


def fingerprint_cache_path(file_hash: str) -> Path:
    """Get the storage path for a cached fingerprint."""
    prefix = file_hash[:2]
    return Path(settings.audio_storage_root) / "fingerprints" / prefix / f"{file_hash}.json"
```

### What's Stored Where

| Data | Store | Regenerable? | Notes |
|------|-------|-------------|-------|
| Original audio files | `data/raw/` (local filesystem) | No — this is the source | Immutable archive |
| Olaf fingerprint index | `data/olaf_db/` (LMDB) | Yes — re-decode + re-index from raw | Used for query-time exact search |
| Chromaprint fingerprints | PostgreSQL `tracks.chromaprint_fingerprint` + `data/fingerprints/` cache | Yes — re-decode + re-fingerprint from raw | Ingestion-time content dedup only |
| Audio embeddings (chunked) | Qdrant collection `audio_embeddings` | Yes — re-embed from raw (48kHz on-the-fly) | ~47 chunks per track, track_id links to PostgreSQL |
| Track metadata | PostgreSQL `tracks` table | Partially — tags from raw files; user edits are not | Primary metadata store |

### `make rebuild-index` — Full Re-indexing

This target drops computed data and regenerates everything from raw audio files:

```makefile
rebuild-index: ## Rebuild all fingerprints and embeddings from raw audio
	@echo "WARNING: This will drop and recreate Qdrant collection, Olaf LMDB, and fingerprint cache."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	@echo "Clearing Chromaprint fingerprint cache..."
	rm -rf data/fingerprints/*
	@echo "Clearing Olaf LMDB index..."
	rm -rf data/olaf_db/*
	@echo "Dropping Qdrant collection..."
	curl -sf -X DELETE "http://localhost:$${QDRANT_HTTP_PORT:-6333}/collections/$${QDRANT_COLLECTION_NAME:-audio_embeddings}" || true
	@echo "Clearing PostgreSQL fingerprint/embedding columns..."
	cd $(SERVICE_DIR) && uv run python -c "
import asyncio
from app.db.session import async_session_factory
from app.models.track import Track
from sqlalchemy import update

async def clear():
    async with async_session_factory() as session:
        await session.execute(
            update(Track).values(
                chromaprint_fingerprint=None,
                chromaprint_duration=None,
                olaf_indexed=False,
                embedding_model=None,
                embedding_dim=None,
            )
        )
        await session.commit()
asyncio.run(clear())
"
	@echo "Re-ingesting all tracks..."
	$(MAKE) ingest-all
```

**Mode-agnostic**: This works the same way for docker and external modes because it only uses `QDRANT_URL` and `DATABASE_URL` — it doesn't care how the services are deployed.

---

## Ingestion Pipeline — Full Flow

```
┌──────────────┐
│  Audio File   │
│ (MP3/WebM/..│
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ SHA-256 Hash  │──── Duplicate? ──── Yes ──→ Skip (return existing track)
└──────┬───────┘
       │ No
       ▼
┌──────────────┐
│ Save Raw File │ → data/raw/{hash_prefix}/{hash}.{ext}
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Extract Tags  │ → mutagen (title, artist, album, duration, etc.)
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ FFmpeg Decode │ → 16kHz mono f32le PCM
│ + Validate   │   (3s ≤ duration ≤ 30min for ingestion)
└──────┬───────┘
       │
       ▼
┌──────────────────────────────────────────────────────┐
│                Parallel Processing                    │
│                                                       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐│
│  │ Chromaprint  │  │ Olaf Index   │  │ CLAP Embed   ││
│  │ (dedup only) │  │ (LMDB store) │  │ (48kHz PCM)  ││
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘│
│         │                 │                  │        │
│         ▼                 ▼                  ▼        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐│
│  │ Content      │  │ Store hashes │  │ Upsert to    ││
│  │ Dedup Check  │  │ in LMDB      │  │ Qdrant       ││
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘│
│         │                 │                  │        │
└─────────┼─────────────────┼──────────────────┼────────┘
          │                  │
          ▼                  ▼
┌──────────────────────────────────────┐
│  INSERT into PostgreSQL tracks table  │
│  (metadata + fingerprint + embedding  │
│   model reference)                    │
└──────────────────────────────────────┘
```

### Ingestion Pipeline Pseudocode

```python
async def ingest_file(file_path: Path, session: AsyncSession, qdrant: AsyncQdrantClient) -> Track | None:
    """
    Ingest a single audio file into the system.
    Returns the Track record or None if duplicate.
    """
    raw_bytes = file_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()

    # Phase 1: File hash dedup
    existing = await session.execute(
        select(Track).where(Track.file_hash_sha256 == file_hash)
    )
    if existing.scalar_one_or_none():
        return None  # Exact duplicate file

    # Extract metadata
    metadata = extract_metadata(file_path, raw_bytes)

    # Save raw file
    ext = file_path.suffix.lstrip(".")
    raw_path = raw_audio_path(file_hash, ext)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_bytes(raw_bytes)

    # Decode to 16kHz PCM in memory (pipe:1, never saved to disk)
    pcm = await decode_to_pcm(raw_bytes)
    duration = pcm_duration_seconds(pcm)

    # Decode to 48kHz for CLAP in memory (separate from 16kHz used above)
    pcm_48k = await decode_to_pcm(raw_bytes, target_sample_rate=48000)

    # Parallel: Chromaprint (dedup) + Olaf (index) + CLAP (embed)
    fingerprint, _, embedding = await asyncio.gather(
        generate_chromaprint(pcm),                 # 16kHz — for content dedup only
        olaf_index_track(pcm, track_id=track_id),  # 16kHz — store hashes in LMDB
        generate_embedding(pcm_48k),               # 48kHz — CLAP requires 48kHz input
    )

    # Phase 2: Content dedup (fingerprint similarity)
    if fingerprint:
        content_dup = await check_content_duplicate(session, fingerprint, duration)
        if content_dup:
            # Same audio content, different file — link instead of duplicate
            # (Policy decision: skip or merge. Default: skip.)
            return None

    # Upsert embedding to Qdrant
    track_id = uuid.uuid4()
    if embedding is not None:
        await ensure_collection(qdrant, settings.qdrant_collection_name, len(embedding))
        await qdrant.upsert(
            collection_name=settings.qdrant_collection_name,
            points=[
                models.PointStruct(
                    id=str(track_id),
                    vector=embedding,
                    payload={"title": metadata.get("title", ""), "artist": metadata.get("artist", "")},
                )
            ],
        )

    # Insert track record
    track = Track(
        id=track_id,
        title=metadata.get("title", file_path.stem),
        artist=metadata.get("artist"),
        album=metadata.get("album"),
        duration_seconds=duration,
        sample_rate=metadata.get("sample_rate"),
        channels=metadata.get("channels"),
        bitrate=metadata.get("bitrate"),
        format=ext,
        file_hash_sha256=file_hash,
        file_size_bytes=len(raw_bytes),
        file_path=str(raw_path.relative_to(settings.audio_storage_root)),
        chromaprint_fingerprint=fingerprint,
        chromaprint_duration=duration,
        olaf_indexed=True,
        embedding_model="clap-laion-music" if embedding else None,
        embedding_dim=len(embedding) if embedding else None,
        ingested_at=datetime.now(tz=timezone.utc),
    )
    session.add(track)
    await session.commit()

    return track
```

---

## Summary

| Component | Technology | Notes |
|-----------|-----------|-------|
| Audio decoding | ffmpeg (subprocess) | All formats → 16kHz mono f32le PCM (Olaf fingerprinting) AND 48kHz mono f32le (CLAP). Chromaprint uses s16le via dtype cast. |
| Metadata extraction | mutagen | ID3, Vorbis, MP4 tags |
| File dedup | SHA-256 hash + UNIQUE constraint | Instant, zero false positives |
| Content dedup | Chromaprint comparison | Duration-filtered linear scan (ingestion-time only) |
| Query-time fingerprinting | Olaf (LMDB inverted index) | Designed for short fragment search, returns track_id + time offset |
| Raw storage | Local filesystem (fan-out by hash prefix) | Immutable archive |
| Metadata | PostgreSQL tracks table | Single source of truth |
| Embeddings | Qdrant collection (cosine distance) | Referenced by track UUID, chunked (~47 per track) |
