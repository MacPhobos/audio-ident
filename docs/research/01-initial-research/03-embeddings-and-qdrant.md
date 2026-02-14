# Section 3 — Vibe Lane: Embeddings + Vector Search

> Last Verified: 2026-02-14

## 3.1 Embedding Model Survey

### Decision Matrix

| Model | Emb. Dim | Model Size | VRAM (Inference) | CPU Feasible? | Music Vibe Quality | Short Snippet (5s) | WebM/Opus Input | Python Package | License | Maintenance |
|---|---|---|---|---|---|---|---|---|---|---|
| **LAION CLAP (larger_clap_music)** | **512** | ~600MB | ~2GB GPU | **Estimated** (~1–3s/clip CPU, **unverified** — must benchmark in Milestone 0) | **Excellent** — trained on music + AudioSet | Good — handles 5s+ via padding/truncation | Needs PCM conversion (ffmpeg), **requires 48kHz input** | `pip install laion-clap` | Apache-2.0 | **Active** (LAION-AI, HuggingFace) |
| **Microsoft CLAP** | **1024** | ~800MB | ~3GB GPU | Moderate (~3–5s/clip CPU) | Good — general audio, not music-specific | Good — flexible input length | Needs PCM conversion | `pip install msclap` or transformers | MIT | Active (Microsoft Research) |
| **MERT-v1-330M** | **1024** (per layer, 24 layers) | 1.3GB | ~4GB GPU | **Slow** (~5–10s/clip CPU) | **Excellent** — music-specific transformer, SOTA on MIR tasks | Good — 24kHz, handles short clips | Needs PCM conversion (24kHz resample) | `transformers` (HuggingFace) | CC-BY-NC-4.0 | Active (m-a-p org) |
| **MERT-v1-95M** | **768** (per layer, 12 layers) | ~380MB | ~1.5GB GPU | **Yes** (~2–4s/clip CPU) | Very Good — lighter version, still music-specific | Good | Needs PCM conversion | `transformers` | CC-BY-NC-4.0 | Active |
| **PANNs (Cnn14)** | **2048** | ~300MB | ~1GB GPU | **Yes** (~1–2s/clip CPU) | Good — general audio patterns, not music-specific | Good — handles variable length | Needs PCM conversion | `pip install panns-inference` | MIT | Low maintenance (stable) |
| **OpenL3** | **512** or **6144** | ~50MB (music model) | ~0.5GB GPU | **Yes** (<1s/clip CPU) | Moderate — learns general audio representations | Moderate — frame-level, good for chunks | Needs PCM conversion | `pip install openl3` | MIT | Low maintenance (stable) |
| **VGGish** | **128** | ~288MB | ~1GB GPU | **Yes** (<1s/clip CPU) | **Poor** — general audio, too low-dimensional for music nuance | Good — 0.96s frames | Needs PCM conversion | `torch-vggish-yamnet` | Apache-2.0 | Abandoned (Google, 2017 era) |
| **YAMNet** | **1024** | ~20MB | Minimal | **Yes** (very fast) | **Poor** — environmental sound focus, not music | Good — 0.48s frames | Needs PCM conversion | TensorFlow Hub / `torch-vggish-yamnet` | Apache-2.0 | Low maintenance (Google) |
| **EnCodec** | Variable (RVQ codes) | ~120MB (24kHz) | ~1GB GPU | Moderate | **Experimental** — codec latents, not designed for similarity search | Good — streaming codec | Needs PCM conversion | `pip install encodec` | MIT | Active (Meta/audiocraft) |
| **MusicFM** | **768** | ~300MB | ~2GB GPU | Moderate (~3–5s CPU) | Very Good — trained on 8K hours music (FMA) | Good | Needs PCM conversion | Manual install (GitHub) | Apache-2.0 | Low maintenance |
| **Essentia (MSD-MusiCNN)** | **200** | ~100MB | Minimal (TF Lite) | **Yes** (very fast) | Moderate — music tagger, limited vibe capture | Good — frame-level | Needs PCM conversion | `pip install essentia-tensorflow` | AGPL-3.0 | **Active** (MTG/UPF) |
| **Essentia (MAEST)** | **768** | ~350MB | ~2GB GPU | Moderate | Good — music spectrogram transformer | Good — 5–30s sequences | Needs PCM conversion | `pip install essentia-tensorflow` | AGPL-3.0 | Active |

---

### Detailed Analysis of Top Contenders

#### LAION CLAP (larger_clap_music) — **Recommended for v1**

- **Repo**: [github.com/LAION-AI/CLAP](https://github.com/LAION-AI/CLAP)
- **HuggingFace**: [laion/larger_clap_music](https://huggingface.co/laion/larger_clap_music)
- **Architecture**: HTSAT (audio encoder) + RoBERTa (text encoder), projecting both into a **512-dimensional** shared embedding space.
- **Training data**: Music + Speech + AudioSet + LAION-Audio-630k (~4M samples). The `larger_clap_music` variant was specifically trained with music data emphasis.
- **Why it's best for vibe search**:
  1. **Joint audio-text embedding** — enables future "search by text description" feature (e.g., "upbeat jazz with saxophone"). This is a massive UX win for v2.
  2. **512-dim embeddings** — compact enough for efficient vector search, expressive enough for music similarity. Good balance.
  3. **Strong benchmark performance** — highest correlation with human perception of music quality in recent FAD benchmarks (2025).
  4. **Simple Python API** — `model.get_audio_embedding_from_filelist()` returns numpy arrays.
  5. **CPU inference feasible** — HTSAT encoder is not enormous; ~1–3s for a 5s clip on modern CPU.
  6. **Apache-2.0 license** — permissive, no copyleft concerns.
- **Concerns**:
  - Input requires resampled audio (48kHz → model's expected rate, typically 48kHz)
  - Audio longer than ~10s is truncated or averaged; need chunking strategy (§3.3)
  - laion-clap pip package has had some installation issues (resolved in recent versions)

#### MERT-v1-330M — **Higher-Quality Alternative (GPU-Required)**

- **Repo**: [github.com/yizhilll/MERT](https://github.com/yizhilll/MERT)
- **HuggingFace**: [m-a-p/MERT-v1-330M](https://huggingface.co/m-a-p/MERT-v1-330M)
- **Architecture**: BERT-style transformer encoder with 24 layers, 1024-dim hidden states. Trained with RVQ-VAE + CQT teachers at 24kHz.
- **Why it's an excellent alternative**:
  1. **Purpose-built for music** — not a general audio model adapted for music. Understands harmony, rhythm, timbre at a deep level.
  2. **SOTA MIR performance** — outperforms models 15x its size on music understanding benchmarks.
  3. **Layer-wise features** — different layers capture different aspects (lower = timbral, higher = semantic). Can combine layers for rich representation.
- **Why not v1**:
  1. **CPU inference is slow** (~5–10s per 5s clip at 330M params) — borderline for <5s latency target.
  2. **CC-BY-NC-4.0 license** — non-commercial restriction.
  3. **No text-audio joint space** — can't do "search by text" later.
  4. **Larger VRAM** — 4GB+ for inference, marginal on RTX 3060 (12GB) alongside other processes.
- **Best for**: v2 upgrade if music-specific quality matters more than query latency. Can pre-compute all library embeddings on GPU, then use smaller model for query-time inference.

#### PANNs (Cnn14) — **Lightweight CPU-Only Alternative**

- **Repo**: [github.com/qiuqiangkong/panns_inference](https://github.com/qiuqiangkong/panns_inference)
- **PyPI**: `pip install panns-inference`
- **Architecture**: 14-layer CNN (Wavegram-Logmel), trained on AudioSet. Embedding dimension: **2048**.
- **Why it's a good CPU fallback**:
  1. **Very fast CPU inference** — <1s for a 5s clip.
  2. **Simple pip install** — zero build complexity.
  3. **MIT license** — maximally permissive.
  4. **Proven at scale** — widely used in audio research, stable package.
- **Why not v1**:
  1. **Not music-specific** — trained on AudioSet (general audio). Captures "sounds like" but not "vibes like" for music.
  2. **2048-dim embeddings** — larger vectors mean more storage and slower vector search. Would need dimensionality reduction.
  3. **No text-audio capability**.
- **Best for**: Quick prototype, CPU-only environments, or as a baseline to compare against CLAP.

---

## 3.2 v1 Recommendation: LAION CLAP (larger_clap_music)

### Confirmed Requirements

| Requirement | LAION CLAP Status |
|---|---|
| Works on single RTX 3060/4070 for batch ingestion | **Yes** — ~2GB VRAM, well within 12GB RTX 3060 budget. Can process batch of 32+ clips simultaneously. |
| Works on CPU for real-time query (<5s latency) | **Estimated Yes** — ~1–3s per 5s clip on modern CPU (Intel i7/Ryzen 7). **CAVEAT: This estimate is not based on published benchmarks.** The HTSAT-large encoder has ~600MB parameters (Swin Transformer), and transformer models can be slow on CPU. Actual latency must be validated in Milestone 0 (prototype #2). If inference exceeds 5s, fallback to PANNs Cnn14 (<1s CPU) or budget for GPU inference. |
| Clear Python API | **Yes** — `laion_clap.CLAP_Module()` with `.get_audio_embedding_from_data()` and `.get_audio_embedding_from_filelist()`. Also available via HuggingFace Transformers. |

### Model Loading Code (Reference)

```python
import laion_clap

# Load model (downloads ~600MB on first run)
model = laion_clap.CLAP_Module(enable_fusion=False, amodel='HTSAT-large')
model.load_ckpt(model_id=3)  # larger_clap_music checkpoint

# Get embedding from audio file
embeddings = model.get_audio_embedding_from_filelist(
    x=["track.wav"],
    use_tensor=False
)  # Returns numpy array, shape (1, 512)

# Get embedding from raw audio data
# IMPORTANT: CLAP requires 48kHz input — NOT 16kHz!
# See Section 05 for dual sample-rate pipeline (16kHz for Olaf, 48kHz for CLAP)
import numpy as np
audio_data = np.random.randn(48000 * 5)  # 5 seconds at 48kHz
embeddings = model.get_audio_embedding_from_data(
    x=audio_data,
    use_tensor=False
)
```

### Alternatives Summary

| Rank | Model | Use When |
|---|---|---|
| **v1 (Primary)** | LAION CLAP (larger_clap_music) | Default — best balance of quality, speed, and features |
| **v2 Upgrade** | MERT-v1-330M | Music-specific quality matters more than query speed |
| **CPU Fallback** | PANNs Cnn14 | GPU unavailable, need fastest possible CPU inference |

---

## 3.3 Chunking Strategy & Expected Scale

### Chunking Parameters

| Parameter | Value | Rationale |
|---|---|---|
| **Chunk window** | **10 seconds** | CLAP's native input length. Long enough to capture musical phrase, short enough for meaningful similarity. |
| **Hop size** | **5 seconds** | 50% overlap between consecutive chunks. Ensures any 5s query snippet aligns well with at least one chunk. |
| **Overlap** | **5 seconds** (50%) | Prevents boundary artifacts. A musical moment at chunk boundary is fully captured by at least one chunk. |

### Vector Count Estimate

Given 20,000 tracks with average duration of ~4 minutes (240 seconds):

```
Chunks per track = ceil((duration - window) / hop) + 1
                 = ceil((240 - 10) / 5) + 1
                 = 47 chunks per track (average)

Total vectors = 20,000 tracks × 47 chunks = 940,000 vectors ≈ ~1M vectors
```

For shorter tracks (2 min) → ~23 chunks. For longer tracks (8 min) → ~95 chunks.
**Conservative estimate: 800K–1.2M vectors.**

### Storage Estimates

| Format | Bytes per Vector (512-dim) | Total for 1M Vectors | Notes |
|---|---|---|---|
| **float32** | 2,048 bytes | **~2.0 GB** | Full precision, maximum quality |
| **float16** | 1,024 bytes | **~1.0 GB** | Negligible quality loss for cosine similarity |
| **int8 (scalar quantized)** | 512 bytes | **~0.5 GB** | 4x compression, ~99% accuracy retained |

**Recommendation**: Use **float32 for ingestion/storage** and **scalar quantization (int8) for search**. Qdrant supports this natively — stores original vectors on disk, quantized vectors in RAM for fast search.

### Qdrant Collection Sizing

| Component | Estimate |
|---|---|
| Vectors (float32 on disk) | ~2.0 GB |
| Quantized vectors (int8 in RAM) | ~0.5 GB |
| HNSW graph (m=16) | ~0.2 GB |
| Payload data (metadata per point) | ~0.1 GB |
| **Total RAM** | **~0.8–1.0 GB** |
| **Total disk** | **~2.5 GB** |

This fits comfortably in a server with 16–32GB RAM.

---

## 3.4 Qdrant Deep-Dive Configuration

### Collection Schema

```python
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance, VectorParams, ScalarQuantization,
    ScalarQuantizationConfig, ScalarType,
    HnswConfigDiff, OptimizersConfigDiff,
    PayloadSchemaType,
)

client = QdrantClient(host="localhost", port=6333)

# Create collection
client.create_collection(
    collection_name="audio_embeddings",
    vectors_config=VectorParams(
        size=512,           # CLAP embedding dimension
        distance=Distance.COSINE,
    ),
    hnsw_config=HnswConfigDiff(
        m=16,               # connections per node (balanced)
        ef_construct=200,   # index build quality (high)
        full_scan_threshold=10000,  # use brute-force below this
    ),
    quantization_config=ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.99,       # clip outliers at 1st/99th percentile
            always_ram=True,     # keep quantized vectors in RAM
        ),
    ),
    optimizers_config=OptimizersConfigDiff(
        indexing_threshold=20000,  # start building HNSW after 20K points
    ),
)
```

### Distance Metric: Cosine

**Why Cosine over Dot Product or Euclidean:**
- CLAP embeddings are **not L2-normalized** by default. Cosine similarity handles unnormalized vectors correctly.
- Cosine is the standard metric used in CLAP papers and benchmarks.
- Qdrant internally normalizes and uses dot product for cosine distance, so there's no performance penalty.
- Alternative: Pre-normalize all vectors to unit length and use Dot Product. Saves a tiny amount of compute per query but adds a normalization step to ingestion.

### HNSW Parameters (Tuned for ~1M Vectors)

| Parameter | Value | Rationale |
|---|---|---|
| **m** | **16** | Standard balanced setting. Higher recall than m=8, lower memory than m=32. At 1M vectors with 512-dim, this gives ~95%+ recall. |
| **ef_construct** | **200** | Higher than default (100) for better index quality. Build time increases but this is a one-time cost during ingestion. |
| **ef** (search time) | **128** (set per query) | Start at 128, tune based on latency/recall tradeoff. Can increase to 256 for better recall at cost of ~2x query time. |

### Quantization Strategy

**Scalar Quantization (int8)** is recommended:
- **4x memory reduction** (float32 → int8)
- **~99%+ accuracy** retained for cosine similarity search
- Works reliably with CLAP embeddings (well-distributed, no extreme outliers)
- Qdrant applies it transparently — full-precision vectors stored on disk, quantized copies in RAM

**Why not Product Quantization (PQ)?**
- PQ gives better compression (8-16x) but requires tuning number of subvectors
- At 512-dim and 1M vectors, scalar quantization is sufficient — total RAM is only ~0.8GB
- PQ would add complexity without meaningful benefit at this scale

### Storage Mode

**Recommendation: mmap (memory-mapped) with quantized vectors in RAM**

```yaml
# Qdrant storage config
storage:
  storage_path: /qdrant/storage
  on_disk_payload: false    # payloads are small, keep in RAM

  # Performance settings
  performance:
    max_search_threads: 0   # auto-detect
    max_optimization_threads: 2
```

- **Vectors on disk** (mmap): OS page cache handles hot vectors automatically
- **Quantized vectors in RAM** (`always_ram: true`): Fast search path
- **Payloads in RAM** (`on_disk_payload: false`): Small metadata, no reason to go to disk
- At 16–32GB server RAM, this is very comfortable

### Payload Fields and Indexes

```python
# Payload schema per point
payload = {
    "track_id": 12345,          # int — foreign key to PostgreSQL tracks table
    "offset_sec": 30.0,         # float — start time of this chunk in the track
    "chunk_index": 6,           # int — sequential chunk number
    "duration_sec": 10.0,       # float — chunk duration
    "artist": "Miles Davis",    # string — for filtering
    "genre": "jazz",            # string — for filtering
    "bpm": 120,                 # int — for range filtering
    "year": 1959,               # int — for range filtering
    "energy": 0.7,              # float — computed audio feature
}

# Create payload indexes for filtered search
client.create_payload_index(
    collection_name="audio_embeddings",
    field_name="track_id",
    field_schema=PayloadSchemaType.INTEGER,
)
client.create_payload_index(
    collection_name="audio_embeddings",
    field_name="genre",
    field_schema=PayloadSchemaType.KEYWORD,
)
client.create_payload_index(
    collection_name="audio_embeddings",
    field_name="bpm",
    field_schema=PayloadSchemaType.INTEGER,
)
client.create_payload_index(
    collection_name="audio_embeddings",
    field_name="year",
    field_schema=PayloadSchemaType.INTEGER,
)
```

### Snapshot / Backup Strategy

```bash
# Create snapshot via Qdrant REST API
curl -X POST "http://localhost:6333/collections/audio_embeddings/snapshots"

# List snapshots
curl "http://localhost:6333/collections/audio_embeddings/snapshots"

# Download snapshot
curl "http://localhost:6333/collections/audio_embeddings/snapshots/{snapshot_name}" --output snapshot.tar

# Restore from snapshot
curl -X PUT "http://localhost:6333/collections/audio_embeddings/snapshots/recover" \
  -H "Content-Type: application/json" \
  -d '{"location": "/path/to/snapshot.tar"}'
```

**Backup schedule**: After each full ingestion run and before any index parameter changes. Store snapshots alongside PostgreSQL backups.

### Docker Configuration

```yaml
# Addition to existing docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:v1.16.3
    profiles:
      - qdrant  # Only starts when QDRANT_MODE=docker (activates "qdrant" profile)
    ports:
      - "${QDRANT_HTTP_PORT:-6333}:6333"
      - "${QDRANT_GRPC_PORT:-6334}:6334"
    volumes:
      - qdrant_data:/qdrant/storage
    environment:
      - QDRANT__SERVICE__GRPC_PORT=6334
      - QDRANT__SERVICE__HTTP_PORT=6333
      # API key for external access (optional)
      - QDRANT__SERVICE__API_KEY=${QDRANT_API_KEY:-}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  qdrant_data:
```

### External Qdrant Connectivity

```python
from qdrant_client import QdrantClient
import os

def get_qdrant_client() -> QdrantClient:
    """Create Qdrant client that works for both Docker and external modes."""
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_HTTP_PORT", "6333"))
    api_key = os.getenv("QDRANT_API_KEY", None)

    # If API key is set, assume HTTPS (external/production)
    use_https = api_key is not None and len(api_key) > 0

    return QdrantClient(
        host=host,
        port=port,
        api_key=api_key if api_key else None,
        https=use_https,
        timeout=30,  # seconds
    )
```

---

## 3.5 Ranking: Chunk Hits → Track-Level Results

### Problem

A single query returns the top-N **chunk** matches from Qdrant. Multiple chunks may belong to the same track. We need to aggregate chunk scores into **track-level** scores and rank tracks.

### Recommended Algorithm: Top-K Average with Diversity Bonus

```python
from collections import defaultdict
from dataclasses import dataclass

@dataclass
class ChunkHit:
    track_id: int
    chunk_index: int
    offset_sec: float
    score: float  # cosine similarity (0.0 to 1.0)

@dataclass
class TrackResult:
    track_id: int
    score: float
    matched_chunks: list[ChunkHit]
    diversity_bonus: float

def aggregate_chunk_hits(
    chunk_hits: list[ChunkHit],
    top_k_per_track: int = 3,
    diversity_weight: float = 0.05,
    exact_match_track_id: int | None = None,
) -> list[TrackResult]:
    """
    Aggregate chunk-level Qdrant results into track-level rankings.

    Algorithm:
    1. Group chunks by track_id
    2. For each track, compute base score = mean of top-K chunk scores
    3. Add diversity bonus based on how many distinct offsets matched
    4. Optionally remove exact-match track from vibe results
    5. Sort by final score descending
    """
    # Step 1: Group by track
    track_chunks: dict[int, list[ChunkHit]] = defaultdict(list)
    for hit in chunk_hits:
        track_chunks[hit.track_id].append(hit)

    results: list[TrackResult] = []

    for track_id, chunks in track_chunks.items():
        # Step 4: Optionally exclude exact-match track
        if exact_match_track_id and track_id == exact_match_track_id:
            continue

        # Step 2: Sort chunks by score, take top-K
        sorted_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)
        top_chunks = sorted_chunks[:top_k_per_track]
        base_score = sum(c.score for c in top_chunks) / len(top_chunks)

        # Step 3: Diversity bonus — reward tracks that match at multiple offsets
        # Intuition: if 5 different parts of a track match your query,
        # it's more likely to be a good vibe match than if only 1 chunk matches.
        unique_offsets = len(set(c.chunk_index for c in chunks))
        # Normalize: bonus scales from 0 (1 chunk) to diversity_weight (5+ chunks)
        diversity = min(unique_offsets / 5.0, 1.0) * diversity_weight

        final_score = base_score + diversity

        results.append(TrackResult(
            track_id=track_id,
            score=round(final_score, 4),
            matched_chunks=top_chunks,
            diversity_bonus=round(diversity, 4),
        ))

    # Step 5: Sort by final score
    results.sort(key=lambda r: r.score, reverse=True)
    return results
```

### Why Top-K Average (Not Max Pool or RRF)

| Method | Pros | Cons | Verdict |
|---|---|---|---|
| **Max pool** (take highest chunk score per track) | Simple, fast | One lucky chunk match inflates score. Noisy. | Too brittle |
| **Mean of all chunks** | Considers all evidence | Diluted by many low-scoring chunks from long tracks (bias toward short tracks) | Unfair to long tracks |
| **Top-K average** (K=3) | Robust: requires multiple good chunks. Length-independent. | Slightly more complex. | **Best balance** |
| **Reciprocal Rank Fusion** | Good for combining multiple ranking systems | Overkill for single-model, single-query scenario | v2 (multi-model) |

### Query Flow (End-to-End Pseudocode)

```python
async def vibe_search(
    audio_pcm: np.ndarray,
    sample_rate: int,
    top_n_chunks: int = 50,
    top_k_tracks: int = 10,
    exact_match_track_id: int | None = None,
) -> list[TrackResult]:
    """Full vibe search pipeline."""

    # 1. Compute query embedding
    embedding = clap_model.get_audio_embedding_from_data(
        x=audio_pcm, use_tensor=False
    )  # shape: (1, 512)

    # 2. Search Qdrant
    search_results = qdrant_client.query_points(
        collection_name="audio_embeddings",
        query=embedding[0].tolist(),
        limit=top_n_chunks,
        with_payload=True,
        search_params=SearchParams(
            hnsw_ef=128,
            exact=False,  # use HNSW, not brute-force
        ),
    )

    # 3. Convert to ChunkHit objects
    chunk_hits = [
        ChunkHit(
            track_id=result.payload["track_id"],
            chunk_index=result.payload["chunk_index"],
            offset_sec=result.payload["offset_sec"],
            score=result.score,
        )
        for result in search_results.points
    ]

    # 4. Aggregate to track-level results
    track_results = aggregate_chunk_hits(
        chunk_hits,
        exact_match_track_id=exact_match_track_id,
    )

    return track_results[:top_k_tracks]
```

---

## 3.6 Vibe Evaluation Plan

### Human Evaluation Rubric

**Setup**: For N query snippets, show the human evaluator the query audio and top-5 vibe results. Evaluator scores each result on a 1–5 scale:

| Score | Label | Description |
|---|---|---|
| **5** | Perfect vibe match | "I would add this to the same playlist without hesitation" |
| **4** | Strong vibe match | "Similar mood/energy, close genre, would fit the same playlist" |
| **3** | Moderate match | "Some shared qualities but noticeably different in feel" |
| **2** | Weak match | "I can see *why* the algorithm matched this, but it's a stretch" |
| **1** | No match | "Completely different vibe — this is a bad recommendation" |

**Metrics from human scores:**
- **Mean Reciprocal Rank (MRR)** — position of first result scored ≥4
- **nDCG@5** — normalized discounted cumulative gain using human scores as relevance
- **"Playlist-worthy" rate** — fraction of top-5 results scored ≥4

### Objective Proxies (Automated Sanity Checks)

These don't replace human evaluation but catch obvious failures:

| Proxy Metric | How to Compute | What it Catches |
|---|---|---|
| **Genre overlap** | Compare Essentia-predicted genre of query vs. results | Classical matched with heavy metal = bad |
| **Tempo similarity** | Compare BPM (from librosa/Essentia) | Slow ballad matched with fast dance track |
| **Key compatibility** | Compare estimated key (from Essentia) | All results in same key = suspicious overfitting |
| **Energy correlation** | Compare RMS energy / spectral centroid | Quiet acoustic matched with loud electronic |

### A/B Testing Methodology

For comparing models or chunking strategies:

1. **Select 50 diverse query snippets** spanning genres, energy levels, and recording quality.
2. **Run each configuration** (model A vs model B, or chunk size X vs Y) on the same queries.
3. **Blind evaluation**: Interleave results from both configurations, hide which system produced which result.
4. **Statistical test**: Wilcoxon signed-rank test on paired human scores (non-parametric, appropriate for ordinal data).
5. **Minimum detectable effect**: With 50 queries × 5 results = 250 pairs, can detect ~0.3 point difference in mean score at α=0.05.

### Evaluation Dataset

| Set | Count | Source | Purpose |
|---|---|---|---|
| Genre-diverse queries | 20 | One per major genre in library | Coverage of genre space |
| Similar-artist queries | 15 | Known similar artists (e.g., Miles Davis → Coltrane) | Validates vibe matching within genre |
| Cross-genre queries | 10 | "Jazz-influenced rock" → should find both | Tests cross-genre understanding |
| Edge cases | 5 | Spoken word, ambient, very short clips | Failure mode exploration |

---

## References

- LAION CLAP: [GitHub](https://github.com/LAION-AI/CLAP) | [HuggingFace](https://huggingface.co/laion/larger_clap_music) | [PyPI](https://pypi.org/project/laion-clap/)
- Microsoft CLAP: [GitHub](https://github.com/microsoft/CLAP)
- MERT: [GitHub](https://github.com/yizhilll/MERT) | [HuggingFace](https://huggingface.co/m-a-p/MERT-v1-330M) | [Paper](https://arxiv.org/abs/2306.00107)
- PANNs: [GitHub](https://github.com/qiuqiangkong/panns_inference) | [Paper](https://arxiv.org/abs/1912.10211)
- OpenL3: [GitHub](https://github.com/marl/openl3) | [PyPI](https://pypi.org/project/openl3/)
- VGGish/YAMNet: [torch-vggish-yamnet](https://pypi.org/project/torch-vggish-yamnet/)
- EnCodec: [GitHub](https://github.com/facebookresearch/encodec)
- MusicFM: [GitHub](https://github.com/minzwon/musicfm) | [Paper](https://arxiv.org/abs/2311.03318)
- Essentia: [Website](https://essentia.upf.edu/) | [Models](https://essentia.upf.edu/models.html) | [PyPI](https://pypi.org/project/essentia-tensorflow/)
- Qdrant: [Documentation](https://qdrant.tech/documentation/) | [Docker Hub](https://hub.docker.com/r/qdrant/qdrant) | [Quantization Guide](https://qdrant.tech/articles/scalar-quantization/) | [HNSW Tuning](https://qdrant.tech/documentation/concepts/indexing/) | [Capacity Planning](https://qdrant.tech/documentation/guides/capacity-planning/)
- Zilliz Audio Embeddings Guide: [Article](https://zilliz.com/learn/top-10-most-used-embedding-models-for-audio-data)
- FAD Benchmark (2025): [Paper](https://arxiv.org/html/2506.19085v1)
