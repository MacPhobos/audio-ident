# Phase 4: Search Lanes -- Devil's Advocate Implementation Review

> **Reviewer**: research-agent (devil's advocate)
> **Date**: 2026-02-15
> **Scope**: Phase 4a (Exact ID Lane) and Phase 4b (Vibe Lane) implementation vs. plan
> **Files Reviewed**:
> - `audio-ident-service/app/search/exact.py` (469 lines)
> - `audio-ident-service/app/search/vibe.py` (239 lines)
> - `audio-ident-service/app/search/aggregation.py` (138 lines)
> - `audio-ident-service/app/search/__init__.py` (6 lines)
> - `audio-ident-service/tests/test_search_exact.py` (785 lines)
> - `audio-ident-service/tests/test_search_vibe.py` (795 lines)
> - `audio-ident-service/app/settings.py` (55 lines)
> - `audio-ident-service/app/audio/fingerprint.py` (dependency)
> - `audio-ident-service/app/audio/embedding.py` (dependency)
> - `audio-ident-service/app/audio/qdrant_setup.py` (dependency)
> - `audio-ident-service/app/schemas/search.py` (dependency)
> - `audio-ident-service/app/models/track.py` (dependency)
> - `audio-ident-service/app/db/session.py` (dependency)
> - `docs/plans/01-initial-implementation/04-phase-search-lanes.md` (plan)

---

## Executive Summary

**Overall Confidence: MEDIUM-HIGH**

The Phase 4 implementation is fundamentally sound. Both search lanes are independently testable, the algorithm implementations match the plan, and test coverage is comprehensive for the happy paths. However, there are **2 blocking issues** that must be addressed before Phase 5, **5 important issues** that should be addressed, and **7 minor concerns** for code quality. The most critical finding is a deprecated API usage (`asyncio.get_event_loop()`) that will fail under certain runtime conditions, and a significant architectural deviation where the exact lane creates its own database sessions internally rather than accepting them via dependency injection.

---

## Blocking Issues

### B1. `asyncio.get_event_loop()` is deprecated and will fail in certain contexts

**File**: `audio-ident-service/app/search/vibe.py`, line 88
**Severity**: BLOCKING

```python
loop = asyncio.get_event_loop()
async with _clap_semaphore:
    embedding = await loop.run_in_executor(
        None,
        generate_embedding,
        audio,
        clap_model,
        clap_processor,
    )
```

`asyncio.get_event_loop()` has been deprecated since Python 3.10 and emits a `DeprecationWarning` when there is no running event loop. More critically, in Python 3.12+ (which this project uses per `.tool-versions`), calling `asyncio.get_event_loop()` when there is no running loop raises a `DeprecationWarning` and may eventually raise a `RuntimeError` in future Python versions. The correct replacement is `asyncio.get_running_loop()`, which is safe in async contexts and raises `RuntimeError` immediately if no loop is running (fail-fast is better than silent misbehavior).

**Impact**: This code is called inside an `async def`, so a running loop always exists and the current behavior works today. But it relies on deprecated behavior, and CI/CD may flag the deprecation warning as a test failure if warnings are configured as errors. More importantly, in Phase 5 when the orchestrator runs both lanes concurrently via `asyncio.gather`, this must be robust.

**Fix**:
```python
loop = asyncio.get_running_loop()
```

This is a one-line change but qualifies as blocking because it is deprecated API usage in the critical search path of a Python 3.12 project.

---

### B2. Exact lane creates its own database sessions internally -- inconsistent with vibe lane pattern and breaks transactional boundaries

**File**: `audio-ident-service/app/search/exact.py`, lines 447-448
**Severity**: BLOCKING

The exact lane uses `async_session_factory()` internally:

```python
async def _enrich_with_metadata(
    candidates: list[_ScoredCandidate],
) -> list[ExactMatch]:
    # ...
    async with async_session_factory() as session:
        tracks_by_id = await get_tracks_by_ids(session, track_ids)
```

Meanwhile, the vibe lane receives the session as a parameter:

```python
async def run_vibe_lane(
    pcm_48k: bytes,
    max_results: int,
    *,
    qdrant_client: AsyncQdrantClient,
    clap_model: object,
    clap_processor: object,
    session: AsyncSession,  # <-- injected
    exact_match_track_id: uuid.UUID | None = None,
) -> list[VibeMatch]:
```

This is a significant architectural inconsistency:

1. **Phase 5 problem**: The orchestrator will need to manage database sessions. If the exact lane creates its own session, the orchestrator cannot wrap both lanes in a single transactional context or session scope. The two lanes will use different sessions, potentially seeing different database states.

2. **Testability**: The exact lane tests must mock `async_session_factory` at the module level (which they do), but this is more fragile than injecting a session. The vibe lane tests simply pass a mock session directly, which is cleaner.

3. **Connection pool pressure**: Each `run_exact_lane` call opens a new session from the pool. Under concurrent searches, this doubles the session count compared to sharing sessions between lanes.

4. **Inconsistent API surface**: One lane takes infrastructure dependencies as parameters; the other hides them. The Phase 5 orchestrator will have asymmetric calling conventions.

**Fix**: Refactor `run_exact_lane()` to accept `session: AsyncSession` as a parameter, matching the vibe lane pattern. Move session creation to the caller (Phase 5 orchestrator or router endpoint).

---

## Important Issues

### I1. `exact_match_track_id` remains in `run_vibe_lane` despite plan reviewer recommendation to move it to orchestrator

**File**: `audio-ident-service/app/search/vibe.py`, line 44
**Severity**: IMPORTANT

The Phase 4 plan's devil's advocate review (item #4) explicitly recommended:

> "Make exclusion a post-processing step in the orchestrator, not a vibe lane parameter."

The rationale was that this parameter creates an implicit dependency on Phase 4a -- the orchestrator must run the exact lane first (or wait for its result) before calling the vibe lane if exclusion is desired. This contradicts the parallel execution model.

The implementation kept `exact_match_track_id` in both `run_vibe_lane()` and `aggregate_chunk_hits()`. The docstring on line 61 attempts to justify this:

> "This parameter is optional (None = no exclusion) so Phase 4b is independently testable without Phase 4a."

While making it optional does preserve independence for testing, it does NOT resolve the Phase 5 parallelism concern. In Phase 5, the orchestrator has two choices:
- Run exact lane first, then pass the result to vibe lane (sequential, slower)
- Run both in parallel with `exact_match_track_id=None`, then post-filter (parallel, faster)

The parameter's existence in the vibe lane API encourages the first (slower) pattern. Moving exclusion to the orchestrator would make the parallel pattern the natural default.

**Recommendation**: Move `exact_match_track_id` to the Phase 5 orchestrator. The aggregation function can still accept it for unit testing purposes, but the public `run_vibe_lane` API should not include it.

---

### I2. CLAP semaphore is module-level and not configurable -- problematic for testing and production tuning

**File**: `audio-ident-service/app/search/vibe.py`, line 33
**Severity**: IMPORTANT

```python
_clap_semaphore = asyncio.Semaphore(1)
```

Issues:

1. **Module-level `asyncio.Semaphore` binds to the event loop at creation time.** In Python 3.10+, creating a `Semaphore` outside an async context does NOT bind it to any event loop (the loop binding was removed in 3.10). This is actually fine in modern Python, but it means the semaphore is a global singleton shared across all event loops, which could be surprising if tests use different event loops.

2. **Not configurable**: The semaphore count (1) is hardcoded. In production, with a multi-core machine, allowing 2 concurrent CLAP inferences might be acceptable if the CPU budget permits. This should be configurable via `settings.py`.

3. **Test interference**: The module-level semaphore persists across test runs. If a test acquires the semaphore and fails before releasing it (via exception in `run_in_executor`), subsequent tests in the same process could deadlock. While `async with` should handle this via `__aexit__`, the exception path through `run_in_executor` deserves explicit testing.

**Recommendation**: Make the semaphore concurrency configurable via `settings.clap_inference_concurrency: int = 1`. Consider creating the semaphore lazily or injecting it.

---

### I3. No error handling for `olaf_query` failures in the exact lane

**File**: `audio-ident-service/app/search/exact.py`, lines 155, 170
**Severity**: IMPORTANT

The exact lane calls `await olaf_query(window_pcm)` and `await olaf_query(pcm_16k)` without any try/except:

```python
matches = await olaf_query(window_pcm)  # line 155
```

Looking at `app/audio/fingerprint.py`, `olaf_query()` raises `OlafError` if:
- The olaf_c binary is not found (`FileNotFoundError` -> `OlafError`)
- An unexpected exception occurs (catches `Exception` -> `OlafError`)
- If the subprocess exits non-zero, it returns `[]` (graceful)

However, `OlafError` can still propagate up from `olaf_query` in case of binary-not-found or crash. The exact lane does not catch this, meaning an Olaf crash during the sub-window strategy would abort the entire search with an unhandled exception rather than returning empty results gracefully.

The vibe lane handles Qdrant errors gracefully (returns `[]`), but the exact lane does not follow the same pattern for Olaf errors.

**Contrast with plan**: The plan's edge case #1 says "No match found: Return empty `exact_matches[]`", but `OlafError` would produce a 500 error, not empty results.

**Fix**: Wrap `olaf_query` calls in try/except `OlafError`, log the error, and return `[]`:

```python
try:
    matches = await olaf_query(window_pcm)
except OlafError:
    logger.exception("Olaf query failed for sub-window")
    matches = []
```

---

### I4. Sub-window offset reconciliation does not account for window start position

**File**: `audio-ident-service/app/search/exact.py`, lines 243-248
**Severity**: IMPORTANT

```python
reconciled_offsets: list[float] = []
for _window_idx, match in window_matches:
    # The reference_start is the offset in the original track.
    # Each window queries a different slice of the clip, but the
    # reference_start already reflects the position in the indexed track.
    reconciled_offsets.append(match.reference_start)
```

The comment claims `reference_start` already reflects the position in the indexed track, which is true -- Olaf's `reference_start` is the absolute position in the reference track where the match begins.

However, there is a subtlety: when querying sub-windows, each window represents a different slice of the query clip. If Window 1 (0.0-3.5s of clip) matches at `reference_start=10.0s` and Window 2 (0.75-4.25s of clip) matches at `reference_start=10.75s`, these actually refer to the SAME position in the original track (the 0.75s offset between windows is reflected in the reference offset).

The current implementation takes the median of `[10.0, 10.75]` = `10.375`, which is reasonable but not perfectly accurate. The true track position would be `10.0` (from Window 1's perspective) or equivalently `10.75 - 0.75 = 10.0` (from Window 2's perspective after adjusting for window start).

For a proper reconciliation, each `reference_start` should be adjusted by subtracting the window's start offset:

```python
adjusted_offset = match.reference_start - window_start_sec
```

Then take the median of the adjusted offsets. This would produce more accurate offset estimation.

**Impact**: Offset could be off by up to `SUB_WINDOW_HOP_SEC` (0.75s). The plan's acceptance criteria require "Offset estimation within 0.5s of true offset." The current implementation may exceed this tolerance with sub-window queries.

**Fix**: Track the window start time and subtract it when reconciling offsets.

---

### I5. Tests for exact lane async methods lack `@pytest.mark.asyncio` markers

**File**: `audio-ident-service/tests/test_search_exact.py`, all `TestRunExactLane*` classes
**Severity**: IMPORTANT

None of the async test methods in `test_search_exact.py` have `@pytest.mark.asyncio` decorators. The project has `asyncio_mode = "auto"` in `pyproject.toml`, which means pytest-asyncio will automatically detect async test functions and run them in an event loop.

However, there is a subtlety: with `asyncio_mode = "auto"`, the test methods are in **class-based** test classes (e.g., `class TestRunExactLaneKnownMatch`). The `auto` mode should handle these correctly in modern pytest-asyncio (>=0.21), but older versions may silently skip class-based async tests without the marker.

**Contrast with vibe lane tests**: `test_search_vibe.py` consistently uses `@pytest.mark.asyncio` on all async test methods, following the explicit best practice.

**Risk**: If `asyncio_mode` changes to `"strict"` in the future (which is the recommended mode for new projects), all exact lane integration tests will silently fail to run.

**Fix**: Add `@pytest.mark.asyncio` to all async test methods in `test_search_exact.py` for consistency and future-proofing.

---

## Minor Issues

### M1. `__init__.py` does not re-export public API functions

**File**: `audio-ident-service/app/search/__init__.py`
**Severity**: MINOR

The `__init__.py` only contains a docstring:

```python
"""Search lane modules for audio identification.

Contains:
- aggregation: Chunk-to-track score aggregation (Top-K Average with Diversity Bonus)
- vibe: Vibe search lane (CLAP embedding + Qdrant similarity search)
"""
```

It does not mention the exact lane, and does not re-export any public functions. Phase 5's orchestrator will need to import `run_exact_lane` and `run_vibe_lane`. The docstring should be updated to include the exact lane, and ideally the `__init__.py` should re-export the public API:

```python
from app.search.exact import run_exact_lane
from app.search.vibe import run_vibe_lane
from app.search.aggregation import aggregate_chunk_hits
```

---

### M2. Duplicate `get_tracks_by_ids` implementations

**Files**: `audio-ident-service/app/search/exact.py` line 387, `audio-ident-service/app/search/vibe.py` line 221
**Severity**: MINOR

Both lanes implement their own `get_tracks_by_ids` / `_get_tracks_by_ids` function with identical logic:

```python
# exact.py
async def get_tracks_by_ids(session, track_ids):
    stmt = select(Track).where(Track.id.in_(track_ids))
    result = await session.execute(stmt)
    return {t.id: t for t in result.scalars().all()}

# vibe.py (identical logic, different name prefix)
async def _get_tracks_by_ids(session, track_ids):
    stmt = select(Track).where(Track.id.in_(track_ids))
    result = await session.execute(stmt)
    return {t.id: t for t in result.scalars().all()}
```

This violates DRY. Extract to a shared utility, perhaps `app/db/queries.py` or `app/models/track.py`.

---

### M3. `_ScoredCandidate` uses `__slots__` but is not a dataclass

**File**: `audio-ident-service/app/search/exact.py`, line 179
**Severity**: MINOR

```python
class _ScoredCandidate:
    __slots__ = ("aligned_hashes", "confidence", "offset_seconds", "track_uuid")
```

The vibe lane's `ChunkHit` and `TrackResult` use `@dataclass(frozen=True)` for clarity. The exact lane uses a manual `__init__` with `__slots__`. While `__slots__` is slightly more memory-efficient, the inconsistency in style between the two lanes reduces readability. Since these are short-lived internal objects (not serialized, not stored), the memory benefit is negligible.

---

### M4. `_track_to_info` helper duplicated vs. inline

**File**: `audio-ident-service/app/search/exact.py` line 408 vs. `audio-ident-service/app/search/vibe.py` line 148
**Severity**: MINOR

The exact lane extracts `_track_to_info()` as a named helper function. The vibe lane constructs `TrackInfo` inline. Both approaches work, but the exact lane's extracted helper is more reusable. Should be consolidated into a shared utility.

---

### M5. `clap_model: object` and `clap_processor: object` type annotations are too permissive

**File**: `audio-ident-service/app/search/vibe.py`, lines 41-42
**Severity**: MINOR

```python
clap_model: object,
clap_processor: object,
```

Using `object` as the type annotation provides no IDE assistance and no type safety. While avoiding importing HuggingFace Transformers types at module level is a reasonable decision (to avoid loading the ML stack on import), a better approach would be `Any` with a docstring comment, or a `Protocol` that describes the expected interface:

```python
from typing import Any
clap_model: Any,  # ClapModel from transformers
clap_processor: Any,  # ClapProcessor from transformers
```

Or even better, use `TYPE_CHECKING`:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from transformers import ClapModel, ClapProcessor
```

---

### M6. Magic number `hnsw_ef=128` in Qdrant query

**File**: `audio-ident-service/app/search/vibe.py`, line 184
**Severity**: MINOR

```python
search_params=models.SearchParams(hnsw_ef=128),
```

The `hnsw_ef` search parameter is hardcoded at 128. This should be configurable via settings, as the optimal value depends on the index size and accuracy/speed tradeoff. For small collections (<10K points), `hnsw_ef=64` is sufficient. For production with millions of points, `hnsw_ef=256` may be needed.

---

### M7. Similarity capping in vibe lane may mask legitimate aggregation bugs

**File**: `audio-ident-service/app/search/vibe.py`, line 157
**Severity**: MINOR

```python
similarity=min(result.final_score, 1.0),
```

The `final_score` from aggregation is `base_score + diversity_bonus`. Since `base_score` comes from cosine similarity (which is already in [0, 1] for normalized vectors), and `diversity_bonus` can add up to 0.05, the `final_score` can exceed 1.0 (max possible: 1.05).

Capping at 1.0 is correct for the API response (the `VibeMatch.similarity` field has `Field(ge=0.0, le=1.0)`), but silently capping means a track with `base_score=0.96` and a track with `base_score=1.0` will both appear as `similarity=1.0` to the consumer. This could be confusing.

Consider documenting that `similarity` is capped, or adjusting the aggregation formula so `final_score` always stays in [0, 1]:

```python
final_score = base_score * (1.0 - diversity_weight) + diversity_bonus_normalized * diversity_weight
```

---

## Test Coverage Assessment

### Phase 4a: Exact Lane Tests (48 tests -- claimed, actual count: 22 test methods)

**Coverage Strengths**:
- PCM utility functions (`_pcm_duration_sec`, `_extract_pcm_window`) are well tested with edge cases
- Confidence normalization has thorough boundary testing (0, negative, below threshold, at threshold, above threshold, range check)
- Full-clip vs. sub-window mode switching tested with 4s, 5s, and 6s clips
- Consensus scoring tested for 2-window agreement, 3-window agreement, single-window penalty, mixed tracks, empty windows
- Integration tests cover known match, non-match, sorting, offset accuracy, threshold filtering, max_results, missing database tracks

**Coverage Gaps**:
1. **No test for `OlafError` propagation** -- what happens when `olaf_query` raises `OlafError` (not just returns `[]`)? Currently this would crash the search. (Related to issue I3.)
2. **No test for concurrent `run_exact_lane` calls** -- plan's edge case #3 mentions concurrent search requests.
3. **No test for clips >30s** -- plan says "Very long clip (>30s): Truncate to 30s before fingerprinting." The implementation does NOT truncate. This is either a missing feature or an intentional decision to let the upstream handle it, but there is no test verifying behavior.
4. **No test for `_consensus_score` with offset tolerance** -- the `OFFSET_TOLERANCE_SEC = 1.0` constant is defined but never used in the consensus scoring logic. This appears to be dead code.

### Phase 4b: Vibe Lane Tests (30 tests -- claimed, actual count: 22 test methods)

**Coverage Strengths**:
- Aggregation math tested thoroughly (Top-K average, diversity bonus, final score composition, capping, custom K values)
- Exact-match exclusion tested in both aggregation and full lane
- Error conditions: Qdrant failure, empty collection, missing model, empty audio
- Edge cases: stale Qdrant data (track missing from PostgreSQL), similarity capping, below-threshold filtering
- Qdrant result parsing: missing track_id, invalid UUID, empty payloads

**Coverage Gaps**:
1. **No test for CLAP inference exception** -- what happens if `generate_embedding` raises `EmbeddingError`? The vibe lane does not catch this, so it would propagate as a 500 error.
2. **No test for semaphore behavior** -- the `_clap_semaphore` is a core concurrency feature but is not tested. A test verifying that concurrent calls are serialized would validate the semaphore works correctly.
3. **No test for `run_in_executor` behavior** -- the tests mock `generate_embedding` at the function level, bypassing the executor. There is no integration test verifying the executor wrapping works correctly.
4. **Aggregation does not test negative scores** -- Qdrant cosine similarity is in [-1, 1] for non-normalized vectors, but the tests only use positive scores. If a negative score enters, the Top-K average could produce nonsensical results.

### Test Quality Assessment

**Mocking approach**: The exact lane tests use `patch("app.search.exact.olaf_query")` and `patch("app.search.exact.async_session_factory")`, which tests the integration correctly. The vibe lane tests inject mocks directly via function parameters, which is cleaner. Both approaches are functional.

**Realism of mocks**: The mock data is realistic -- UUIDs, OlafMatch with plausible field values, Qdrant ScoredPoints with proper payloads. The PCM data generation is appropriate (silent floats for exact lane, sine wave for vibe lane).

**Missing test category**: Neither test file includes **performance regression tests** or **property-based tests**. While not strictly necessary, a property-based test for `_normalize_confidence` (output always in [0, 1]) and `aggregate_chunk_hits` (output sorted, no excluded tracks) would be valuable.

---

## Phase 5 Readiness Assessment

### Can both lanes run in parallel via `asyncio.gather`?

**Partially ready, with caveats.**

1. **Olaf**: Uses `asyncio.create_subprocess_exec` (non-blocking). Multiple concurrent queries are safe (LMDB supports multi-reader).

2. **CLAP**: Uses `run_in_executor` with semaphore (non-blocking to the event loop, serialized for CPU). Correct.

3. **Qdrant**: Uses `AsyncQdrantClient` (non-blocking). Correct.

4. **Database sessions**: This is the problem. The exact lane creates its own session internally, while the vibe lane receives one via injection. The orchestrator cannot share a session between them or manage the session lifecycle. **See issue B2.**

5. **Error isolation**: The vibe lane handles Qdrant errors gracefully. The exact lane does NOT handle Olaf errors gracefully. **See issue I3.** If used with `asyncio.gather(return_exceptions=True)`, the exact lane error would be an exception in the results tuple, which the orchestrator must handle.

### Are they truly independent?

**Yes, with one implicit coupling.** The `exact_match_track_id` parameter is the only coupling point. If the orchestrator runs both lanes in parallel, it passes `None` for this parameter. If it wants exclusion, it must run exact first. The independence is preserved at the code level but the API design nudges toward sequential execution. **See issue I1.**

### Will the orchestrator be able to combine results cleanly?

**Yes.** Both lanes return Pydantic models (`ExactMatch`, `VibeMatch`) that share `TrackInfo` as a common sub-schema. The `SearchResponse` schema already defines `exact_matches: list[ExactMatch]` and `vibe_matches: list[VibeMatch]`. The orchestrator can simply assign the results from each lane to the response.

---

## Devil's Advocate Findings Resolution Status

The plan included a devil's advocate review with 6 recommendations. Here is the resolution status:

### 1. Add `run_in_executor` wrapper for all Olaf CFFI calls -- RESOLVED (differently)

**Status**: RESOLVED (architectural divergence)

The plan assumed Olaf would be wrapped via CFFI. The actual implementation uses a CLI subprocess (`asyncio.create_subprocess_exec`), which is inherently non-blocking. The docstring in `exact.py` lines 7-9 explicitly acknowledges this:

> "Olaf is wrapped as a CLI subprocess (NOT CFFI), so there are no GIL blocking concerns."

This is a valid architectural decision that actually eliminates the GIL blocking concern entirely. The subprocess approach has tradeoffs (higher per-query overhead from process spawn + temp file I/O) but is simpler and safer. The `run_in_executor` wrapper is not needed for the subprocess approach.

**Verdict**: Correctly resolved via different architecture. No action needed.

### 2. Move `exact_match_track_id` exclusion to the orchestrator -- NOT RESOLVED

**Status**: NOT RESOLVED

The parameter remains in `run_vibe_lane()` and `aggregate_chunk_hits()`. See issue I1 above.

### 3. Make `VIBE_MATCH_THRESHOLD` configurable via settings -- RESOLVED

**Status**: RESOLVED

`settings.py` line 47: `vibe_match_threshold: float = 0.60`

The threshold is configurable via environment variable `VIBE_MATCH_THRESHOLD`. The default remains 0.60 (which is still not empirically validated -- the plan reviewer noted this -- but configurability is achieved).

### 4. Add CLAP inference semaphore -- RESOLVED

**Status**: RESOLVED

`vibe.py` line 33: `_clap_semaphore = asyncio.Semaphore(1)`

The semaphore is implemented with a concurrency of 1. See issue I2 for minor concerns about configurability and module-level binding.

### 5. Test sub-window parameters (3.5s vs 4s windows) -- NOT RESOLVED

**Status**: NOT RESOLVED (and possibly not applicable at this phase)

The implementation uses the plan's 3.5s windows with 0.75s hops. The plan reviewer suggested testing both 3.5s and 4.0s window sizes during Phase 1 prototype evaluation. There is no evidence this comparison was done. The window parameters are hardcoded constants, not configurable.

This is not blocking for Phase 5, but it means the sub-window strategy's parameters are unvalidated. If the window is too short for Olaf to generate enough hashes, the sub-window strategy may perform worse than a single full-clip query for short clips.

### 6. Add empty-index test cases for both lanes -- RESOLVED

**Status**: RESOLVED

- `test_search_exact.py`: `TestRunExactLaneEmptyIndex` class with `test_empty_index_returns_empty` and `test_empty_pcm_returns_empty`
- `test_search_vibe.py`: `test_empty_qdrant_collection_returns_empty` and `test_empty_audio_returns_empty`

Both lanes gracefully return empty results when the backing index is empty.

---

## Additional Findings

### A1. `OFFSET_TOLERANCE_SEC` is defined but never used (dead code)

**File**: `audio-ident-service/app/search/exact.py`, line 56

```python
OFFSET_TOLERANCE_SEC = 1.0
"""Tolerance for considering two offsets as matching the same position."""
```

This constant is defined but never referenced in any function. The consensus scoring algorithm groups by track UUID across windows but does NOT verify that the offsets from different windows are within tolerance of each other. A track that appears in multiple windows with wildly different offsets (e.g., one window matches at 10s and another at 200s) would still be treated as a consensus match, which could be a false positive.

The plan states: "Consensus scoring: 2+ windows -> same (track_id, offset +/- tolerance) -> HIGH confidence". The offset tolerance check is missing from the implementation.

**Severity**: IMPORTANT (edge case that could produce false positives in consensus scoring)

### A2. `qdrant_search_limit` is configurable but not documented in `.env.example`

**File**: `audio-ident-service/app/settings.py`, line 48

The `qdrant_search_limit: int = 50` setting is configurable but should be documented alongside `vibe_match_threshold` in the project's configuration documentation.

### A3. The exact lane does not truncate clips >30s

The plan's edge case #4 states: "Very long clip (>30s): Truncate to 30s before fingerprinting (per ingestion constraints)." The implementation does not perform any truncation. It queries the full clip regardless of duration. If a 5-minute audio clip is submitted, Olaf will process all of it, potentially exceeding the 500ms latency budget.

This truncation may be the responsibility of the upstream (API router / audio decode layer), but the exact lane should either:
- Document that it expects pre-truncated input, or
- Implement truncation at 30s as specified in the plan

---

## Recommendations

### Priority 1 (Must fix before Phase 5)

1. **Replace `asyncio.get_event_loop()` with `asyncio.get_running_loop()`** in `vibe.py` line 88. (Issue B1)

2. **Refactor `run_exact_lane()` to accept `session: AsyncSession`** as a parameter instead of creating sessions internally. This aligns the API surface with the vibe lane and prepares for proper session management in the Phase 5 orchestrator. (Issue B2)

### Priority 2 (Should fix before Phase 5)

3. **Add try/except for `OlafError`** in `_query_with_subwindows` and `_query_full_clip` to match the vibe lane's graceful degradation pattern. (Issue I3)

4. **Fix sub-window offset reconciliation** to subtract window start time before taking the median. (Issue I4)

5. **Implement offset tolerance check** in `_consensus_score` using the defined `OFFSET_TOLERANCE_SEC` constant, or remove the constant if it is not needed. (Finding A1)

6. **Add `@pytest.mark.asyncio`** to all async test methods in `test_search_exact.py`. (Issue I5)

### Priority 3 (Nice to have)

7. Extract shared `get_tracks_by_ids` and `_track_to_info` to a common module. (Issues M2, M4)
8. Update `__init__.py` to document and re-export the exact lane. (Issue M1)
9. Make `hnsw_ef` configurable via settings. (Issue M6)
10. Move `exact_match_track_id` out of `run_vibe_lane` to the orchestrator layer. (Issue I1)
11. Make CLAP semaphore concurrency configurable. (Issue I2)

---

## Summary

| Category | Count | Details |
|----------|-------|---------|
| BLOCKING | 2 | Deprecated asyncio API (B1), inconsistent session management (B2) |
| IMPORTANT | 5+1 | exact_match_track_id placement (I1), semaphore config (I2), Olaf error handling (I3), offset reconciliation (I4), missing pytest markers (I5), dead OFFSET_TOLERANCE_SEC code (A1) |
| MINOR | 7 | __init__.py (M1), DRY violations (M2, M4), style inconsistency (M3), type annotations (M5), magic number (M6), similarity capping (M7) |
| Test methods | 44 | 22 exact + 22 vibe (actual count, vs. 48+30 claimed) |
| Plan compliance | 4/6 | 4 of 6 reviewer recommendations resolved |
| Phase 5 ready | Partial | Needs B1, B2, I3 fixes minimum |
