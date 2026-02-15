# Phase 3: Ingestion Pipeline -- Devil's Advocate Review

> **Reviewer**: research-agent (Claude Opus 4.6)
> **Date**: 2026-02-15
> **Spec reviewed**: `docs/plans/01-initial-implementation/03-phase-ingestion-pipeline.md`
> **Implementation reviewed**: All source and test files listed in File Summary below

---

## 1. Confidence Assessment

**Overall: MEDIUM-LOW**

The implementation covers the structural requirements well -- all specified modules exist, the 7-step pipeline flow is implemented, and the dual-rate decode + parallel processing architecture is in place. However, the review uncovered **2 CRITICAL** issues, **4 HIGH** issues, and **5 MEDIUM** issues that collectively undermine production readiness. The most severe problem is that the "parallel" `asyncio.gather` in the pipeline is an illusion: two of the three tasks contain synchronous blocking calls that serialize execution and block the event loop. This defeats the entire concurrency design described in the spec.

**Breakdown by area:**

| Area | Confidence | Notes |
|------|-----------|-------|
| Audio decode (`decode.py`) | HIGH | Clean async implementation, correct ffmpeg flags |
| Metadata extraction (`metadata.py`) | HIGH | Solid mutagen handling, all tag formats covered |
| Duplicate detection (`dedup.py`) | MEDIUM | Correct logic, but synchronous `subprocess.run` in async pipeline |
| Olaf fingerprint (`fingerprint.py`) | MEDIUM | Spec deviation (CLI vs CFFI), but pragmatic implementation |
| CLAP embedding (`embedding.py`) | LOW | No `run_in_executor`, CPU-bound work blocks event loop |
| Qdrant setup (`qdrant_setup.py`) | MEDIUM | Synchronous client calls in async context |
| Pipeline orchestration (`pipeline.py`) | LOW | Illusory parallelism, no transaction strategy, race conditions |
| Test suite | MEDIUM | Good coverage of happy/error paths, but all mocked -- no integration tests |

---

## 2. Spec Compliance Gaps

### GAP-1: Olaf module uses CLI subprocess, not CFFI (MEDIUM)

**Spec says** (line 433): `app/audio/fingerprint.py` is described as "Olaf CFFI wrapper (index + query)". Step 4 title (line 158): "Olaf Fingerprint Indexing". Step 4.1 (line 164-171) discusses productionizing the "CFFI wrapper from prototyping" and mentions `install_name_tool` fixes for the CFFI shared library.

**Implementation does**: `fingerprint.py:1` declares itself as "Olaf acoustic fingerprint indexing and querying via CLI subprocess." All operations use `asyncio.create_subprocess_exec` with the `olaf_c` binary (lines 117-125, 185-193, 239-246).

**Impact**: This is a pragmatic deviation -- CLI subprocess is arguably more portable and avoids CFFI compilation issues. However, it introduces temp file I/O overhead for every operation (lines 112-115, 180-183) and means the spec's `install_name_tool` guidance and CFFI-specific risk mitigations are irrelevant. The CLAUDE.md convention about wrapping "CFFI calls in `loop.run_in_executor`" (CLAUDE.md line 87-95) does not apply to the current implementation, but the async subprocess approach is already non-blocking, so this is acceptable.

**Severity**: MEDIUM -- Spec/implementation mismatch in documentation, but functionally sound.

### GAP-2: `generate_chromaprint` uses `fpcalc` CLI, not `pyacoustid` library (LOW)

**Spec says** (line 135): "Python package: `cd audio-ident-service && uv add pyacoustid`"

**Implementation does**: `dedup.py:73-89` calls the `fpcalc` binary directly via `subprocess.run`, bypassing the `pyacoustid` Python package entirely.

**Impact**: Low -- `pyacoustid` itself is just a wrapper around `fpcalc`. Direct CLI usage avoids the Python dependency. However, the `subprocess.run` call is synchronous (see CRITICAL-1 below).

**Severity**: LOW -- Functionally equivalent, reduces a dependency.

### GAP-3: `generate_chunked_embeddings` signature differs from spec (LOW)

**Spec says** (line 234): `generate_chunked_embeddings(pcm_48k, track_id, metadata, qdrant)` -- the function generates embeddings AND upserts to Qdrant.

**Implementation does**: `embedding.py:155-194` -- `generate_chunked_embeddings(pcm_48k_f32le, model, processor)` generates embeddings only. The Qdrant upsert is handled separately in `pipeline.py:179`.

**Impact**: This is actually a better separation of concerns. The embedding module has no Qdrant dependency, making it more testable and reusable.

**Severity**: LOW -- Positive deviation from spec.

### GAP-4: No `decode_and_validate` usage in pipeline (LOW)

**Spec says** (line 56-57): "Add `decode_and_validate()` wrapper that enforces min/max duration constraints."

**Implementation does**: `decode.py:108-136` implements `decode_and_validate()` but the pipeline (`pipeline.py:128-144`) uses `decode_dual_rate()` directly and performs manual duration validation afterward.

**Impact**: The `decode_and_validate` function exists but is dead code -- never called in the actual pipeline. The pipeline's manual duration check is functionally equivalent but duplicates logic.

**Severity**: LOW -- Dead code, minor inconsistency.

---

## 3. Error Handling Issues

### ERR-1: Raw file saved before duration validation (HIGH)

**File**: `pipeline.py:118-144`

**Problem**: Step 3 (save raw file, line 122) executes before Step 5 (decode + duration check, lines 128-144). If a file is too short or too long, it has already been copied to `data/raw/` but is never cleaned up.

```python
# Step 3: Save raw file (line 118-122)
storage_path = raw_audio_path(file_hash, extension)
ensure_storage_dirs(file_hash)
shutil.copy2(file_path, storage_path)

# ... later ...

# Duration validation (lines 134-144)
if duration < MIN_INGESTION_DURATION:
    result.status = "skipped"  # raw file already saved!
    return result
```

**Impact**: Disk space leak. Over thousands of ingestion runs, skipped files accumulate in `data/raw/` with no Track record pointing to them. `make rebuild-index` will attempt to re-ingest these orphaned files, potentially skipping them again in an infinite loop.

**Fix**: Move raw file save after duration validation, or clean up the saved file when skipping.

### ERR-2: File read twice -- `compute_file_hash` and `file_path.read_bytes()` (MEDIUM)

**File**: `pipeline.py:103,128`

**Problem**: The file is read entirely into memory twice: once for SHA-256 hashing via `compute_file_hash(file_path)` (which uses 64KB chunked reads -- `metadata.py:172-177`), and once via `file_path.read_bytes()` at line 128 to feed to ffmpeg.

**Impact**: For a 30-minute MP3 at 320kbps, that is ~72MB read twice. Not a crash risk, but wasteful. The `compute_file_hash` function reads in 64KB chunks specifically to avoid loading the whole file, but then the pipeline loads the whole file anyway on line 128.

**Fix**: Either compute the hash from `file_bytes` (already in memory), or pipe the file path to ffmpeg instead of piping bytes through stdin.

### ERR-3: `metadata.py` computes hash redundantly (MEDIUM)

**File**: `metadata.py:115`, `pipeline.py:103,125`

**Problem**: `extract_metadata()` at `metadata.py:115` calls `compute_file_hash(file_path)` and stores it in `AudioMetadata.file_hash_sha256`. But `pipeline.py:103` has already computed the hash separately. The hash is computed twice for every file.

**Impact**: Minor performance waste (two full file reads for hashing). The metadata's `file_hash_sha256` result is never used by the pipeline -- it uses the separately computed `file_hash` variable.

**Fix**: Either pass the pre-computed hash into `extract_metadata()`, or remove the hash computation from metadata extraction.

### ERR-4: No cleanup of raw file on pipeline failure after Step 3 (MEDIUM)

**File**: `pipeline.py:118-254`

**Problem**: If the pipeline fails at any step after saving the raw file (line 122) -- whether due to decode error (line 245), Olaf/CLAP failure, or DB insert failure -- the raw file remains on disk with no corresponding Track record. The general exception handler (lines 250-254) does not clean up the saved file.

**Impact**: Orphaned files on disk. Unlike the SHA-256 hash duplicate skip, these files WILL be re-processed on `make rebuild-index` since they exist in `data/raw/`. If they consistently fail, each rebuild will re-attempt and re-fail.

**Fix**: Add cleanup in the exception handlers, or defer raw file save until after all processing succeeds.

### ERR-5: `check_content_duplicate` performs full table scan (MEDIUM)

**File**: `dedup.py:184-192`

**Problem**: The content duplicate check queries ALL tracks with a chromaprint fingerprint within +/-10% duration. At 20K tracks, this could return thousands of rows, all loaded into memory, and each fingerprint is compared using a Python-level bit manipulation loop (`_fingerprint_similarity`, lines 131-158).

**Impact**: O(n) scaling per ingestion. At 20K tracks, this is still manageable (the spec acknowledges this at line 450). But at 100K+ tracks, this becomes a significant bottleneck. No index exists on `chromaprint_duration` to speed up the range query.

**Fix**: Add a database index on `chromaprint_duration`. For future scaling, consider storing a hashed prefix of the fingerprint for faster candidate filtering.

---

## 4. Concurrency & Safety Concerns

### CRITICAL-1: `asyncio.gather` parallelism is an illusion (CRITICAL)

**File**: `pipeline.py:150-189`

**Problem**: The pipeline claims to run three tasks "in parallel" via `asyncio.gather` (line 187-189):

```python
chromaprint_result, olaf_success, embedding_result = await asyncio.gather(
    chromaprint_task(), olaf_task(), embedding_task()
)
```

However, two of these three tasks contain **synchronous blocking calls** that hold the GIL and prevent any actual concurrency:

1. **`chromaprint_task()`** (line 150-158): Calls `generate_chromaprint()` which uses `subprocess.run()` (`dedup.py:73`) -- a **synchronous blocking call**. This blocks the event loop for the duration of the `fpcalc` subprocess (up to 30 seconds per the timeout at `dedup.py:88`).

2. **`embedding_task()`** (line 170-184): Calls `generate_chunked_embeddings()` (`embedding.py:155-194`) which is entirely synchronous CPU-bound work (PyTorch inference). For a 4-minute track with ~47 chunks, this blocks the event loop for the entire inference duration. Then it calls `upsert_track_embeddings()` (`qdrant_setup.py:73-148`) which uses the synchronous Qdrant client -- another blocking call.

3. **`olaf_task()`** (line 161-167): Calls `olaf_index_track()` which correctly uses `asyncio.create_subprocess_exec` -- this is the ONLY actually async task.

**Result**: The three tasks execute **sequentially**, not in parallel. `chromaprint_task` blocks the event loop, then `olaf_task` runs (truly async), then `embedding_task` blocks the event loop again. The `asyncio.gather` provides zero concurrency benefit.

**Impact**: In a CLI batch context, this merely means slower processing than expected. In a FastAPI server context (if the pipeline is ever exposed as an API endpoint), this would block the entire web server during ingestion, preventing it from serving any other requests.

**Fix**: Wrap all synchronous blocking calls in `loop.run_in_executor()`:
```python
# For chromaprint
loop = asyncio.get_event_loop()
fingerprint = await loop.run_in_executor(
    None, generate_chromaprint, pcm_s16le, duration
)

# For CLAP
chunks = await loop.run_in_executor(
    None, generate_chunked_embeddings, pcm_48k, clap_model, clap_processor
)

# For Qdrant upsert
count = await loop.run_in_executor(
    None, upsert_track_embeddings, qdrant_client, track_id, chunks, meta
)
```

**Severity**: CRITICAL -- Defeats the core concurrency architecture described in the spec.

### CRITICAL-2: CLAP inference blocks event loop without `run_in_executor` (CRITICAL)

**File**: `embedding.py:155-194`, `pipeline.py:172`

**Problem**: `generate_chunked_embeddings()` performs CPU-bound PyTorch inference in a tight loop (line 180-191):

```python
for audio_data, offset_sec, chunk_index, duration_sec in raw_chunks:
    embedding = generate_embedding(audio_data, model, processor)  # CPU-bound!
```

`generate_embedding()` (lines 62-98) runs `model.get_audio_features(**inputs)` which is a synchronous PyTorch forward pass. For a 4-minute track (~47 chunks at ~200ms-2s per chunk on CPU), this blocks the event loop for 10-90 seconds.

**CLAUDE.md explicitly warns** (lines 83-95):
> "CFFI / GIL Blocking (Critical): ... If called directly in an async function, they block the entire asyncio event loop and prevent parallel execution."
> "**Always** wrap ... calls in `loop.run_in_executor(None, ...)`"

While this convention specifically mentions CFFI, the principle applies equally to any CPU-bound work in an async context, including PyTorch inference.

**Impact**: Same as CRITICAL-1. Additionally, CLAUDE.md line 103 recommends `asyncio.Semaphore(1)` to prevent concurrent CPU-bound CLAP inferences from degrading latency -- this is also not implemented.

**Severity**: CRITICAL -- Violates explicit CLAUDE.md convention, blocks the event loop.

### CONC-3: Olaf indexing runs in parallel with Chromaprint dedup (HIGH)

**File**: `pipeline.py:161-167, 186-204`

**Problem**: The pipeline starts Olaf indexing (`olaf_task`, line 163) in parallel with the Chromaprint content dedup check (`chromaprint_task`, lines 150-158). If the Chromaprint check determines the file is a content duplicate, the pipeline then has to clean up the already-indexed Olaf data (lines 198-200).

This is documented as "best effort" cleanup (line 197: "Best effort - don't fail if cleanup fails"), but it means:
1. Olaf LMDB has been written to and then deleted from -- unnecessary I/O
2. If the cleanup fails (suppressed by `contextlib.suppress(Exception)` on line 199), Olaf retains orphaned fingerprint data for a track that does not exist in PostgreSQL

**Impact**: The spec's devil's advocate review (line 497-498) identified the lack of a transaction strategy as Gap #2, but the implementation proceeded without addressing it. Orphaned Olaf data does not cause incorrect search results (queries return track IDs that can be validated against PG), but it wastes LMDB space and could accumulate over time.

**Fix**: Consider running Chromaprint dedup check BEFORE Olaf indexing, since the dedup check is much faster (a subprocess call + DB query) than Olaf indexing. This would avoid the need for rollback entirely.

### CONC-4: Synchronous Qdrant client in async context (HIGH)

**File**: `qdrant_setup.py:20-22, 42-56, 73-148, 151-174`

**Problem**: All Qdrant operations use the synchronous `QdrantClient` (`qdrant_client` from `qdrant-client` package). The `qdrant-client` package provides both sync and async clients:
- `QdrantClient` (synchronous) -- currently used
- `AsyncQdrantClient` (asynchronous) -- not used

The synchronous client blocks the event loop during network I/O to Qdrant (collection creation, upserts, deletes). This is particularly problematic during `upsert_track_embeddings()` which can make multiple network calls (one per batch of 100 points, line 135-140).

**Impact**: Combined with CRITICAL-1, the synchronous Qdrant client is another source of event loop blocking. In the current CLI-only usage this is acceptable, but it prevents future use of the pipeline from a FastAPI endpoint.

**Fix**: Switch to `AsyncQdrantClient` from `qdrant-client`, or wrap synchronous calls in `run_in_executor`.

---

## 5. Transaction & Consistency Issues

### TXN-1: No atomicity across PostgreSQL, Olaf LMDB, and Qdrant (HIGH)

**File**: `pipeline.py:146-243`

**Problem**: The pipeline writes to three data stores with no transactional coordination:

1. **Olaf LMDB** -- written during `olaf_task()` (line 163)
2. **Qdrant** -- written during `embedding_task()` (line 179)
3. **PostgreSQL** -- written during Step 7 (lines 209-232)

If PostgreSQL insert fails (line 232 `session.commit()` raises), Olaf and Qdrant already have data for a track that does not exist in PG. There is no rollback of Olaf or Qdrant on PG failure.

**Failure scenarios and their consequences:**

| Olaf | Qdrant | PG | Result |
|------|--------|----|--------|
| OK | OK | FAIL | Orphaned data in Olaf + Qdrant. Search returns track IDs that resolve to no metadata. |
| OK | FAIL | OK | Track exists in PG and Olaf but has no embeddings. `olaf_indexed=True` but `embedding_model=None`. Vibe search will not find this track. |
| FAIL | OK | OK | Track exists in PG and Qdrant but `olaf_indexed=False`. Exact search will not find this track. |

**Impact**: The spec's own devil's advocate review identified this as Gap #2 (line 497-498) and noted `make rebuild-index` should handle it. The CLAUDE.md (lines 116-117) documents this: "If PostgreSQL succeeds but Qdrant fails, the system is in an inconsistent state. Use `make rebuild-index` to recover." However, `rebuild-index` only recovers from the Olaf/Qdrant perspective -- it does NOT handle the PG-fails case (orphaned Olaf/Qdrant data).

**Fix**: Move PG insert to execute BEFORE Olaf/Qdrant writes (with a status column like `ingestion_status='processing'`), then update the status to `'complete'` after Olaf/Qdrant succeed. This provides a simple state machine for recovery. Alternatively, add cleanup of Olaf/Qdrant in the exception handler.

### TXN-2: No consistency verification mechanism (MEDIUM)

**Problem**: There is no command, script, or code path that verifies all three data stores are consistent. The spec's devil's advocate review recommended (line 536): "Add a consistency check script that verifies PG tracks all have corresponding Olaf entries and Qdrant vectors." This was not implemented.

**Impact**: Silent data inconsistency. A track could exist in PG with `olaf_indexed=True` but actually be missing from Olaf LMDB (e.g., after manual LMDB corruption). There is no way to detect this without querying all three stores and cross-referencing.

**Fix**: Add a `make check-consistency` target that:
1. Queries all tracks from PG where `olaf_indexed=True`
2. Verifies each track ID exists in Olaf LMDB
3. Verifies each track ID has embeddings in Qdrant
4. Reports mismatches

### TXN-3: Session used for both read and write without explicit flush (LOW)

**File**: `pipeline.py:209-232`

**Problem**: The async session for Track insertion uses `session.add(track)` followed by `session.commit()`, but there is no explicit `session.flush()` before commit. While SQLAlchemy's `commit()` implicitly flushes, if any error occurs between `add` and `commit`, the session state could be unclear. This is a minor concern given the simple usage pattern.

**Severity**: LOW -- Standard SQLAlchemy usage, unlikely to cause issues.

---

## 6. Missing Features

### MISS-1: No ffmpeg version check at startup (MEDIUM)

**Spec reference**: Line 525: "The overview pins `ffmpeg >= 5.0` but this phase doesn't verify the installed version."
**CLAUDE.md reference**: Line 65: "ffmpeg >= 5.0 (verified at startup)"

**Problem**: CLAUDE.md claims ffmpeg version is verified at startup, but no such check exists in the codebase. Neither `decode.py`, `pipeline.py`, `cli.py`, nor `main.py` verifies the ffmpeg version. An older ffmpeg version could produce incorrect PCM output or fail with different error messages.

**Fix**: Add `verify_ffmpeg_version()` to `decode.py` and call it from the CLI entry point and FastAPI lifespan handler.

### MISS-2: No `fpcalc` binary availability check (MEDIUM)

**Spec reference**: Lines 511, 527: "pyacoustid requires `fpcalc` binary" and "Verify `fpcalc` installation alongside `pyacoustid`."

**Problem**: If `fpcalc` is not installed, `generate_chromaprint()` silently returns `None` (via the `FileNotFoundError` handler at `dedup.py:107-108`). The track is still ingested but with `chromaprint_fingerprint=None`, meaning content deduplication silently stops working for all files. There is no startup warning or configuration check.

**Impact**: On a new deployment without `fpcalc`, ALL tracks are ingested without Chromaprint fingerprints. Content duplicates (same audio, different encoding) will not be detected. This failure is completely silent -- the only clue is a warning log message that could easily be missed in a batch of thousands.

**Fix**: Add a startup check in `cli.py` that verifies `fpcalc` is available, and emit a prominent warning (or fail-fast) if not found.

### MISS-3: No progress persistence for crash recovery (LOW)

**Spec reference**: Line 501-502: "No progress persistence for batch ingestion. If `make ingest` crashes at track 5000 of 20K..."

**Problem**: The pipeline relies on SHA-256 dedup to skip already-ingested files on restart. This works but is inefficient: every previously-ingested file still requires reading the full file (to compute SHA-256) and a database query (to check the hash) on restart.

**Impact**: For a 20K file library, restarting after a crash at file 5000 means re-reading and re-hashing 5000 files. At ~5MB per file, that is ~25GB of redundant I/O. Acceptable for a dev tool, but inefficient.

**Fix**: Write a simple checkpoint file (`data/.ingest_progress`) with the last successfully processed file path. On restart, skip all files before the checkpoint.

### MISS-4: No disk space check before ingestion (LOW)

**Spec reference**: Line 529: "Disk space for raw audio storage. 20K tracks at ~5MB each = ~100GB."

**Problem**: No check for available disk space before starting ingestion. If the disk fills up mid-ingestion, `shutil.copy2` (pipeline.py:122) will raise `OSError`, which is caught by the general exception handler (line 250) and logged as an error. The pipeline will continue trying to ingest remaining files, all of which will also fail with disk space errors.

**Fix**: Add a pre-flight disk space check in `ingest_directory()` before processing begins.

### MISS-5: No warm-up inference for CLAP in CLI mode (LOW)

**CLAUDE.md reference**: Lines 99-101: "Run warm-up inference with 5s silence during startup"

**Problem**: The CLI entry point (`cli.py:44-45`) loads the CLAP model but does not run a warm-up inference. The first actual track will experience cold-start latency (~1-3 seconds extra). In CLI mode this is tolerable (the delay is amortized over the batch), but it deviates from the documented convention.

**Severity**: LOW -- Minimal impact in CLI-only usage.

---

## 7. Test Quality Assessment

### Overall Test Coverage

| Module | Test File | LOC (src) | LOC (test) | Test Ratio | Assessment |
|--------|-----------|-----------|------------|------------|------------|
| `decode.py` | `test_audio_decode.py` | 137 | ~200* | 1.46x | GOOD - Real WAV fixtures |
| `metadata.py` | `test_audio_metadata.py` | 179 | ~200* | 1.12x | GOOD - Real WAV files on disk |
| `dedup.py` | `test_audio_dedup.py` | 214 | ~200* | 0.93x | FAIR - Mixed mock/real |
| `fingerprint.py` | `test_audio_fingerprint.py` | 351 | ~250* | 0.71x | FAIR - Fully mocked |
| `embedding.py` | `test_audio_embedding.py` | 195 | ~200* | 1.03x | FAIR - Mock model, real chunking |
| `qdrant_setup.py` | `test_audio_qdrant_setup.py` | 175 | ~200* | 1.14x | FAIR - Fully mocked |
| `pipeline.py` | `test_ingest_pipeline.py` | 325 | 1058 | 3.26x | GOOD volume, LOW integration |

*Approximate line counts from prior reading.

### TEST-1: No integration tests with real services (HIGH)

**Problem**: Every test file mocks all external dependencies (ffmpeg, fpcalc, Olaf, CLAP model, Qdrant, PostgreSQL). There are zero integration tests that verify the actual pipeline works with real services.

**Impact**: The tests verify that the code calls the right functions with the right arguments, but they do NOT verify that:
- ffmpeg actually produces correct PCM output for MP3/WebM/OGG/MP4 formats
- `fpcalc` produces valid Chromaprint fingerprints from real audio
- Olaf actually indexes and retrieves fingerprints correctly
- CLAP actually produces 512-dim embeddings from 48kHz audio
- Qdrant actually stores and retrieves vectors

The spec's acceptance criteria (e.g., line 71: "Decode 100 test MP3s without error", line 203: "100 tracks indexed into Olaf LMDB without error") require real service integration. None of these are tested.

**Fix**: Add a small integration test suite (marked with `pytest.mark.integration`) that uses real audio fixtures and real services (Docker containers).

### TEST-2: Pipeline tests mock at the wrong layer (MEDIUM)

**File**: `test_ingest_pipeline.py`

**Problem**: The pipeline tests (lines 134-185 for the success test, for example) mock at the `app.ingest.pipeline` module level, patching every imported function. This means the tests verify that `ingest_file()` calls functions in the right order with the right arguments, but they do NOT test:
- Whether the functions are correctly imported
- Whether the function signatures match (mocks accept any arguments)
- Whether the data transformations between steps are correct

For example, the test at line 162 patches `f32le_to_s16le` to return `b"\x00" * 100` regardless of input. If the real function signature changed or the PCM format conversion broke, the test would still pass.

**Impact**: False confidence. The tests provide coverage numbers but limited actual defect detection capability.

### TEST-3: No test for content duplicate cleanup (MEDIUM)

**File**: `test_ingest_pipeline.py:240-326`

**Problem**: The content duplicate test (`test_content_duplicate_detected`, lines 240-326) verifies the status is "duplicate" but does NOT verify that the cleanup of Olaf and Qdrant data occurs. The mocked `olaf_delete_track` (line 312-314) is set up but its invocation is never asserted.

**Impact**: If the cleanup code (pipeline.py:196-203) is removed or breaks, the test still passes.

**Fix**: Assert that `olaf_delete_track` was called when Olaf indexing succeeded and a content duplicate was found.

### TEST-4: No tests for edge cases identified in spec review (MEDIUM)

**Missing test scenarios:**
- Unicode characters in file paths (spec line 513)
- Symlinks in the audio directory
- Files that are exactly at the MIN/MAX duration boundary (3.0s and 1800.0s exactly)
- Empty audio file (0 bytes)
- Audio file that decodes to less than 1 second (below CLAP MIN_CHUNK_SEC)
- Concurrent access to the same file
- File permissions errors (read-only files)

### TEST-5: No test for `rebuild-index` Makefile target (LOW)

**Problem**: The `make rebuild-index` target is untested. It uses shell commands (`rm -rf`, `curl -X DELETE`, `uv run python -m app.ingest`) that could break silently with path changes.

**Severity**: LOW -- Makefile targets are typically not unit-tested, but a smoke test would increase confidence.

---

## 8. Code Quality Notes

### CQ-1: Redundant exception catch in `embedding_task` (LOW)

**File**: `pipeline.py:182`

```python
except (EmbeddingError, Exception) as e:
```

`EmbeddingError` is a subclass of `Exception`, so `(EmbeddingError, Exception)` is equivalent to just `Exception`. The `EmbeddingError` in the tuple is redundant.

**Fix**: Either catch `Exception` only, or handle `EmbeddingError` and `Exception` separately with different behaviors.

### CQ-2: `compute_file_hash` is synchronous and reads entire file (LOW)

**File**: `metadata.py:162-178`

The function reads the file in 64KB chunks, which is good for memory. However, it is a synchronous function called from an async pipeline. For large files (>50MB), this blocks the event loop.

**Severity**: LOW in CLI mode, MEDIUM in server mode.

### CQ-3: Hard-coded embedding model string (LOW)

**File**: `pipeline.py:228`

```python
embedding_model=("clap-htsat-large" if embedding_count > 0 else None),
```

The model name is hard-coded as `"clap-htsat-large"` instead of using `settings.embedding_model`. If the setting changes, the Track record will still store the old hard-coded value.

**Fix**: Use `settings.embedding_model` instead of the string literal.

### CQ-4: Hard-coded embedding dimension (LOW)

**File**: `pipeline.py:229`

```python
embedding_dim=512 if embedding_count > 0 else None,
```

Same issue as CQ-3: `512` should be `settings.embedding_dim`.

### CQ-5: Missing `__init__.py` files not verified (LOW)

The `app/audio/` and `app/ingest/` packages require `__init__.py` files. While these likely exist (the imports work), this was not explicitly verified in the review.

### CQ-6: `_fingerprint_similarity` is O(n) in Python, no vectorization (LOW)

**File**: `dedup.py:131-158`

The fingerprint similarity function uses a Python `for` loop with `bin()` and `.count("1")` for bitwise Hamming distance. NumPy is already imported -- the comparison could be vectorized:

```python
arr1 = np.array([int(x) for x in fp1.split(",")], dtype=np.uint32)
arr2 = np.array([int(x) for x in fp2.split(",")], dtype=np.uint32)
xor = np.bitwise_xor(arr1[:min_len], arr2[:min_len])
differing = sum(bin(x).count("1") for x in xor)  # or use popcount
```

**Impact**: Minor performance improvement. At 20K tracks, the current implementation is fast enough.

### CQ-7: `f32le_to_s16le` clipping not handled (LOW)

**File**: `dedup.py:49-50`

```python
samples = np.frombuffer(pcm_f32le, dtype=np.float32)
return (samples * 32767).astype(np.int16).tobytes()
```

If any sample exceeds 1.0 (which is valid in floating-point audio and common with clipping), the multiplication by 32767 will overflow the int16 range, causing wrapping artifacts. The standard approach is:

```python
return np.clip(samples * 32767, -32768, 32767).astype(np.int16).tobytes()
```

**Impact**: LOW -- Chromaprint is robust to minor clipping artifacts, and most audio files have samples in the [-1.0, 1.0] range.

---

## 9. Recommended Fixes (Prioritized)

### Priority 1: CRITICAL -- Must fix before production use

| # | Issue | File:Line | Fix | Effort |
|---|-------|-----------|-----|--------|
| 1 | CRITICAL-1: Synchronous `subprocess.run` in async `asyncio.gather` | `dedup.py:73`, `pipeline.py:152` | Replace `subprocess.run` with `asyncio.create_subprocess_exec` in `generate_chromaprint`, or wrap in `run_in_executor` | 2h |
| 2 | CRITICAL-2: CPU-bound CLAP inference blocks event loop | `embedding.py:180-182`, `pipeline.py:172` | Wrap `generate_chunked_embeddings` call in `loop.run_in_executor(None, ...)` | 1h |

### Priority 2: HIGH -- Should fix before regular use

| # | Issue | File:Line | Fix | Effort |
|---|-------|-----------|-----|--------|
| 3 | ERR-1: Raw file saved before duration validation | `pipeline.py:118-144` | Move `shutil.copy2` after duration validation, or add cleanup on skip | 1h |
| 4 | CONC-3: Olaf writes before dedup check completes | `pipeline.py:161-167,196-203` | Run Chromaprint dedup check before starting Olaf/CLAP tasks. Restructure gather to check dedup first. | 3h |
| 5 | CONC-4: Synchronous Qdrant client | `qdrant_setup.py:20-22` | Switch to `AsyncQdrantClient` or wrap calls in `run_in_executor` | 2h |
| 6 | TXN-1: No atomicity across data stores | `pipeline.py:146-243` | Add cleanup in exception handler; or insert PG record first with `status='processing'` and update on completion | 4h |
| 7 | TEST-1: No integration tests | N/A | Add pytest integration test suite with real audio fixtures and Docker services | 8h |

### Priority 3: MEDIUM -- Should fix before scaling beyond dev

| # | Issue | File:Line | Fix | Effort |
|---|-------|-----------|-----|--------|
| 8 | MISS-1: No ffmpeg version check | `decode.py` (new function) | Add `verify_ffmpeg_version()` callable from CLI and FastAPI startup | 1h |
| 9 | MISS-2: No fpcalc availability check | `cli.py` (startup) | Add `shutil.which("fpcalc")` check with prominent warning | 0.5h |
| 10 | ERR-2: File read twice | `pipeline.py:103,128` | Compute hash from `file_bytes` or pipe file path to ffmpeg | 1h |
| 11 | ERR-3: Hash computed twice | `metadata.py:115`, `pipeline.py:103` | Remove hash from `extract_metadata()` or pass pre-computed hash | 0.5h |
| 12 | ERR-4: No raw file cleanup on failure | `pipeline.py:245-254` | Add `Path(storage_path).unlink(missing_ok=True)` in exception handlers | 0.5h |
| 13 | TXN-2: No consistency check | N/A (new script) | Add `make check-consistency` target | 4h |
| 14 | CQ-3/CQ-4: Hard-coded model name and dim | `pipeline.py:228-229` | Use `settings.embedding_model` and `settings.embedding_dim` | 0.5h |
| 15 | TEST-3: Content duplicate cleanup not asserted | `test_ingest_pipeline.py:240-326` | Add assertion that `olaf_delete_track` was called | 0.5h |

### Priority 4: LOW -- Nice to have

| # | Issue | File:Line | Fix | Effort |
|---|-------|-----------|-----|--------|
| 16 | MISS-3: No progress persistence | `pipeline.py` (new feature) | Add checkpoint file for crash recovery | 2h |
| 17 | MISS-4: No disk space check | `pipeline.py` (new feature) | Add pre-flight `shutil.disk_usage()` check | 0.5h |
| 18 | CQ-1: Redundant exception catch | `pipeline.py:182` | Simplify to `except Exception as e:` | 0.1h |
| 19 | CQ-6: Unvectorized similarity | `dedup.py:145-158` | Vectorize with numpy | 1h |
| 20 | CQ-7: No clipping in f32le_to_s16le | `dedup.py:50` | Add `np.clip()` before cast | 0.1h |

**Total estimated effort for all fixes: ~33h (~4 days)**
**Critical + High fixes only: ~21h (~3 days)**

---

## File Summary

| File | Path | Lines | Issues Found |
|------|------|-------|-------------|
| Audio decode | `audio-ident-service/app/audio/decode.py` | 137 | GAP-4 |
| Metadata extraction | `audio-ident-service/app/audio/metadata.py` | 179 | ERR-3, CQ-2 |
| Duplicate detection | `audio-ident-service/app/audio/dedup.py` | 214 | CRITICAL-1, GAP-2, ERR-5, CQ-6, CQ-7 |
| Olaf fingerprint | `audio-ident-service/app/audio/fingerprint.py` | 351 | GAP-1 |
| CLAP embedding | `audio-ident-service/app/audio/embedding.py` | 195 | CRITICAL-2 |
| Qdrant setup | `audio-ident-service/app/audio/qdrant_setup.py` | 175 | CONC-4 |
| File storage | `audio-ident-service/app/audio/storage.py` | 44 | None |
| Pipeline orchestration | `audio-ident-service/app/ingest/pipeline.py` | 325 | CRITICAL-1, ERR-1, ERR-2, ERR-4, CONC-3, TXN-1, CQ-1, CQ-3, CQ-4 |
| CLI entry point | `audio-ident-service/app/ingest/cli.py` | 75 | MISS-5 |
| Settings | `audio-ident-service/app/settings.py` | 51 | None |
| Track model | `audio-ident-service/app/models/track.py` | 57 | None |
| Pipeline tests | `audio-ident-service/tests/test_ingest_pipeline.py` | 1058 | TEST-1, TEST-2, TEST-3, TEST-4 |
| Makefile (targets) | `Makefile` | N/A | TEST-5 |

---

## Appendix: Spec-to-Implementation Traceability

| Spec Requirement | Line | Implemented | Notes |
|-----------------|------|-------------|-------|
| `decode_to_pcm()` with target_sample_rate | 30 | YES | `decode.py:17-71` |
| `decode_dual_rate()` parallel via gather | 38 | YES | `decode.py:74-87` |
| `pcm_duration_seconds()` helper | 54 | YES | `decode.py:90-105` |
| `decode_and_validate()` wrapper | 56 | YES (unused) | `decode.py:108-136`, not called by pipeline |
| `extract_metadata()` via mutagen | 88 | YES | `metadata.py:99-159` |
| SHA-256 file hash | 93 | YES | `metadata.py:162-178` |
| `check_file_duplicate()` | 140 | YES | `dedup.py:21-37` |
| `generate_chromaprint()` | 141 | YES | `dedup.py:53-115` (fpcalc CLI, not pyacoustid) |
| `check_content_duplicate()` 0.85 threshold | 142-143 | YES | `dedup.py:161-213` |
| `olaf_index_track()` | 179 | YES | `fingerprint.py:87-155` (CLI, not CFFI) |
| `olaf_query()` | 180 | YES | `fingerprint.py:158-219` |
| `olaf_delete_track()` | 181 | YES | `fingerprint.py:222-270` |
| CLAP model loading (HF Transformers) | 233-242 | YES | `embedding.py:40-59` |
| 10s window, 5s hop, skip <1s, pad final | 254-258 | YES | `embedding.py:18-21, 101-152` |
| `ensure_collection()` lazy creation | 278 | YES | `qdrant_setup.py:25-70` |
| 512-dim, cosine, HNSW m=16, ef_construct=200 | 281-282 | YES | `qdrant_setup.py:44-48` |
| INT8 scalar quantization, quantile=0.99 | 283 | YES | `qdrant_setup.py:49-55` |
| Payload indexes on track_id, genre | 284 | YES | `qdrant_setup.py:58-68` |
| Batch upsert of 100 | 288 | YES | `qdrant_setup.py:17, 135-140` |
| 7-step pipeline flow | 317-326 | YES | `pipeline.py:68-243` |
| `ingest_directory()` sequential | 328-333 | YES | `pipeline.py:257-324` |
| `make ingest` target | 355-358 | YES | `Makefile:80-82` |
| `make rebuild-index` target | 359-368 | YES | `Makefile:84-93` |
| Chromaprint s16le via numpy dtype cast | 52 | YES | `dedup.py:40-50` |
