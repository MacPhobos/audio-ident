# Phase 1: Validation Prototypes (~4 days)

> **Depends on**: Nothing — this is the first phase
> **Blocks**: All subsequent phases
> **Goal**: Validate all critical technology choices BEFORE committing to production code

---

## Overview

The devil's advocate review (08-devils-advocate-review.md) identified that several core assumptions are unverified. This phase runs 5 focused prototypes that each produce a pass/fail decision, gating the entire v1 implementation.

**All prototypes are disposable** — the code written here is throwaway validation code, not production. Keep it in a `prototypes/` directory that is gitignored.

---

## Setup

```bash
# Create prototype workspace (gitignored)
mkdir -p prototypes/{olaf,clap,sample-rate,browser,qdrant}
echo "prototypes/" >> .gitignore
```

---

## Prototype 1: Olaf 5s Mic Accuracy (2 days)

**Reference**: 02-fingerprinting-survey.md §2.1-2.3, 00-reconciliation-summary.md §1

### Step 1: Install Olaf System Dependencies (2 hours)

**macOS:**
```bash
brew install fftw lmdb
```

**Ubuntu/Debian (or Docker):**
```bash
apt-get update && apt-get install -y build-essential libfftw3-dev liblmdb-dev
```

### Step 2: Compile Olaf C Library (2 hours)

```bash
cd prototypes/olaf
git clone https://github.com/JorenSix/Olaf.git
cd Olaf
make
# Expected: produces olaf_c binary and shared library
```

**If compilation fails:**
- Check `gcc --version` (requires GCC or Clang)
- Verify FFTW3 is installed: `pkg-config --libs fftw3f`
- Verify LMDB is installed: `pkg-config --libs lmdb`
- Try Docker: `docker run --rm -v $(pwd):/olaf -w /olaf ubuntu:24.04 bash -c "apt-get update && apt-get install -y build-essential libfftw3-dev liblmdb-dev && make"`

### Step 3: Build Python CFFI Wrapper (3 hours)

Follow [Joren Six's blog post](https://0110.be/posts/A_Python_wrapper_for_Olaf_-_Acoustic_fingerprinting_in_Python):

```bash
cd prototypes/olaf
uv init --python 3.12
uv add cffi numpy
```

Create `prototypes/olaf/build_wrapper.py`:
```python
# Build CFFI wrapper per blog post instructions
# This produces _olaf_ffi.so that can be imported from Python
import cffi
ffi = cffi.FFI()
# ... (follow blog post for exact C declarations)
```

**Expected output**: A Python module that exposes `olaf_store()` and `olaf_query()` functions.

**If CFFI build fails on Python 3.12+:**
- Try `pip install cffi --no-build-isolation`
- Pin `setuptools>=75` in the environment
- If all else fails: this is a NO-GO for Olaf → evaluate Dejavu

### Step 4: Prepare Test Audio (1 hour)

```bash
# Select 50 random tracks from your library
ls /path/to/mp3s/*.mp3 | shuf | head -50 > prototypes/olaf/test_tracks.txt

# Extract 20 clean 10s clips at random offsets
python prototypes/olaf/extract_clips.py --tracks test_tracks.txt --count 20 --duration 10 --output clean_clips/

# Record 20 mic recordings (5s each) using QuickTime/Audacity/browser
# Play tracks through speakers, record with laptop/phone mic
# Save as WAV or WebM in mic_recordings/
```

### Step 5: Index Test Tracks (1 hour)

```bash
# Convert all 50 tracks to 16kHz mono PCM (Olaf's required format)
for f in $(cat test_tracks.txt); do
  hash=$(sha256sum "$f" | cut -c1-12)
  ffmpeg -i "$f" -ar 16000 -ac 1 -f f32le -acodec pcm_f32le "prototypes/olaf/pcm/${hash}.pcm"
done

# Index all 50 tracks via the CFFI wrapper
python prototypes/olaf/index_tracks.py
```

### Step 6: Run Queries and Measure (2 hours)

```python
# prototypes/olaf/evaluate.py
import time
import csv

results = []
for clip_path in clean_clips + mic_recordings:
    pcm = decode_to_16k_pcm(clip_path)
    t0 = time.perf_counter()
    matches = olaf_query(pcm)
    latency_ms = (time.perf_counter() - t0) * 1000

    top_match = matches[0] if matches else None
    correct = top_match and top_match.track_id == ground_truth[clip_path]

    results.append({
        "clip": clip_path,
        "type": "clean" or "mic",
        "correct": correct,
        "top1_track": top_match.track_id if top_match else None,
        "aligned_hashes": top_match.aligned_hashes if top_match else 0,
        "offset_error": abs(top_match.offset - true_offset) if correct else None,
        "latency_ms": latency_ms,
    })

# Compute metrics
clean_accuracy = sum(r["correct"] for r in results if r["type"] == "clean") / 20
mic_accuracy = sum(r["correct"] for r in results if r["type"] == "mic") / 20
avg_latency = sum(r["latency_ms"] for r in results) / len(results)

print(f"Clean clip top-1 accuracy: {clean_accuracy:.0%}")
print(f"Mic recording top-1 accuracy: {mic_accuracy:.0%}")
print(f"Average query latency: {avg_latency:.0f}ms")
```

### Go/No-Go Criteria

| Metric | Go | Marginal | No-Go |
|--------|-----|----------|-------|
| Clean clip top-1 accuracy | >=80% | 60-79% | <60% |
| Mic recording top-1 accuracy | >=50% | 30-49% | <30% |
| Query latency (p95) | <500ms | 500ms-2s | >2s |
| Compilation success | Yes on macOS + Docker | Yes on one platform | Neither |

### Fallback Plan (if No-Go)

Switch to **Dejavu** (pure Python, MIT license):
1. Install: `uv add dejavu` (use [denis-stepanov fork](https://github.com/denis-stepanov/dejavu))
2. Replace MySQL backend with PostgreSQL (we already have it)
3. Re-run the same test: index 50 tracks, query 40 clips
4. Accept lower scalability (SQL-based lookup) but zero compilation issues
5. **Time budget for Dejavu evaluation**: 1 additional day

### Ambiguous Results

If results fall in the "Marginal" range:
- Try adding the high-pass filter: `ffmpeg -af "highpass=f=80"` for mic recordings
- Try the sub-window query strategy (02-fingerprinting-survey.md §2.3)
- If marginal improves to Go with these additions, proceed with Olaf but add 1 day to Phase 4a estimates

---

## Prototype 2: CLAP CPU Inference Benchmark (0.5 days)

**Reference**: 03-embeddings-and-qdrant.md §3.1-3.2, 00-reconciliation-summary.md §4

### Step 1: Install CLAP (1 hour)

```bash
cd prototypes/clap
uv init --python 3.12
uv add laion-clap numpy torch
```

**If laion-clap fails to install:**
- Try `uv add transformers torch` and use HuggingFace integration instead
- Document every error encountered — this feeds into Phase 3 planning

### Step 2: Benchmark Inference (2 hours)

```python
# prototypes/clap/benchmark.py
import time
import numpy as np
import laion_clap

# Load model (time it)
t0 = time.perf_counter()
model = laion_clap.CLAP_Module(enable_fusion=False, amodel='HTSAT-large')
model.load_ckpt(model_id=3)  # larger_clap_music
load_time = time.perf_counter() - t0
print(f"Model load time: {load_time:.1f}s")

# Generate test audio at 48kHz (CLAP's required sample rate)
durations = [5, 10, 30]
results = {}

for dur in durations:
    latencies = []
    for _ in range(10):  # 10 runs per duration
        audio = np.random.randn(48000 * dur).astype(np.float32)
        t0 = time.perf_counter()
        embedding = model.get_audio_embedding_from_data(x=audio, use_tensor=False)
        latency = time.perf_counter() - t0
        latencies.append(latency)

    latencies.sort()
    results[dur] = {
        "p50": latencies[4],
        "p95": latencies[9],
        "p99": latencies[9],  # Only 10 samples
    }
    print(f"{dur}s clip: p50={results[dur]['p50']:.2f}s, p95={results[dur]['p95']:.2f}s")
```

### Go/No-Go Criteria

| Metric | Go | Marginal | No-Go |
|--------|-----|----------|-------|
| 10s clip p95 latency (CPU) | <3s | 3-5s | >5s |
| 5s clip p50 latency (CPU) | <2s | 2-4s | >4s |
| Model load time | <15s | 15-30s | >30s |

### Fallback Plan (if No-Go)

Switch to **PANNs Cnn14** (MIT license, ~300MB, <1s CPU inference):
```bash
uv add panns-inference
```
- PANNs produces 2048-dim embeddings (vs CLAP's 512) — need to adjust Qdrant collection schema
- No text-audio joint space (lose future text search feature)
- Significantly faster CPU inference

### Ambiguous Results

If latency is in the Marginal range (3-5s):
- Test with `torch.set_num_threads(4)` to see if multi-threading helps
- Test with ONNX export: `torch.onnx.export()` can speed up CPU inference 2-3x
- If ONNX brings it under 3s, proceed with CLAP + ONNX optimization (add 0.5d to Phase 3)

---

## Prototype 3: CLAP Sample Rate Quality (0.5 days)

**Reference**: 00-reconciliation-summary.md §2, 03-embeddings-and-qdrant.md §3.2

### Purpose

The reconciliation summary established that CLAP requires 48kHz input. But what if 16kHz input produces embeddings that are "close enough"? If cosine similarity between 16kHz and 48kHz embeddings for the same track is >0.95, we could simplify to a single sample rate pipeline.

### Steps

```python
# prototypes/sample-rate/compare.py
import numpy as np
import laion_clap

model = laion_clap.CLAP_Module(enable_fusion=False, amodel='HTSAT-large')
model.load_ckpt(model_id=3)

# For 20 test tracks, generate embeddings at both sample rates
tracks = load_20_test_tracks()  # list of file paths
similarities = []

for track_path in tracks:
    # Decode at 48kHz (CLAP's expected rate)
    pcm_48k = ffmpeg_decode(track_path, sample_rate=48000)
    emb_48k = model.get_audio_embedding_from_data(x=pcm_48k[:48000*10], use_tensor=False)

    # Decode at 16kHz, then CLAP processes it (will it resample internally?)
    pcm_16k = ffmpeg_decode(track_path, sample_rate=16000)
    emb_16k = model.get_audio_embedding_from_data(x=pcm_16k[:16000*10], use_tensor=False)

    # Cosine similarity
    cos_sim = np.dot(emb_48k[0], emb_16k[0]) / (np.linalg.norm(emb_48k[0]) * np.linalg.norm(emb_16k[0]))
    similarities.append(cos_sim)
    print(f"{track_path}: cosine similarity = {cos_sim:.4f}")

mean_sim = np.mean(similarities)
min_sim = np.min(similarities)
print(f"\nMean cosine similarity: {mean_sim:.4f}")
print(f"Min cosine similarity: {min_sim:.4f}")
```

### Go/No-Go Criteria

| Metric | Decision |
|--------|----------|
| Mean cosine similarity > 0.95 AND min > 0.90 | Can simplify to single-rate pipeline (16kHz only) |
| Mean cosine similarity 0.85-0.95 | Must use dual-rate pipeline (confirmed) |
| Mean cosine similarity < 0.85 | Must use dual-rate pipeline; 16kHz embeddings are significantly degraded |

### Expected Outcome

Based on the reconciliation summary, we expect dual-rate to be necessary (CLAP was trained on 48kHz). This prototype confirms that assumption and quantifies the quality difference.

---

## Prototype 4: Browser End-to-End (1 day)

**Reference**: 10-browser-recording.md, 09-reality-check.md §9.2

### Purpose

Validate the full chain: browser recording -> upload -> ffmpeg decode -> Olaf query + CLAP embed. This catches codec/container issues that only appear with real browser audio.

### Step 1: Create Minimal Recording Page (2 hours)

```html
<!-- prototypes/browser/index.html -->
<!-- Minimal page that records audio and downloads the blob -->
<!-- Test in Chrome, Firefox, Safari -->
```

Use the MediaRecorder config from 10-browser-recording.md:
- `audio/webm;codecs=opus` at 128kbps (Chrome/Firefox)
- `audio/mp4;codecs=aac` at 128kbps (Safari fallback)

### Step 2: Record Test Clips (2 hours)

| Browser | Clips | Environment |
|---------|-------|-------------|
| Chrome (desktop) | 5 | Quiet room, speaker 1m away |
| Chrome (Android) | 3 | Quiet room, phone near speaker |
| Safari (desktop) | 3 | Quiet room, speaker 1m away |
| Safari (iOS) | 2 | Quiet room, phone near speaker |
| Firefox (desktop) | 2 | Quiet room, speaker 1m away |

Play known tracks from the 50 indexed in Prototype 1. Record 5-10s each.

### Step 3: Process Through Pipeline (2 hours)

```python
# prototypes/browser/evaluate.py
import subprocess

for recording in browser_recordings:
    # Test ffmpeg decode (16kHz for Olaf)
    result_16k = subprocess.run(
        ["ffmpeg", "-i", recording, "-ar", "16000", "-ac", "1", "-f", "s16le", "pipe:1"],
        capture_output=True
    )
    decode_16k_ok = result_16k.returncode == 0

    # Test ffmpeg decode (48kHz for CLAP)
    result_48k = subprocess.run(
        ["ffmpeg", "-i", recording, "-ar", "48000", "-ac", "1", "-f", "f32le", "pipe:1"],
        capture_output=True
    )
    decode_48k_ok = result_48k.returncode == 0

    # Test Olaf fingerprint match (if Prototype 1 passed)
    if decode_16k_ok and olaf_available:
        match = olaf_query(result_16k.stdout)
        fingerprint_match = match and match.track_id == ground_truth[recording]

    # Test CLAP embedding (if Prototype 2 passed)
    if decode_48k_ok and clap_available:
        embedding = clap_embed(result_48k.stdout)
        embedding_ok = embedding is not None and embedding.shape == (1, 512)

    print(f"{recording}: decode_16k={decode_16k_ok}, decode_48k={decode_48k_ok}, "
          f"fingerprint={fingerprint_match}, embedding={embedding_ok}")
```

### Go/No-Go Criteria

| Metric | Go | Marginal | No-Go |
|--------|-----|----------|-------|
| Decode success rate (all browsers) | >=90% | 70-89% | <70% |
| Chrome fingerprint match rate | >=70% | 50-69% | <50% |
| Safari decode success | >=80% | 60-79% | <60% |
| Embedding generation success | >=95% | 80-94% | <80% |

### Fallback Plan

If Safari recordings fail to decode:
- Add explicit `-f mp4` format hint for Safari recordings
- Detect container format via `python-magic` before passing to ffmpeg
- If still failing, accept Safari limitations and document workaround

---

## Prototype 5: Qdrant Load Test (0.5 days)

**Reference**: 03-embeddings-and-qdrant.md §3.3-3.4, 00-reconciliation-summary.md §3

### Step 1: Start Qdrant (10 min)

```bash
docker run -d --name qdrant-test -p 6333:6333 qdrant/qdrant:v1.16.3
```

### Step 2: Create Collection with Scalar Quantization (10 min)

```python
# prototypes/qdrant/setup.py
from qdrant_client import QdrantClient, models

client = QdrantClient(host="localhost", port=6333)

client.create_collection(
    collection_name="test_audio",
    vectors_config=models.VectorParams(size=512, distance=models.Distance.COSINE),
    hnsw_config=models.HnswConfigDiff(m=16, ef_construct=200),
    quantization_config=models.ScalarQuantization(
        scalar=models.ScalarQuantizationConfig(
            type=models.ScalarType.INT8, quantile=0.99, always_ram=True
        )
    ),
)
```

### Step 3: Insert ~50K Synthetic Vectors (1 hour)

```python
# prototypes/qdrant/load_test.py
import numpy as np
import time
import uuid

# Simulate ~1K tracks × 47 chunks = ~50K vectors (10% scale test)
TOTAL_VECTORS = 50_000
BATCH_SIZE = 100
TRACKS = 1000

t_insert_start = time.perf_counter()
for batch_start in range(0, TOTAL_VECTORS, BATCH_SIZE):
    points = []
    for i in range(batch_start, min(batch_start + BATCH_SIZE, TOTAL_VECTORS)):
        track_id = i % TRACKS
        chunk_index = i // TRACKS
        points.append(models.PointStruct(
            id=str(uuid.uuid4()),
            vector=np.random.randn(512).astype(np.float32).tolist(),
            payload={
                "track_id": str(uuid.uuid4()),
                "offset_sec": chunk_index * 5.0,
                "chunk_index": chunk_index,
                "duration_sec": 10.0,
                "artist": f"Artist {track_id % 100}",
                "title": f"Track {track_id}",
                "genre": ["rock", "jazz", "electronic", "classical", "hip-hop"][track_id % 5],
            },
        ))
    client.upsert(collection_name="test_audio", points=points)

t_insert = time.perf_counter() - t_insert_start
print(f"Inserted {TOTAL_VECTORS} vectors in {t_insert:.1f}s ({TOTAL_VECTORS/t_insert:.0f} vec/s)")
```

### Step 4: Benchmark Queries (1 hour)

```python
# Query latency benchmark
latencies = []
for _ in range(100):
    query_vec = np.random.randn(512).astype(np.float32).tolist()
    t0 = time.perf_counter()
    results = client.query_points(
        collection_name="test_audio",
        query=query_vec,
        limit=50,
        with_payload=True,
        search_params=models.SearchParams(hnsw_ef=128),
    )
    latency = (time.perf_counter() - t0) * 1000
    latencies.append(latency)

latencies.sort()
print(f"Query latency (50K vectors, top-50):")
print(f"  p50: {latencies[49]:.1f}ms")
print(f"  p95: {latencies[94]:.1f}ms")
print(f"  p99: {latencies[98]:.1f}ms")

# Memory usage
info = client.get_collection("test_audio")
print(f"Collection info: {info}")
```

### Step 5: Extrapolate to 940K Vectors

```python
# Linear extrapolation (HNSW query is O(log n), so this overestimates)
# But insert time scales linearly
estimated_insert_940k = t_insert * (940_000 / TOTAL_VECTORS)
print(f"Estimated insert time for 940K vectors: {estimated_insert_940k/60:.1f} minutes")

# For query, HNSW is roughly O(log n) * ef
# At 940K with ef=128, expect ~2-3x the latency of 50K
estimated_p95_940k = latencies[94] * 2.5  # rough estimate
print(f"Estimated query p95 at 940K: {estimated_p95_940k:.1f}ms")
```

### Go/No-Go Criteria

| Metric (at 50K, extrapolated to 940K) | Go | Marginal | No-Go |
|----------------------------------------|-----|----------|-------|
| Query p95 at 50K vectors | <200ms | 200-500ms | >500ms |
| Extrapolated query p95 at 940K | <500ms | 500ms-1s | >1s |
| Memory usage at 50K | <500MB | 500MB-1GB | >1GB |
| Extrapolated memory at 940K | <4GB | 4-8GB | >8GB |

### Fallback Plan

If Qdrant performance is insufficient at 940K vectors:
- Reduce chunking: 10s window, 10s hop (no overlap) → ~24 chunks/track → ~480K vectors
- Or: use one embedding per track (~20K vectors) — simpler but worse vibe quality
- At 20K vectors, brute-force search is <10ms — no HNSW needed

---

## Deliverables

After completing all 5 prototypes, create a decision document:

**File**: `prototypes/validation-report.md`

```markdown
# Validation Report

| Prototype | Result | Decision |
|-----------|--------|----------|
| 1: Olaf Accuracy | PASS/FAIL | Use Olaf / Switch to Dejavu |
| 2: CLAP CPU Latency | PASS/FAIL | Use CLAP / Switch to PANNs |
| 3: Sample Rate Quality | PASS/FAIL | Dual-rate / Single-rate |
| 4: Browser E2E | PASS/FAIL | Proceed / Investigate codecs |
| 5: Qdrant Load | PASS/FAIL | Chunked / Reduced / Per-track |

## Updated Effort Estimates
(Based on actual measurements, adjust Phase 3-6 estimates)

## Risks Retired
(Which risks from 00-plan-overview.md are now resolved?)

## New Risks Discovered
(Any unexpected issues found during validation?)
```

---

## Edge Cases & Gotchas

1. **Olaf LMDB lock files**: If a prototype crashes mid-index, LMDB lock files may prevent re-running. Delete `data.mdb` and `lock.mdb` to reset.
2. **CLAP model download**: First run downloads ~600MB. Ensure stable internet. Cache the model weights for subsequent runs.
3. **macOS Gatekeeper**: Compiled Olaf binary may be blocked. Run `xattr -d com.apple.quarantine ./olaf_c` if needed.
4. **Docker memory limits**: Qdrant with 50K vectors needs ~200MB. Ensure Docker has at least 2GB memory allocated.
5. **Safari WebM recording on macOS**: Requires Safari 18.4+. Check `About Safari` for version. Older versions need MP4/AAC fallback.

---

## Rollback Procedures

Each prototype is isolated in its own directory. To clean up:

```bash
# Remove all prototype artifacts
rm -rf prototypes/
docker rm -f qdrant-test  # Remove test Qdrant container

# Nothing in the main codebase was modified during this phase
```

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| Olaf system deps + compilation | 4h |
| Olaf CFFI wrapper + test audio prep | 4h |
| Olaf indexing + query evaluation | 4h |
| CLAP installation + benchmarking | 4h |
| Sample rate comparison | 4h |
| Browser recording + E2E test | 8h |
| Qdrant setup + load test | 4h |
| **Total** | **~32h (4 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: HIGH** — This is the strongest phase plan. Testing assumptions before committing is exactly right. The go/no-go criteria are measurable and the fallback plans are concrete.

### Gaps Identified

1. **Prototype 1 Go/No-Go threshold (>=80% clean) may be too low.** The Evaluation phase (Phase 7) targets >=98% for clean clips. If Prototype 1 passes at 80%, you've committed to the full build only to likely fail at evaluation. The prototype's clean-clip threshold should be closer to the final target — suggest >=90%.

2. **Prototype 2 benchmark uses random noise, not real audio.** `np.random.randn(48000 * dur)` as test input may not reflect real inference cost. Real audio with tonal structure exercises different code paths in the model (attention layers, spectral features). The benchmark should use at least 5 real audio clips alongside synthetic ones.

3. **Prototype 3 has no "No-Go" action.** The criteria say "Must use dual-rate pipeline" for both the 0.85-0.95 and <0.85 ranges. There's no scenario where this prototype produces a No-Go — it's purely confirmatory. This is fine conceptually but should be labeled as "validation" rather than "go/no-go" to set expectations correctly.

4. **Prototype 4 depends on Prototype 1 and 2 results.** The browser E2E test tries to run Olaf queries and CLAP embeddings, which requires Prototype 1 and 2 to have passed. The dependency isn't explicit in the ordering. If Prototype 1 fails (Olaf doesn't compile), Prototype 4 can only test ffmpeg decode — not the full E2E chain.

5. **Prototype 5 uses synthetic random vectors.** Random 512-dim vectors have different distribution properties than real CLAP embeddings (which tend to cluster by genre). Query performance on random vectors may underestimate latency for real-world queries where more candidates pass the HNSW distance threshold. Suggest generating a few real embeddings to mix in.

### Edge Cases Not Addressed

1. **Olaf LMDB concurrent access.** Prototype 1 indexes 50 tracks sequentially, but production (Phase 3) will need to handle concurrent ingestion. LMDB has specific locking semantics (single writer, multiple readers). This isn't tested in the prototype.

2. **CLAP model memory footprint.** Prototype 2 measures inference latency but not peak RSS memory. If CLAP + PyTorch uses 2-3GB RSS, it constrains what else can run on the same machine. Add `resource.getrusage()` or `psutil.Process().memory_info()` measurement.

3. **macOS vs Linux performance difference.** Prototypes run on macOS (dev machine) but production may use Linux (Docker). CPU inference on Apple Silicon (M-series) vs x86_64 may differ significantly for PyTorch workloads. The plan should note that prototype results may not transfer directly.

### Feasibility Concerns

1. **4 days for 5 prototypes is tight.** Prototype 1 alone (Olaf compilation + CFFI wrapper) is estimated at 12h (1.5 days), and C compilation issues commonly double the estimate. If Olaf compilation hits FFTW linking issues or Python 3.12 CFFI problems (both flagged in research), it could consume the entire 4-day budget.

2. **"20 mic recordings" in Prototype 1 Step 4 is manual work.** Playing 20 tracks through speakers and recording each with a phone takes ~1 hour minimum (setup, play, record, label, per track). This is noted as "1 hour" which is optimistic. Budget 2 hours.

3. **Prototype 4 requires physical devices.** Testing on Android Chrome and iOS Safari requires actual phones, not simulators (MediaRecorder API behaves differently on real hardware). If the developer doesn't have both an Android phone and iPhone immediately available, this stalls.

### Missing Dependencies

1. **No mention of Python version compatibility testing.** Research flagged "CFFI + Python 3.12" as an open question (00-reconciliation-summary.md §6). The prototype should explicitly test on the project's pinned Python version (3.12 per `.tool-versions`), not just "whatever is installed."

2. **Prototype 5 doesn't test Qdrant scalar quantization query accuracy.** The quantized index may return slightly different nearest neighbors than the non-quantized index. For vibe search quality, this could matter. Add a quick accuracy check: query with and without quantization and verify overlap.

### Recommended Changes

1. **Raise Prototype 1 clean-clip Go threshold** from >=80% to >=90% to align with Phase 7 expectations.
2. **Add real audio clips to Prototype 2 benchmark** (at least 5 real files alongside synthetic).
3. **Add memory profiling to Prototype 2** (peak RSS during inference).
4. **Make Prototype 4 dependency on 1+2 explicit** — define what Prototype 4 tests if Olaf or CLAP failed.
5. **Add a note about macOS vs Linux performance differences** to the deliverables section.
6. **Budget 2h (not 1h) for mic recording** in Prototype 1 Step 4.
