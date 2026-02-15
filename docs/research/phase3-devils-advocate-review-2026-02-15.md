# Phase 3: Admin Ingest UI -- Devil's Advocate Review

> **Date**: 2026-02-15
> **Reviewer**: Research Agent (Devil's Advocate)
> **Scope**: Full implementation review of Phase 3 (admin auth, ingest router, pipeline modifications, frontend ingest page, client.ts, NavBar, tests)
> **Plan Reference**: `docs/plans/02-initial-ui-enhancements/phase3-ingest-ui.md`

---

## Summary

Phase 3 implements a web-based audio ingestion interface with admin token authentication. The overall implementation is solid and follows the plan closely. However, I identified **2 critical issues**, **7 significant issues**, and **6 minor issues** that warrant attention. The critical issues relate to a timing-attack vulnerability in admin key comparison and a concurrency TOCTOU race that, while safe in single-worker mode, is undocumented and fragile.

---

## 1. Critical Issues (MUST FIX)

### CRIT-1: Admin Key Comparison Is Not Timing-Safe

**File**: `audio-ident-service/app/auth/admin.py`, line 37
**Severity**: Critical (Security)

The admin key comparison uses Python's `!=` operator:

```python
if x_admin_key != settings.admin_api_key:
```

Python's `!=` on strings performs a byte-by-byte comparison that short-circuits on the first differing byte. This is susceptible to timing attacks where an attacker can iteratively guess the key one character at a time by measuring response time differences.

**Impact**: An attacker on the same network (or with precise timing measurement) could theoretically recover the admin API key. While this is described as a "development tool" with plans for proper auth in Phase 4, the risk is real for any network-accessible deployment.

**Recommended Fix**:

```python
import hmac

if not hmac.compare_digest(x_admin_key or "", settings.admin_api_key):
    raise HTTPException(...)
```

`hmac.compare_digest()` is constant-time and prevents timing attacks. This is a one-line change with no performance impact. Note that `hmac.compare_digest` requires both arguments to be the same type (both `str` or both `bytes`), so the fallback `x_admin_key or ""` handles the `None` case.

---

### CRIT-2: TOCTOU Race Between Lock Check and Lock Acquisition

**File**: `audio-ident-service/app/routers/ingest.py`, lines 112-151
**Severity**: Critical (Concurrency Safety)

The code checks if the lock is locked, then later acquires it:

```python
# Line 112: Check
if _ingest_lock.locked():
    return _error_response(429, ...)

# ... validation code runs here (lines 119-140) ...

# Line 151: Acquire
async with _ingest_lock:
```

Between the `locked()` check and the `async with _ingest_lock:`, there are several `await` points:
- Line 120: `await audio.read()` -- this yields control to the event loop
- Line 148: `tmp.write(content)` -- synchronous but occurs after the check

In a single asyncio event loop, the `await audio.read()` call on line 120 is a yield point. If Request A checks `locked()` and gets `False`, then starts reading the upload, Request B could arrive, also check `locked()` and get `False`, and then both proceed to the `async with` block. One would acquire the lock and the other would **block/wait** (not reject) -- defeating the 429 rejection mechanism. The second request would eventually succeed when the first finishes, rather than being rejected.

This is acknowledged in the plan (Section 4b, paragraph about TOCTOU), but the plan says "In practice this is a single asyncio event loop with no preemption between the check and the acquire." That statement is **incorrect** -- the `await audio.read()` between the check and acquire IS a preemption point.

**Impact**: Two concurrent requests could both pass the `locked()` check, with the second one silently queuing behind the first rather than receiving a 429. This violates the spec's documented behavior and could cause HTTP timeouts for the second request if the first takes 30+ seconds.

**Recommended Fix**: Move validation before the lock check, or use `Lock.acquire()` with an immediate release pattern:

```python
# Option A: Non-blocking acquire attempt
if not _ingest_lock.locked():
    acquired = _ingest_lock.locked()  # Double-check is still racy

# Option B (Recommended): Read content first, then check-and-acquire atomically
content = await audio.read()  # Do upload read BEFORE lock check

# ... validate content (no await points) ...

if _ingest_lock.locked():
    return _error_response(429, ...)

async with _ingest_lock:
    # ... process ...
```

Option B works because after the `await audio.read()` completes, there are no more `await` points between `locked()` and `async with _ingest_lock:`. The file validation (`len(content)`, `magic.from_buffer()`, `NamedTemporaryFile`) are all synchronous operations. However, this relies on the implementation detail that there are no other await points in the synchronous validation section.

The most robust fix would be to try non-blocking acquire:

```python
if _ingest_lock.locked():
    return _error_response(429, ...)

# No await between here and acquire is critical
async with _ingest_lock:
    content = await audio.read()
    # ... rest of processing ...
```

But this holds the lock during upload, which is also undesirable. The trade-off needs documentation at minimum.

**At the very least**: Add a prominent comment and document in CLAUDE.md that single-worker mode is required, and add a note about the race condition window.

---

## 2. Significant Issues (SHOULD FIX)

### SIG-1: Duplicate Files Return No Title/Artist from Pipeline

**File**: `audio-ident-service/app/ingest/pipeline.py`, lines 107-115
**Router Impact**: `audio-ident-service/app/routers/ingest.py`, line 199
**Severity**: Significant (Data Quality)

When the pipeline detects a hash-based duplicate (Step 1), it returns early without extracting metadata:

```python
if existing_id:
    result.status = "duplicate"
    result.track_id = existing_id
    return result  # result.title and result.artist are None
```

The router compensates at line 199:

```python
title=result.title or audio.filename or "Unknown"
```

This means duplicate detections show the uploaded filename (e.g., "track_231.mp3") instead of the actual track title ("Bohemian Rhapsody"). The plan explicitly identified this and recommended querying the existing track for its metadata (Section 4c, Option A), but this was not implemented.

**Impact**: For duplicates, the UI shows a generic filename instead of the actual track name, making the result confusing. The user sees `track_231.mp3 - Duplicate` instead of `"Bohemian Rhapsody" by Queen - Duplicate`.

**Recommended Fix**: In the pipeline's hash-duplicate path, query the existing track for title/artist:

```python
if existing_id:
    result.status = "duplicate"
    result.track_id = existing_id
    # Fetch metadata for the existing track
    from app.models.track import Track
    existing = await session.get(Track, existing_id)
    if existing:
        result.title = existing.title
        result.artist = existing.artist
    return result
```

---

### SIG-2: Error Response Shape Inconsistency Between Auth and Router

**File**: `audio-ident-service/app/auth/admin.py` (lines 29-35, 38-46) vs. `audio-ident-service/app/routers/ingest.py` (lines 58-68)
**Severity**: Significant (Contract Compliance)

The auth dependency raises `HTTPException` with `detail` as a nested dict:

```python
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

FastAPI wraps this as `{"detail": {"error": {"code": ..., "message": ...}}}`.

The router's `_error_response` returns:

```python
JSONResponse(content={"error": {"code": ..., "message": ...}})
```

This produces `{"error": {"code": ..., "message": ...}}` (no `detail` wrapper).

The API contract (Section "Common Types") specifies `ErrorResponse` as:

```typescript
interface ErrorResponse {
    error: {
        code: string;
        message: string;
        details?: unknown;
    };
}
```

So the router's format matches the contract, but the auth dependency's format wraps it inside `detail`, producing `{"detail": {"error": {...}}}` which does NOT match the contract.

**Impact**: Frontend error handling for 403 errors must parse `body.detail.error.code` instead of `body.error.code`. The test file confirms this discrepancy -- tests 3-5 assert `body["detail"]["error"]["code"]` (the HTTPException format), while test 6 asserts `body["error"]["code"]` (the JSONResponse format). The frontend `ingestAudio` function handles both patterns (lines 209-222 in client.ts), but this is a workaround, not a fix.

**Recommended Fix**: Change the auth dependency to return a JSONResponse directly (matching the router pattern), or create a custom exception handler that transforms HTTPExceptions into the standard ErrorResponse format:

```python
# In admin.py -- return JSONResponse instead of raising HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse

async def require_admin_key(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> None:
    if not settings.admin_api_key:
        # Cannot return JSONResponse from a dependency -- must raise
        # Best approach: use a custom exception class
        raise AdminAuthError("AUTH_NOT_CONFIGURED", "Admin API key not configured.")
```

Alternatively, add a global exception handler in main.py that normalizes HTTPException detail into the standard ErrorResponse shape.

---

### SIG-3: No `magic.from_buffer()` Exception Handling

**File**: `audio-ident-service/app/routers/ingest.py`, line 133
**Severity**: Significant (Error Handling)

The call to `magic.from_buffer(content, mime=True)` is not wrapped in a try/except:

```python
detected_type = magic.from_buffer(content, mime=True)
```

`python-magic` can raise `MagicException` if:
- The magic database is corrupt or missing
- The buffer contains data that causes libmagic to crash
- The system library `libmagic` is not properly installed

An unhandled exception here would result in a bare 500 Internal Server Error with no structured error response.

**Impact**: If libmagic encounters a problematic file, the user gets an opaque 500 error instead of a helpful message.

**Recommended Fix**:

```python
try:
    detected_type = magic.from_buffer(content, mime=True)
except Exception:
    logger.exception("Failed to detect MIME type for uploaded file")
    return _error_response(400, "UNSUPPORTED_FORMAT", "Unable to detect file format.")
```

---

### SIG-4: Multi-Worker Deployment Silently Breaks Concurrency Safety

**File**: `audio-ident-service/app/routers/ingest.py`, line 50
**Severity**: Significant (Operational Risk)

The `_ingest_lock = asyncio.Lock()` is a per-process lock. If the service is deployed with multiple Uvicorn workers (e.g., `uvicorn app.main:app --workers 4`), each worker has its own independent lock. Two concurrent requests handled by different workers would both acquire their respective locks and proceed, violating the Olaf LMDB single-writer constraint and potentially corrupting the fingerprint index.

The plan acknowledges this (Risk #7 in Section 10) but the implementation contains no guard against it.

**Impact**: LMDB corruption in multi-worker deployment. Recovery requires `make rebuild-index`.

**Recommended Fix**:
1. Add a startup check that warns or refuses to start if `workers > 1` is detected
2. Document in CLAUDE.md that the ingest endpoint requires single-worker mode
3. Consider a file-based lock or Redis lock for production deployments

At minimum, add a comment to the lock:

```python
# WARNING: This lock is per-process. Multi-worker deployments (e.g., --workers > 1)
# will have separate locks per worker, defeating the single-writer constraint.
# The ingest endpoint REQUIRES single-worker mode for correctness.
_ingest_lock = asyncio.Lock()
```

---

### SIG-5: `uuid_mod` Import Inside Function Body

**File**: `audio-ident-service/app/routers/ingest.py`, line 195
**Severity**: Significant (Code Quality)

```python
import uuid as uuid_mod

return IngestResponse(
    track_id=result.track_id or uuid_mod.uuid4(),
    ...
)
```

There is an import statement inside the function body (`import uuid as uuid_mod`). This is a code quality issue for several reasons:

1. **Import at module level is the Python convention** (PEP 8)
2. The `uuid` module is a stdlib import with negligible cost
3. Inline imports are harder to find and maintain
4. The aliasing to `uuid_mod` suggests a naming conflict that should be resolved at the module level

Additionally, the fallback `result.track_id or uuid_mod.uuid4()` generates a random UUID when the pipeline returns `None` for `track_id`. This should NEVER happen for successful ingestion (the pipeline always sets `track_id`). A random UUID here would create a response pointing to a non-existent track, which is worse than returning an error.

**Recommended Fix**:
- Move `import uuid` to the top of the file
- Replace the fallback with an explicit error:

```python
if result.track_id is None:
    return _error_response(503, "SERVICE_UNAVAILABLE", "Ingestion succeeded but no track ID returned.")
```

---

### SIG-6: Frontend Tests Only Test the Client Function, Not the Component

**File**: `audio-ident-ui/tests/ingest.test.ts`
**Severity**: Significant (Test Coverage)

The four frontend tests exclusively test the `ingestAudio()` client function by mocking `fetch`. They do not test:

1. The `/admin/ingest` page component renders correctly
2. The security warning banner appears when no admin key is set
3. The two-step confirmation flow works
4. The drag-and-drop interaction
5. The recent results list
6. The disabled state when ingesting
7. File validation in the component (`validateFile()`)

The tests verify that the API client correctly parses HTTP responses, but they provide zero coverage of the actual user interface logic, which is where most bugs tend to live.

**Impact**: UI bugs in the component (e.g., broken drag-and-drop, missing state transitions, incorrect conditional rendering) would not be caught by the test suite.

**Recommended Fix**: Add component-level tests using `@testing-library/svelte` or similar:

```typescript
// Example component test
import { render, fireEvent } from '@testing-library/svelte';
import IngestPage from '../src/routes/admin/ingest/+page.svelte';

test('shows security warning when no admin key configured', () => {
    const { getByText } = render(IngestPage);
    expect(getByText('Admin API key not configured')).toBeTruthy();
});
```

---

### SIG-7: Frontend `confirmStep` State Can Persist Across File Changes

**File**: `audio-ident-ui/src/routes/admin/ingest/+page.svelte`, lines 98-110, 151-157
**Severity**: Significant (UX Bug)

If a user:
1. Selects file A
2. Clicks "Ingest" (entering confirm step -- `confirmStep = true`)
3. Drags and drops file B (which calls `handleFile`)

The `handleFile` function at line 100 resets `confirmStep = false`, which is correct. However, consider this sequence:

1. Selects file A
2. Clicks "Ingest" (confirmStep = true, button shows "Are you sure?")
3. Clicks "Are you sure?" -- this calls `doIngest()` at line 157
4. While ingesting (`isIngesting = true`), the button area shows the spinner
5. Ingestion completes, `selectedFile` is set to `null` at line 173
6. But `confirmStep` is reset to `false` at line 164 inside `doIngest()`

This flow is actually correct. However, there is a subtle issue: the confirm button has no timeout. If a user clicks "Ingest" and then walks away, the confirmation state persists indefinitely. When they return and click again, it immediately submits. This is a minor UX concern but worth noting.

More importantly: there is no double-submit protection beyond the `isIngesting` flag. If the user rapidly double-clicks the confirmation button, `doIngest()` could be called twice before `isIngesting` is set to `true` (since `isIngesting = true` is on line 162, inside the async function). In practice, the `_ingest_lock` on the backend would reject the second request with 429, but the user would see an error.

**Recommended Fix**: Add a guard at the start of `doIngest()`:

```typescript
async function doIngest() {
    if (!selectedFile || !hasAdminKey || isIngesting) return;  // Add isIngesting check
    isIngesting = true;
    // ...
}
```

---

## 3. Minor Issues (NICE TO FIX)

### MIN-1: No File Size Limit in FastAPI Configuration

**File**: `audio-ident-service/app/routers/ingest.py`
**Severity**: Minor (Defense in Depth)

While the router checks `len(content) > MAX_UPLOAD_BYTES` after reading the entire file into memory (line 120: `content = await audio.read()`), FastAPI/Starlette reads the entire upload into memory BEFORE this check. A malicious actor could upload a 1 GB file, which would be fully loaded into memory before being rejected.

FastAPI does not have a built-in request size limit (Starlette's `max_request_size` must be configured explicitly). The application relies solely on the post-hoc check.

**Recommended Fix**: Configure a request body size limit in the ASGI middleware or reverse proxy:

```python
# In main.py or middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
# Or use Nginx: client_max_body_size 60m;
```

---

### MIN-2: Keyed `{#each}` Block Uses Non-Unique Key

**File**: `audio-ident-ui/src/routes/admin/ingest/+page.svelte`, line 350
**Severity**: Minor (Potential Rendering Bug)

```svelte
{#each recentResults as entry (entry.timestamp.getTime() + entry.filename)}
```

The key is `timestamp.getTime() + filename`. `getTime()` returns a number (milliseconds since epoch), and `filename` is a string. The `+` operator converts the number to a string and concatenates. This means:
- `1739000000000 + "song.mp3"` = `"1739000000000song.mp3"`

While unlikely, if two files with the same name are ingested in the same millisecond, the keys would collide. A UUID or auto-incrementing counter would be more robust.

**Recommended Fix**: Add an `id` field to `IngestResultEntry`:

```typescript
interface IngestResultEntry {
    id: number;  // auto-incrementing counter
    filename: string;
    response: IngestResponse | null;
    error: string | null;
    timestamp: Date;
}
```

---

### MIN-3: `audio/m4a` and `audio/x-m4a` MIME Types Not in Backend Allowed List

**File**: `audio-ident-service/app/routers/ingest.py`, lines 37-47
**Frontend**: `audio-ident-ui/src/routes/admin/ingest/+page.svelte`, lines 45-57
**Severity**: Minor (Format Support Inconsistency)

The frontend `ALLOWED_TYPES` includes `audio/x-m4a` but the backend `ALLOWED_MIME_TYPES` does not. While `.m4a` files are typically detected as `audio/mp4` by libmagic (so they would pass), there is a theoretical inconsistency.

The API contract (line 320) lists "Supported audio formats: MP3, WAV, FLAC, OGG" -- it does not mention WebM or MP4/M4A at all. The implementation supports more formats than the contract documents.

**Recommended Fix**: Either add `audio/x-m4a` to the backend's `ALLOWED_MIME_TYPES`, or remove it from the frontend's `ALLOWED_TYPES`. Also consider updating the contract to reflect the actual supported formats.

---

### MIN-4: No Abort/Cancel Support During Ingestion

**File**: `audio-ident-ui/src/routes/admin/ingest/+page.svelte`, line 168
**Client**: `audio-ident-ui/src/lib/api/client.ts`, line 187 (signal parameter)
**Severity**: Minor (UX)

The `ingestAudio` function accepts an `AbortSignal` parameter, but the page component never creates an `AbortController` or passes a signal:

```typescript
const response = await ingestAudio(selectedFile, adminKey);  // No signal
```

There is no cancel button during ingestion. The UI shows "Ingesting... This may take 10-30 seconds" but the user cannot abort the operation.

**Recommended Fix**: Add a cancel button during the ingestion progress state that aborts the fetch request. Note that cancellation only aborts the HTTP request client-side; the backend will continue processing.

---

### MIN-5: NavBar Mobile Layout May Be Crowded with Three Items

**File**: `audio-ident-ui/src/lib/components/NavBar.svelte`, lines 37-73
**Severity**: Minor (Responsive Design)

The NavBar now has four items: logo + Identify + Library + Admin + health dot. On small screens (< 375px), these items could overflow or wrap. The plan noted this concern (Section 5d) and suggested evaluating whether a hamburger menu is needed.

The current implementation uses `gap-2 sm:gap-4` with small text sizes (`text-xs` at mobile), which helps. However, no explicit overflow handling exists.

**Recommended Fix**: Test on a 320px viewport (iPhone SE) and add `overflow-x-auto` or a hamburger menu if items wrap.

---

### MIN-6: Temp File Suffix Extraction Could Fail Silently

**File**: `audio-ident-service/app/routers/ingest.py`, line 143
**Severity**: Minor (Edge Case)

```python
suffix = Path(audio.filename or "upload").suffix or ".bin"
```

If the filename is `None` (possible when uploaded programmatically without a filename), `Path("upload").suffix` returns `""`, so the suffix falls back to `".bin"`. This is acceptable but the downstream pipeline relies on file extension for format detection in some cases. A `.bin` extension could cause the pipeline to fail at the ffmpeg decode step with an unhelpful error message.

Additionally, extremely long filenames (>255 characters), filenames with null bytes, or filenames with path separators (`../../evil.mp3`) are passed directly to `Path()`. While `tempfile.NamedTemporaryFile` uses only the suffix (not the full filename), this is worth documenting.

**Recommended Fix**: Sanitize the suffix more explicitly:

```python
raw_suffix = Path(audio.filename or "").suffix.lower() if audio.filename else ""
suffix = raw_suffix if raw_suffix in {".mp3", ".wav", ".flac", ".ogg", ".webm", ".mp4", ".m4a"} else ".bin"
```

---

## 4. Things Done Well

### GOOD-1: Fail-Closed Admin Auth Design

The admin auth dependency correctly implements fail-closed behavior: if `ADMIN_API_KEY` is not configured (empty string), ALL requests are rejected. This prevents accidental exposure of the ingest endpoint in deployments where the environment variable was forgotten. The dedicated error code `AUTH_NOT_CONFIGURED` provides clear guidance.

### GOOD-2: Clean Separation of Concerns

The implementation follows a clean layered architecture:
- `app/auth/admin.py` -- authentication concern only
- `app/routers/ingest.py` -- HTTP layer (validation, response mapping)
- `app/ingest/pipeline.py` -- business logic (ingestion steps)
- `app/schemas/ingest.py` -- data contracts

Each layer has a single responsibility and is independently testable.

### GOOD-3: Comprehensive Backend Test Coverage

The 12 backend tests cover all documented scenarios: success, duplicate, auth (missing/wrong/unconfigured), format validation, empty file, oversized file, duration limits, concurrent rejection, and missing field. The concurrent rejection test uses `asyncio.Event` for precise control, which is better than relying on `asyncio.sleep()` timing.

### GOOD-4: Temp File Cleanup in `finally` Block

The temp file cleanup at line 164-166 is correctly placed in a `finally` block:

```python
finally:
    tmp_path.unlink(missing_ok=True)
```

This ensures cleanup happens even if the pipeline raises an unexpected exception. The `missing_ok=True` prevents errors if the file was already cleaned up by the pipeline.

### GOOD-5: Frontend Error Handling Covers Multiple Response Formats

The `ingestAudio()` client function handles three different error response formats:
1. Standard `{"error": {"code": ..., "message": ...}}` (from `_error_response`)
2. FastAPI's `{"detail": {...}}` format (from `HTTPException`)
3. FastAPI's validation errors `{"detail": [{"msg": ...}]}` (from 422)

This defensive parsing ensures the frontend can display meaningful error messages regardless of which backend component generates the error.

### GOOD-6: Two-Step Confirmation Flow

The ingest button requires two clicks (first shows "Are you sure?", second submits). This prevents accidental ingestion and is a good UX pattern for irreversible operations. The confirmation state is properly reset on file change, file clear, and after submission.

### GOOD-7: Generated Types Used Correctly

The `IngestResponse` type is correctly imported from `generated.ts` in `client.ts` (line 11), not hand-written. The Svelte component imports from `client.ts` (line 11 of +page.svelte). This follows the project convention and ensures type safety across the stack.

### GOOD-8: Proper Use of Svelte 5 Runes

The page component uses `$state()`, `$derived()`, and modern Svelte 5 event handlers (`onclick`, `ondrop`, etc.) rather than legacy Svelte 4 patterns. No `$:` reactive statements or stores are used.

### GOOD-9: Accessibility Attributes

The implementation includes appropriate accessibility attributes:
- `aria-live="polite"` on the results area (line 347)
- `aria-busy` on the page container (line 200)
- `aria-disabled` on the ingest button (line 318)
- `role="alert"` on warning and error banners (lines 205, 295)
- `aria-label` on the file input and drop zone (lines 236, 264)

### GOOD-10: Lock Reset Between Tests

The test fixture `_reset_ingest_lock` (lines 99-108) creates a fresh `asyncio.Lock()` for each test and restores the original after. This prevents test pollution from a locked state leaking between tests.

---

## 5. Summary Table

| ID | Category | Severity | File | Issue |
|----|----------|----------|------|-------|
| CRIT-1 | Security | Critical | `app/auth/admin.py:37` | String comparison not timing-safe |
| CRIT-2 | Concurrency | Critical | `app/routers/ingest.py:112-151` | TOCTOU race between lock check and acquire |
| SIG-1 | Data Quality | Significant | `app/ingest/pipeline.py:107-115` | Duplicate files return no title/artist |
| SIG-2 | Contract | Significant | `app/auth/admin.py` vs router | Error response shape inconsistency |
| SIG-3 | Error Handling | Significant | `app/routers/ingest.py:133` | No exception handling for `magic.from_buffer()` |
| SIG-4 | Operational | Significant | `app/routers/ingest.py:50` | Multi-worker deployment silently breaks concurrency |
| SIG-5 | Code Quality | Significant | `app/routers/ingest.py:195` | Inline import + dangerous UUID fallback |
| SIG-6 | Test Coverage | Significant | `tests/ingest.test.ts` | No component-level frontend tests |
| SIG-7 | UX | Significant | `+page.svelte:151-157` | No double-submit protection in `doIngest()` |
| MIN-1 | Security | Minor | `app/routers/ingest.py` | No ASGI-level upload size limit |
| MIN-2 | Rendering | Minor | `+page.svelte:350` | Non-unique `{#each}` key possible |
| MIN-3 | Consistency | Minor | Router vs Frontend | M4A MIME type mismatch between backend/frontend |
| MIN-4 | UX | Minor | `+page.svelte:168` | No abort/cancel support during ingestion |
| MIN-5 | Responsive | Minor | `NavBar.svelte` | Mobile layout may be crowded with 3 nav items |
| MIN-6 | Edge Case | Minor | `app/routers/ingest.py:143` | Temp file suffix not sanitized |

---

## 6. Recommended Fix Priority

### Immediate (Before Merge)

1. **CRIT-1**: Use `hmac.compare_digest()` for admin key comparison (5 minutes)
2. **SIG-5**: Move `import uuid` to module level and remove dangerous UUID fallback (5 minutes)
3. **SIG-3**: Add try/except around `magic.from_buffer()` (5 minutes)
4. **SIG-7**: Add `isIngesting` guard to `doIngest()` (2 minutes)

### Short-Term (Before Phase 4)

5. **CRIT-2**: Document TOCTOU race and single-worker requirement; consider restructuring to minimize race window (30 minutes)
6. **SIG-1**: Query existing track metadata for hash-based duplicates (15 minutes)
7. **SIG-2**: Normalize error response format between auth and router (30 minutes)
8. **SIG-4**: Add single-worker mode documentation and optional startup check (15 minutes)

### Medium-Term (Backlog)

9. **SIG-6**: Add component-level frontend tests (60 minutes)
10. **MIN-1**: Configure ASGI request body size limit (15 minutes)
11. **MIN-3**: Align MIME type lists between backend and frontend (10 minutes)
12. **MIN-4**: Add abort controller support to ingest page (20 minutes)
13. **MIN-6**: Sanitize temp file suffix (10 minutes)

---

*Review completed 2026-02-15. All file paths are relative to the project root `/Users/mac/workspace/audio-ident/`.*
