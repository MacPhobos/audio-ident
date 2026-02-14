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

---

## 1) Define the Two Problems Precisely

Explain why:
- **Exact ID** (snippet → same recording / same track) is best solved with **acoustic fingerprinting** (landmark hashing / inverted index) and not embeddings.
- **Vibe similarity** is best solved with **audio embeddings + vector search** and not fingerprinting.

Include a short “failure modes” section for each approach if used for the wrong job.

---

## 2) Exact ID Lane — Acoustic Fingerprinting for Short Queries

### 2.1 Survey candidate solutions (detailed)
Research and compare at least these:
- **Olaf** (Overly Lightweight Acoustic Fingerprinting)
- **Panako**
- **audfprint** (LabROSA / Dan Ellis)
- **Chromaprint / AcoustID** (include but explain suitability/limits for *short mic* queries)
- Any other actively maintained option you find relevant

For each candidate, include:
- Target use-case (short fragments vs full-song ID)
- Robustness: MP3 transcodes, background noise, reverberation, phone mic EQ, clipping
- Whether it can estimate **offset / alignment**
- Storage/index structure (hash → (track_id, t))
- Performance and scalability characteristics
- Ease of integration (language, packaging, docker, APIs)
- Licenses / constraints

### 2.2 Recommend ONE for v1 (and why)
Pick a single fingerprinting engine for v1 based on:
- **5s mic** identification reliability
- Engineering simplicity
- Operating at 20k MP3 scale

Also propose a “Plan B” if the v1 choice fails reliability tests.

### 2.3 Parameter guidance (very specific)
Provide recommended defaults for:
- resample rate (e.g. 8k/11k/16k/22k/44.1k)
- mono conversion
- query strategy for 5s mic recordings:
  - overlap sub-windows? multiple attempts? denoise?
- confidence thresholds (what is “strong hit” vs “weak hit”)
- expected top-k returns

### 2.4 Exact ID evaluation plan
Design a measurable test suite:
- Build a labeled query set:
  - 200 clean 10s MP3 clips
  - 200 mic recordings recorded via phone/laptop in varied environments
- Metrics:
  - Top‑1 accuracy, Top‑5 accuracy
  - Offset error distribution (seconds)
  - False positives per query
- How to iterate on parameters to improve mic performance

---

## 3) Vibe Lane — Embeddings + Vector Search

### 3.1 Survey embedding model options
Compare at least:
- **CLAP** variants (audio-text models)
- **OpenL3** (and music‑oriented configurations)
- **VGGish / YAMNet** (baseline / limitations for music vibe)
- Music-specialized taggers/embeddings (e.g. musicnn, Essentia-based approaches)
- If you find a modern “music similarity” embedding model (recent papers or repos), include it.

For each, cover:
- Embedding dimension
- Compute cost (CPU vs GPU feasibility)
- Suitability for music “vibe” similarity
- How it handles short snippets / mic recordings
- License and ease of deployment

### 3.2 Recommend v1 model + two alternatives
Pick a pragmatic v1 embedding approach and also provide:
- A higher-quality but heavier alternative
- A lightweight CPU-only alternative

### 3.3 Chunking strategy & expected scale
Given 20k tracks, propose:
- chunk window length (e.g. 8–10s)
- hop size (e.g. 4–5s)
- overlap rationale
- how many vectors we’ll store (ballpark)
- memory/storage estimates for float32 vs float16 vs quantized

### 3.4 Vector DB choices
Compare:
- **Qdrant**
- FAISS local index
- Milvus (if relevant)
- pgvector (discuss pros/cons at ~1M vectors)

Recommend one for v1 with reasons and show:
- Collection schema (payload fields)
- Index settings (HNSW parameters or equivalent)
- Any payload indexes for filtering

### 3.5 Ranking: chunk hits → track-level results
Provide a concrete algorithm:
- query returns top N chunk matches
- group by track_id
- track_score using:
  - max pooling vs top-k average vs reciprocal rank fusion
- optionally add “diversity” bonus when matches span different offsets
- dedupe the exact-match track from vibe results (optional UX toggle)

Include pseudocode.

### 3.6 Vibe evaluation plan
Create a practical evaluation approach:
- human-in-the-loop scoring rubric (1–5 “vibe match” score)
- objective proxies (tag overlap, tempo/key similarity) as sanity checks
- A/B tests across models and chunking settings

---

## 4) Combined Orchestration & API Design

Design a single search endpoint:
- `POST /search`
- input: audio bytes + metadata (source=mic|mp3clip, optional sample rate)
- output:
  - `exact_matches[]` (track_id, confidence, offset_sec)
  - `vibe_matches[]` (track_id, score, why/offsets/snippets)

Define:
- When to trust exact lane results
- When to still show vibe lane even if exact match succeeds
- UX suggestion: two tabs (“Exact ID” and “Similar vibe”)

Include error cases and fallbacks.

---

## 5) Ingestion Pipeline & Storage Plan

### 5.1 Audio decoding and preprocessing
Provide a best-practice pipeline using `ffmpeg`:
- decode MP3 → PCM
- normalize loudness (optional, discuss)
- high-pass filter (mic recordings)
- resampling
- consistent channel layout

### 5.2 Metadata
Recommend extracting:
- duration, sample rate, channels
- ID3 tags (artist/album/title)
- file hash for duplicates (fast)
- optional: loudness/tempo/key

### 5.3 Duplicate detection
Explain how to detect duplicates:
- file hash duplicates vs content duplicates
- where Chromaprint can help even if not used for short snippets

### 5.4 Storage layout
Recommend:
- where raw audio lives (keep MP3 paths)
- where derived artifacts live (fingerprints index, embeddings DB, metadata DB)
- a reproducible “rebuild index” workflow

---

## 6) Implementation Plan (v1 → v2)

### 6.1 v1 milestones (buildable in small steps)
Propose:
1. ingest metadata + decode pipeline
2. implement fingerprint lane + query
3. implement embedding lane + query
4. unify orchestration
5. simple UI (optional)

### 6.2 v2 improvements
Include:
- vocal/instrument separation options (if it materially improves vibe)
- multi-embedding fusion (timbre vs harmony/chroma)
- caching, batch processing, GPU acceleration
- advanced filtering facets (BPM range, “instrumental”, energy)

---

## 7) Concrete Deliverables I Want From You

At the end, produce:
1. **Recommended v1 stack** with exact library/tool names and versions if possible
2. **Data model / schema** (tables or payload fields)
3. **Config defaults** (chunk sizes, embedding dims, search N/k values, thresholds)
4. **Pseudocode** for:
   - ingestion loop
   - exact query
   - vibe query + aggregation
5. **Docker-compose** outline (services: api, qdrant, optional worker)
6. **Risks & mitigations** (top 10)
7. **Back-of-envelope sizing** (CPU/GPU/RAM/disk) for 20k tracks

Be opinionated: pick a v1 path, but include alternatives and why you didn’t choose them.

---

## 8) Output Format Requirements

- Write results to docs/resarch/01-initial-research/ as a **structured markdown report** with headings and bullet points.
- Include a **decision matrix table** for fingerprinting options and embedding options.
- Include references/links to primary docs/repos/papers for each major component.
- If any component looks abandoned or unmaintained, flag it clearly.

---

## 9) Bonus: “Reality Check” Section

Add a section that answers:
- What hit rate should I realistically expect for **5s mic** snippets in noisy environments?
- What are the common reasons exact ID fails?
- What are the common reasons vibe similarity disappoints?
- What simple changes give the biggest improvements?

