# QA Research: Edge Cases & Test Plan

> **Author**: QA Researcher
> **Date**: 2026-02-16
> **Scope**: MP3 playback feature — backend streaming endpoint + frontend Mp3Player component

---

## Findings

### Existing Test Infrastructure

#### Backend (pytest)

| File | Purpose | Pattern |
|------|---------|---------|
| `audio-ident-service/tests/conftest.py` | Global fixture: `AsyncClient` via `httpx.ASGITransport` | Shared `client` fixture |
| `audio-ident-service/tests/test_health.py` | Basic endpoint testing | `@pytest.mark.asyncio`, simple GET assertions |
| `audio-ident-service/tests/test_tracks.py` | Paginated listing + detail tests | In-memory SQLite via `aiosqlite`, dependency override for `get_db`, class-based test grouping |
| `audio-ident-service/tests/test_ingest_router.py` | Ingest endpoint with auth, MIME, concurrency | `unittest.mock.patch` for external deps, custom WAV fixture, `asyncio.Lock` testing |
| `audio-ident-service/tests/test_audio_storage.py` | Storage path generation | `@patch("...settings")`, `tmp_path` fixture |
| `audio-ident-service/tests/test_search_exact.py` | Exact search lane with Olaf mocks | Complex `AsyncMock` patterns for CFFI calls, sub-window consensus testing |

**Key backend testing patterns**:
- `asyncio_mode = "auto"` in `pyproject.toml` — no need for `@pytest.mark.asyncio` on every test (but some files still use it explicitly)
- Integration tests create minimal FastAPI apps with only the router under test
- `dependency_overrides[get_db]` pattern for SQLite-backed tests
- External services (Qdrant, Olaf, CLAP model) are always mocked in unit tests
- `app.state.*` attributes are set via mock for tests that need CLAP/Qdrant
- No test fixtures directory for audio files currently exists (noted in `CLAUDE.md` as `tests/fixtures/audio/` but directory not yet created)

#### Frontend (Vitest)

| Item | Detail |
|------|--------|
| Config | `audio-ident-ui/vite.config.ts` — `test.include: ['tests/**/*.test.ts']`, `environment: 'jsdom'` |
| Libraries | `vitest ^4.0.18`, `@testing-library/svelte ^5.3.1`, `jsdom ^28.0.0` |
| Existing tests | **None** — no `tests/` directory or `.test.ts` files exist yet |
| Test runner | `pnpm test` runs `vitest run` |

**Key frontend testing patterns**:
- Uses `@testing-library/svelte` (Svelte 5 compatible) — `render`, `screen`, `fireEvent` available
- jsdom environment does NOT implement `HTMLMediaElement` — `Audio()`, `play()`, `pause()`, `currentTime`, etc. are all stubs that do nothing
- No Playwright or E2E setup exists

#### E2E Tests

**No E2E test infrastructure exists.** No Playwright configuration, no browser test files.

### Relevant Existing Architecture

| Component | Detail |
|-----------|--------|
| Track model | `app/models/track.py` — `file_path` (e.g., `/data/raw/ab/abc123.mp3`), `file_size_bytes`, `format` |
| Storage | `app/audio/storage.py` — `raw_audio_path(hash, ext)` returns `{root}/raw/{hash[:2]}/{hash}.{ext}` |
| API client | `audio-ident-ui/src/lib/api/client.ts` — `fetchJSON<T>()` helper, `ApiRequestError` class |
| CORS | `allow_methods=["*"]`, `allow_headers=["*"]` from `http://localhost:17000` — **Range and Accept-Ranges headers will pass through** |
| Vite proxy | `/api` proxied to `http://localhost:17010` with `changeOrigin: true` |
| API contract | `docs/api-contract.md` v1.1.0 — **no streaming endpoint yet** |
| Auth | Stub only — no JWT enforcement. Admin key used only for ingest endpoint |

---

## Proposal

### Risk List with Mitigations

#### RISK-01: Safari / iOS Audio Playback Quirks

**Risk**: Safari requires user gesture to start audio playback (autoplay policy). iOS Safari has additional restrictions: `Audio()` objects created outside user gesture handlers may be silently blocked. Safari's `<audio>` element can have different behavior with Range requests.

**Impact**: HIGH — audio won't play on Safari/iOS without user gesture.

**Mitigations**:
1. Always create `Audio()` object and call `.play()` inside a click/touch event handler (Svelte `onclick`)
2. Never auto-play — require explicit "Play" button interaction
3. Use `audio.play().catch(err => ...)` to detect autoplay rejection and show a "Tap to play" fallback
4. Test: `audio.canPlayType('audio/mpeg')` returns `'probably'` or `'maybe'` on all target browsers
5. For iOS: avoid creating `new Audio()` in advance — construct it only when the user clicks play

**Test approach**: Manual test matrix on Safari macOS + iOS Safari (simulator or device). Automated Vitest tests can verify the component calls `.play()` inside event handlers.

---

#### RISK-02: Auth + CORS + Range Header Interaction

**Risk**: If authentication is added later (JWT Bearer tokens), preflight CORS requests for Range headers may fail if the backend doesn't include `Range` in `Access-Control-Allow-Headers` and `Accept-Ranges` / `Content-Range` in `Access-Control-Expose-Headers`.

**Impact**: MEDIUM — currently low risk since auth is stub-only and CORS allows `*` headers. Becomes HIGH when real auth is enforced.

**Mitigations**:
1. Backend CORS already uses `allow_headers=["*"]` — Range requests pass through
2. Verify `Access-Control-Expose-Headers` includes `Content-Range`, `Accept-Ranges`, `Content-Length` — the frontend needs to read `Content-Range` from the response to determine total file size for seeking
3. The current `allow_headers=["*"]` does NOT expose response headers — explicitly add `expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"]` to the CORS middleware

**Test approach**: curl with `-H "Origin: http://localhost:17000"` to verify CORS response headers. Backend pytest test for OPTIONS preflight with Range.

---

#### RISK-03: Proxy / CDN Behavior with Range Requests

**Risk**: The Vite dev proxy (`changeOrigin: true`) and future production reverse proxies / CDNs may strip Range headers, convert 206 to 200, or incorrectly cache partial responses.

**Impact**: MEDIUM — dev proxy is tested path; production CDN behavior varies.

**Mitigations**:
1. Verify Vite proxy preserves Range headers (test in dev)
2. For production, ensure reverse proxy (nginx/Caddy) passes Range headers
3. Backend must set `Accept-Ranges: bytes` on both 200 and 206 responses
4. Backend should return 200 (full file) if no Range header is present (graceful fallback)

**Test approach**: curl through both direct backend (port 17010) and Vite proxy (port 17000) with Range headers. Verify 206 status and correct Content-Range.

---

#### RISK-04: VBR MP3 Seeking Accuracy

**Risk**: Variable bitrate MP3 files have inconsistent byte-to-time mapping. HTTP byte-range seeking (what browsers use natively with `<audio>`) may land at incorrect positions in VBR files. Duration calculation may be inaccurate.

**Impact**: LOW-MEDIUM — the browser's media element handles VBR seeking via the Xing/VBRI header if present. The backend's byte-range serving is independent of VBR.

**Mitigations**:
1. The backend serves raw bytes — it does NOT need to understand VBR. The browser's audio decoder handles time-to-byte mapping internally.
2. The `duration_seconds` field comes from mutagen (metadata extraction at ingestion time) — this is accurate for both VBR and CBR.
3. Browser's `audio.duration` property is populated from the file's metadata headers, not from byte counting.
4. Ensure the backend sends correct `Content-Length` for the full file — browsers need this for seeking.

**Test approach**: Ingest both CBR and VBR MP3 files. Verify `duration_seconds` in the database matches `audio.duration` in the browser (within 0.5s tolerance).

---

#### RISK-05: Dialog Close During Active Playback

**Risk**: If the user closes the playback dialog while audio is playing, the `Audio` object may continue playing in the background (orphaned audio), cause memory leaks, or trigger errors when event handlers fire on unmounted components.

**Impact**: HIGH — directly affects user experience and resource cleanup.

**Mitigations**:
1. Component `onDestroy` (Svelte) or `$effect` cleanup must: call `audio.pause()`, set `audio.src = ""`, remove all event listeners, and null the reference
2. Dialog close handler should explicitly trigger cleanup before DOM removal
3. Avoid storing `Audio` objects in module-level state — keep them scoped to the component instance

**Test approach**: Vitest test mocking `Audio` to verify cleanup is called on component destroy. Manual test: open dialog, play audio, close dialog, verify no audio continues.

---

#### RISK-06: Concurrent Playback Prevention

**Risk**: Multiple playback dialogs could be opened simultaneously (e.g., rapid clicking on different tracks), creating multiple `Audio` objects and overlapping playback. Race conditions with rapid open/close.

**Impact**: MEDIUM — confusing UX but not data corruption.

**Mitigations**:
1. Use a single shared `Audio` instance (or ensure only one exists at a time)
2. Dialog should be modal — only one open at a time (Svelte 5 `<dialog>` element or controlled state)
3. When opening a new track, stop/cleanup the previous one first
4. Debounce rapid open/close actions

**Test approach**: Vitest test: render component, trigger play for track A, immediately trigger play for track B — verify track A is stopped before track B starts.

---

#### RISK-07: Large File Handling

**Risk**: Files in the database can be up to 50MB (ingest limit). Streaming very large files on slow connections may cause long load times, timeout errors, or excessive memory usage on the client.

**Impact**: LOW — Range requests mitigate this since the browser only requests chunks as needed. The backend streams from disk, not memory.

**Mitigations**:
1. Backend streams file from disk (not loading entire file into memory)
2. Browser's `<audio>` element with Range support naturally handles buffering
3. Consider adding a `loading` / `buffering` UI state indicator
4. No explicit timeout on the backend for streaming responses (Starlette `FileResponse` / `StreamingResponse` handles this)

**Test approach**: Backend test with a mock large file verifying streaming behavior. Frontend test verifying loading/buffering state is shown.

---

#### RISK-08: Missing / Corrupted / Deleted Files

**Risk**: The file referenced by `Track.file_path` may have been deleted, moved, or corrupted on disk. The backend must handle this gracefully.

**Impact**: HIGH — will cause 500 errors if unhandled.

**Mitigations**:
1. Backend: Check `Path.exists()` and `Path.is_file()` before attempting to serve — return 404 with `FILE_NOT_FOUND` error code
2. Backend: Catch `OSError` / `PermissionError` during file read — return 500 with logging
3. Frontend: Display clear error message ("Track audio file is unavailable") rather than a generic error
4. Optional: Verify file size matches `Track.file_size_bytes` to detect truncation/corruption

**Test approach**: Backend pytest test with `tmp_path` — create a track pointing to a non-existent file, request streaming endpoint, assert 404. Test with a zero-byte file, assert appropriate error.

---

#### RISK-09: Network Interruption During Streaming

**Risk**: Network drops during audio streaming will cause playback to stall. The browser may or may not retry automatically depending on the implementation.

**Impact**: MEDIUM — the browser's `<audio>` element handles buffering and retry natively for HTTP Range requests.

**Mitigations**:
1. Browser's built-in `<audio>` element handles buffering/retry automatically
2. Listen for `audio.onerror` and `audio.onstalled` events — show user-friendly error with retry button
3. Listen for `audio.onwaiting` — show buffering indicator
4. Frontend should NOT implement custom retry logic — let the browser handle it

**Test approach**: Vitest test that simulates `error` and `stalled` events on the mock Audio object and verifies UI shows appropriate error state.

---

#### RISK-10: Browser Memory with Multiple Audio Objects

**Risk**: Creating `new Audio()` on each dialog open without proper cleanup leads to memory leaks. Each `Audio` object holds internal buffers and decoded audio data.

**Impact**: MEDIUM — only noticeable with repeated open/close cycles.

**Mitigations**:
1. Single instance pattern — reuse the same `Audio` object, just change `src`
2. On close: `audio.pause(); audio.removeAttribute('src'); audio.load();` — this releases internal buffers
3. Remove all event listeners on cleanup to prevent handler accumulation

**Test approach**: Vitest test verifying `Audio` object cleanup methods are called. Manual test: open/close dialog 50 times with DevTools Memory panel open — verify no memory growth.

---

#### RISK-11: Content-Type Header for Streaming Response

**Risk**: Backend must return correct `Content-Type: audio/mpeg` for MP3 files. Incorrect MIME type may cause the browser to download instead of play, or refuse to decode.

**Impact**: HIGH — playback won't work without correct Content-Type.

**Mitigations**:
1. Determine Content-Type from the file's `format` field in the Track model, or use `python-magic` for MIME detection
2. For MP3: `audio/mpeg` (NOT `audio/mp3`)
3. For WAV: `audio/wav`
4. For FLAC: `audio/flac`
5. For OGG: `audio/ogg`

**Test approach**: Backend pytest test verifying `Content-Type` header on streaming responses for each supported format.

---

#### RISK-12: Security — Path Traversal via Track ID

**Risk**: The streaming endpoint takes a track UUID, looks up the file_path, and serves it. If there's a bug that allows injecting a custom path (e.g., SQL injection or parameter manipulation), an attacker could read arbitrary files.

**Impact**: CRITICAL (but low probability with current design).

**Mitigations**:
1. Always look up `file_path` from the database by UUID — never accept a file path from the client
2. Validate that the resolved file path starts with `settings.audio_storage_root` (path traversal guard)
3. UUID parameter validation in FastAPI ensures only valid UUIDs reach the handler
4. Never expose `file_path` in the streaming endpoint's error messages

**Test approach**: Backend pytest test: attempt path traversal by manipulating the database record's `file_path` to point outside storage root — verify the endpoint rejects it.

---

### Detailed Test Plan

#### Phase 1: Backend Streaming Endpoint Tests

**File**: `audio-ident-service/tests/test_track_stream.py`

**Test infrastructure setup** (follows existing patterns from `test_tracks.py`):

```python
"""Tests for GET /api/v1/tracks/{track_id}/stream.

Uses in-memory SQLite + temporary audio files to test Range request
handling, error cases, and header correctness.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.models.track import Track

_test_engine = create_async_engine("sqlite+aiosqlite://", echo=False)
_test_session_factory = async_sessionmaker(_test_engine, expire_on_commit=False)

@event.listens_for(_test_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()

@pytest.fixture(autouse=True)
async def _setup_tables():
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

async def _override_get_db():
    async with _test_session_factory() as session:
        yield session

@pytest.fixture
def stream_app(tmp_path):
    from fastapi import FastAPI
    from app.db.session import get_db
    from app.routers import tracks

    application = FastAPI()
    application.include_router(tracks.router, prefix="/api/v1")
    application.dependency_overrides[get_db] = _override_get_db
    return application

@pytest.fixture
async def client(stream_app) -> AsyncClient:
    transport = ASGITransport(app=stream_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

@pytest.fixture
def mp3_file(tmp_path) -> Path:
    """Create a minimal fake MP3 file for testing."""
    # Real MP3 header: ID3 tag + MPEG frame header
    # For range-request testing, content doesn't need to be playable
    fake_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 9996  # 10KB fake MP3
    path = tmp_path / "raw" / "ab" / "abcdef.mp3"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(fake_mp3)
    return path

@pytest.fixture
async def seed_track(mp3_file) -> Track:
    """Insert a track with a real file path."""
    track = Track(
        id=uuid.uuid4(),
        title="Test Song",
        artist="Test Artist",
        duration_seconds=180.0,
        file_hash_sha256="ab" + "cd" * 31,
        file_size_bytes=len(mp3_file.read_bytes()),
        file_path=str(mp3_file),
        format="mp3",
        ingested_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    async with _test_session_factory() as session:
        session.add(track)
        await session.commit()
    return track
```

**Test cases** (38 tests organized by category):

##### 1. Happy Path — Full File Request (no Range header)

```
test_stream_full_file_returns_200
  - GET /api/v1/tracks/{id}/stream (no Range header)
  - Assert: status 200, Content-Type audio/mpeg, Accept-Ranges: bytes
  - Assert: Content-Length matches file size
  - Assert: body matches file bytes

test_stream_full_file_correct_content_type_mp3
  - Track with format="mp3" -> Content-Type: audio/mpeg

test_stream_full_file_accept_ranges_header_present
  - Assert: Accept-Ranges: bytes in response headers
```

##### 2. Happy Path — Range Requests (206 Partial Content)

```
test_stream_range_first_1000_bytes
  - Range: bytes=0-999
  - Assert: 206, Content-Range: bytes 0-999/{total}, Content-Length: 1000

test_stream_range_last_1000_bytes
  - Range: bytes=-1000
  - Assert: 206, Content-Range: bytes {total-1000}-{total-1}/{total}

test_stream_range_from_offset_to_end
  - Range: bytes=5000-
  - Assert: 206, Content-Range: bytes 5000-{total-1}/{total}

test_stream_range_middle_chunk
  - Range: bytes=1000-1999
  - Assert: 206, body is exactly bytes 1000-1999 of the file

test_stream_range_single_byte
  - Range: bytes=0-0
  - Assert: 206, Content-Length: 1, Content-Range: bytes 0-0/{total}

test_stream_range_entire_file
  - Range: bytes=0-{total-1}
  - Assert: 206 (or 200 — both acceptable), full file returned
```

##### 3. Range Error Cases

```
test_stream_range_beyond_file_size
  - Range: bytes=99999-100000 (file is only 10KB)
  - Assert: 416 Range Not Satisfiable, Content-Range: bytes */{total}

test_stream_range_invalid_format
  - Range: bytes=abc-def
  - Assert: 200 (ignore malformed range, serve full file) OR 400

test_stream_range_reversed_range
  - Range: bytes=5000-1000
  - Assert: 200 (ignore) or 416

test_stream_range_multipart_not_supported
  - Range: bytes=0-100, 200-300
  - Assert: 200 (serve full file — multipart ranges not required)
```

##### 4. Error Cases — Track / File Not Found

```
test_stream_track_not_found
  - GET /api/v1/tracks/{random_uuid}/stream
  - Assert: 404, error.code == "NOT_FOUND"

test_stream_invalid_uuid
  - GET /api/v1/tracks/not-a-uuid/stream
  - Assert: 422

test_stream_file_missing_on_disk
  - Track exists in DB but file_path points to non-existent file
  - Assert: 404, error.code == "FILE_NOT_FOUND" (or 500 with appropriate message)

test_stream_file_is_empty
  - Track exists, file exists but is 0 bytes
  - Assert: appropriate error (not a crash)
```

##### 5. Security Tests

```
test_stream_path_traversal_guard
  - Track with file_path set to "../../etc/passwd" in DB
  - Assert: endpoint rejects it (does not serve /etc/passwd)

test_stream_does_not_expose_file_path_in_error
  - Assert: error responses do not contain internal file paths
```

##### 6. Header Correctness

```
test_stream_content_disposition_inline
  - Assert: Content-Disposition: inline (not attachment — browser should play, not download)

test_stream_etag_or_last_modified
  - Optional: If ETag or Last-Modified is set, verify correctness

test_stream_cache_control
  - Assert: appropriate caching headers (at minimum: no unexpected no-cache)
```

##### 7. CORS Integration

```
test_stream_cors_preflight
  - OPTIONS /api/v1/tracks/{id}/stream with Origin header
  - Assert: Access-Control-Allow-Origin present

test_stream_cors_expose_headers
  - Assert: Content-Range is in Access-Control-Expose-Headers
```

#### curl Verification Commands (Manual / CI)

```bash
# 1. Full file request (no Range)
curl -v http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: 200 OK, Content-Type: audio/mpeg, Accept-Ranges: bytes

# 2. First 1KB range request
curl -v -H "Range: bytes=0-1023" \
  http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: 206 Partial Content, Content-Range: bytes 0-1023/{total}

# 3. Last 1KB
curl -v -H "Range: bytes=-1024" \
  http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: 206 Partial Content

# 4. Out-of-range request
curl -v -H "Range: bytes=999999999-999999999" \
  http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: 416 Range Not Satisfiable

# 5. Non-existent track
curl -v http://localhost:17010/api/v1/tracks/00000000-0000-0000-0000-000000000000/stream
# Expect: 404

# 6. CORS preflight
curl -v -X OPTIONS \
  -H "Origin: http://localhost:17000" \
  -H "Access-Control-Request-Method: GET" \
  -H "Access-Control-Request-Headers: Range" \
  http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: Access-Control-Allow-Headers includes Range (or *)

# 7. Verify through Vite proxy
curl -v -H "Range: bytes=0-1023" \
  http://localhost:17000/api/v1/tracks/{TRACK_ID}/stream 2>&1 | head -30
# Expect: 206, same headers as direct backend request

# 8. Content-Type verification
curl -sI http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream | grep -i content-type
# Expect: Content-Type: audio/mpeg

# 9. Accept-Ranges verification
curl -sI http://localhost:17010/api/v1/tracks/{TRACK_ID}/stream | grep -i accept-ranges
# Expect: Accept-Ranges: bytes
```

#### Phase 2: Frontend Component Tests (Vitest)

**File**: `audio-ident-ui/tests/Mp3Player.test.ts`

**jsdom limitations**: `HTMLMediaElement` is stubbed in jsdom. `Audio.play()` returns `undefined` (not a Promise), `.duration` is `NaN`, `.currentTime` is always 0. We must mock `Audio` or use a custom stub.

**Audio mock helper** (`audio-ident-ui/tests/helpers/audio-mock.ts`):

```typescript
/**
 * Creates a mock Audio object for Vitest/jsdom testing.
 *
 * Usage in test:
 *   const { audioMock, AudioConstructor } = createAudioMock();
 *   vi.stubGlobal('Audio', AudioConstructor);
 */
export function createAudioMock() {
  const listeners: Record<string, Function[]> = {};
  const audioMock = {
    src: '',
    currentTime: 0,
    duration: 180,
    paused: true,
    volume: 1,
    muted: false,
    readyState: 0,
    error: null as MediaError | null,
    play: vi.fn(() => {
      audioMock.paused = false;
      listeners['play']?.forEach(fn => fn());
      return Promise.resolve();
    }),
    pause: vi.fn(() => {
      audioMock.paused = true;
      listeners['pause']?.forEach(fn => fn());
    }),
    load: vi.fn(),
    addEventListener: vi.fn((event: string, handler: Function) => {
      (listeners[event] ??= []).push(handler);
    }),
    removeEventListener: vi.fn((event: string, handler: Function) => {
      const list = listeners[event];
      if (list) {
        const idx = list.indexOf(handler);
        if (idx >= 0) list.splice(idx, 1);
      }
    }),
    removeAttribute: vi.fn(),
    // Helpers for tests to trigger events
    _emit(event: string, data?: any) {
      listeners[event]?.forEach(fn => fn(data ?? {}));
    },
    _setReadyState(state: number) {
      audioMock.readyState = state;
      if (state >= 1) listeners['loadedmetadata']?.forEach(fn => fn());
      if (state >= 4) listeners['canplaythrough']?.forEach(fn => fn());
    },
  };

  const AudioConstructor = vi.fn(() => audioMock);

  return { audioMock, AudioConstructor, listeners };
}
```

**Test cases** (27 tests):

##### 1. Component Rendering

```
test_renders_play_button_initially
  - Render Mp3Player with trackId and src props
  - Assert: play button visible, pause button not visible

test_shows_track_title_in_player
  - Assert: track title displayed in the player UI

test_shows_duration_formatted
  - Assert: duration displayed as mm:ss

test_renders_progress_bar_at_zero
  - Assert: progress bar is at 0% initially
```

##### 2. Playback Controls

```
test_clicking_play_calls_audio_play
  - Click play button
  - Assert: audioMock.play() called
  - Assert: UI shows pause button

test_clicking_pause_calls_audio_pause
  - Start playback, click pause
  - Assert: audioMock.pause() called
  - Assert: UI shows play button

test_play_pause_toggle
  - Click play, then pause, then play
  - Assert: alternating play/pause calls

test_seeking_via_progress_bar
  - Click on progress bar at 50% position
  - Assert: audioMock.currentTime set to ~90 (for 180s track)
```

##### 3. State Updates

```
test_timeupdate_updates_current_time_display
  - Emit 'timeupdate' event with currentTime = 60
  - Assert: UI shows "1:00" or similar

test_loadedmetadata_sets_duration
  - Emit 'loadedmetadata' event
  - Assert: duration display updates

test_ended_event_resets_to_beginning
  - Emit 'ended' event
  - Assert: play button shown (not pause), currentTime display reset

test_buffering_state_shown_on_waiting
  - Emit 'waiting' event
  - Assert: loading/buffering indicator shown

test_buffering_hidden_on_canplay
  - Emit 'waiting' then 'canplay'
  - Assert: buffering indicator hidden
```

##### 4. Error Handling

```
test_error_event_shows_error_message
  - Set audioMock.error = { code: 4, message: 'MEDIA_ERR_SRC_NOT_SUPPORTED' }
  - Emit 'error' event
  - Assert: error message displayed in UI

test_network_error_shows_retry
  - Set audioMock.error = { code: 2, message: 'MEDIA_ERR_NETWORK' }
  - Emit 'error' event
  - Assert: retry button visible

test_404_src_shows_file_unavailable
  - Audio src returns 404 (simulated via error event)
  - Assert: "Track audio file is unavailable" message
```

##### 5. Cleanup / Lifecycle

```
test_component_destroy_pauses_audio
  - Render component, start playback, unmount component
  - Assert: audioMock.pause() called

test_component_destroy_clears_src
  - Unmount component
  - Assert: audioMock.removeAttribute('src') or audioMock.src = '' called

test_component_destroy_removes_event_listeners
  - Unmount component
  - Assert: audioMock.removeEventListener called for all registered events

test_changing_track_stops_previous
  - Play track A, then switch src to track B
  - Assert: track A audio paused, new Audio created or src changed
```

##### 6. Edge Cases

```
test_rapid_play_pause_does_not_crash
  - Click play/pause 10 times rapidly
  - Assert: no errors thrown

test_play_before_metadata_loaded
  - Click play when readyState is 0
  - Assert: graceful handling (wait for canplay, or show loading)

test_seek_while_paused
  - Seek to 50% while paused
  - Assert: currentTime updated but audio remains paused

test_volume_control
  - If volume slider exists: set volume to 0.5
  - Assert: audioMock.volume = 0.5

test_mute_toggle
  - If mute button exists: click mute
  - Assert: audioMock.muted = true
```

#### Phase 3: Integration Test Scenarios

These require the full stack running (`make dev`) and are best run as manual or Playwright tests.

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| 1 | Play a track from the library | Navigate to /tracks, click a track, click play in dialog | Audio plays, progress bar moves |
| 2 | Seek to middle of track | While playing, click middle of progress bar | Audio jumps to ~50%, continues playing |
| 3 | Close dialog during playback | Play a track, close dialog | Audio stops immediately, no background sound |
| 4 | Reopen same track | Play, close, reopen same track | Player resets to beginning, ready to play |
| 5 | Switch between tracks | Play track A, close, open track B | Track B loads and plays correctly |
| 6 | Network interruption | Play a track, disconnect network | Playback stalls, shows buffering indicator |
| 7 | Reconnect after interruption | Reconnect network after stall | Playback resumes or shows retry |
| 8 | Non-existent track | Navigate to /tracks/{invalid-uuid} | 404 page shown |
| 9 | Deleted audio file | Track in DB but file deleted from disk | Error message in player UI |
| 10 | Play from search results | Search, find a match, click to play | Navigates to track detail, player works |

#### Phase 4: Browser Test Matrix

| Browser | Platform | Priority | Key Concerns |
|---------|----------|----------|-------------|
| Chrome latest | macOS | P0 | Primary target, auto-play policy |
| Firefox latest | macOS | P0 | Different audio decoder, range request handling |
| Safari latest | macOS | P1 | WebKit audio quirks, autoplay restrictions |
| Safari | iOS 17+ | P1 | User gesture requirement, no auto-play |
| Chrome | Android | P2 | Touch events, audio focus |
| Edge | Windows | P2 | Chromium-based, should match Chrome |

**Manual test checklist per browser**:
- [ ] Audio loads and plays on first click
- [ ] Seeking via progress bar works
- [ ] Pause/resume works
- [ ] Duration display is correct
- [ ] Closing dialog stops audio
- [ ] Error state shown for missing files
- [ ] No console errors during playback

#### Phase 5: Performance Test Scenarios

| # | Scenario | Method | Threshold |
|---|----------|--------|-----------|
| 1 | Time to first audio byte | curl with timing | < 100ms (local), < 500ms (network) |
| 2 | Memory stability (50 open/close cycles) | Chrome DevTools Memory tab | Heap growth < 5MB |
| 3 | Concurrent streams (10 clients) | `for i in {1..10}; do curl ... &; done` | All return 206, no errors |
| 4 | Large file (50MB) first byte time | curl with timing + Range | < 200ms |
| 5 | Seek latency | Chrome DevTools Performance tab | < 300ms from click to audio |

---

## Minimal Steps (Implementation Order)

### Step 1: Backend Tests First (test-driven)

1. Create `audio-ident-service/tests/test_track_stream.py` with the test infrastructure shown above
2. Write the 20+ backend tests (all will fail initially since the endpoint doesn't exist)
3. Implement the streaming endpoint in `app/routers/tracks.py`
4. Run tests until all pass: `cd audio-ident-service && uv run pytest tests/test_track_stream.py`

### Step 2: CORS Fix

1. Add `expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"]` to CORS middleware in `app/main.py`
2. Verify with curl CORS test commands above

### Step 3: Frontend Test Setup

1. Create `audio-ident-ui/tests/` directory
2. Create `audio-ident-ui/tests/helpers/audio-mock.ts` with the Audio mock helper
3. Create `audio-ident-ui/tests/Mp3Player.test.ts` with component tests
4. Run: `cd audio-ident-ui && pnpm test`

### Step 4: Frontend Component

1. Implement `Mp3Player.svelte` component
2. Integrate into track detail page (dialog)
3. Run frontend tests until all pass

### Step 5: Integration Verification

1. Start full stack: `make dev`
2. Run curl verification commands
3. Manual browser testing (Chrome, Firefox, Safari)

### Step 6: API Contract Update

1. Add streaming endpoint to `docs/api-contract.md` (version bump to 1.2.0)
2. Copy contract to all three locations
3. Regenerate frontend types: `make gen-client`

---

## Open Questions

1. **Should the streaming endpoint require authentication?** Currently no auth is enforced on any endpoint except ingest. If the track library is public, streaming should also be public. If auth is added later, the streaming endpoint needs Bearer token support in the CORS preflight.

2. **Should we support formats other than MP3?** The system ingests MP3, WAV, FLAC, and OGG. The Track model has a `format` field. The streaming endpoint should serve the original file regardless of format, but the frontend player component might need different handling for non-MP3 formats (e.g., Safari doesn't support OGG).

3. **Content-Disposition: inline vs attachment?** `inline` is needed for browser playback. But should we also support a `?download=true` query parameter for downloading?

4. **Rate limiting for streaming?** Currently only ingest has rate limiting (asyncio.Lock). Should streaming have per-IP rate limiting to prevent abuse? Probably not needed for MVP.

5. **ETag / Conditional requests?** Should the streaming endpoint support `If-None-Match` / `If-Range` headers? FastAPI's `FileResponse` handles these automatically. Worth verifying in tests.

6. **Should the frontend tests use `@testing-library/svelte` or direct Svelte `mount()`?** The project already has `@testing-library/svelte` installed. Recommendation: use `@testing-library/svelte` for consistency and better assertions (`screen.getByRole`, etc.).

7. **Is Playwright needed for this feature?** Recommendation: Not for MVP. The backend is well-testable with pytest, and the frontend component logic is testable with Vitest + Audio mock. Add Playwright only if manual testing reveals browser-specific bugs that can't be caught in unit tests.

8. **Vite proxy Range header pass-through**: Need to verify that the Vite dev proxy (`changeOrigin: true`) correctly forwards Range request headers to the backend. This is a known area where proxies can silently strip headers.
