# Phase 5: Orchestration (~2-3 days)

> **Depends on**: Phase 4a (Exact ID Lane) + Phase 4b (Vibe Lane) — both must work independently
> **Blocks**: Phase 6 (Frontend)
> **Goal**: Working `POST /api/v1/search` endpoint with parallel lanes, timeouts, and error isolation

---

## Overview

This phase wires both search lanes into a single endpoint that:
1. Accepts multipart audio uploads
2. Decodes audio to dual-rate PCM
3. Runs both lanes in parallel via `asyncio.gather`
4. Handles per-lane timeouts and failures gracefully
5. Returns a combined `SearchResponse`

**Corresponds to**: 06-implementation-plan.md Milestone 6

---

## Step 1: Unified Search Endpoint (~8 hours)

**Reference**: 04-architecture-and-api.md §4.1-4.3

### 1.1 Create Search Router

**File**: `audio-ident-service/app/routers/search.py` (NEW)

```python
from fastapi import APIRouter, File, Form, UploadFile, HTTPException

from app.schemas.search import SearchMode, SearchResponse

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search_audio(
    audio: UploadFile = File(
        ...,
        description="Audio file (WebM/Opus, MP3, MP4/AAC, WAV). Max 10 MB.",
    ),
    mode: SearchMode = Form(default=SearchMode.BOTH),
    max_results: int = Form(default=10, ge=1, le=50),
) -> SearchResponse:
    """
    Search for audio matches using fingerprint (exact) and/or
    embedding (vibe) similarity.

    Accepts multipart/form-data with:
    - `audio`: The audio file to search for
    - `mode`: Search mode ("exact", "vibe", or "both")
    - `max_results`: Max results per lane (1-50, default 10)
    """
    # 1. Validate upload (size, content type)
    content = await validate_upload(audio)

    # 2. Decode to dual-rate PCM
    pcm_16k, pcm_48k = await decode_dual_rate(content, format_hint)

    # 3. Validate duration
    duration = pcm_duration_seconds(pcm_16k, sample_rate=16000)
    if duration < MIN_QUERY_DURATION:
        raise HTTPException(status_code=400, detail={"error": {"code": "AUDIO_TOO_SHORT", ...}})

    # 4. Orchestrate search
    response = await orchestrate_search(pcm_16k, pcm_48k, mode, max_results)
    return response
```

### 1.2 Upload Validation

**Reference**: 04-architecture-and-api.md §4.3

```python
import magic  # python-magic for content type detection

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_CONTENT_TYPES = {
    "audio/webm", "audio/ogg", "audio/mpeg",
    "audio/mp4", "audio/wav", "audio/x-wav",
}

async def validate_upload(audio: UploadFile) -> bytes:
    content = await audio.read()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "FILE_TOO_LARGE", "message": f"Max size is 10 MB"}}
        )

    # Verify content type via magic bytes (not just Content-Type header)
    detected_type = magic.from_buffer(content, mime=True)
    if detected_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "UNSUPPORTED_FORMAT", "message": f"Unsupported: {detected_type}"}}
        )

    return content
```

### 1.3 Format Detection for ffmpeg

Detect the container format from magic bytes before passing to ffmpeg:

```python
def detect_audio_format(content: bytes) -> str | None:
    """Detect audio container format for ffmpeg -f flag."""
    detected = magic.from_buffer(content, mime=True)
    format_map = {
        "audio/webm": "webm",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "mp4",
        "audio/wav": "wav",
        "audio/x-wav": "wav",
    }
    return format_map.get(detected)
```

This is important for piping to ffmpeg via stdin (per 08-devils-advocate-review.md §9: "ffmpeg may struggle with container format detection without the `-f` flag").

### 1.4 Register Router

**File**: `audio-ident-service/app/main.py`

Add to `create_app()`:
```python
from app.routers import search
application.include_router(search.router)
```

### 1.5 Add python-magic Dependency

```bash
cd audio-ident-service && uv add python-magic
# System dependency: brew install libmagic (macOS) / apt install libmagic1 (Ubuntu)
```

### Acceptance Criteria
- [ ] `POST /api/v1/search` accepts multipart form upload
- [ ] Invalid format returns 400 with UNSUPPORTED_FORMAT
- [ ] Too-large file returns 400 with FILE_TOO_LARGE
- [ ] Too-short audio returns 400 with AUDIO_TOO_SHORT
- [ ] Format detection correctly identifies WebM, MP3, MP4, WAV

---

## Step 2: Parallel Lane Execution (~8 hours)

**Reference**: 04-architecture-and-api.md §4.2

### 2.1 Orchestration Logic

**File**: `audio-ident-service/app/search/orchestrator.py` (NEW)

```python
import asyncio
import time
import uuid

from app.schemas.search import ExactMatch, SearchMode, SearchResponse, VibeMatch
from app.search.exact import run_exact_lane
from app.search.vibe import run_vibe_lane

# Timeout budget: total p95 target is <5s end-to-end.
# Budget: preprocessing (ffmpeg decode) ~1s + max(exact, vibe) <=4s = 5s total.
# Lanes run in parallel, so the per-lane timeouts must fit within 4s.
EXACT_TIMEOUT_SECONDS = 3.0   # Olaf LMDB lookup is fast (<500ms typical)
VIBE_TIMEOUT_SECONDS = 4.0    # CLAP inference (~1-3s) + Qdrant query (~200ms)
TOTAL_REQUEST_TIMEOUT = 5.0   # Hard cap on total request time (including preprocessing)


async def orchestrate_search(
    pcm_16k: bytes,
    pcm_48k: bytes,
    mode: SearchMode,
    max_results: int,
) -> SearchResponse:
    request_id = uuid.uuid4()
    t0 = time.perf_counter()

    exact_matches: list[ExactMatch] = []
    vibe_matches: list[VibeMatch] = []

    if mode == SearchMode.EXACT:
        exact_matches = await asyncio.wait_for(
            run_exact_lane(pcm_16k, max_results),
            timeout=EXACT_TIMEOUT_SECONDS,
        )
    elif mode == SearchMode.VIBE:
        vibe_matches = await asyncio.wait_for(
            run_vibe_lane(pcm_48k, max_results),
            timeout=VIBE_TIMEOUT_SECONDS,
        )
    else:  # BOTH
        exact_task = asyncio.create_task(
            asyncio.wait_for(
                run_exact_lane(pcm_16k, max_results),
                timeout=EXACT_TIMEOUT_SECONDS,
            )
        )
        vibe_task = asyncio.create_task(
            asyncio.wait_for(
                run_vibe_lane(pcm_48k, max_results),
                timeout=VIBE_TIMEOUT_SECONDS,
            )
        )

        # return_exceptions=True: one lane failing doesn't kill the other
        results = await asyncio.gather(exact_task, vibe_task, return_exceptions=True)

        if not isinstance(results[0], BaseException):
            exact_matches = results[0]
        if not isinstance(results[1], BaseException):
            vibe_matches = results[1]

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return SearchResponse(
        request_id=request_id,
        query_duration_ms=round(elapsed_ms, 2),
        exact_matches=exact_matches,
        vibe_matches=vibe_matches,
        mode_used=mode,
    )
```

### 2.2 Latency Budget Breakdown

**Reference**: 04-architecture-and-api.md §4.2

| Step | Expected Latency | Timeout Cap | Notes |
|------|-----------------|-------------|-------|
| Upload receive + read | ~50ms | — | 80KB at 128kbps WebM |
| Content type validation | ~1ms | — | magic bytes check |
| ffmpeg decode (dual rate) | ~200ms | **1s** | Two parallel ffmpeg subprocesses |
| Olaf fingerprint extraction + query | ~100-300ms | **3s** (exact lane timeout) | LMDB lookup is fast; run_in_executor for CFFI |
| CLAP embedding inference | ~1-3s (CPU) | **4s** (vibe lane timeout) | The bottleneck |
| Qdrant nearest neighbor query | ~50-200ms | (included in vibe timeout) | HNSW with ef=128 |
| Chunk aggregation + DB lookup | ~50ms | (included in vibe timeout) | In-memory + single SQL query |
| **Total (BOTH mode)** | **~1.5-4s** | **5s hard cap** | preprocessing (1s) + max(exact, vibe) (4s) |

**Timeout budget**: Total request must complete within **5s** (p95 target).
- Preprocessing (ffmpeg decode): 1s max
- Exact lane: 3s timeout (Olaf is fast, typically <500ms)
- Vibe lane: 4s timeout (CLAP inference + Qdrant search)
- Lanes run in parallel, so total = preprocessing + max(exact_timeout, vibe_timeout) = 1s + 4s = 5s

### Acceptance Criteria
- [ ] `mode=exact` runs only the fingerprint lane
- [ ] `mode=vibe` runs only the embedding lane
- [ ] `mode=both` runs both lanes in parallel
- [ ] If one lane times out, the other lane's results are still returned
- [ ] If one lane throws an exception, the other lane's results are still returned
- [ ] `query_duration_ms` accurately reflects wall-clock time

---

## Step 3: CLAP Model Lifecycle (~4 hours)

**Reference**: 00-reconciliation-summary.md §8h, 04-architecture-and-api.md (lifespan handler)

### 3.1 Pre-load in Lifespan Handler

Update the lifespan handler from Phase 2 to include CLAP model loading:

**File**: `audio-ident-service/app/main.py` (modify lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 1. Check Postgres (existing)
    # 2. Check Qdrant (existing)

    # 3. Pre-load CLAP model
    import time as _time
    t_model = _time.perf_counter()
    logger.info("Loading CLAP embedding model...")

    from app.audio.embedding import load_clap_model
    clap_model = load_clap_model()
    app.state.clap_model = clap_model

    load_time = _time.perf_counter() - t_model
    logger.info(f"CLAP model loaded in {load_time:.1f}s")
    if load_time > 10:
        logger.warning(
            f"CLAP model load took {load_time:.1f}s — "
            "consider caching model weights in Docker image"
        )

    # 4. Warm-up inference (prevent cold-start latency on first request)
    import numpy as np
    warmup_audio = np.zeros(48000 * 5, dtype=np.float32)  # 5s silence
    _ = clap_model.get_audio_embedding_from_data(x=warmup_audio, use_tensor=False)
    logger.info("CLAP warm-up inference complete")

    yield

    # Shutdown
    await qdrant.close()
    await engine.dispose()
```

### 3.2 GPU Detection

```python
import torch

def get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"  # Apple Silicon
    return "cpu"
```

Log the device at startup so operators know what's being used.

### Acceptance Criteria
- [ ] CLAP model is loaded during startup (not on first request)
- [ ] Warm-up inference completes during startup
- [ ] GPU is used when available; CPU fallback is automatic
- [ ] Model load time is logged
- [ ] First search request does not incur cold-start penalty

---

## Step 4: Error Handling (~4 hours)

**Reference**: 04-architecture-and-api.md §4.1 (error models)

### Error Response Mapping

| Condition | Status Code | Error Code | Message |
|-----------|------------|------------|---------|
| Invalid audio format | 422 | UNSUPPORTED_FORMAT | "Unsupported audio format: {detected_type}" |
| File too large | 400 | FILE_TOO_LARGE | "Max upload size is 10 MB" |
| Audio too short | 400 | AUDIO_TOO_SHORT | "Audio too short: {duration}s (minimum 3s)" |
| Both lanes fail | 503 | SEARCH_UNAVAILABLE | "Search service temporarily unavailable. Please retry." |
| Both lanes timeout | 504 | SEARCH_TIMEOUT | "Search timed out. Please try with a shorter clip." |
| Partial results (one lane fails) | 200 | — | Return results from the working lane; empty array for the failed lane |
| ffmpeg decode error | 422 | DECODE_FAILED | "Unable to decode audio file. Please try a different format." |

### Partial Results Strategy

When `mode=both` and one lane fails:
- Return HTTP 200 (not 500/503)
- Populate the working lane's results normally
- Set the failed lane's array to `[]`
- The client determines presentation based on which array is populated

This is a **presentation concern**: the API always returns both arrays, even if one is empty. The frontend decides how to display.

### Acceptance Criteria
- [ ] Invalid format returns 422 with specific error code
- [ ] Both-lanes-fail returns 503 (not 500)
- [ ] One-lane-fail returns 200 with partial results
- [ ] Error responses match the `{"error": {"code": ..., "message": ...}}` convention

---

## Request Flow Diagram

```
Client
  │
  │  POST /api/v1/search
  │  Content-Type: multipart/form-data
  │  Body: audio file + mode + max_results
  │
  ▼
┌─────────────────────────────────────────────┐
│              Upload Validation               │
│  • Read bytes (<=10MB)                       │
│  • Magic bytes content type check            │
│  • Detect container format for ffmpeg        │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│         FFmpeg Dual-Rate Decode              │
│  • 16kHz mono f32le (→ Olaf)                │
│  • 48kHz mono f32le (→ CLAP)                │
│  • Both run in parallel (~200ms)            │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│          Duration Validation                 │
│  • Check 3s <= duration <= 30s              │
│  • Truncate if > 30s                        │
└──────────────────┬──────────────────────────┘
                   │
          mode == BOTH?
          ┌────────┴────────┐
          │ Yes             │ No
          ▼                 ▼
┌───────────────┐  ┌───────────────┐
│asyncio.gather │  │ Single lane   │
│               │  │ with timeout  │
│┌─────────────┐│  └───────┬───────┘
││ Exact Lane  ││          │
││ (16kHz PCM) ││          ▼
││ • Olaf hash ││     (results)
││ • LMDB query││
││ • Consensus ││
│└─────────────┘│
│               │
│┌─────────────┐│
││ Vibe Lane   ││
││ (48kHz PCM) ││
││ • CLAP embed││
││ • Qdrant ANN││
││ • Chunk agg ││
│└─────────────┘│
└───────┬───────┘
        │
        ▼
┌─────────────────────────────────────────────┐
│          Build SearchResponse                │
│  • request_id (UUID)                        │
│  • query_duration_ms                        │
│  • exact_matches[] (may be empty)           │
│  • vibe_matches[] (may be empty)            │
│  • mode_used                                │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
              HTTP 200 JSON
```

---

## Integration Tests

**File**: `audio-ident-service/tests/test_search_integration.py` (NEW)

Use `httpx.AsyncClient` with the FastAPI test client:

```python
import httpx
import pytest
from app.main import app

@pytest.fixture
async def client():
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield client

async def test_search_both_mode(client, sample_audio_file):
    with open(sample_audio_file, "rb") as f:
        response = await client.post(
            "/api/v1/search",
            files={"audio": ("test.mp3", f, "audio/mpeg")},
            data={"mode": "both", "max_results": "5"},
        )
    assert response.status_code == 200
    data = response.json()
    assert "request_id" in data
    assert "query_duration_ms" in data
    assert isinstance(data["exact_matches"], list)
    assert isinstance(data["vibe_matches"], list)
    assert data["mode_used"] == "both"
```

Test cases:
1. Upload MP3 → get results (both mode)
2. Upload WebM → get results (both mode)
3. Upload with mode=exact → only exact_matches populated
4. Upload with mode=vibe → only vibe_matches populated
5. Upload invalid format → 422
6. Upload too-large file → 400
7. Upload too-short audio → 400

---

## File Summary

| File | Purpose |
|------|---------|
| `app/routers/search.py` | Search endpoint (POST /api/v1/search) |
| `app/search/orchestrator.py` | Parallel lane execution + error handling |
| `tests/test_search_integration.py` | Integration tests |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| CLAP model load delays startup by >15s | Medium | Medium | Log warning; consider baking model into Docker image |
| python-magic requires libmagic system dep | Low | Medium | Document in CLAUDE.md; add to Dockerfile |
| asyncio.gather masks lane errors silently | Low | Medium | Log exceptions from return_exceptions; add metrics |
| First request after restart is slow (cold JIT) | Low | Low | Warm-up inference in lifespan |

## Rollback Procedures

```bash
# Remove search endpoint
rm audio-ident-service/app/routers/search.py
rm audio-ident-service/app/search/orchestrator.py
# Remove router registration from main.py (revert edit)
# Remove python-magic dependency
cd audio-ident-service && uv remove python-magic
```

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| Search router + upload validation | 8h |
| Orchestration logic (parallel lanes) | 8h |
| CLAP model lifecycle (lifespan) | 4h |
| Error handling + partial results | 4h |
| Integration tests | 4h |
| **Total** | **~28h (3.5 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM-HIGH** — The orchestration design is clean and the error handling strategy (partial results) is well-thought-out. The main concerns are around latency budget realism and the `asyncio.gather` + `return_exceptions` pattern's subtleties.

### Gaps Identified

1. **`asyncio.gather` with `return_exceptions=True` swallows `asyncio.CancelledError`.** If one lane times out via `asyncio.wait_for`, the timeout raises `asyncio.TimeoutError` — which is correctly caught. But if the *other* lane is still running when the timeout fires, `asyncio.gather` doesn't cancel it. The timed-out task raises immediately, but the surviving task continues until completion. This means the response returns after `max(timeout, other_lane_duration)`, not after the timeout. If the vibe lane takes 8s and the exact lane times out at 5s, the response still takes 8s. The plan should use `asyncio.wait` with `return_when=ALL_COMPLETED` and explicit cancellation, or accept that timeouts don't actually bound response time.

2. **~~RESOLVED~~ The `wait_for` + `create_task` + `gather` pattern has a subtle bug.** Fixed: Timeout values have been corrected to enforce the <5s end-to-end target. Exact lane timeout is now 3s, vibe lane timeout is now 4s, with a 5s total request hard cap. The total wall-clock time is `max(min(exact_time, 3), min(vibe_time, 4))` which fits within the 5s budget (including ~1s for preprocessing). The latency budget table has been updated to reflect these corrected values.

3. **`python-magic` dependency is a common source of installation issues.** There are two packages on PyPI: `python-magic` and `python-magic-bin`. The former requires `libmagic` system library; the latter bundles it. On macOS, `brew install libmagic` is needed. On CI, it may not be installed. The plan should specify which package to use and document the system dependency.

4. **Error response status codes are inconsistent.** Step 4 says "Invalid audio format" returns 422, but Step 1.2 returns 400 with `UNSUPPORTED_FORMAT`. The contract (Phase 2 Step 1.1) also says 400. Pick one status code and use it consistently — suggest 422 (Unprocessable Entity) since the server understood the request but can't process the audio.

5. **No rate limiting or request queuing.** CLAP inference is CPU-bound and takes 1-3s. If 5 concurrent requests arrive, they all compete for CPU, and each takes 5-15s instead of 1-3s. The plan should either: (a) add an asyncio.Semaphore to limit concurrent CLAP inferences, or (b) document that the system is designed for single-user/low-concurrency use.

6. **Upload validation reads the entire file into memory.** `content = await audio.read()` loads up to 10MB into RAM per request. For concurrent requests, this is 10MB × N. FastAPI's `UploadFile` supports streaming via `read(size)` — consider reading in chunks for the size check, then seeking back for processing.

### Edge Cases Not Addressed

1. **Zero-byte upload.** If `content = await audio.read()` returns `b""`, `magic.from_buffer(b"", mime=True)` may return `"application/x-empty"` or raise an error. Handle explicitly.

2. **Exact lane returns results but vibe lane is empty (mode=both).** The response is valid (HTTP 200), but the frontend needs to handle this gracefully. The plan addresses this in Step 4 but the integration tests don't include this case.

3. **ffmpeg not installed.** If the host doesn't have ffmpeg in PATH, `decode_dual_rate` will fail with a cryptic subprocess error. Add an ffmpeg availability check at startup (in the lifespan handler) with a clear error message.

### Feasibility Concerns

1. **28h (3.5 days) seems high for what's essentially wiring.** The orchestrator is ~50 lines of code. Upload validation is ~20 lines. Error handling is routing logic. The bulk of the work (search lanes, decode, embedding) is already done in Phases 3-4. 2 days (16h) seems more realistic unless integration testing reveals issues.

2. **The CLAP warm-up inference during startup blocks the entire application.** `model.get_audio_embedding_from_data(x=warmup_audio, use_tensor=False)` takes 1-3s on CPU. During this time, the application doesn't accept connections. If deployed behind a load balancer with health checks, the health check may fail during startup. Consider making warm-up async or accepting the cold-start penalty.

### Missing Dependencies

1. **`python-magic` system dependency (`libmagic`)** — not listed in prerequisites.
2. **ffmpeg availability check** — should be verified at startup.
3. **Explicit dependency on Phase 3's `decode_dual_rate()` function** and Phase 4's lane functions.

### Recommended Changes

1. **~~RESOLVED~~ Fix the timeout inconsistency**: Timeouts have been corrected: exact=3s, vibe=4s, total=5s hard cap. The latency budget table and timeout constants are now consistent with the p95 < 5s target.
2. **Add an asyncio.Semaphore(1) for CLAP inference** to prevent concurrent CPU contention.
3. **Harmonize error status codes**: Use 422 for format/validation errors consistently.
4. **Add ffmpeg availability check** to the lifespan handler.
5. **Add zero-byte upload test case** to integration tests.
6. **Reduce effort estimate** to 16-20h (2-2.5 days) given that this is primarily wiring.
