# Section 6: Implementation Plan

> **Status**: Research Complete
> **Date**: 2026-02-14
> **Scope**: v1 milestones with effort estimates, v2 roadmap

---

## 6.1 — v1 Milestones (API-Contract-First Workflow)

Total estimated v1 effort: **26-38 developer-days** (includes Milestone 0 validation + Olaf complexity)

### Milestone 0: Validation Prototypes

**Goal**: Validate all critical technology choices and unresolved assumptions BEFORE committing to the v1 stack. This milestone was added based on the devil's advocate review (Section 08) which identified that several core assumptions are unverified.

| Attribute | Value |
|-----------|-------|
| **Effort** | 4 days |
| **Dependencies** | None — this is the very first milestone |
| **Independently testable** | Yes — each prototype produces a pass/fail decision |

**Prototype 1: Olaf Compilation + Accuracy (2 days)**
1. Compile Olaf C library on target platform (macOS + Linux/Docker)
2. Set up CFFI Python wrapper following [Joren Six's blog post](https://0110.be/posts/A_Python_wrapper_for_Olaf_-_Acoustic_fingerprinting_in_Python)
3. Index 100 test tracks from the library
4. Record 10 samples: 5 clean clips (10s from known offsets), 5 mic recordings via browser WebM/Opus (5s each)
5. Run both sets through Olaf query
6. Measure: hit rate, offset accuracy, query latency
7. **Decision gate**: If Olaf compiles and achieves >60% accuracy on mic recordings, proceed with Olaf. If compilation fails or accuracy <50%, fall back to Dejavu (pure Python, Plan B).

**Prototype 2: CLAP CPU Inference Latency (0.5 days)**
1. Install `laion-clap` (document every installation issue)
2. Load the `larger_clap_music` model on the target CPU
3. Measure wall-clock inference time for: 5s clip, 10s clip, 30s clip (all at 48kHz)
4. Measure model cold-start load time
5. **Decision gate**: If inference > 5s for a 5s clip, switch to PANNs Cnn14 or budget for GPU

**Prototype 3: Dual Sample-Rate Pipeline (0.5 days)**
1. Decode a WebM/Opus browser recording to both 16kHz and 48kHz PCM
2. Generate Olaf fingerprint from 16kHz PCM
3. Generate CLAP embedding from 48kHz PCM
4. Verify both paths produce valid outputs
5. Measure total preprocessing latency (decode + dual resample)

**Prototype 4: Qdrant at Target Scale (0.5 days)**
1. Spin up Qdrant (latest stable, v1.16.x)
2. Generate 1M random 512-dim vectors (simulating chunked ingestion: 20K tracks x 47 chunks)
3. Also test with 20K vectors (alternative: one embedding per track)
4. Measure: insert time, query latency at ef=128, recall at various ef values
5. **Decision gate**: Validates whether chunking is worth the complexity at this scale

**Prototype 5: Dependency Resolution (0.5 days)**
1. Create clean virtual environment with `uv`
2. Install ALL proposed dependencies: `laion-clap`, `pyacoustid`, `mutagen`, `python-magic`, `qdrant-client`, `fastapi`, `sqlalchemy[asyncio]`, `asyncpg`
3. Verify no version conflicts (especially numpy 2.x + PyTorch + laion-clap)
4. Run smoke test: load CLAP model, generate a Chromaprint, connect to Qdrant
5. **Decision gate**: If dependency resolution takes >30 minutes of debugging, switch to HuggingFace Transformers integration for CLAP

**Deliverable**: Go/no-go decision document for each technology choice. Updated effort estimates for subsequent milestones based on actual measurements.

---

### Milestone 1: API Contract & Schemas

**Goal**: Define the search endpoint contract and update the frozen API contract document.

| Attribute | Value |
|-----------|-------|
| **Effort** | 1-2 days |
| **Dependencies** | None — this is the starting point |
| **Independently testable** | Yes — contract review is a document review |

**Tasks:**
1. Add `POST /api/v1/search` to `docs/api-contract.md`
2. Add `POST /api/v1/ingest` (admin/CLI endpoint) to contract
3. Add `GET /api/v1/tracks` (list ingested tracks) to contract
4. Add `GET /api/v1/tracks/{id}` (track detail) to contract
5. Define all request/response shapes in the contract (TypeScript interfaces)
6. Copy contract to all 3 locations (service, UI, root)
7. Peer review the contract before proceeding

**Deliverable**: Updated `docs/api-contract.md` v1.1.0 with all search/ingest/tracks endpoints.

---

### Milestone 2: Database Schema + Migrations

**Goal**: Create the PostgreSQL tracks table and Alembic migration.

| Attribute | Value |
|-----------|-------|
| **Effort** | 1-2 days |
| **Dependencies** | Milestone 1 (contract defines field names) |
| **Independently testable** | Yes — `uv run alembic upgrade head` + verify schema |

**Tasks:**
1. Create `app/models/track.py` with the `Track` SQLAlchemy model
2. Import Track into `app/models/__init__.py` (so Alembic detects it)
3. Generate migration: `uv run alembic revision --autogenerate -m "add tracks table"`
4. Review generated migration — verify indexes, constraints, column types
5. Test: `make db-reset` → verify clean schema creation
6. Add `qdrant_url`, `qdrant_api_key`, `qdrant_collection_name`, `audio_storage_root` to `app/settings.py`
7. Add Qdrant client dependency to `pyproject.toml`

**Deliverable**: Working migration that creates the `tracks` table with all indexes.

---

### Milestone 3: Ingestion Pipeline (`make ingest`)

**Goal**: Build the CLI tool that reads audio files from a directory and populates PostgreSQL + Olaf LMDB + Qdrant.

| Attribute | Value |
|-----------|-------|
| **Effort** | 5-7 days |
| **Dependencies** | Milestone 2 (database schema must exist) |
| **Independently testable** | Yes — `make ingest DIR=./test-audio/` and verify DB rows + Qdrant points |

**Tasks:**
1. Add Python dependencies: `pyacoustid`, `mutagen`, `qdrant-client`, `python-magic`, `laion-clap`
2. Compile Olaf C library and set up CFFI wrapper (see Milestone 0 validation)
3. Create `app/audio/decode.py` — ffmpeg PCM decoding wrapper (dual sample-rate: 16kHz for Olaf, 48kHz for CLAP)
4. Create `app/audio/metadata.py` — metadata extraction (mutagen)
5. Create `app/audio/storage.py` — file storage layout helpers
6. Create `app/audio/fingerprint.py` — Olaf CFFI wrapper for indexing tracks into LMDB
7. Create `app/audio/dedup.py` — Chromaprint-based content dedup (ingestion-time only)
8. Create `app/audio/embedding.py` — CLAP embedding generation with chunking (10s window, 5s hop)
9. Create `app/ingest/pipeline.py` — full ingestion orchestration
10. Create `app/ingest/cli.py` — CLI entry point (`uv run python -m app.ingest.cli`)
11. Add `make ingest` target to Makefile
12. Add duplicate detection (file hash + Chromaprint content fingerprint)
13. Write tests for each module independently
14. Test end-to-end with 5-10 sample audio files

**Deliverable**: `make ingest DIR=path/to/audio` populates the database and Qdrant.

---

### Milestone 4: Fingerprint Lane (Olaf)

**Goal**: Implement the exact-match search path using Olaf's LMDB inverted index.

| Attribute | Value |
|-----------|-------|
| **Effort** | 2-3 days |
| **Dependencies** | Milestone 3 (Olaf LMDB index must be populated with ingested tracks) |
| **Independently testable** | Yes — unit test: index a known file, query with a clip, verify match + offset |

**Tasks:**
1. Create `app/search/exact.py` — Olaf query wrapper (CFFI calls to query LMDB index)
2. Implement `run_exact_lane()`: decode query audio (16kHz) -> extract Olaf hashes -> query LMDB -> consensus scoring
3. Implement overlapping sub-window strategy for 5s mic clips (Section 02, 2.3)
4. Map Olaf results (track_id, offset) back to PostgreSQL track metadata
5. Add confidence scoring based on aligned hash count (normalize to 0-1)
6. Write unit tests with known audio pairs (matching + non-matching + offset verification)
7. Benchmark: verify sub-second response for 20k tracks

**Deliverable**: Function that takes PCM audio and returns ranked `ExactMatch` results with time offsets.

---

### Milestone 5: Embedding Lane

**Goal**: Implement the vibe/similarity search path using audio embeddings + Qdrant.

| Attribute | Value |
|-----------|-------|
| **Effort** | 2-3 days |
| **Dependencies** | Milestone 3 (ingested embeddings must exist in Qdrant) |
| **Independently testable** | Yes — unit test: embed a known file, query Qdrant, verify similar tracks returned |

**Tasks:**
1. Create `app/search/vibe.py` — embedding generation + Qdrant query
2. Implement `run_vibe_lane()`: decode query audio → embed → Qdrant nearest neighbors
3. Map Qdrant results back to PostgreSQL track metadata (by UUID)
4. Handle embedding model version mismatch (warn if collection was built with different model)
5. Write tests with curated test set (similar genre pairs, dissimilar pairs)
6. Benchmark: verify response time for 20k vectors in Qdrant

**Deliverable**: Function that takes PCM audio and returns ranked `VibeMatch` results.

---

### Milestone 6: Orchestration (`POST /api/v1/search`)

**Goal**: Wire up both lanes into the search endpoint with parallel execution.

| Attribute | Value |
|-----------|-------|
| **Effort** | 2-3 days |
| **Dependencies** | Milestones 4 + 5 (both lanes must work independently) |
| **Independently testable** | Yes — integration test via httpx against the running service |

**Tasks:**
1. Create `app/routers/search.py` — endpoint implementation
2. Implement orchestration logic (asyncio.gather for parallel lanes)
3. Add file upload validation (size, format, duration)
4. Add PCM decoding step
5. Wire up both lanes with timeout + error isolation
6. Register router in `app/main.py`
7. Add startup health checks (Postgres + Qdrant) in lifespan handler
8. Write integration tests (upload audio file → verify response shape)
9. Test error cases: invalid format, too short, too large, one lane timeout

**Deliverable**: Working `POST /api/v1/search` endpoint accessible at `http://localhost:17010/api/v1/search`.

---

### Milestone 7: Type Generation (`make gen-client`)

**Goal**: Generate frontend TypeScript types from the updated OpenAPI spec.

| Attribute | Value |
|-----------|-------|
| **Effort** | 0.5 day |
| **Dependencies** | Milestone 6 (service must be running with new endpoints) |
| **Independently testable** | Yes — verify `generated.ts` contains `SearchResponse` type |

**Tasks:**
1. Start the service: `make dev`
2. Run `make gen-client` (which runs `pnpm gen:api`)
3. Verify `audio-ident-ui/src/lib/api/generated.ts` contains:
   - `SearchResponse` type with `exact_matches` and `vibe_matches`
   - `SearchMode` enum
   - Correct endpoint path types
4. Update `audio-ident-ui/src/lib/api/client.ts` with a `searchAudio()` function
5. Copy contract to UI: `cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/`

**Deliverable**: Generated TypeScript types matching the search API.

---

### Milestone 8: SvelteKit UI (Mic Recording + File Upload)

**Goal**: Build the search interface with microphone recording and file upload.

| Attribute | Value |
|-----------|-------|
| **Effort** | 3-4 days |
| **Dependencies** | Milestone 7 (generated types must exist) |
| **Independently testable** | Yes — manual testing in browser, Vitest for component logic |

**Tasks:**
1. Create `src/routes/search/+page.svelte` — main search page
2. Create `src/lib/components/AudioRecorder.svelte` — microphone recording component
3. Create `src/lib/components/AudioUploader.svelte` — file upload component
4. Create `src/lib/components/SearchResults.svelte` — results display
5. Implement MediaRecorder API with WebM/Opus codec
6. Add audio level metering (visual feedback that mic is active)
7. Add minimum duration enforcement (3s countdown)
8. Implement file drag-and-drop upload
9. Wire up TanStack Query mutation for the search API call
10. Display results: exact matches (high confidence highlighted) + vibe matches
11. Error handling: permission denied, no mic, too quiet, upload failure
12. Responsive layout with Tailwind CSS
13. Write Vitest tests for non-DOM logic (duration validation, etc.)

**Deliverable**: Working search page at `http://localhost:17000/search` with mic recording and file upload.

---

### Milestone Dependency Graph

```
M0 (Validation Prototypes)
 └─→ M1 (Contract)
      └─→ M2 (DB Schema)
           └─→ M3 (Ingestion)
                ├─→ M4 (Fingerprint Lane — Olaf)
                └─→ M5 (Embedding Lane)
                     └─→ M6 (Orchestration) ←── M4
                          └─→ M7 (Type Gen)
                               └─→ M8 (UI)
```

**Parallelism opportunities:**
- M4 and M5 can run in parallel (both depend on M3, independent of each other)
- M0 must complete before M1 (validates technology choices)
- M8 UI wireframing can start in parallel with M4-M6 (use mock data initially)

---

## 6.2 — v2 Roadmap

Features ordered by estimated impact and complexity:

### v2.1 — Source Separation (High Impact, High Effort)

**Goal**: Isolate vocals, drums, bass, melody before fingerprinting/embedding for better accuracy with noisy recordings.

- **Technology**: Demucs (Meta) or HTDemucs for music source separation
- **Integration point**: Pre-processing step before fingerprint/embedding generation
- **Impact**: Dramatically improves search accuracy for noisy recordings (concert recordings, background music in video)
- **Effort**: 3-5 days
- **Dependencies**: GPU recommended for real-time separation

### v2.2 — Multi-Embedding Strategy

**Goal**: Use multiple embedding models and aggregate results for higher recall.

- Combine CLAP (text-audio aligned) + audio-specific embeddings (e.g., VGGish, MERT)
- Weighted aggregation or late fusion of similarity scores
- Store multiple vectors per track in Qdrant (named vectors)
- **Effort**: 3-4 days
- **Impact**: Better recall for diverse query types (humming vs. recording vs. text description)

### v2.3 — Query Result Caching

**Goal**: Cache fingerprint and embedding results for repeated queries.

- Redis or in-memory cache (LRU) for:
  - Fingerprint → match results (keyed by fingerprint hash)
  - Embedding → nearest neighbors (keyed by embedding hash)
- TTL-based invalidation (1 hour default)
- **Effort**: 1-2 days
- **Impact**: Sub-50ms response for repeat queries

### v2.4 — Filtering Facets

**Goal**: Allow users to filter search results by genre, artist, year, tempo, key.

- Add genre/year/tempo/key columns to tracks table (populated during ingestion or via enrichment)
- Pre-filter in Qdrant using payload filters alongside vector search
- UI: facet checkboxes/dropdowns alongside search
- **Effort**: 2-3 days

### v2.5 — Relevance Feedback Loop

**Goal**: Let users upvote/downvote results to improve future rankings.

- Store feedback in PostgreSQL (user_id, query_hash, track_id, vote)
- Use feedback to re-rank results (simple boosting) or fine-tune embeddings
- **Effort**: 3-5 days (simple boosting: 1 day; fine-tuning: 5+ days)

### v2.6 — Playlist Generation

**Goal**: Given a seed track or query, generate a playlist of similar tracks.

- "More like this" button on any result
- Uses embedding similarity to chain tracks
- Diversity injection to avoid monotonous playlists
- **Effort**: 2-3 days
- **Impact**: Major UX feature, builds on existing vibe search infrastructure

### v2.7 — Text-to-Audio Search

**Goal**: Search by text description ("upbeat jazz with saxophone").

- Requires CLAP or similar text-audio model
- Text → embedding → Qdrant search (same pipeline as audio, different input modality)
- **Effort**: 1-2 days (if CLAP already deployed in v1)
- **Impact**: Entirely new search modality

---

## Summary

| Milestone | Effort | Depends On | Parallel? |
|-----------|--------|-----------|-----------|
| M0: Validation Prototypes | 4d | — | Start immediately |
| M1: API Contract | 1-2d | M0 | After M0 |
| M2: DB Schema | 1-2d | M1 | After M1 |
| M3: Ingestion | 5-7d | M2 | After M2 |
| M4: Fingerprint Lane (Olaf) | 2-3d | M3 | Parallel with M5 |
| M5: Embedding Lane | 2-3d | M3 | Parallel with M4 |
| M6: Orchestration | 2-3d | M4 + M5 | After both lanes |
| M7: Type Gen | 0.5d | M6 | After M6 |
| M8: UI | 3-4d | M7 | After M7 (wireframes earlier) |
| **Total** | **26-38d** | | |

**Critical path**: M0 → M1 → M2 → M3 → M5 (longer lane) → M6 → M7 → M8 = ~19-28 days minimum with one developer. With two developers, M4/M5 parallelism shaves 2-3 days.
