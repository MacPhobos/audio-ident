# Phase 4: Search Lanes (~4-5 days total)

> **Depends on**: Phase 3 (ingestion pipeline — tracks must be indexed)
> **Blocks**: Phase 5 (orchestration)
> **Goal**: Two independently testable search functions: `run_exact_lane()` and `run_vibe_lane()`
> **Parallelism**: Phase 4a and 4b can be developed simultaneously

---

## Phase 4a: Exact ID Lane (~2-3 days)

**Reference**: 02-fingerprinting-survey.md §2.2-2.3, 07-deliverables.md §7.4 (Exact Query pseudocode)

### Overview

The exact ID lane takes 16kHz mono PCM audio, generates Olaf fingerprint hashes, queries the LMDB inverted index, applies consensus scoring, and returns ranked `ExactMatch` results with time offsets.

**Olaf output format note**: Olaf produces comma-separated CSV output (not semicolons). Parse accordingly when reading query results from the CFFI wrapper.

### Step 1: Olaf Query Wrapper (~4 hours)

**File**: `audio-ident-service/app/search/exact.py` (NEW)

Build on the `app/audio/fingerprint.py` module from Phase 3. The query path differs from indexing:
- **Indexing**: Store hashes for a full track → LMDB
- **Querying**: Extract hashes from a short clip → lookup in LMDB → return (track_id, offset, hash_count)

Implement:
```python
import asyncio
import functools

async def run_exact_lane(pcm_16k: bytes, max_results: int) -> list[ExactMatch]:
    """Search by audio fingerprint using Olaf's LMDB inverted index."""
    loop = asyncio.get_event_loop()

    # 1. Extract Olaf fingerprint hashes from query PCM
    #    IMPORTANT: Olaf CFFI calls are synchronous C code that holds the GIL.
    #    We MUST run them in a thread pool executor to avoid blocking the
    #    asyncio event loop, which would prevent the vibe lane from running
    #    in parallel during asyncio.gather in the orchestrator (Phase 5).
    query_hashes = await loop.run_in_executor(
        None, functools.partial(olaf_extract_hashes, pcm_16k, sample_rate=16000)
    )

    # 2. Query Olaf's LMDB index (also a blocking CFFI call — run in executor)
    raw_matches = await loop.run_in_executor(
        None, functools.partial(olaf_query, query_hashes)
    )  # [(track_id, offset_sec, aligned_hashes)]

    # 3. Apply consensus scoring (sub-window strategy for 5s clips)
    scored_matches = consensus_score(raw_matches)

    # 4. Filter by minimum aligned hash threshold (MIN_ALIGNED_HASHES = 8)
    # 5. Normalize confidence: min(aligned_hashes / STRONG_MATCH_HASHES, 1.0)
    # 6. Look up track metadata from PostgreSQL
    # 7. Sort by confidence, take top N
    ...
```

### Step 2: Overlapping Sub-Window Strategy (~4 hours)

**Reference**: 02-fingerprinting-survey.md §2.3

For 5s mic recordings, implement the sub-window query strategy to improve reliability:

1. Split the clip into 3 overlapping windows:
   - Window 1: 0.0s - 3.5s
   - Window 2: 0.75s - 4.25s
   - Window 3: 1.5s - 5.0s

2. Query each window independently against Olaf

3. Consensus scoring:
   - 2+ windows → same (track_id, offset ± tolerance) → HIGH confidence
   - 1 window → match, others empty → LOW confidence
   - Windows → different tracks → AMBIGUOUS (discard)

4. Offset reconciliation:
   - Align offsets from multiple windows (should differ by hop amount)
   - Final offset = median of reconciled offsets

**Implementation note**: For clips >5s (e.g., 10s clean clips), skip sub-windowing and query the full clip directly. Sub-windowing is only needed for short, noisy clips.

### Step 3: Confidence Thresholds (~2 hours)

**Reference**: 02-fingerprinting-survey.md §2.3, 07-deliverables.md §7.3

| Level | Aligned Hashes | Normalized Confidence | Action |
|-------|---------------|----------------------|--------|
| Strong match | >=20 | >=1.0 (capped) | Return as top match |
| Probable match | 8-19 | 0.40-0.99 | Return as candidate |
| Weak/spurious | <8 | Below threshold | Discard |
| No match | 0 | 0.0 | Return empty list |

Normalize: `confidence = min(aligned_hashes / STRONG_MATCH_HASHES, 1.0)` where `STRONG_MATCH_HASHES = 20`.

### Step 4: Track Metadata Lookup (~2 hours)

Map Olaf track IDs back to PostgreSQL `Track` records:

```python
async def get_tracks_by_ids(session: AsyncSession, track_ids: list[uuid.UUID]) -> dict[uuid.UUID, Track]:
    stmt = select(Track).where(Track.id.in_(track_ids))
    results = await session.execute(stmt)
    return {t.id: t for t in results.scalars().all()}
```

Build `ExactMatch` responses with `TrackInfo` from the database.

### Step 5: Unit Tests (~4 hours)

**File**: `audio-ident-service/tests/test_search_exact.py` (NEW)

Test cases:
1. **Known match**: Index a track, query with a 10s clip from the same track → verify correct track_id and offset
2. **Known non-match**: Query with audio NOT in the index → verify empty results
3. **Short clip (5s)**: Query with a 5s clip → verify sub-window consensus works
4. **Multiple matches with different confidence**: Verify sorting by confidence
5. **Offset accuracy**: Verify returned offset is within 0.5s of true offset
6. **Below threshold**: Query with very noisy audio → verify matches below MIN_ALIGNED_HASHES are discarded

### Edge Cases

1. **No match found**: Return empty `exact_matches[]` — this is normal (track not in library, or too noisy)
2. **Multiple matches with similar confidence**: Return all, sorted by confidence descending. Let the UI handle display.
3. **Very short clip (<3s)**: Fewer hashes → fewer potential matches. The sub-window strategy degrades. Warn but don't reject.
4. **Very long clip (>30s)**: Truncate to 30s before fingerprinting (per ingestion constraints)
5. **Query during silence/low-energy passage**: Few spectral peaks → few hashes → weak or no match. This is expected.
6. **CFFI/GIL blocking (critical gotcha)**: All Olaf CFFI calls (`olaf_extract_hashes`, `olaf_query`) are synchronous C code that holds the Python GIL. If called directly in an async function, they block the entire asyncio event loop, preventing any other coroutines (including the vibe lane) from making progress. **Always** wrap CFFI calls in `loop.run_in_executor(None, ...)` to offload them to a thread pool. This is essential for the `asyncio.gather` parallelism in Phase 5 to work correctly.

### Acceptance Criteria
- [ ] `run_exact_lane()` returns correct matches for known tracks
- [ ] Offset estimation is within 0.5s of true offset
- [ ] No false positives on negative controls (tracks not in library)
- [ ] Sub-window consensus improves accuracy on 5s clips
- [ ] Query latency < 500ms for 20K indexed tracks
- [ ] Confidence normalization produces values in [0.0, 1.0]

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_search_exact.py -v
```

---

## Phase 4b: Vibe Lane (~2-3 days)

**Reference**: 03-embeddings-and-qdrant.md §3.5, 07-deliverables.md §7.4 (Vibe Query pseudocode)

### Overview

The vibe lane takes 48kHz mono PCM audio, generates a CLAP embedding, queries Qdrant for the top-50 nearest chunks, aggregates chunk scores to track-level results, and returns ranked `VibeMatch` results.

**CLAP model config**: `laion/larger_clap_music_and_speech` via HuggingFace Transformers (`ClapModel` / `ClapProcessor`), HTSAT-large architecture, 512-dim audio embeddings. Load time ~1.1s, inference p50 ~0.208s for 10s clips, peak memory ~844 MB.

### Step 1: Query Embedding Generation (~4 hours)

**File**: `audio-ident-service/app/search/vibe.py` (NEW)

```python
async def run_vibe_lane(
    pcm_48k: bytes,
    max_results: int,
    exact_match_track_id: uuid.UUID | None = None,
) -> list[VibeMatch]:
    """Search by audio embedding (vibe/similarity)."""
    # 1. Convert PCM bytes to numpy array (f32le — already 32-bit float from ffmpeg)
    audio = np.frombuffer(pcm_48k, dtype=np.float32)

    # 2. Generate CLAP embedding (HuggingFace Transformers API)
    #    Model: laion/larger_clap_music_and_speech (HTSAT-large, 512-dim)
    import torch
    inputs = clap_processor(audios=[audio], sampling_rate=48000, return_tensors="pt")
    with torch.no_grad():
        embedding = clap_model.get_audio_features(**inputs)
    # shape: (1, 512) — torch.Tensor, convert to numpy/list for Qdrant

    # 3. Query Qdrant for nearest CHUNKS
    search_results = await qdrant.query_points(
        collection_name=settings.qdrant_collection_name,
        query=embedding[0].numpy().tolist(),
        limit=50,  # QDRANT_SEARCH_LIMIT — get more chunks than final results
        with_payload=True,
        search_params=models.SearchParams(hnsw_ef=128),
    )

    # 4. Aggregate chunks → track scores
    # 5. Look up track metadata from PostgreSQL
    # 6. Build VibeMatch responses
    ...
```

### Step 2: Chunk-to-Track Aggregation (~6 hours)

**Reference**: 03-embeddings-and-qdrant.md §3.5

Implement the `aggregate_chunk_hits()` algorithm from Section 03:

**Algorithm: Top-K Average with Diversity Bonus**

```python
def aggregate_chunk_hits(
    chunk_hits: list[ChunkHit],
    top_k_per_track: int = 3,
    diversity_weight: float = 0.05,
    exact_match_track_id: uuid.UUID | None = None,
) -> list[TrackResult]:
    """
    1. Group chunks by track_id
    2. For each track: base_score = mean of top-K chunk scores
    3. Diversity bonus: reward tracks matching at multiple offsets
       bonus = min(unique_offsets / 5.0, 1.0) * diversity_weight
    4. Exclude exact-match track if present (avoid "you searched for X, we found X")
    5. Sort by final_score descending
    """
```

**Why Top-K Average** (not Max Pool or Mean of all chunks):
- Max Pool: One lucky chunk inflates score → too brittle
- Mean of all: Long tracks have many low-scoring chunks that dilute → biased against long tracks
- Top-K (K=3): Requires multiple good chunks, length-independent → best balance

### Step 3: Track Metadata Lookup (~2 hours)

After aggregation, map track UUIDs back to PostgreSQL for full metadata:

```python
track_ids = [r.track_id for r in track_results[:max_results]]
async with async_session_factory() as session:
    stmt = select(Track).where(Track.id.in_(track_ids))
    results = await session.execute(stmt)
    tracks_by_id = {t.id: t for t in results.scalars().all()}
```

Build `VibeMatch` responses maintaining the aggregated ranking order.

### Step 4: Unit Tests (~4 hours)

**File**: `audio-ident-service/tests/test_search_vibe.py` (NEW)

Test cases:
1. **Known similar tracks**: Query with a jazz track → verify jazz tracks rank higher than metal
2. **Aggregation correctness**: Given mock chunk hits, verify Top-K average + diversity bonus math
3. **Exact-match exclusion**: If exact_match_track_id is set, verify that track is excluded from results
4. **Empty results**: Query with silence → verify empty/low-score results
5. **Qdrant connection error**: Mock Qdrant failure → verify graceful error handling
6. **Embedding model not loaded**: Verify clear error message

### Edge Cases

1. **Qdrant unavailable**: Return empty `vibe_matches[]` — don't crash the whole search
2. **CLAP model not loaded**: Raise clear error in lifespan handler (should have pre-loaded)
3. **All results below threshold**: Return empty list (VIBE_MATCH_THRESHOLD = 0.60)
4. **All chunks from same track**: After aggregation, only 1 track result → still valid
5. **Query audio too short for meaningful embedding**: CLAP pads short inputs; quality degrades but doesn't crash
6. **Genre imbalance in library**: If library is 80% rock, vibe search will be biased toward rock. Document as known limitation.

### Acceptance Criteria
- [ ] `run_vibe_lane()` returns ranked VibeMatch results
- [ ] Similar-genre tracks score higher than cross-genre tracks
- [ ] Chunk aggregation produces sensible track-level scores
- [ ] exact_match_track_id exclusion works correctly
- [ ] Query latency < 3s (including CLAP inference + Qdrant query)
- [ ] Graceful handling when Qdrant is unavailable

### Commands to Verify
```bash
cd audio-ident-service && uv run pytest tests/test_search_vibe.py -v
```

---

## Independence Requirement

**Each lane must be independently deployable and testable before the orchestration phase (Phase 5).**

This means:
- `run_exact_lane()` can be called standalone without `run_vibe_lane()` existing
- `run_vibe_lane()` can be called standalone without `run_exact_lane()` existing
- Each has its own test suite that passes independently
- Each handles its own errors without depending on the other

This is critical for Phase 5, where both lanes are combined with `asyncio.gather` and `return_exceptions=True`.

---

## File Summary

| File | Purpose |
|------|---------|
| `app/search/exact.py` | Exact ID lane (Olaf query + consensus scoring) |
| `app/search/vibe.py` | Vibe lane (CLAP embed + Qdrant query + aggregation) |
| `app/search/aggregation.py` | Chunk-to-track aggregation algorithm |
| `tests/test_search_exact.py` | Unit tests for exact lane |
| `tests/test_search_vibe.py` | Unit tests for vibe lane |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Olaf returns inconsistent results across queries | Low | High | Use deterministic hashing; cache LMDB connection |
| CLAP inference latency exceeds budget on CPU | Medium | High | Validated in Phase 1; PANNs fallback |
| Qdrant query returns stale results after recent ingestion | Low | Low | Qdrant updates are eventually consistent; wait for optimizer |
| Sub-window consensus adds complexity without accuracy gain | Medium | Low | Measure with and without; disable if no improvement |

## Rollback Procedures

```bash
# Remove search modules
rm -rf audio-ident-service/app/search/
rm -rf audio-ident-service/tests/test_search_*.py
```

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| **Phase 4a: Exact ID Lane** | |
| Olaf query wrapper | 4h |
| Sub-window consensus strategy | 4h |
| Confidence thresholds + normalization | 2h |
| Track metadata lookup | 2h |
| Unit tests | 4h |
| **Subtotal 4a** | **16h (2 days)** |
| | |
| **Phase 4b: Vibe Lane** | |
| Query embedding generation | 4h |
| Chunk-to-track aggregation | 6h |
| Track metadata lookup | 2h |
| Unit tests | 4h |
| **Subtotal 4b** | **16h (2 days)** |
| | |
| **Total (if parallel)** | **~2-3 days** |
| **Total (if sequential)** | **~4 days** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM** — The search lane designs are sound and correctly derived from research. However, two critical runtime concerns are not addressed: Olaf's CFFI calls blocking the async event loop, and the CLAP CPU latency risk persisting from Phase 1.

### Gaps Identified

1. **~~RESOLVED~~ Olaf CFFI calls are synchronous and will block the asyncio event loop.** Fixed: `run_exact_lane()` now wraps both `olaf_extract_hashes()` and `olaf_query()` in `loop.run_in_executor(None, ...)` to offload blocking CFFI calls to a thread pool. An edge case note (#6) has been added documenting the CFFI/GIL gotcha.

2. **~~RESOLVED~~ No mention of `run_in_executor` anywhere in Phase 4a.** Fixed: The pseudocode now uses `run_in_executor` for both CFFI calls with explanatory comments.

3. **Sub-window strategy hop size doesn't match 5s clip math.** The plan states:
   - Window 1: 0.0s - 3.5s
   - Window 2: 0.75s - 4.25s
   - Window 3: 1.5s - 5.0s

   These are 3.5s windows with 0.75s hops. But the research (02-fingerprinting-survey.md §2.3) doesn't specify exact window/hop values — it says "overlapping sub-windows." The chosen 3.5s window may be too short for Olaf to generate enough hashes. Olaf works best with >5s clips. Consider 4s windows with 0.5s hops instead, or test both configurations in Phase 1.

4. **Phase 4b's `exact_match_track_id` parameter creates an implicit dependency on Phase 4a.** The `run_vibe_lane()` signature includes `exact_match_track_id: uuid.UUID | None = None` for excluding the exact-matched track from vibe results. This means Phase 5 must run the exact lane first (or at least wait for its result) before calling the vibe lane if exclusion is desired. This contradicts the parallel execution model. **Fix**: Make exclusion a post-processing step in the orchestrator, not a vibe lane parameter.

5. **`VIBE_MATCH_THRESHOLD = 0.60` is not validated.** This threshold determines which vibe results are returned. The value 0.60 is stated but never justified against empirical data. Real CLAP cosine similarity scores for "similar" music may be much lower (0.3-0.5 range is common for embedding models). Setting the threshold too high could cause the vibe lane to return empty results for most queries. This should be validated in Phase 1 Prototype 5 or made configurable.

### Edge Cases Not Addressed

1. **Olaf LMDB empty index.** What happens if `run_exact_lane()` is called before any tracks are ingested? Olaf should return empty results, but the CFFI wrapper may crash or return garbage if the LMDB database doesn't exist or is empty. Test this case.

2. **Qdrant collection empty or non-existent.** Similarly, `run_vibe_lane()` querying an empty Qdrant collection should return empty results gracefully, not throw an exception. The Phase 4b edge cases mention "Qdrant unavailable" but not "empty collection."

3. **Concurrent search requests.** If two search requests arrive simultaneously, both will call `run_exact_lane()` and `run_vibe_lane()`. LMDB supports concurrent readers, so Olaf is fine. But CLAP inference is single-threaded and CPU-bound — two concurrent CLAP inferences will contend for CPU and potentially double latency. Consider a semaphore to serialize CLAP inference.

### Feasibility Concerns

1. **"2-3 days per lane" assumes Olaf and CLAP integration work smoothly.** Phase 1 validates that they work at all, but production integration (error handling, async wrapping, connection management) always takes longer than prototype code. Budget 3 days each, not 2.

2. **Phase 4b's aggregation algorithm (Top-K Average with Diversity Bonus) has no test data.** The unit tests propose "Given mock chunk hits, verify Top-K average + diversity bonus math" — but without real CLAP embeddings and real similarity scores, the test can only verify arithmetic, not quality. The aggregation parameters (top_k=3, diversity_weight=0.05) are borrowed from research but never validated empirically.

### Missing Dependencies

1. **Phase 4a depends on a working Olaf CFFI wrapper from Phase 3.** If Phase 3's fingerprint module (`app/audio/fingerprint.py`) has bugs, Phase 4a's `run_exact_lane()` inherits them. The dependency is noted at the phase level but the specific module dependency should be explicit.

2. **Phase 4b depends on the Qdrant collection schema from Phase 3 Step 5.3.** If the payload fields change between Phase 3 and Phase 4b, the aggregation code breaks.

### Recommended Changes

1. **~~RESOLVED~~ Add `run_in_executor` wrapper for all Olaf CFFI calls** in Phase 4a. Done: both `olaf_extract_hashes` and `olaf_query` are now wrapped in `run_in_executor`.
2. **Move `exact_match_track_id` exclusion to the orchestrator** (Phase 5) instead of baking it into `run_vibe_lane()`.
3. **Make `VIBE_MATCH_THRESHOLD` configurable** via settings and validate it empirically before finalizing.
4. **Add a CLAP inference semaphore** to prevent concurrent CPU-bound inference from degrading latency.
5. **Test the sub-window parameters** (3.5s vs 4s windows) during Phase 1 Prototype 1 evaluation.
6. **Add empty-index test cases** for both lanes.
