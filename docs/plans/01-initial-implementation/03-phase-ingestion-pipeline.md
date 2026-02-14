# Phase 3: Ingestion Pipeline (~4-5 days)

> **Depends on**: Phase 2 (DB schema, Docker services, settings)
> **Blocks**: Phase 4a (Exact ID Lane), Phase 4b (Vibe Lane)
> **Goal**: `make ingest AUDIO_DIR=/path/to/mp3s` populates PostgreSQL + Olaf LMDB + Qdrant

---

## Overview

The ingestion pipeline processes audio files through three parallel paths:
1. **Chromaprint** — content deduplication (ingestion-time only, not for search)
2. **Olaf** — fingerprint indexing into LMDB (for query-time exact search)
3. **CLAP** — embedding generation + Qdrant upsert (for query-time vibe search)

All paths share a common preprocessing pipeline that decodes audio to PCM at two sample rates (16kHz f32le for Olaf/Chromaprint, 48kHz f32le for CLAP). Note: Olaf requires 32-bit float (f32le) PCM, not 16-bit signed integer (s16le). Chromaprint expects s16le but can receive its input via a dtype cast from the f32le stream.

**Corresponds to**: 06-implementation-plan.md Milestone 3

---

## Step 1: Audio Decode Pipeline (~8 hours)

**Reference**: 05-ingestion-pipeline.md §5.1, 00-reconciliation-summary.md §2

### 1.1 Create Decode Module

**File**: `audio-ident-service/app/audio/decode.py` (NEW)

Implement `decode_to_pcm()` with `target_sample_rate` parameter and `decode_dual_rate()` convenience function.

Key requirements:
- Accept any format ffmpeg supports (MP3, WebM/Opus, MP4/AAC, WAV, FLAC, OGG)
- Output raw PCM bytes at the specified sample rate
- Mono channel, f32le format for Olaf and CLAP, s16le for Chromaprint
- Use `asyncio.create_subprocess_exec` for non-blocking ffmpeg calls
- Pipe audio via stdin/stdout (no temp files)
- `decode_dual_rate()` runs both 16kHz and 48kHz decodes in parallel via `asyncio.gather`

**ffmpeg commands** (from 05-ingestion-pipeline.md):
```bash
# 16kHz f32le for Olaf (Olaf requires 32-bit float PCM)
ffmpeg -hide_banner -loglevel error -i pipe:0 -ar 16000 -ac 1 -f f32le -acodec pcm_f32le pipe:1

# 16kHz s16le for Chromaprint (Chromaprint expects 16-bit signed integer)
ffmpeg -hide_banner -loglevel error -i pipe:0 -ar 16000 -ac 1 -f s16le -acodec pcm_s16le pipe:1

# 48kHz f32le for CLAP (CLAP requires 32-bit float PCM at 48kHz)
ffmpeg -hide_banner -loglevel error -i pipe:0 -ar 48000 -ac 1 -f f32le -acodec pcm_f32le pipe:1
```

**Note**: The dual-rate pipeline produces three PCM streams: 16kHz f32le (Olaf), 16kHz s16le (Chromaprint), and 48kHz f32le (CLAP). In practice, `decode_dual_rate()` produces two f32le streams (16kHz and 48kHz). The s16le conversion for Chromaprint can be done from the 16kHz f32le output via numpy dtype cast, avoiding a third ffmpeg invocation.

Add `pcm_duration_seconds()` helper to calculate duration from PCM bytes.

Add `decode_and_validate()` wrapper that enforces min/max duration constraints.

### 1.2 Duration Validation Constants

Per 07-deliverables.md §7.3:
- Ingestion max duration: 1800s (30 minutes)
- Query min duration: 3s
- Query max duration: 30s (truncate, don't reject)

### Acceptance Criteria
- [ ] `decode_to_pcm()` handles MP3, WebM, MP4, WAV input
- [ ] 16kHz output is correct (verify with `ffprobe` on sample output)
- [ ] 48kHz output is correct
- [ ] `decode_dual_rate()` produces both outputs in parallel
- [ ] Error messages are clear when ffmpeg fails (e.g., corrupt file)
- [ ] Decode 100 test MP3s without error

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_audio_decode.py -v
```

---

## Step 2: Metadata Extraction (~4 hours)

**Reference**: 05-ingestion-pipeline.md §5.2

### Create Metadata Module

**File**: `audio-ident-service/app/audio/metadata.py` (NEW)

Implement `extract_metadata()` using mutagen:
- Extract ID3 tags (MP3): TIT2 (title), TPE1 (artist), TALB (album)
- Extract Vorbis comments (WebM/OGG): title, artist, album
- Extract MP4 tags: ©nam (title), ©ART (artist), ©alb (album)
- Compute SHA-256 file hash
- Extract audio properties: duration, sample_rate, channels, bitrate

### Dependencies

```bash
cd audio-ident-service && uv add mutagen
```

### Acceptance Criteria
- [ ] Extracts title, artist, album from MP3 files with ID3 tags
- [ ] Extracts metadata from WebM/OGG files
- [ ] Computes correct SHA-256 hash
- [ ] Handles files with missing tags gracefully (returns None for missing fields)
- [ ] Duration matches ffprobe output within 0.1s

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_audio_metadata.py -v
```

---

## Step 3: Duplicate Detection (~4 hours)

**Reference**: 05-ingestion-pipeline.md §5.3

### 3.1 Create Dedup Module

**File**: `audio-ident-service/app/audio/dedup.py` (NEW)

Two-phase strategy:
1. **Phase 1: File hash** — SHA-256 UNIQUE constraint on `tracks.file_hash_sha256`. Instant, zero false positives.
2. **Phase 2: Content fingerprint** — Chromaprint similarity for same-audio-different-encoding detection. Duration-filtered linear scan (~2K candidates at 20K tracks).

### 3.2 Chromaprint Dependency

```bash
# System dependency
brew install chromaprint  # macOS
# apt install libchromaprint-dev  # Ubuntu

# Python package
cd audio-ident-service && uv add pyacoustid
```

### 3.3 Implementation

- `check_file_duplicate(session, file_hash)` — query by SHA-256
- `generate_chromaprint(pcm_16k)` — generate fingerprint from 16kHz PCM
- `check_content_duplicate(session, fingerprint, duration)` — scan candidates within ±10% duration, compare fingerprint similarity
- Threshold: 0.85 similarity = same content (per 07-deliverables.md §7.3)

### Acceptance Criteria
- [ ] Identical files detected by SHA-256 hash (instant)
- [ ] Same-content different-encoding detected by Chromaprint (>0.85 similarity)
- [ ] Different tracks are not flagged as duplicates
- [ ] Duration pre-filter narrows candidate set appropriately

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_audio_dedup.py -v
```

---

## Step 4: Olaf Fingerprint Indexing (~8 hours)

**Reference**: 02-fingerprinting-survey.md §2.1, 00-reconciliation-summary.md §1

### 4.1 Install Olaf System Dependencies

Based on Phase 1 Prototype 1 results. The compiled Olaf library and CFFI wrapper from prototyping should be productionized here.

### 4.2 Create Fingerprint Module

**File**: `audio-ident-service/app/audio/fingerprint.py` (NEW)

Implement:
- `olaf_index_track(pcm_16k, track_id)` — index a track's fingerprint hashes into Olaf's LMDB
- `olaf_query(pcm_16k)` — query the index (used later in Phase 4a)
- `olaf_delete_track(track_id)` — remove a track from the index

Configuration:
- `OLAF_LMDB_PATH` from settings (default: `./data/olaf_db`)
- Sample rate: 16kHz (Olaf's documented default per JOSS paper)
- Bit depth: 32-bit float (Olaf's expected format)

### 4.3 LMDB Database Setup

Olaf manages its own LMDB database. The Python wrapper needs to know the path:

```python
# In settings.py, already added in Phase 2:
olaf_lmdb_path: str = "./data/olaf_db"
```

Create directory on first use:
```python
Path(settings.olaf_lmdb_path).mkdir(parents=True, exist_ok=True)
```

### Acceptance Criteria
- [ ] 100 tracks indexed into Olaf LMDB without error
- [ ] Index size is reasonable (~4MB for 20K tracks per 07-deliverables.md §7.8)
- [ ] Query on a known 10s clip returns the correct track
- [ ] `olaf_delete_track()` removes the track from index
- [ ] LMDB data directory is created automatically

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_audio_fingerprint.py -v
ls -la data/olaf_db/  # Should contain data.mdb, lock.mdb
```

---

## Step 5: CLAP Embedding Generation + Qdrant (~8 hours)

**Reference**: 03-embeddings-and-qdrant.md §3.3-3.4, 07-deliverables.md §7.4

### 5.1 Install Dependencies

```bash
cd audio-ident-service && uv add laion-clap numpy
# Note: laion-clap pulls in torch as a dependency
```

### 5.2 Create Embedding Module

**File**: `audio-ident-service/app/audio/embedding.py` (NEW)

Implement chunked embedding generation:
- `load_clap_model()` — load LAION CLAP larger_clap_music checkpoint
- `generate_chunked_embeddings(pcm_48k, track_id, metadata, qdrant)` — chunk audio into 10s windows with 5s hop, embed each chunk, upsert to Qdrant

Chunking parameters (from 03-embeddings-and-qdrant.md §3.3):
- Window: 10 seconds (CLAP's native input length)
- Hop: 5 seconds (50% overlap)
- Skip chunks shorter than 1 second
- Pad final chunk to window length if shorter
- Expected: ~47 chunks per 4-minute track

Qdrant upsert payload per chunk (from 00-reconciliation-summary.md §8g):
```python
{
    "track_id": str(track_id),     # UUID string
    "offset_sec": float,            # chunk start time
    "chunk_index": int,             # sequential index
    "duration_sec": float,          # chunk duration
    "artist": str,                  # from metadata
    "title": str,                   # from metadata
    "genre": str,                   # from metadata (if available)
}
```

### 5.3 Qdrant Collection Management

**File**: `audio-ident-service/app/audio/qdrant_setup.py` (NEW)

Implement `ensure_collection()` — lazy collection creation (per 04-architecture-and-api.md):
- Check if collection exists
- If not, create with schema from 03-embeddings-and-qdrant.md §3.4:
  - 512-dim vectors, cosine distance
  - HNSW: m=16, ef_construct=200
  - Scalar quantization: int8, quantile=0.99, always_ram=True
  - Payload indexes on `track_id` (keyword) and `genre` (keyword)

### 5.4 Batch Processing

Upsert in batches of 100 points to avoid oversized requests (per 07-deliverables.md §7.4).

### Acceptance Criteria
- [ ] CLAP model loads successfully
- [ ] 10s audio chunk produces a 512-dim embedding
- [ ] ~47 vectors per 4-minute track in Qdrant
- [ ] Payload fields (track_id, offset_sec, chunk_index, etc.) are correctly stored
- [ ] Nearest-neighbor query on a known track returns chunks from similar tracks
- [ ] Collection has scalar quantization enabled

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_audio_embedding.py -v
curl "http://localhost:6333/collections/audio_embeddings" | python -m json.tool
# Should show vectors_count matching expected chunk count
```

---

## Step 6: Ingestion Pipeline Orchestration + Make Targets (~4 hours)

**Reference**: 05-ingestion-pipeline.md (full flow diagram), 07-deliverables.md §7.4

### 6.1 Create Pipeline Module

**File**: `audio-ident-service/app/ingest/pipeline.py` (NEW)

Implement `ingest_file()` — full orchestration:
1. Read file bytes, compute SHA-256 hash
2. Check file hash duplicate (fast path)
3. Save raw file to `data/raw/{hash_prefix}/{hash}.{ext}` (fan-out by first 2 chars of hash)
4. Extract metadata via mutagen
5. Decode to dual-rate PCM (16kHz + 48kHz in parallel)
6. Run 3 parallel tasks via `asyncio.gather`:
   - Chromaprint fingerprint generation + content dedup check
   - Olaf fingerprint indexing
   - CLAP chunked embedding generation + Qdrant upsert
7. Insert Track record into PostgreSQL

Implement `ingest_directory()`:
- Scan for audio files (`.mp3`, `.wav`, `.webm`, `.ogg`, `.mp4`, `.m4a`, `.flac`)
- Process files sequentially (to manage memory — don't load all 20K into RAM)
- Progress reporting: print `[X/N] Ingesting: filename.mp3` to stdout
- Error recovery: skip failed files, continue with next
- Return `IngestReport` with counts and errors

### 6.2 Create CLI Entry Point

**File**: `audio-ident-service/app/ingest/cli.py` (NEW)

```python
# Usage: uv run python -m app.ingest.cli /path/to/audio/
```

### 6.3 Create Storage Helpers

**File**: `audio-ident-service/app/audio/storage.py` (NEW)

Implement path helpers (from 05-ingestion-pipeline.md §5.4):
- `raw_audio_path(file_hash, extension)` — `data/raw/{prefix}/{hash}.{ext}`

### 6.4 Add Make Targets

**File**: `Makefile` (append)

```makefile
ingest: ## Ingest audio files (usage: make ingest AUDIO_DIR=/path/to/mp3s)
	@test -n "$(AUDIO_DIR)" || (echo "Error: AUDIO_DIR required. Usage: make ingest AUDIO_DIR=/path/to/mp3s" && exit 1)
	cd $(SERVICE_DIR) && uv run python -m app.ingest.cli "$(AUDIO_DIR)"

rebuild-index: ## Drop computed data and rebuild from raw audio (decodes from MP3s on-the-fly)
	@echo "WARNING: This will drop Qdrant collection and Olaf LMDB."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	@echo "Clearing Olaf LMDB index..."
	rm -rf data/olaf_db/*
	@echo "Dropping Qdrant collection..."
	curl -sf -X DELETE "http://localhost:$${QDRANT_HTTP_PORT:-6333}/collections/$${QDRANT_COLLECTION_NAME:-audio_embeddings}" || true
	@echo "Re-ingesting from raw audio..."
	cd $(SERVICE_DIR) && uv run python -m app.ingest.cli "$(AUDIO_STORAGE_ROOT)/raw"
```

### Acceptance Criteria
- [ ] `make ingest AUDIO_DIR=/path/to/mp3s` processes files end-to-end
- [ ] Track records appear in PostgreSQL
- [ ] Fingerprints indexed in Olaf LMDB
- [ ] Embeddings appear in Qdrant (verify via REST API)
- [ ] Duplicate files are skipped (SHA-256 check)
- [ ] Progress reporting shows X/N tracks
- [ ] Failed files are logged and skipped (don't halt pipeline)
- [ ] `make rebuild-index` clears and re-indexes

### Commands to Verify
```bash
# Ingest 10 test files
make ingest AUDIO_DIR=./test-audio/

# Verify PostgreSQL
docker compose exec -T postgres psql -U audio_ident -c "SELECT count(*) FROM tracks"

# Verify Qdrant
curl "http://localhost:6333/collections/audio_embeddings" | python -m json.tool

# Verify Olaf
ls -la data/olaf_db/

# Test rebuild
make rebuild-index
```

---

## Memory Management for Batch Processing

Per 08-devils-advocate-review.md (Missing Risk #6):

- Process files **sequentially** (one at a time) to limit memory
- A 30-minute file produces ~46MB of 48kHz PCM — acceptable for a single file
- Don't accumulate PCM buffers across files — process and discard
- CLAP model stays in memory (~600MB-1GB) throughout the batch
- If running on <8GB RAM, consider setting `OMP_NUM_THREADS=1` to limit PyTorch memory

---

## Error Handling for Corrupt Files

The pipeline must gracefully handle:
- **Corrupt MP3 headers** — ffmpeg returns non-zero exit code → log error, skip file
- **Zero-length files** — metadata extraction returns no duration → skip
- **Files too short** (<3s) — below minimum fingerprint duration → skip
- **Unsupported format** — ffmpeg can't decode → log error, skip
- **Chromaprint failure** — some files may not fingerprint → set `chromaprint_fingerprint=None`, continue
- **CLAP inference error** — model failure → set `embedding_model=None`, continue
- **Qdrant connection error** — fail the file, don't fail the batch (retry later)

---

## File Summary

| File | Purpose |
|------|---------|
| `app/audio/decode.py` | FFmpeg PCM decoding (dual sample-rate) |
| `app/audio/metadata.py` | Mutagen metadata extraction |
| `app/audio/dedup.py` | SHA-256 + Chromaprint duplicate detection |
| `app/audio/fingerprint.py` | Olaf CFFI wrapper (index + query) |
| `app/audio/embedding.py` | CLAP embedding generation (chunked) |
| `app/audio/storage.py` | File path helpers (fan-out by hash prefix) |
| `app/audio/qdrant_setup.py` | Qdrant collection management |
| `app/ingest/pipeline.py` | Full ingestion orchestration |
| `app/ingest/cli.py` | CLI entry point |
| `tests/test_audio_*.py` | Unit tests for each module |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Olaf CFFI compilation fails in production environment | Medium | High | Validated in Phase 1; Docker build pins all deps |
| laion-clap dependency conflicts (numpy/torch) | Medium | Medium | Validated in Phase 1; HuggingFace Transformers as alt |
| Full 20K ingestion takes too long (>24h CPU) | Medium | Low | Can parallelize with GPU for embeddings; fingerprinting is fast |
| Chromaprint content dedup O(n) scaling | Low | Low | Acceptable at 20K; optimize at 100K+ (add indexed hash column) |
| 48kHz PCM memory for long tracks | Low | Medium | All PCM decoded on-the-fly (no disk cache); stream per chunk for very long tracks |

## Rollback Procedures

```bash
# Remove all ingested data (non-destructive to source audio)
rm -rf data/raw/ data/olaf_db/
curl -X DELETE "http://localhost:6333/collections/audio_embeddings" || true
docker compose exec -T postgres psql -U audio_ident -c "TRUNCATE tracks CASCADE"

# Remove new modules
rm -rf audio-ident-service/app/audio/
rm -rf audio-ident-service/app/ingest/
```

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| Audio decode pipeline (dual rate) | 8h |
| Metadata extraction | 4h |
| Duplicate detection | 4h |
| Olaf fingerprint indexing | 8h |
| CLAP embedding + Qdrant | 8h |
| Pipeline orchestration + CLI + Make targets | 4h |
| Unit tests for each module | 4h |
| **Total** | **~40h (5 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM** — This is the highest-risk phase. It depends on Olaf CFFI compilation, CLAP model loading, and three external services (PostgreSQL, Qdrant, Olaf LMDB) all working together. The plan is thorough but underestimates integration complexity.

### Gaps Identified

1. **~~RESOLVED~~ Olaf expects 32-bit float PCM, but the ffmpeg command outputs s16le (16-bit signed integer).** Fixed: Step 1.1 now correctly specifies f32le for Olaf and s16le only for Chromaprint. The ffmpeg commands have been updated to produce three streams: 16kHz f32le (Olaf), 16kHz s16le (Chromaprint), and 48kHz f32le (CLAP). The overview text also clarifies the format requirements.

2. **No transaction strategy for the ingestion pipeline.** Step 6.1 runs PostgreSQL insert, Olaf indexing, and Qdrant upsert as three independent operations via `asyncio.gather`. If PostgreSQL succeeds but Qdrant fails, the system is in an inconsistent state (track exists in PG but has no embeddings). The plan should define: (a) whether partial ingestion is acceptable, (b) how to detect and recover from inconsistent state, (c) whether `make rebuild-index` handles this.

3. **CLAP inference for 20K tracks is not estimated.** At ~47 chunks per track and ~2s per chunk on CPU, that's 20K × 47 × 2s = ~522 hours (~22 days) of continuous CPU inference. This is **orders of magnitude beyond the 5-day phase estimate**. The plan needs to address this explicitly: Will ingestion of 20K tracks happen during development? Is GPU required? What's the actual target library size for v1?

4. **No progress persistence for batch ingestion.** If `make ingest` crashes at track 5000 of 20K, there's no way to resume. The pipeline detects SHA-256 duplicates, so re-running will skip already-ingested tracks, but it will re-decode and re-check every file. A simple "last processed file" checkpoint would save significant time on restarts.

5. **`make rebuild-index` deletes Olaf LMDB with `rm -rf` but doesn't re-ingest from original files.** The command re-ingests from `$(AUDIO_STORAGE_ROOT)/raw`, but raw files are only saved if ingestion completed Step 6.1 #3 (save raw file). If a track failed at that step, its raw file doesn't exist, and rebuild can't recover it. The plan should clarify that rebuilding requires either original source files or that raw file saving is a prerequisite for all subsequent steps.

### Edge Cases Not Addressed

1. **Olaf LMDB locking during concurrent operations.** LMDB allows only one writer at a time. If `make ingest` is running and someone queries via the search API (Phase 4a), the Olaf LMDB read shouldn't block — LMDB supports concurrent readers. But if two ingestion processes run simultaneously, one will get a lock error. The plan should document this single-writer constraint.

2. **CLAP model memory during batch processing.** The plan notes "CLAP model stays in memory (~600MB-1GB) throughout the batch" but doesn't account for PyTorch peak memory during inference. With a 10s chunk at 48kHz, the intermediate tensors may spike to 2-3GB. If running on an 8GB machine with PostgreSQL and Qdrant also in memory, this could cause OOM kills.

3. **Chromaprint `pyacoustid` requires `fpcalc` binary.** The plan installs `libchromaprint-dev` (the library) and `pyacoustid` (Python wrapper), but `pyacoustid` actually calls the `fpcalc` command-line tool, not the library directly. On some platforms, `fpcalc` must be installed separately. Verify: `which fpcalc`.

4. **Files with Unicode characters in paths/filenames.** `glob("*.mp3")` works with Unicode on Python 3, but ffmpeg subprocess calls with Unicode paths may fail on some systems. Use `shlex.quote()` or pass bytes paths.

### Feasibility Concerns

1. **40h (5 days) is likely underestimated.** The Olaf CFFI integration alone (Step 4) was estimated at 8h, but this is a C library integration requiring: understanding Olaf's C API, writing CFFI declarations, handling memory management, testing across platforms. Research doc 08-devils-advocate-review.md identified this as a critical risk. Budget 12-16h.

2. **Step 5 (CLAP + Qdrant) may hit dependency conflicts.** Research flagged `laion-clap` installation issues (00-reconciliation-summary.md §6 open question #3). If `laion-clap` conflicts with other packages (numpy version, torch version), resolving this could add 0.5-1 day.

3. **The pipeline processes files sequentially "to manage memory."** For 20K tracks, sequential processing at ~30s per track (decode + fingerprint + embed) is ~167 hours. Even for a test set of 100 tracks, that's ~50 minutes. The plan should provide expected wall-clock times for different corpus sizes.

### Missing Dependencies

1. **`ffmpeg` version requirement.** The overview pins `ffmpeg >= 5.0` but this phase doesn't verify the installed version. Add a startup check: `ffmpeg -version | head -1`.

2. **`fpcalc` binary** (see Edge Case #3 above).

3. **Disk space for raw audio storage.** If storing a copy of every ingested file under `data/raw/`, 20K tracks at ~5MB each = ~100GB. The plan doesn't mention disk space requirements.

### Recommended Changes

1. **~~RESOLVED~~ Fix the PCM format contradiction**: Step 1.1 ffmpeg commands now correctly output f32le for Olaf and s16le only for Chromaprint.
2. **Add wall-clock time estimates** for ingestion at different corpus sizes (100, 1K, 20K tracks).
3. **Define the v1 development corpus size** — probably 100-500 tracks, not 20K. Full 20K ingestion is a Phase 7/post-v1 activity.
4. **Add a consistency check script** that verifies PG tracks all have corresponding Olaf entries and Qdrant vectors.
5. **Document the LMDB single-writer constraint** explicitly.
6. **Add disk space estimate** to prerequisites (~100GB for 20K tracks with raw copies).
7. **Verify `fpcalc` installation** alongside `pyacoustid`.
