# Research Prompt — Dual‑Mode MP3 Snippet Search (Exact ID + Vibe Similarity)

I have **~20,000 MP3 files** and I want to build a system that supports two modes:

1. **Exact / near‑exact identification**: given a **short snippet** (either **~5s mic recording** or **~10s clip from an MP3**), identify the **same or very similar track** in my library and ideally return the **time offset**.
2. **Vibe similarity search**: given the same snippet types, find **songs that sound similar** (instrumentation, timbre, mood/energy), not necessarily the same recording.

Your job: produce an **engineering research report + recommended v1 design** with options, tradeoffs, and a concrete implementation plan.

---

## 0) Constraints & Assumptions

- Library size: **20k MP3s**
- Snippets:
  - **5 seconds** from **microphone** recordings (noisy room / phone mic coloration possible)
  - **10 seconds** extracted directly from MP3 (clean)
- Target platform: assume **Linux or MacOS** server, can run Docker.
- I prefer a design that is:
  - practical to implement
  - robust enough to be useful on day 1
  - scalable to **hundreds of thousands** of tracks later if needed

Explicitly call out where assumptions matter and how changing them affects design.

### 0.1) Existing Stack (MUST integrate with)

This system is being built into an **existing monorepo** with the following architecture:

| Component | Technology | Port | Notes |
|-----------|-----------|------|-------|
| **Backend** | FastAPI (Python, `uv` for deps) | 17010 | Routes: `/health`, `/api/v1/*` |
| **Frontend** | SvelteKit (TypeScript, `pnpm`) | 17000 | Browser-based mic recording |
| **Database** | PostgreSQL | 5432 | Docker OR existing install (see §0.4) |
| **Vector DB** | **Qdrant** (decided) | 6333/6334 | Docker OR existing install (see §0.4) |
| **Orchestration** | Makefile, Docker Compose | — | All dev commands via `make` targets |

**Integration requirements:**
- New endpoints MUST follow the existing API contract workflow (see `docs/api-contract.md`)
- The search endpoint should be versioned: `POST /api/v1/search`
- Pydantic schemas for request/response models in `audio-ident-service/app/schemas/`
- Router implementation in `audio-ident-service/app/routers/`
- Frontend types are **generated from OpenAPI** — never hand-written
- PostgreSQL is already available for metadata storage; propose schema extensions
- Qdrant should be added as a new service in the existing `docker-compose.yml`
- Both PostgreSQL and Qdrant must support **dual deployment modes** (see §0.4)

### 0.2) Performance Requirements

| Metric | Target | Notes |
|--------|--------|-------|
| Query latency (end-to-end) | **< 5 seconds** | From audio upload to results displayed |
| Ingestion throughput | Best effort | Batch processing acceptable |
| Concurrent queries | Low (1–5) | Single-user / small team initially |
| Compute | **Single consumer GPU** (e.g., RTX 3060/4070) with **CPU fallback** | Must work without GPU, just slower |

### 0.3) Mic Recording Input Format

Mic recordings will originate from the **SvelteKit browser UI** using the **WebAudio API / MediaRecorder API**:

- **Expected codec**: WebM/Opus (Chrome, Firefox) or MP4/AAC (Safari)
- **Sample rate**: Typically 48kHz (browser default) but may vary
- **Channels**: Mono (request mono from getUserMedia, but handle stereo gracefully)
- **Quality**: Variable — depends on device mic, browser audio processing, ambient noise

**The research must address:**
- Decoding WebM/Opus and MP4/AAC server-side (ffmpeg pipeline)
- Whether browser-side preprocessing (gain normalization, noise gate) improves results
- Latency of audio upload vs. streaming considerations
- Minimum viable snippet length if user stops recording early (e.g., 3s instead of 5s)

### 0.4) Infrastructure Deployment Modes (Docker vs. Existing Installs)

Both **PostgreSQL** and **Qdrant** must support two deployment modes, selectable per-service via environment variables in `.env` (with defaults in `.env.example`):

| Mode | When to use | How it works |
|------|-------------|--------------|
| **Docker-managed** (default) | Local dev, CI, fresh setups | `docker-compose up` starts the service; lifecycle managed by compose |
| **External / existing install** | Production, shared infra, developers who already run Postgres/Qdrant locally | App connects to a user-provided host:port; docker-compose skips that service |

**`.env.example` must define these flags (at minimum):**

```env
# ── PostgreSQL ──────────────────────────────────────────
POSTGRES_MODE=docker              # "docker" | "external"
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=audio_ident
POSTGRES_USER=audio_ident
POSTGRES_PASSWORD=audio_ident

# ── Qdrant ──────────────────────────────────────────────
QDRANT_MODE=docker                # "docker" | "external"
QDRANT_HOST=localhost
QDRANT_REST_PORT=6333
QDRANT_GRPC_PORT=6334
QDRANT_API_KEY=                   # optional, for secured external instances
```

**Design requirements for the research to address:**

1. **docker-compose conditional services** — Propose a pattern (compose profiles, `--scale=0`, or separate compose files) so that `make dev` only starts Docker containers for services where `*_MODE=docker`. Document tradeoffs of each approach.
2. **Application connection logic** — The FastAPI service must read the same env vars regardless of mode. When `POSTGRES_MODE=docker`, the app connects to `POSTGRES_HOST:POSTGRES_PORT` (which happens to be the compose container). When `POSTGRES_MODE=external`, it connects to whatever the user configured. Same pattern for Qdrant.
3. **Health check / startup validation** — On app startup, verify connectivity to both PostgreSQL and Qdrant. Fail fast with a clear error message if a required service is unreachable (e.g., "Qdrant not reachable at localhost:6333 — is QDRANT_MODE correct?").
4. **`make dev` behavior** — The Makefile must respect the mode flags. Propose how `make dev` starts only the Docker services that are set to `docker` mode, then starts the FastAPI and SvelteKit processes.
5. **Migration safety** — Database migrations (Alembic or equivalent) must work identically against Docker-managed and external PostgreSQL. The research should confirm this is straightforward or flag any gotchas.
6. **Qdrant collection initialization** — Whether Qdrant collections are created lazily on first use or via an explicit `make init-qdrant` step, it must work for both modes.

---

## 1) Define the Two Problems Precisely

Explain why:
- **Exact ID** (snippet → same recording / same track) is best solved with **acoustic fingerprinting** (landmark hashing / inverted index) and not embeddings.
- **Vibe similarity** is best solved with **audio embeddings + vector search** and not fingerprinting.

Include a short "failure modes" section for each approach if used for the wrong job.

### 1.1) Hybrid Approach Consideration

Evaluate whether there's value in a **hybrid approach** where:
- Fingerprint confidence scores could weight or filter vibe results
- Embedding similarity could break ties in ambiguous fingerprint matches
- A shared preprocessing pipeline reduces duplication

If the hybrid adds meaningful value, include it in the v1 design. If not, explain why keeping them fully independent is better.

---

## 2) Exact ID Lane — Acoustic Fingerprinting for Short Queries

### 2.1 Survey candidate solutions (detailed)
Research and compare at least these:
- **Olaf** (Overly Lightweight Acoustic Fingerprinting)
- **Panako**
- **audfprint** (LabROSA / Dan Ellis)
- **Chromaprint / AcoustID** (include but explain suitability/limits for *short mic* queries)
- **Dejavu** (Python-based, discuss current maintenance status)
- Any other actively maintained option you find relevant (check for 2024–2026 releases)

For each candidate, include:
- Target use-case (short fragments vs full-song ID)
- Robustness: MP3 transcodes, background noise, reverberation, phone mic EQ, clipping
- Whether it can estimate **offset / alignment**
- Storage/index structure (hash → (track_id, t))
- Performance and scalability characteristics
- Ease of integration (language, packaging, docker, APIs)
- **Python integration path** (native Python, subprocess wrapper, or FFI/binding)
- Licenses / constraints
- **Last commit date / maintenance status** — flag anything with no commits in 12+ months

### 2.2 Recommend ONE for v1 (and why)
Pick a single fingerprinting engine for v1 based on:
- **5s mic** identification reliability
- Engineering simplicity
- Operating at 20k MP3 scale
- **Ease of wrapping in a FastAPI endpoint** (Python-native strongly preferred)

Also propose a "Plan B" if the v1 choice fails reliability tests.

### 2.3 Parameter guidance (very specific)
Provide recommended defaults for:
- resample rate (e.g. 8k/11k/16k/22k/44.1k)
- mono conversion
- query strategy for 5s mic recordings:
  - overlap sub-windows? multiple attempts? denoise?
- confidence thresholds (what is "strong hit" vs "weak hit")
- expected top-k returns
- **WebM/Opus decoding** settings for browser-captured audio

### 2.4 Exact ID evaluation plan
Design a measurable test suite:
- Build a labeled query set:
  - 200 clean 10s MP3 clips
  - 200 mic recordings recorded via phone/laptop in varied environments
  - **50 browser-captured WebM/Opus recordings** (to validate the actual input path)
- Metrics:
  - Top‑1 accuracy, Top‑5 accuracy
  - Offset error distribution (seconds)
  - False positives per query
  - **Latency per query** (must be measurable against the < 5s target)
- How to iterate on parameters to improve mic performance

---

## 3) Vibe Lane — Embeddings + Vector Search

### 3.1 Survey embedding model options
Compare at least:
- **CLAP** variants (LAION-CLAP, Microsoft CLAP — audio-text models)
- **MERT** (Music Understanding Model — Hugging Face, music-specific transformer)
- **MusicFM / MULE** (Meta's music foundation models, if publicly available)
- **OpenL3** (and music‑oriented configurations)
- **VGGish / YAMNet** (baseline / limitations for music vibe)
- **EnCodec / Encodec-based representations** (Meta's neural audio codec — evaluate if latent representations are useful for similarity)
- Music-specialized taggers/embeddings (e.g. musicnn, Essentia-based approaches, **Essentia TensorFlow models**)
- **PANNs** (Pretrained Audio Neural Networks) — large-scale audio pattern recognition
- Any other modern "music similarity" embedding model from 2024–2026 papers or repos

For each, cover:
- Embedding dimension
- Compute cost: **GPU inference time** and **CPU fallback feasibility** (this is critical — must work on CPU, faster on GPU)
- Suitability for music "vibe" similarity
- How it handles short snippets / mic recordings
- **How it handles WebM/Opus input** (or does it require WAV/PCM?)
- License and ease of deployment
- **Python package availability** (pip installable? manual build?)
- **Model size** (download size, VRAM requirement)

### 3.2 Recommend v1 model + two alternatives
Pick a pragmatic v1 embedding approach and also provide:
- A higher-quality but heavier alternative (GPU-required is acceptable)
- A lightweight CPU-only alternative

**For the v1 recommendation, explicitly confirm:**
- Works on single RTX 3060/4070 for batch ingestion
- Works on CPU for real-time query (< 5s latency including preprocessing)
- Has a clear Python API

### 3.3 Chunking strategy & expected scale
Given 20k tracks, propose:
- chunk window length (e.g. 8–10s)
- hop size (e.g. 4–5s)
- overlap rationale
- how many vectors we'll store (ballpark)
- memory/storage estimates for float32 vs float16 vs quantized
- **Qdrant collection sizing** (RAM, disk, segments)

### 3.4 Vector DB: Qdrant Configuration

**Qdrant is the chosen vector DB.** Do NOT compare alternatives — instead, provide a deep-dive on optimal Qdrant configuration:

- **Collection schema** (payload fields: track_id, offset_sec, chunk_index, metadata)
- **Distance metric** (cosine vs dot product vs euclidean — recommend based on embedding model)
- **HNSW index parameters** (m, ef_construct, ef — tuned for ~1M vectors)
- **Quantization strategy** (scalar quantization, product quantization, or none — with tradeoff analysis)
- **Storage mode** (in-memory, mmap, on-disk) — recommend based on single-GPU server with 16–32GB RAM
- **Payload indexes** for filtering (genre, BPM range, etc.)
- **Snapshot/backup strategy** for the collection
- **Qdrant Docker configuration** for the existing docker-compose.yml (conditional on `QDRANT_MODE=docker` per §0.4)
- **External Qdrant connectivity** — API key auth, TLS considerations, and how the Python client handles both local and remote instances transparently

### 3.5 Ranking: chunk hits → track-level results
Provide a concrete algorithm:
- query returns top N chunk matches
- group by track_id
- track_score using:
  - max pooling vs top-k average vs reciprocal rank fusion
- optionally add "diversity" bonus when matches span different offsets
- dedupe the exact-match track from vibe results (optional UX toggle)

Include pseudocode.

### 3.6 Vibe evaluation plan
Create a practical evaluation approach:
- human-in-the-loop scoring rubric (1–5 "vibe match" score)
- objective proxies (tag overlap, tempo/key similarity) as sanity checks
- A/B tests across models and chunking settings

---

## 4) Combined Orchestration & API Design

Design a single search endpoint that fits the **existing API contract pattern**:

- `POST /api/v1/search` (versioned, matching existing route structure)
- input: audio file upload (multipart/form-data) + metadata (source=mic|mp3clip)
- output:
  - `exact_matches[]` (track_id, confidence, offset_sec)
  - `vibe_matches[]` (track_id, score, matched_chunks[])

### 4.1 Request/Response Models

Provide **Pydantic v2 schemas** for:
- `SearchRequest` (multipart: audio file + JSON metadata)
- `SearchResponse` with nested `ExactMatch` and `VibeMatch` models
- Error response models

These will live in `audio-ident-service/app/schemas/search.py`.

### 4.2 Orchestration Logic

Define:
- Whether exact and vibe lanes run **in parallel** (asyncio.gather) or sequentially
- When to trust exact lane results
- When to still show vibe lane even if exact match succeeds
- Timeout handling per lane (what if fingerprinting is fast but embedding is slow?)
- UX suggestion: two tabs ("Exact ID" and "Similar vibe")

### 4.3 Browser Upload Considerations

- Maximum upload size for audio snippets
- Whether to accept raw audio or require the browser to encode first
- Content-Type handling for WebM/Opus vs MP4/AAC
- Streaming upload vs. complete-then-send

Include error cases and fallbacks.

---

## 5) Ingestion Pipeline & Storage Plan

### 5.1 Audio decoding and preprocessing
Provide a best-practice pipeline using `ffmpeg`:
- decode MP3 → PCM (for library tracks)
- **decode WebM/Opus or MP4/AAC → PCM** (for browser mic recordings)
- normalize loudness (optional, discuss)
- high-pass filter (mic recordings)
- resampling
- consistent channel layout

**Include the actual ffmpeg commands** for each scenario.

### 5.2 Metadata
Recommend extracting:
- duration, sample rate, channels
- ID3 tags (artist/album/title)
- file hash for duplicates (fast)
- optional: loudness/tempo/key

**Propose PostgreSQL schema** for the tracks metadata table, designed to extend the existing database.

### 5.3 Duplicate detection
Explain how to detect duplicates:
- file hash duplicates vs content duplicates
- where Chromaprint can help even if not used for short snippets

### 5.4 Storage layout
Recommend:
- where raw audio lives (keep MP3 paths)
- where derived artifacts live (fingerprints index, embeddings in Qdrant, metadata in PostgreSQL)
- a reproducible "rebuild index" workflow (`make rebuild-index` target)
- how the storage layout works identically for both Docker-managed and external service modes (no path differences)

---

## 6) Implementation Plan (v1 → v2)

### 6.1 v1 milestones (buildable in small steps)

**These must align with the existing project workflow** (API contract first, then implementation, then type generation):

1. **API contract** — Define search endpoint in `docs/api-contract.md`
2. **Database schema** — Migrations for tracks metadata table in PostgreSQL
3. **Ingestion pipeline** — `make ingest` target: decode, fingerprint, embed, store
4. **Fingerprint lane** — Implement exact ID query in FastAPI router
5. **Embedding lane** — Implement vibe query with Qdrant
6. **Orchestration** — Unified `/api/v1/search` endpoint
7. **Type generation** — `make gen-client` to produce frontend types
8. **UI** — Search interface in SvelteKit with mic recording + file upload

For each milestone, estimate:
- Engineering effort (days)
- Dependencies on previous milestones
- What can be tested independently

### 6.2 v2 improvements
Include:
- vocal/instrument separation options (if it materially improves vibe)
- multi-embedding fusion (timbre vs harmony/chroma)
- caching, batch processing, GPU acceleration
- advanced filtering facets (BPM range, "instrumental", energy)
- **Feedback loop**: user "thumbs up/down" on vibe results to improve ranking
- **Playlist generation**: "find me 20 tracks like this snippet"

---

## 7) Concrete Deliverables I Want From You

At the end, produce:
1. **Recommended v1 stack** with exact library/tool names and versions if possible
2. **Data model / schema** (PostgreSQL tables + Qdrant collection schema)
3. **Config defaults** (chunk sizes, embedding dims, search N/k values, thresholds)
4. **Pseudocode** for:
   - ingestion loop
   - exact query
   - vibe query + aggregation
   - **browser audio preprocessing** (WebM/Opus → query-ready PCM)
5. **Docker-compose additions** (Qdrant service added to existing docker-compose.yml, with conditional service pattern for docker vs. external mode per §0.4)
6. **`.env.example`** — complete environment variable template covering both deployment modes
7. **Risks & mitigations** (top 10)
8. **Back-of-envelope sizing** (CPU/GPU/RAM/disk) for 20k tracks
9. **Dependency list** — exact Python packages (pip/uv) and system dependencies (ffmpeg version, etc.)

Be opinionated: pick a v1 path, but include alternatives and why you didn't choose them.

---

## 8) Output Format Requirements

- Write results to `docs/research/01-initial-research/` as a **structured markdown report** with headings and bullet points.
- Include a **decision matrix table** for fingerprinting options and embedding options.
- Include references/links to primary docs/repos/papers for each major component.
- If any component looks abandoned or unmaintained, flag it clearly.
- **Include a "Last Verified" date** for each external tool/library recommendation.

---

## 9) Bonus: "Reality Check" Section

Add a section that answers:
- What hit rate should I realistically expect for **5s mic** snippets in noisy environments?
- What hit rate for **5s browser-captured WebM/Opus** snippets specifically?
- What are the common reasons exact ID fails?
- What are the common reasons vibe similarity disappoints?
- What simple changes give the biggest improvements?
- **What are the biggest risks** of the recommended v1 stack becoming unmaintained or deprecated?

---

## 10) Browser Recording Implementation Guidance

Since the frontend is SvelteKit, provide guidance on:
- **MediaRecorder API** configuration (codec preference, sample rate, channels)
- **Minimum recording duration** enforcement (UI should prevent < 3s recordings)
- **Audio level metering** — show the user their mic is active and capturing
- Whether to send raw PCM from the browser (larger, simpler) or encoded WebM (smaller, needs server decode)
- **Error handling**: mic permission denied, no audio input detected, recording too quiet
- Recommended npm packages (if any) for browser audio capture in SvelteKit
