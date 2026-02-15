# Phase 3: Admin Ingest UI

> **Date**: 2026-02-15
> **Status**: PLAN (not yet implemented)
> **Dependencies**: Phase 2 complete (track library exists so ingested tracks are viewable)
> **Scope**: Backend ingest router + Frontend admin ingest page

---

## 1. Objective

**Deliver a web-based audio ingestion interface** that allows admin users to upload individual audio files into the identification library without requiring CLI access (`make ingest`).

### What This Phase Delivers

1. A new FastAPI router at `POST /api/v1/ingest` accepting single-file multipart uploads
2. Security gating via an admin API token (environment variable)
3. Concurrency safety enforcing the Olaf LMDB single-writer constraint
4. A new SvelteKit page at `/admin/ingest` with file upload, progress display, and result feedback
5. Auto-generated TypeScript types via `make gen-client` (no hand-written types)
6. Backend and frontend tests

### Success Criteria (Measurable)

- [ ] `POST /api/v1/ingest` returns `201 Created` with `IngestResponse` for a valid MP3 file
- [ ] Duplicate files return `IngestResponse` with `status: "duplicate"` (not an error)
- [ ] Files shorter than 3 seconds or longer than 30 minutes are rejected with appropriate error codes
- [ ] Unsupported formats are rejected with `UNSUPPORTED_FORMAT` error
- [ ] Requests without a valid `X-Admin-Key` header receive `403 Forbidden`
- [ ] Concurrent ingestion requests receive `429 Too Many Requests` (not silent corruption)
- [ ] The frontend page at `/admin/ingest` can upload a file and display the result
- [ ] The frontend shows a security warning banner when no auth token is configured
- [ ] All backend tests pass: `uv run pytest tests/test_ingest_router.py`
- [ ] All frontend tests pass: `pnpm test`
- [ ] Generated types include `IngestResponse`, `IngestStatus`, `IngestError`, `IngestReport`

---

## 2. Critical Constraints

### From Devil's Advocate Review (SIG-5): Ingest Without Auth Is a Security Risk

The ingest endpoint **mutates the database, indexes files in Olaf LMDB, and generates CLAP embeddings**. These are expensive, irreversible operations. On any network-accessible deployment, anyone can flood the system with audio files.

**This plan addresses SIG-5 by implementing Option B: Admin token via environment variable** (see Section 4a below). This is chosen over full auth (deferred to Phase 4) because:
- It provides meaningful protection without the complexity of a full auth system
- It is a single environment variable, trivial to configure
- It can be enforced in both backend middleware and frontend UI
- The CLI (`make ingest`) remains unaffected (it bypasses HTTP entirely)

### From CLAUDE.md (BLOCK-1): NO Hand-Written TypeScript Types

All TypeScript types MUST come from `generated.ts` via `make gen-client`. This plan does NOT contain any TypeScript interface definitions. The implementer must:
1. Implement the backend Pydantic schemas and router FIRST
2. Start the backend (`make dev`)
3. Run `make gen-client` to generate types from the live OpenAPI spec
4. Import generated types in `client.ts`

### Contract-First Workflow (CLAUDE.md Golden Rule)

The `POST /api/v1/ingest` endpoint is already defined in `docs/api-contract.md` v1.1.0. The contract does NOT need modification for single-file upload (the contract already specifies the `audio` field for single files). However, **two deviations from the contract require attention**:

1. **The `directory` field**: The contract allows `directory` as an alternative to `audio`. This plan implements **single-file upload only** for the HTTP endpoint (not directory). The `directory` field is a server-side-only concern that makes no sense in a browser upload context. The router will accept `audio` only and reject requests with `directory`. This is a subset of the contract, not a violation.

2. **Security header**: The contract does not mention `X-Admin-Key`. Adding a header requirement does not change the response schema. This is additive and does not require a contract version bump.

### From CLAUDE.md: Operational Constraints

- Do NOT run multiple ingest processes simultaneously (Olaf LMDB single-writer)
- SHA-256 dedup will skip already-ingested files
- Duration limits: 3 seconds minimum, 30 minutes maximum
- Do NOT ingest files shorter than 3 seconds or longer than 30 minutes

---

## 3. Contract Verification

### Current Contract Status

The `POST /api/v1/ingest` endpoint is defined in `docs/api-contract.md` v1.1.0 (FROZEN).

**Contract review checklist:**

| Aspect | Contract Says | This Plan Does | Match? |
|--------|---------------|----------------|--------|
| Method | `POST` | `POST` | YES |
| Path | `/api/v1/ingest` | `/api/v1/ingest` | YES |
| Content-Type | `multipart/form-data` | `multipart/form-data` | YES |
| `audio` field | Optional file | Required file | SUBSET (see note) |
| `directory` field | Optional string | NOT IMPLEMENTED | SUBSET (see note) |
| Response (single file) | `201 Created` with `IngestResponse` | `201 Created` with `IngestResponse` | YES |
| Response (batch) | `200 OK` with `IngestReport` | NOT IMPLEMENTED (CLI only) | N/A |
| Error codes | `VALIDATION_ERROR`, `UNSUPPORTED_FORMAT`, `AUDIO_TOO_SHORT`, `AUDIO_TOO_LONG`, `DIRECTORY_NOT_FOUND`, `SERVICE_UNAVAILABLE` | All except `DIRECTORY_NOT_FOUND` | SUBSET |

**Note on subset implementation:** The contract defines both `audio` and `directory` fields with "exactly one must be provided." This plan implements only the `audio` field because:
- The `directory` field requires server-side filesystem access, which is inappropriate for a browser-based UI
- The `directory` functionality already exists via `make ingest AUDIO_DIR=/path` (CLI)
- Batch ingestion via HTTP would require background task management (deferred to future work)
- This is a **valid subset** of the contract, not a deviation

**No contract version bump needed.** The endpoint is already in v1.1.0. We are implementing a subset of its defined behavior.

### Contract Copy Locations

All three copies must remain identical:
1. `audio-ident-service/docs/api-contract.md` (source of truth)
2. `audio-ident-ui/docs/api-contract.md` (copy)
3. `docs/api-contract.md` (copy)

Since no contract changes are needed, no copy operation is required. Verify all three are identical before starting implementation:

```bash
diff audio-ident-service/docs/api-contract.md audio-ident-ui/docs/api-contract.md
diff audio-ident-service/docs/api-contract.md docs/api-contract.md
```

---

## 4. Backend Implementation

### 4a. Security Approach

**Recommended: Option B -- Admin token via environment variable**

| Option | Approach | Pros | Cons | Recommendation |
|--------|----------|------|------|----------------|
| A | IP-based restriction (localhost only) | Zero config | Breaks in Docker, useless for remote dev | REJECT |
| B | Admin token via env var (`ADMIN_API_KEY`) | Simple, effective, configurable | Not full auth, token in header | **RECOMMENDED** |
| C | Full auth (defer to Phase 4) | Proper security | Delays ingest UI, CLI works fine | FALLBACK |

**Rationale for Option B:**
- A single environment variable (`ADMIN_API_KEY`) is trivial to set in `.env`
- The header `X-Admin-Key` is checked on every ingest request
- If `ADMIN_API_KEY` is empty or unset, the endpoint returns `403 Forbidden` for ALL requests (fail-closed)
- The frontend reads a separate env var (`VITE_ADMIN_API_KEY`) and includes it in requests
- This provides meaningful protection without the complexity of JWT/OAuth2

**Implementation detail:**

Add to `app/settings.py`:

```python
# Admin
admin_api_key: str = ""  # Empty = endpoint locked (fail-closed)
```

Create a dependency function in `app/auth/admin.py`:

```python
from fastapi import Header, HTTPException

from app.settings import settings


async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    """Verify the admin API key header.

    If ADMIN_API_KEY is not configured (empty string), ALL requests are
    rejected with 403. This is fail-closed by design.
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "AUTH_NOT_CONFIGURED",
                    "message": "Admin API key not configured. Set ADMIN_API_KEY in environment.",
                }
            },
        )

    if x_admin_key != settings.admin_api_key:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Invalid or missing admin API key.",
                }
            },
        )
```

**Important:** The `403` responses use the standard `ErrorResponse` shape (`{"error": {"code": ..., "message": ...}}`) for consistency with the rest of the API.

### 4b. Concurrency Safety

**Recommended: Reject-if-busy with HTTP 429**

| Option | Approach | Pros | Cons | Recommendation |
|--------|----------|------|------|----------------|
| `asyncio.Lock` | Queue requests, process sequentially | No rejections | Unbounded memory, request timeouts | REJECT |
| Background queue | Decouple upload from processing | Async, scalable | Complex state tracking, need task status endpoint | OVER-ENGINEERED |
| **Reject-if-busy (429)** | Return 429 if ingestion in progress | Simple, clear, predictable | User must retry | **RECOMMENDED** |

**Rationale:**
- The Olaf LMDB single-writer constraint means we CANNOT process two ingestions concurrently
- A lock would queue requests, but the user has no visibility into queue position, and HTTP timeouts would fire
- A 429 response tells the frontend "try again in a moment" which is the honest answer
- The frontend can show "Ingestion in progress, please wait" and auto-retry or let the user retry manually

**Implementation detail:**

Use a module-level `asyncio.Lock` in the router, but instead of waiting on it, use `lock.locked()` to check and reject immediately:

```python
import asyncio

_ingest_lock = asyncio.Lock()

async def _acquire_or_reject() -> bool:
    """Try to acquire the ingest lock without blocking.

    Returns True if acquired (caller MUST release), False if busy.
    """
    return _ingest_lock.locked() is False and await _try_acquire()

async def _try_acquire() -> bool:
    """Non-blocking lock acquisition."""
    try:
        # acquire() with timeout=0 is not available, so use try_lock pattern
        if _ingest_lock.locked():
            return False
        await _ingest_lock.acquire()
        return True
    except Exception:
        return False
```

Actually, the simplest correct approach is:

```python
_ingest_lock = asyncio.Lock()

# In the endpoint handler:
if _ingest_lock.locked():
    return _error_response(
        429,
        "RATE_LIMITED",
        "Another ingestion is in progress. Please try again in a moment.",
    )

async with _ingest_lock:
    # ... perform ingestion ...
```

There is a TOCTOU race between `locked()` and `async with`, but in practice this is a single asyncio event loop with no preemption between the check and the acquire. For production hardening, wrap in a try/except or use a flag variable. The plan below uses a safe pattern.

### 4c. Router Implementation

**File:** `audio-ident-service/app/routers/ingest.py`

```python
"""Ingest endpoint for adding audio files to the identification library.

Accepts single-file multipart uploads, validates format and duration,
and processes through the full ingestion pipeline (metadata extraction,
fingerprinting, embedding generation).

Protected by admin API key (X-Admin-Key header).
Enforces single-writer constraint (rejects concurrent requests with 429).
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

import magic
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse

from app.auth.admin import require_admin_key
from app.db.session import async_session_factory
from app.ingest.pipeline import ingest_file
from app.schemas.ingest import IngestResponse, IngestStatus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ingest"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB (larger than search, ingestion files can be big)

ALLOWED_MIME_TYPES: set[str] = {
    "audio/webm",
    "video/webm",
    "audio/ogg",
    "audio/mpeg",
    "audio/mp4",
    "audio/wav",
    "audio/x-wav",
    "audio/flac",
    "audio/x-flac",
}

# Single-writer lock (Olaf LMDB constraint)
_ingest_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build a JSON error response matching the project convention."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=201,
    responses={
        400: {"description": "Validation error (format, size, duration)"},
        403: {"description": "Missing or invalid admin API key"},
        429: {"description": "Another ingestion is in progress"},
        503: {"description": "Backend service unavailable"},
    },
    dependencies=[Depends(require_admin_key)],
)
async def ingest_audio(
    request: Request,
    audio: UploadFile = File(
        ...,
        description="Audio file to ingest (MP3, WAV, FLAC, OGG, WebM, MP4). Max 50 MB.",
    ),
) -> IngestResponse | JSONResponse:
    """Ingest a single audio file into the identification library.

    The file is processed through the full pipeline:
    1. SHA-256 hash check (duplicate detection)
    2. Metadata extraction (title, artist, album)
    3. Dual-rate PCM decode (16kHz + 48kHz)
    4. Duration validation (3s - 30min)
    5. Chromaprint content dedup
    6. Olaf fingerprint indexing + CLAP embedding generation (parallel)
    7. PostgreSQL track record insertion

    Only one ingestion can run at a time (Olaf LMDB single-writer constraint).
    If another ingestion is in progress, returns 429.

    Requires X-Admin-Key header matching the ADMIN_API_KEY environment variable.
    """
    # 1. Reject if another ingestion is in progress
    if _ingest_lock.locked():
        return _error_response(
            429,
            "RATE_LIMITED",
            "Another ingestion is in progress. Please try again in a moment.",
        )

    # 2. Read and validate upload
    content = await audio.read()

    if len(content) == 0:
        return _error_response(400, "EMPTY_FILE", "Empty file uploaded.")

    if len(content) > MAX_UPLOAD_BYTES:
        return _error_response(
            400,
            "FILE_TOO_LARGE",
            f"File too large. Maximum upload size is {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )

    # 3. Verify content type via magic bytes
    detected_type = magic.from_buffer(content, mime=True)
    if detected_type not in ALLOWED_MIME_TYPES:
        return _error_response(
            400,
            "UNSUPPORTED_FORMAT",
            f"Unsupported audio format: {detected_type}. "
            "Supported: MP3, WAV, FLAC, OGG, WebM, MP4.",
        )

    # 4. Write to temp file (pipeline.ingest_file expects a Path)
    suffix = Path(audio.filename or "upload").suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(content)
        tmp.close()
        tmp_path = Path(tmp.name)

        # 5. Acquire lock and run ingestion
        async with _ingest_lock:
            clap_model = getattr(request.app.state, "clap_model", None)
            clap_processor = getattr(request.app.state, "clap_processor", None)
            qdrant_client = request.app.state.qdrant

            result = await ingest_file(
                file_path=tmp_path,
                clap_model=clap_model,
                clap_processor=clap_processor,
                qdrant_client=qdrant_client,
                session_factory=async_session_factory,
            )

    finally:
        # Clean up temp file
        Path(tmp.name).unlink(missing_ok=True)

    # 6. Map pipeline result to HTTP response
    if result.status == "error":
        # Determine appropriate error code
        error_msg = result.error or "Unknown error"
        if "too short" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_SHORT", error_msg)
        elif "too long" in error_msg.lower():
            return _error_response(400, "AUDIO_TOO_LONG", error_msg)
        elif "decode" in error_msg.lower():
            return _error_response(400, "UNSUPPORTED_FORMAT", error_msg)
        else:
            return _error_response(503, "SERVICE_UNAVAILABLE", error_msg)

    if result.status == "skipped":
        # "skipped" means duration validation failed
        return _error_response(400, "VALIDATION_ERROR", result.error or "File skipped")

    # Success or duplicate
    if result.status == "duplicate":
        status = IngestStatus.DUPLICATE
    else:
        status = IngestStatus.INGESTED

    # We need the track title/artist. For duplicates, the pipeline returns
    # the existing track_id but not metadata. Query it if needed.
    # For new tracks, the pipeline extracted metadata from the file.
    # The IngestResult dataclass does not carry title/artist, so we need
    # to extract metadata here for the response.
    from app.audio.metadata import extract_metadata

    metadata = extract_metadata(tmp_path) if tmp_path.exists() else None
    title = (metadata.title if metadata else None) or audio.filename or "Unknown"
    artist = metadata.artist if metadata else None

    return IngestResponse(
        track_id=result.track_id or uuid.uuid4(),  # Should always have ID
        title=title,
        artist=artist,
        status=status,
    )
```

**IMPORTANT NOTE:** The code above is a structural guide for the implementer. The actual implementation must handle an edge case: `extract_metadata` is called on the temp file, but the temp file is deleted in the `finally` block. The implementer must extract metadata BEFORE the finally block, or restructure the cleanup. The recommended fix is to extract metadata inside the `try` block before cleanup, and store the title/artist in local variables.

**Revised approach for metadata extraction:**

The `ingest_file()` function in `pipeline.py` already extracts metadata internally (Step 2), but the `IngestResult` dataclass does not expose title/artist. Two options:

**Option A (Recommended):** Add `title` and `artist` fields to `IngestResult` in `pipeline.py`. This is a minimal change (2 lines) that makes the pipeline return complete information.

```python
# In pipeline.py, IngestResult dataclass:
@dataclass
class IngestResult:
    file_path: str
    track_id: uuid.UUID | None = None
    status: str = "pending"
    error: str | None = None
    duration_seconds: float | None = None
    title: str | None = None      # ADD THIS
    artist: str | None = None     # ADD THIS
```

Then in the `ingest_file` function, after metadata extraction (Step 2), set:
```python
result.title = metadata.title or file_path.stem
result.artist = metadata.artist
```

And for duplicates (after Step 1 hash check), query the existing track for its title/artist.

**Option B:** Extract metadata separately in the router before calling `ingest_file()`. This duplicates work but avoids modifying the pipeline.

**This plan recommends Option A** because it keeps the pipeline as the single source of truth for ingestion results.

### 4d. Schema Verification

The existing schemas in `app/schemas/ingest.py` are already correct and match the API contract:

| Schema | Contract Field | Pydantic Field | Match? |
|--------|---------------|----------------|--------|
| `IngestResponse.track_id` | `string (UUID)` | `uuid.UUID` | YES |
| `IngestResponse.title` | `string` | `str` | YES |
| `IngestResponse.artist` | `string \| null` | `str \| None = None` | YES |
| `IngestResponse.status` | `"ingested" \| "duplicate" \| "error"` | `IngestStatus` (StrEnum) | YES |
| `IngestError.file` | `string` | `str` | YES |
| `IngestError.error` | `string` | `str` | YES |
| `IngestReport.total` | `number` | `int` | YES |
| `IngestReport.ingested` | `number` | `int = 0` | YES |
| `IngestReport.duplicates` | `number` | `int = 0` | YES |
| `IngestReport.errors` | `IngestError[]` | `list[IngestError]` | YES |

**No schema modifications needed** for the HTTP response models. The only modification is to the internal `IngestResult` dataclass in `pipeline.py` (add `title` and `artist` fields -- this is NOT a Pydantic schema, it is an internal data transfer object).

### 4e. Router Registration

Add to `app/main.py`:

```python
from app.routers import health, ingest, search, version

# In create_app():
application.include_router(ingest.router, prefix="/api/v1")
```

### 4f. Settings Addition

Add to `app/settings.py` (in the `Settings` class):

```python
# Admin
admin_api_key: str = ""  # Empty = ingest endpoint locked (fail-closed)
```

Add to `.env.example`:

```
# Admin API key for ingest endpoint (required for POST /api/v1/ingest)
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
ADMIN_API_KEY=
```

### 4g. Backend Tests

**File:** `audio-ident-service/tests/test_ingest_router.py`

Tests MUST use the existing `conftest.py` pattern (`httpx.AsyncClient` with `ASGITransport`). All external dependencies (Olaf, Qdrant, CLAP, DB) must be mocked.

| # | Test Name | What It Tests | Expected Result |
|---|-----------|---------------|-----------------|
| 1 | `test_ingest_success` | Upload a valid audio file with correct admin key | `201 Created`, `IngestResponse` with `status: "ingested"` |
| 2 | `test_ingest_duplicate` | Upload a file that already exists (SHA-256 match) | `201 Created`, `IngestResponse` with `status: "duplicate"` |
| 3 | `test_ingest_missing_admin_key` | Upload without `X-Admin-Key` header | `403 Forbidden` |
| 4 | `test_ingest_wrong_admin_key` | Upload with incorrect `X-Admin-Key` | `403 Forbidden` |
| 5 | `test_ingest_no_admin_key_configured` | `ADMIN_API_KEY` env var is empty | `403 Forbidden` |
| 6 | `test_ingest_unsupported_format` | Upload a non-audio file (e.g., text file) | `400`, `UNSUPPORTED_FORMAT` |
| 7 | `test_ingest_empty_file` | Upload a zero-byte file | `400`, `EMPTY_FILE` |
| 8 | `test_ingest_file_too_large` | Upload exceeding 50 MB | `400`, `FILE_TOO_LARGE` |
| 9 | `test_ingest_audio_too_short` | Upload audio shorter than 3 seconds | `400`, `AUDIO_TOO_SHORT` |
| 10 | `test_ingest_audio_too_long` | Upload audio longer than 30 minutes | `400`, `AUDIO_TOO_LONG` |
| 11 | `test_ingest_concurrent_rejection` | Two simultaneous ingest requests | First: `201`, Second: `429 RATE_LIMITED` |
| 12 | `test_ingest_missing_audio_field` | POST without `audio` field | `422` (FastAPI validation) |

**Test fixture approach:**

- Use the existing `conftest.py` client fixture
- Mock `app.settings.settings.admin_api_key` to a known value for auth tests
- Mock `ingest_file()` to return pre-built `IngestResult` objects (avoid running the actual pipeline)
- For concurrent test: use `asyncio.gather` to send two requests simultaneously, mock `ingest_file` with a delay
- Use a small valid WAV file fixture (generate programmatically: 1 second of silence as WAV bytes with correct headers)

**Test file structure:**

```python
"""Tests for the ingest router (POST /api/v1/ingest).

All external dependencies (pipeline, CLAP, Qdrant, DB) are mocked.
"""

import asyncio
import io
import struct
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Helper to generate a minimal valid WAV file
def make_wav_bytes(duration_seconds: float = 5.0, sample_rate: int = 16000) -> bytes:
    """Generate a minimal WAV file with silence."""
    num_samples = int(sample_rate * duration_seconds)
    data_size = num_samples * 2  # 16-bit mono
    header = struct.pack(
        '<4sI4s4sIHHIIHH4sI',
        b'RIFF', 36 + data_size, b'WAVE',
        b'fmt ', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16,
        b'data', data_size,
    )
    return header + b'\x00' * data_size

# ... individual test functions following table above
```

---

## 5. Frontend Implementation

### 5a. Type Generation

After the backend router is implemented and the service is running:

```bash
make dev          # Start backend + infrastructure
make gen-client   # Regenerate TypeScript types from /openapi.json
```

**Verify** that `audio-ident-ui/src/lib/api/generated.ts` now contains:
- `IngestResponse` type with `track_id`, `title`, `artist`, `status` fields
- `IngestStatus` enum/union with values `"ingested" | "duplicate" | "error"`
- `IngestError` type with `file`, `error` fields
- `IngestReport` type with `total`, `ingested`, `duplicates`, `errors` fields

If generation fails (backend cannot start), use the fallback documented in CLAUDE.md: commit the `openapi.json` and generate from the static file.

### 5b. API Client Function

**File:** `audio-ident-ui/src/lib/api/client.ts`

Add one export and one function. Import types from `generated.ts` (never hand-write them).

```typescript
// At the top of client.ts, update imports:
import type { components, SearchMode, SearchResponse, IngestResponse } from './generated';

// ... existing exports ...
export type { IngestResponse };

// Add this function:
export async function ingestAudio(
    file: File,
    adminKey: string,
    signal?: AbortSignal
): Promise<IngestResponse> {
    const form = new FormData();
    form.append('audio', file, file.name);

    const res = await fetch(`${BASE_URL}/api/v1/ingest`, {
        method: 'POST',
        body: form,
        headers: {
            'X-Admin-Key': adminKey,
        },
        signal,
    });

    if (!res.ok) {
        let apiError: ApiError = {
            code: 'UNKNOWN',
            message: `Ingest failed: ${res.status} ${res.statusText}`,
            status: res.status,
        };

        try {
            const body = await res.json();
            if (body?.error) {
                apiError = {
                    code: body.error.code ?? 'UNKNOWN',
                    message: body.error.message ?? apiError.message,
                    status: res.status,
                };
            } else if (body?.detail) {
                const msg = Array.isArray(body.detail)
                    ? body.detail.map((d: Record<string, unknown>) => d.msg).join('; ')
                    : typeof body.detail === 'object'
                      ? body.detail.error?.message ?? JSON.stringify(body.detail)
                      : String(body.detail);
                apiError = { code: 'VALIDATION_ERROR', message: msg, status: res.status };
            }
        } catch {
            // Use the default error
        }

        throw new ApiRequestError(apiError);
    }

    return res.json() as Promise<IngestResponse>;
}
```

**Note on the `adminKey` parameter:** The admin key is passed as a function argument rather than read from environment inside the client. This allows the UI page to manage the key (prompt the user, read from local storage, or read from a Vite env var) without coupling the client to a specific key source. The page component will read `import.meta.env.VITE_ADMIN_API_KEY` and pass it to `ingestAudio()`.

### 5c. Ingest Page

**File:** `audio-ident-ui/src/routes/admin/ingest/+page.svelte` (NEW)

**Page structure:**

```
+------------------------------------------------------------------+
| [NavBar]                                                          |
+------------------------------------------------------------------+
|                                                                  |
|  [Warning banner if no VITE_ADMIN_API_KEY configured]            |
|                                                                  |
|  Ingest Audio                                                    |
|  Add new tracks to the identification library                    |
|                                                                  |
|  +--------------------------------------------------------------+|
|  |     [upload icon]                                            ||
|  |     Drag and drop an audio file here                         ||
|  |     or click to browse                                       ||
|  |                                                              ||
|  |     Supported: MP3, WAV, FLAC, OGG, WebM, MP4, M4A          ||
|  |     Duration: 3 seconds - 30 minutes                         ||
|  +--------------------------------------------------------------+|
|                                                                  |
|  [Ingest button - disabled until file selected]                  |
|                                                                  |
|  Recent Ingestions (this session)                                 |
|  +--------------------------------------------------------------+|
|  | [green] song.mp3 - "Bohemian Rhapsody" (Queen) - Ingested    ||
|  | [yellow] song2.mp3 - Duplicate (already in library)           ||
|  | [red] corrupt.mp3 - Error: Unsupported format                 ||
|  +--------------------------------------------------------------+|
|                                                                  |
+------------------------------------------------------------------+
```

**Component specification:**

The page is a single Svelte component with these state variables (all using Svelte 5 runes):

```
State variables:
  selectedFile: File | null = $state(null)
  isIngesting: boolean = $state(false)
  ingestError: string | null = $state(null)
  recentResults: IngestResultEntry[] = $state([])
  adminKey: string (from import.meta.env.VITE_ADMIN_API_KEY or '')
  hasAdminKey: boolean = $derived(adminKey.length > 0)

Types (local to component, NOT API types):
  IngestResultEntry = {
    filename: string
    response: IngestResponse | null
    error: string | null
    timestamp: Date
  }
```

**Page title:** `<svelte:head><title>Ingest Audio - audio-ident</title></svelte:head>`

**Key behaviors:**

1. **Security warning banner:** If `VITE_ADMIN_API_KEY` is not set (empty or undefined), show an amber banner at the top of the page:
   - "Admin API key not configured. Set VITE_ADMIN_API_KEY in your environment to enable ingestion."
   - The ingest button is disabled when no key is configured
   - Use `lucide-svelte` `ShieldAlert` icon

2. **File selection:** Reuse the same drag-and-drop pattern from `AudioUploader.svelte`. Do NOT import `AudioUploader` directly because:
   - AudioUploader is designed for search (10 MB limit, specific validation messages)
   - Ingest has different constraints (larger files allowed, different messaging)
   - Creating a new file selection area with the same visual pattern but different validation is cleaner than adding conditional logic to AudioUploader
   - The accepted formats are the same: `.mp3, .wav, .webm, .ogg, .mp4, .m4a, .flac`

3. **Upload and ingest (two-step):** The user selects a file, previews the file info (name, size, format), then clicks "Ingest" to submit. This prevents accidental ingestion. The ingest button shows a confirmation state: "Are you sure? This will permanently add this track." (on first click, change button text; on second click, submit).

4. **Progress display:** While ingestion is running (`isIngesting = true`):
   - Disable the file selector and ingest button
   - Show a spinner with "Ingesting... This may take 10-30 seconds for embedding generation."
   - The CLAP embedding step is the slowest part (0.5-1.5s per chunk, ~47 chunks for a 4-minute track)

5. **Result display:** After ingestion completes:
   - Success (ingested): Green banner with track title, artist, and "Added to library" message. Include a link to `/tracks/{track_id}` if Phase 2 is complete.
   - Duplicate: Yellow/amber banner with "This file is already in the library" message.
   - Error: Red banner with error message.
   - Add the result to `recentResults` array (prepend, most recent first)

6. **Recent results log:** A session-local list of all ingestion attempts during this page session. Not persisted across page navigations. Each entry shows:
   - Status icon (green checkmark, yellow duplicate, red X)
   - Filename
   - Track title (if available)
   - Status text
   - Timestamp

7. **Rate limit handling:** If the API returns 429, show an amber banner: "Another ingestion is in progress. Please wait and try again."

8. **Accessibility:**
   - `aria-live="polite"` on the result area so screen readers announce new results
   - `aria-disabled="true"` on the ingest button when ingesting or no file selected
   - `aria-busy="true"` on the page content area while ingesting

**Lucide icons to use:**
- `Upload` -- file drop zone
- `FileAudio` -- selected file info
- `ShieldAlert` -- security warning banner
- `CheckCircle` -- ingested successfully
- `AlertTriangle` -- duplicate or warning
- `XCircle` -- error
- `Loader2` -- spinner during ingestion (with `animate-spin` class)

### 5d. NavBar Updates

**File:** `audio-ident-ui/src/lib/components/NavBar.svelte` (modify existing from Phase 1)

Add an "Admin" or "Ingest" link to the navigation bar. Two options:

**Option A (Recommended): Always visible, minimal text**

Add a text link "Admin" to the nav bar, positioned after "Library":

```
+------------------------------------------------------------------+
| audio-ident     [Identify]  [Library]  [Admin]        [* status] |
+------------------------------------------------------------------+
```

The "Admin" link navigates to `/admin/ingest`. Style it as a subdued text link (not a button), since it is a secondary/admin action.

Active state: Use `page.url.pathname.startsWith('/admin')` to highlight when on admin pages.

**Option B: Gated visibility**

Only show the "Admin" link if `VITE_ADMIN_API_KEY` is configured. This hides admin functionality from users who do not have the key. However, this requires the NavBar to read an environment variable, which is unusual for a navigation component.

**This plan recommends Option A** (always visible) because:
- The admin key check happens at the API level, not the UI level
- Hiding the link provides zero security (the URL `/admin/ingest` is still accessible)
- Showing the link with a "key required" message on the page is more transparent

If Phase 1 used a hamburger menu for mobile (3+ items), the "Admin" link goes in the mobile menu. If Phase 1 kept all items inline (as recommended by the devil's advocate review for 2 items), adding a third item may require switching to a hamburger menu on mobile. The implementer should evaluate based on the Phase 1 nav bar implementation.

### 5e. Environment Variable

Add to `audio-ident-ui/.env` (or `.env.example`):

```
# Admin API key for ingest endpoint (must match ADMIN_API_KEY on the backend)
VITE_ADMIN_API_KEY=
```

**Note:** Vite environment variables starting with `VITE_` are exposed to client-side code. This is acceptable for a development tool where the admin key provides access control, not secrecy. In production, a proper auth system (Phase 4) would replace this.

---

## 6. Important Constraints from CLAUDE.md (Consolidated)

These constraints MUST be respected during implementation:

| # | Constraint | Source | Enforcement |
|---|-----------|--------|-------------|
| 1 | Do NOT manually write TypeScript types for API responses | CLAUDE.md | Use `make gen-client` |
| 2 | Do NOT add endpoints without updating `docs/api-contract.md` first | CLAUDE.md | Already in contract v1.1.0 |
| 3 | Do NOT run multiple ingest processes simultaneously | CLAUDE.md | `asyncio.Lock` + 429 rejection |
| 4 | SHA-256 dedup will skip already-ingested files | CLAUDE.md | Pipeline handles this |
| 5 | Duration limits: 3s minimum, 30 minutes maximum | CLAUDE.md | Pipeline validates, router maps to error codes |
| 6 | Do NOT modify generated files in `generated.ts` | CLAUDE.md | Run `make gen-client` instead |
| 7 | `make gen-client` requires the backend to be running | CLAUDE.md | Start with `make dev` first |
| 8 | API routes: `/health` (no prefix), `/api/v1/*` (versioned) | CLAUDE.md | Router registered with `prefix="/api/v1"` |
| 9 | Contract sync: service -> UI (never reverse) | CLAUDE.md | No contract changes needed |

---

## 7. Testing Strategy

### 7a. Backend pytest Tests

**File:** `audio-ident-service/tests/test_ingest_router.py`

See Section 4g above for the complete test matrix (12 tests).

**Run:** `cd audio-ident-service && uv run pytest tests/test_ingest_router.py -v`

**Mocking strategy:**
- Mock `ingest_file()` to return controlled `IngestResult` objects
- Mock `settings.admin_api_key` for auth tests
- Mock `magic.from_buffer()` for format validation tests
- Use programmatically generated WAV files (no external fixtures needed)
- For concurrent test: mock `ingest_file` with `asyncio.sleep(0.5)` to hold the lock

### 7b. Frontend Component Tests

**File:** `audio-ident-ui/tests/ingest.test.ts`

| # | Test Name | What It Tests |
|---|-----------|---------------|
| 1 | `test_ingestAudio_success` | `ingestAudio()` client function returns `IngestResponse` on 201 |
| 2 | `test_ingestAudio_auth_error` | `ingestAudio()` throws `ApiRequestError` with code `FORBIDDEN` on 403 |
| 3 | `test_ingestAudio_rate_limited` | `ingestAudio()` throws `ApiRequestError` with code `RATE_LIMITED` on 429 |
| 4 | `test_ingestAudio_validation_error` | `ingestAudio()` throws `ApiRequestError` with correct code on 400 |

**Run:** `cd audio-ident-ui && pnpm test`

**Mocking strategy:** Use `vi.fn()` to mock `fetch` and return controlled responses (same pattern as existing `health.test.ts`).

### 7c. Manual Verification Steps

After implementation, the developer should manually verify these scenarios:

1. **Happy path:**
   - Start the system: `make dev`
   - Set `ADMIN_API_KEY=test-key-123` in `audio-ident-service/.env`
   - Set `VITE_ADMIN_API_KEY=test-key-123` in `audio-ident-ui/.env`
   - Navigate to `http://localhost:17000/admin/ingest`
   - Upload a valid MP3 file (> 3 seconds)
   - Verify: 201 response, track appears in result log, track visible in library (`/tracks`)

2. **Duplicate detection:**
   - Upload the same MP3 file again
   - Verify: Response shows `status: "duplicate"`, not an error

3. **Auth rejection:**
   - Remove `VITE_ADMIN_API_KEY` from `.env`
   - Refresh the ingest page
   - Verify: Warning banner appears, ingest button is disabled

4. **Concurrent rejection:**
   - Upload a large file (takes several seconds to process)
   - While processing, open a second browser tab and try to upload another file
   - Verify: Second request receives 429 response

5. **Invalid file:**
   - Upload a `.txt` file renamed to `.mp3`
   - Verify: `UNSUPPORTED_FORMAT` error displayed

### 7d. Concurrent Request Testing

This is critical due to the Olaf LMDB single-writer constraint.

**Backend test approach:**

```python
@pytest.mark.asyncio
async def test_ingest_concurrent_rejection(client, mock_ingest):
    """Second concurrent request should receive 429."""
    # mock_ingest sleeps for 0.5s to simulate processing time
    mock_ingest.side_effect = slow_ingest  # async def that sleeps

    # Send two requests concurrently
    wav_bytes = make_wav_bytes(5.0)

    async def send_request():
        return await client.post(
            "/api/v1/ingest",
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
            headers={"X-Admin-Key": "test-key"},
        )

    results = await asyncio.gather(send_request(), send_request())

    status_codes = sorted([r.status_code for r in results])
    assert status_codes == [201, 429], f"Expected [201, 429], got {status_codes}"
```

---

## 8. Implementation Order (Dependency-Ordered Steps)

Every step must be completed before the next step begins. Steps within a group can be done in parallel if noted.

### Step 1: Verify contract (5 minutes)
- Run `diff` on all three contract copies to confirm they are identical
- Read the `POST /api/v1/ingest` section of the contract
- Confirm no contract changes are needed

### Step 2: Add admin_api_key to settings (5 minutes)
- Add `admin_api_key: str = ""` to `app/settings.py` Settings class
- Add `ADMIN_API_KEY=` to `.env.example`
- Add `ADMIN_API_KEY=dev-test-key-change-me` to `.env` (for local development)

### Step 3: Create admin auth dependency (15 minutes)
- Create `app/auth/admin.py` with `require_admin_key()` function
- Test manually: the function should raise HTTPException 403 when key is missing/wrong

### Step 4: Modify IngestResult dataclass (10 minutes)
- Add `title: str | None = None` and `artist: str | None = None` to `IngestResult` in `app/ingest/pipeline.py`
- After metadata extraction in `ingest_file()`, set `result.title` and `result.artist`
- For duplicates detected at hash check (Step 1 in pipeline), query the existing track to get title/artist, OR leave as None (the router can handle this)
- Run existing pipeline tests: `uv run pytest tests/test_ingest_pipeline.py -v`

### Step 5: Create ingest router (45 minutes)
- Create `app/routers/ingest.py` following the specification in Section 4c
- Implement file validation (size, empty, MIME type)
- Implement temp file handling (write upload to temp, pass to pipeline, clean up)
- Implement lock-based concurrency rejection (429)
- Implement result mapping (pipeline IngestResult -> HTTP IngestResponse)

### Step 6: Register router in main.py (5 minutes)
- Add `from app.routers import ingest` to imports
- Add `application.include_router(ingest.router, prefix="/api/v1")`

### Step 7: Write backend tests (60 minutes)
- Create `tests/test_ingest_router.py` with all 12 tests from Section 4g
- Run: `uv run pytest tests/test_ingest_router.py -v`
- Ensure all pass

### Step 8: Run full backend test suite (10 minutes)
- Run: `uv run pytest -v`
- Ensure no regressions in existing tests

### Step 9: Start backend and verify OpenAPI spec (10 minutes)
- Run: `make dev`
- Visit `http://localhost:17010/openapi.json`
- Verify the `POST /api/v1/ingest` endpoint appears with correct request/response schemas
- Visit `http://localhost:17010/docs` and test the endpoint via Swagger UI

### Step 10: Regenerate frontend types (5 minutes)
- Run: `make gen-client`
- Verify `audio-ident-ui/src/lib/api/generated.ts` contains `IngestResponse`, `IngestStatus`, etc.
- If generation fails, troubleshoot backend startup or use openapi.json fallback

### Step 11: Add ingestAudio to client.ts (15 minutes)
- Add the `ingestAudio()` function to `audio-ident-ui/src/lib/api/client.ts` (Section 5b)
- Import `IngestResponse` from `generated.ts`
- Export the new function and type

### Step 12: Add VITE_ADMIN_API_KEY to frontend env (5 minutes)
- Add `VITE_ADMIN_API_KEY=` to `audio-ident-ui/.env.example`
- Add `VITE_ADMIN_API_KEY=dev-test-key-change-me` to `audio-ident-ui/.env` (matching backend)

### Step 13: Create ingest page (90 minutes)
- Create directory: `audio-ident-ui/src/routes/admin/ingest/`
- Create `+page.svelte` following the specification in Section 5c
- Implement file selection (drag-and-drop + click-to-browse)
- Implement security warning banner
- Implement two-step ingest flow (select file -> confirm -> submit)
- Implement result display (success/duplicate/error)
- Implement recent results log
- Implement 429 rate limit handling

### Step 14: Update NavBar (15 minutes)
- Add "Admin" link to `NavBar.svelte` (Section 5d)
- Add active state detection for `/admin/*` routes
- Test mobile layout (may need hamburger menu if 3+ items)

### Step 15: Write frontend tests (30 minutes)
- Create `audio-ident-ui/tests/ingest.test.ts` with 4 tests from Section 7b
- Run: `pnpm test`

### Step 16: Run full frontend checks (10 minutes)
- Run: `cd audio-ident-ui && pnpm check` (type checking)
- Run: `cd audio-ident-ui && pnpm lint` (linting)
- Run: `cd audio-ident-ui && pnpm test` (all tests)

### Step 17: Manual end-to-end verification (20 minutes)
- Follow the manual verification steps in Section 7c
- Test all 5 scenarios (happy path, duplicate, auth rejection, concurrent, invalid file)

### Step 18: Run full project checks (5 minutes)
- Run: `make test` (both backend and frontend)
- Run: `make lint` (both backend and frontend)
- Run: `make typecheck` (both backend and frontend)

**Total estimated implementation time:** ~6 hours

---

## 9. Acceptance Criteria

All of the following must be true before Phase 3 is considered complete:

### Backend
- [ ] `POST /api/v1/ingest` endpoint exists and is registered in `app/main.py`
- [ ] Endpoint accepts `multipart/form-data` with `audio` file field
- [ ] Endpoint requires `X-Admin-Key` header matching `ADMIN_API_KEY` env var
- [ ] Endpoint returns `403` when admin key is missing, wrong, or not configured
- [ ] Endpoint returns `201 Created` with `IngestResponse` for successful ingestion
- [ ] Endpoint returns `201 Created` with `status: "duplicate"` for duplicate files
- [ ] Endpoint returns `400` with appropriate error code for invalid files
- [ ] Endpoint returns `429` when another ingestion is in progress
- [ ] All 12 backend tests pass
- [ ] `make test` passes with no regressions
- [ ] `make lint` and `make typecheck` pass

### Frontend
- [ ] `ingestAudio()` function exists in `client.ts`
- [ ] Types imported from `generated.ts` (not hand-written)
- [ ] `/admin/ingest` page exists and renders
- [ ] Page shows security warning when `VITE_ADMIN_API_KEY` is not set
- [ ] Page allows file selection via drag-and-drop and click-to-browse
- [ ] Page shows file preview before ingestion
- [ ] Page shows confirmation before submitting
- [ ] Page shows ingestion progress spinner
- [ ] Page displays success/duplicate/error results
- [ ] Page maintains a session-local log of recent ingestion results
- [ ] NavBar includes "Admin" link
- [ ] All 4 frontend tests pass
- [ ] `pnpm check`, `pnpm lint`, `pnpm test` all pass

### Integration
- [ ] End-to-end flow works: upload file -> ingest -> see in track library
- [ ] Duplicate detection works via SHA-256 hash
- [ ] Concurrent request rejection works (429)
- [ ] Invalid format rejection works
- [ ] Duration validation works (< 3s and > 30min rejected)

---

## 10. Risks and Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | **CLAP model not loaded at ingestion time** | Medium | High -- embeddings not generated | The pipeline already handles this gracefully: if CLAP is None, embeddings are skipped but fingerprinting still works. The track is ingested without embeddings. Document this behavior in the UI ("Vibe matching may not be available for this track if the embedding model was unavailable during ingestion"). |
| 2 | **Temp file cleanup failure** | Low | Low -- disk space leak | Use `finally` block with `Path.unlink(missing_ok=True)`. The temp directory is cleaned by the OS eventually. |
| 3 | **Large file upload timeout** | Medium | Medium -- poor UX | The ingest pipeline for a 4-minute track takes ~10-30s (mostly CLAP embedding). FastAPI's default request timeout is generous, but the browser may timeout for very large files. Mitigation: set a frontend-side progress message ("This may take up to 60 seconds..."). Consider adding a backend timeout of 120 seconds for the entire operation. |
| 4 | **LMDB corruption from crash during ingestion** | Low | High -- fingerprint index broken | The pipeline already handles this: if Olaf indexing fails, `olaf_indexed` is set to `False` in the Track record. Recovery: `make rebuild-index`. Document this in the error display. |
| 5 | **Admin key exposed in client-side code** | Certain | Low (dev tool) | `VITE_` env vars are exposed in the browser bundle. This is acceptable for a development tool. In production (Phase 4), replace with proper JWT auth. Add a comment in the code explaining this trade-off. |
| 6 | **Pipeline IngestResult dataclass change breaks existing tests** | Medium | Medium | Adding optional fields (`title`, `artist`) with defaults of `None` should not break existing tests. Run `uv run pytest tests/test_ingest_pipeline.py` after the change to verify. |
| 7 | **Race condition between lock check and lock acquire** | Very Low | Medium | In a single-threaded asyncio event loop, there is no preemption between `_ingest_lock.locked()` and `async with _ingest_lock:`. The check-then-acquire pattern is safe. If the application ever uses multiple workers (e.g., Uvicorn with multiple workers), the lock would not be shared across processes. Mitigation: document that single-worker mode is required for correct ingestion behavior. |
| 8 | **`make gen-client` fails because backend cannot start** | Medium | High -- blocks frontend work | Fallback: commit `openapi.json` to the repo after the backend router is implemented. The frontend can generate types from the static file. Document this in Step 10. |
| 9 | **Ingestion takes too long, user navigates away** | Medium | Low -- ingestion still completes on backend | The backend processes the request regardless of client disconnection. The result is lost for the user's session, but the track is successfully ingested. Mitigation: inform the user "Ingestion will complete even if you leave this page." |
| 10 | **Contract version confusion** | Low | Medium | This plan does NOT require a contract version bump (implementing a subset of v1.1.0). If a future implementer adds the `directory` field, THAT would require verifying no contract change is needed. Document the subset explicitly. |

---

## Appendix A: Files Created

| File | Purpose |
|------|---------|
| `audio-ident-service/app/auth/admin.py` | Admin API key dependency |
| `audio-ident-service/app/routers/ingest.py` | Ingest HTTP router |
| `audio-ident-service/tests/test_ingest_router.py` | Backend tests |
| `audio-ident-ui/src/routes/admin/ingest/+page.svelte` | Ingest page |
| `audio-ident-ui/tests/ingest.test.ts` | Frontend tests |

## Appendix B: Files Modified

| File | Change |
|------|--------|
| `audio-ident-service/app/settings.py` | Add `admin_api_key` field |
| `audio-ident-service/app/main.py` | Register ingest router |
| `audio-ident-service/app/ingest/pipeline.py` | Add `title`, `artist` to `IngestResult` |
| `audio-ident-service/.env.example` | Add `ADMIN_API_KEY` |
| `audio-ident-ui/src/lib/api/client.ts` | Add `ingestAudio()` function |
| `audio-ident-ui/src/lib/api/generated.ts` | Auto-regenerated via `make gen-client` |
| `audio-ident-ui/src/lib/components/NavBar.svelte` | Add "Admin" link |
| `audio-ident-ui/.env.example` | Add `VITE_ADMIN_API_KEY` |

## Appendix C: Environment Variables Added

| Variable | Location | Default | Purpose |
|----------|----------|---------|---------|
| `ADMIN_API_KEY` | Backend `.env` | `""` (fail-closed) | Admin API key for ingest endpoint |
| `VITE_ADMIN_API_KEY` | Frontend `.env` | `""` | Admin API key sent in `X-Admin-Key` header |

---

*Plan authored 2026-02-15. Based on exhaustive analysis of: API contract v1.1.0, backend pipeline source code, existing router patterns (search.py), schema definitions (ingest.py, errors.py), UI inventory, UX recommendations, and devil's advocate review.*
