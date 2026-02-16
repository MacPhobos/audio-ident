# Integration Research: Contract & Minimal-Change Plan

> **Date**: 2026-02-16
> **Task**: #3 — Research integration contract and minimal-change plan for MP3 playback

---

## 1. Current State Analysis

### 1.1 API Contract (v1.1.0 — FROZEN)

**Location**: `docs/api-contract.md` (exists in 3 identical locations: root, `audio-ident-service/docs/`, `audio-ident-ui/docs/`)

**Existing endpoints**:
| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Health check (no prefix) |
| `GET` | `/api/v1/version` | Service version |
| `POST` | `/api/v1/search` | Audio fingerprint + vibe search |
| `POST` | `/api/v1/ingest` | Ingest audio (admin, X-Admin-Key) |
| `GET` | `/api/v1/tracks` | Paginated track listing |
| `GET` | `/api/v1/tracks/{id}` | Single track detail |

**Missing for playback**: No endpoint exists to retrieve the **raw audio bytes** for a given track. The `TrackDetail` schema exposes metadata but not the file itself.

### 1.2 Backend Data Model

**SQLAlchemy model** (`audio-ident-service/app/models/track.py:11-57`):
- `file_path: Mapped[str]` — stores the absolute path to the raw audio file on disk (e.g., `./data/raw/e3/e3b0c442...mp3`)
- `format: Mapped[str | None]` — stores the file extension (e.g., `"mp3"`, `"wav"`, `"flac"`)
- `file_hash_sha256: Mapped[str]` — unique hash for dedup

**Storage layout** (`audio-ident-service/app/audio/storage.py:1-44`):
- Files stored at `{audio_storage_root}/raw/{hash[:2]}/{hash}.{ext}`
- Default `audio_storage_root`: `./data` (configured via `AUDIO_STORAGE_ROOT` env var)
- The `file_path` column in the DB stores the full path including the extension

**Pydantic schemas**:
- `TrackInfo` (`app/schemas/search.py:16-25`): id, title, artist, album, duration_seconds, ingested_at
- `TrackDetail` (`app/schemas/track.py:1-20`): extends TrackInfo with sample_rate, channels, bitrate, format, file_hash_sha256, file_size_bytes, olaf_indexed, embedding_model, embedding_dim, updated_at
- **Neither schema exposes `file_path`** (correct — internal storage detail should not leak to API consumers)

### 1.3 Frontend Type Chain

**Generation pipeline**:
1. Backend runs at `http://localhost:17010` and exposes `/openapi.json`
2. `pnpm gen:api` / `make gen-client` runs `openapi-typescript` against the live OpenAPI spec
3. Output: `audio-ident-ui/src/lib/api/generated.ts` (auto-generated, DO NOT EDIT)
4. `audio-ident-ui/src/lib/api/client.ts` re-exports named types from `generated.ts` and provides typed fetch wrappers

**Current type exports** (`client.ts:1-11`):
```typescript
export type TrackDetail = components['schemas']['TrackDetail'];
export type TrackInfo = components['schemas']['TrackInfo'];
// etc.
```

**Current API functions** (`client.ts:108-132`):
- `fetchHealth()`, `fetchVersion()`, `fetchTracks()`, `fetchTrackDetail()`, `searchAudio()`, `ingestAudio()`

### 1.4 Frontend Pages

- `src/routes/tracks/+page.svelte` — Track library list (uses `fetchTracks`)
- `src/routes/tracks/[id]/+page.svelte` — Track detail (uses `fetchTrackDetail`, shows metadata/indexing status)
- `src/routes/search/+page.svelte` — Audio search
- `src/routes/admin/ingest/+page.svelte` — Admin ingestion UI

**Key observation**: The track detail page (`tracks/[id]/+page.svelte`) already has the track's `id` available. A playback button here is the natural integration point.

---

## 2. Proposed Contract Addition

### 2.1 New Endpoint: `GET /api/v1/tracks/{id}/audio`

This is a **streaming binary endpoint** (not JSON). It returns the raw audio file bytes with appropriate `Content-Type` and supports HTTP Range requests for seeking.

#### Contract Definition (to add to `api-contract.md`)

```markdown
### Track Audio Stream

#### `GET /api/v1/tracks/{id}/audio`

Stream the raw audio file for a track. Returns the original ingested audio file with appropriate content type. Supports HTTP Range requests for efficient seeking.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string (UUID) | Track identifier |

**Response Headers**

| Header | Value | Description |
|--------|-------|-------------|
| `Content-Type` | `audio/mpeg`, `audio/wav`, `audio/flac`, `audio/ogg` | Matches the stored audio format |
| `Content-Length` | integer | File size in bytes |
| `Accept-Ranges` | `bytes` | Indicates Range request support |
| `Content-Disposition` | `inline; filename="title.ext"` | Suggested filename for download |

**Response** `200 OK` (full file) / `206 Partial Content` (range request)

Binary audio data.

**Error Codes**

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NOT_FOUND` | 404 | Track with the given ID does not exist |
| `FILE_NOT_FOUND` | 404 | Track exists in DB but audio file is missing from storage |
| `VALIDATION_ERROR` | 400 | Invalid UUID format |
```

### 2.2 Why This Design

1. **No new Pydantic schema needed**: The response is binary, not JSON. No changes to `schemas/` beyond the endpoint itself.
2. **Follows existing pattern**: Uses the same `tracks/{id}` prefix as the detail endpoint.
3. **Range request support**: Essential for `<audio>` element seeking. FastAPI's `FileResponse` or Starlette's `StreamingResponse` handle this natively.
4. **No authentication required** (matching current tracks endpoints): Authentication is scaffolded but not enforced. When auth is enforced in a future version, this endpoint will follow the same auth pattern.
5. **Content-Type from DB**: The `format` column on the Track model provides the file extension, which maps directly to MIME type.

### 2.3 MIME Type Mapping (Backend)

```python
FORMAT_TO_MIME: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
}
```

---

## 3. Contract Version Bump

Since the contract is FROZEN at v1.1.0, adding a new endpoint requires a version bump to **v1.2.0**.

### Changelog Entry

```markdown
| 1.2.0 | 2026-02-16 | Add track audio streaming endpoint (`GET /api/v1/tracks/{id}/audio`) |
```

---

## 4. Complete File Change List

### 4.1 Contract (3 files — identical changes)

| # | File | Change |
|---|------|--------|
| 1 | `audio-ident-service/docs/api-contract.md` | Add `GET /api/v1/tracks/{id}/audio` endpoint definition, bump version to 1.2.0, add changelog entry |
| 2 | `audio-ident-ui/docs/api-contract.md` | Copy from service (identical) |
| 3 | `docs/api-contract.md` | Copy from service (identical) |

### 4.2 Backend (2-3 files)

| # | File | Change |
|---|------|--------|
| 4 | `audio-ident-service/app/routers/tracks.py` | Add `GET /tracks/{track_id}/audio` endpoint using `FileResponse` or `StreamingResponse` with Range support. Reads `file_path` from Track model, validates file exists, sets Content-Type based on `format` column |
| 5 | `audio-ident-service/tests/test_tracks_audio.py` | **NEW FILE** — Tests for audio streaming endpoint: 200 full response, 206 range request, 404 missing track, 404 missing file, correct Content-Type headers |

**No schema changes needed** — binary response, no Pydantic model required.
**No model changes needed** — `file_path` and `format` already exist on the Track model.
**No migration needed** — no database schema changes.

### 4.3 Frontend Type Generation (1 file — auto-generated)

| # | File | Change |
|---|------|--------|
| 6 | `audio-ident-ui/src/lib/api/generated.ts` | AUTO-REGENERATED via `make gen-client`. Will gain a new path entry for `/api/v1/tracks/{track_id}/audio`. **Do not edit manually.** |

### 4.4 Frontend API Client (1 file)

| # | File | Change |
|---|------|--------|
| 7 | `audio-ident-ui/src/lib/api/client.ts` | Add `trackAudioUrl(id: string): string` helper function that returns the URL for the audio endpoint. This is a URL builder (not a fetch wrapper) because `<audio src="...">` handles the HTTP request. |

**Proposed addition to `client.ts`**:

```typescript
/**
 * Build the URL for streaming a track's audio.
 * Used as the `src` attribute of an `<audio>` element.
 */
export function trackAudioUrl(id: string): string {
    return `${BASE_URL}/api/v1/tracks/${id}/audio`;
}
```

Note: This does NOT need a `fetchJSON` wrapper. The browser's `<audio>` element handles the HTTP request directly (including Range requests for seeking). A simple URL builder is all that's needed.

### 4.5 Frontend UI (1 file modified, 0-1 new files)

| # | File | Change |
|---|------|--------|
| 8 | `audio-ident-ui/src/routes/tracks/[id]/+page.svelte` | Add `<audio>` element (or AudioPlayer component) to the track detail page, using `trackAudioUrl(track.id)` as the source |
| 9 | `audio-ident-ui/src/lib/components/AudioPlayer.svelte` | **NEW FILE** (optional) — Reusable audio player component with play/pause, seek, time display. Could also be inlined in the track detail page. |

### 4.6 Summary: Total File Impact

| Category | Files Modified | Files Created |
|----------|---------------|---------------|
| Contract | 3 | 0 |
| Backend | 1 | 1 |
| Frontend (auto-gen) | 1 | 0 |
| Frontend (manual) | 2 | 0-1 |
| **Total** | **7** | **1-2** |

---

## 5. Implementation Sequencing

**CRITICAL**: The repo follows strict "contract first" conventions (CLAUDE.md). The exact order:

### Step 1: Update API Contract (BLOCKING PREREQUISITE)
1. Edit `audio-ident-service/docs/api-contract.md`:
   - Bump version from `1.1.0` → `1.2.0`
   - Add `GET /api/v1/tracks/{id}/audio` endpoint section (after Track Detail)
   - Add changelog entry
2. Copy contract to UI: `cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/`
3. Copy contract to root: `cp audio-ident-service/docs/api-contract.md docs/`

### Step 2: Implement Backend Endpoint
1. Edit `audio-ident-service/app/routers/tracks.py`:
   - Add `GET /tracks/{track_id}/audio` route handler
   - Use Starlette `FileResponse` (handles Range requests, Content-Length, ETag automatically)
   - Look up Track by UUID, verify `file_path` exists on disk
   - Map `track.format` to MIME type for `Content-Type` header
   - Set `Content-Disposition: inline; filename="{title}.{ext}"`
2. Write tests in `audio-ident-service/tests/test_tracks_audio.py`
3. Run backend tests: `cd audio-ident-service && uv run pytest`

### Step 3: Regenerate Frontend Types
1. Ensure backend is running: `make dev`
2. Run type generation: `make gen-client`
3. Verify `audio-ident-ui/src/lib/api/generated.ts` now includes the new path

### Step 4: Update Frontend API Client
1. Edit `audio-ident-ui/src/lib/api/client.ts`:
   - Add `trackAudioUrl(id: string): string` function

### Step 5: Add Playback UI
1. Create `audio-ident-ui/src/lib/components/AudioPlayer.svelte` (if using a reusable component)
2. Edit `audio-ident-ui/src/routes/tracks/[id]/+page.svelte`:
   - Import `trackAudioUrl` from client
   - Add `<audio>` element or `<AudioPlayer>` component to the track detail view
3. Run frontend tests: `cd audio-ident-ui && pnpm test`

### Step 6: Verify End-to-End
1. `make dev` — start everything
2. Navigate to a track detail page
3. Verify audio plays, seeking works, correct format displayed

---

## 6. Key Technical Details

### 6.1 FileResponse vs StreamingResponse

**Recommendation: Use `starlette.responses.FileResponse`**

`FileResponse` is purpose-built for serving files and handles:
- `Content-Length` header (from `stat()`)
- `Content-Type` header (from `media_type` parameter)
- `ETag` and `Last-Modified` headers
- **HTTP Range requests** (206 Partial Content) — **critical for `<audio>` seeking**
- `Content-Disposition` header

FastAPI re-exports it: `from fastapi.responses import FileResponse`.

```python
from fastapi.responses import FileResponse

@router.get("/tracks/{track_id}/audio")
async def stream_track_audio(track_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    track = await db.execute(select(Track).where(Track.id == track_id))
    track = track.scalar_one_or_none()
    if track is None:
        return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", ...}})

    file_path = Path(track.file_path)
    if not file_path.is_file():
        return JSONResponse(status_code=404, content={"error": {"code": "FILE_NOT_FOUND", ...}})

    mime_type = FORMAT_TO_MIME.get(track.format or "", "application/octet-stream")
    return FileResponse(
        path=file_path,
        media_type=mime_type,
        filename=f"{track.title}.{track.format or 'bin'}",
    )
```

### 6.2 Frontend Audio URL Pattern

The `<audio>` element handles all HTTP complexity (Range requests, buffering, seeking). The frontend only needs:

```html
<audio src={trackAudioUrl(track.id)} controls preload="metadata" />
```

Or, with a custom player component:

```svelte
<AudioPlayer src={trackAudioUrl(track.id)} title={track.title} duration={track.duration_seconds} />
```

### 6.3 CORS Considerations

The current CORS config (`app/main.py:145-151`) allows all methods and all headers for `http://localhost:17000`. Since `GET /api/v1/tracks/{id}/audio` is a same-origin request proxied through the same CORS rules, no CORS changes are needed.

However, the `<audio>` element may issue a **preflight OPTIONS** request for Range headers. The current `allow_headers=["*"]` config already handles this.

### 6.4 What Does NOT Need to Change

- **No database migration** — `file_path` and `format` columns already exist
- **No new Pydantic schemas** — binary response, not JSON
- **No `schemas/__init__.py` changes**
- **No `main.py` changes** — the tracks router is already mounted
- **No `settings.py` changes** — `audio_storage_root` already available
- **No new dependencies** — `FileResponse` is built into FastAPI/Starlette

---

## 7. Open Questions

### 7.1 For Team Lead
1. **Authentication**: Should the audio streaming endpoint require authentication? Current tracks endpoints don't, but audio files are arguably more sensitive. Recommendation: match existing tracks pattern (no auth) for consistency.
2. **Rate limiting**: Should there be a rate limit on audio streaming? Large files could saturate bandwidth. Recommendation: defer to future version (not needed for MVP).
3. **Component granularity**: Should the AudioPlayer be a separate component (`AudioPlayer.svelte`) or inline in the track detail page? Recommendation: separate component for reuse (search results may also want playback).

### 7.2 For Backend Researcher (Task #2)
1. **Starlette FileResponse Range support**: Verify that Starlette's `FileResponse` handles `Range` headers correctly for `<audio>` seeking. (It does — Starlette has built-in Range support since v0.20.)
2. **Path traversal security**: Ensure the `file_path` from the DB cannot be manipulated for path traversal attacks. Since `file_path` is written by the ingest pipeline (not user input), this is low risk, but validation is still recommended.

### 7.3 For Frontend Researcher (Task #1)
1. **Preload strategy**: `preload="metadata"` vs `preload="none"` vs `preload="auto"` — what's optimal for a library browsing UX?
2. **Error handling**: What happens when the audio URL returns 404? The `<audio>` element fires an `error` event that should be caught and displayed.

### 7.4 For QA Researcher (Task #4)
1. **Missing file on disk**: Track exists in DB but file was deleted from storage. The endpoint must return a clear 404 with `FILE_NOT_FOUND` code.
2. **Large file streaming**: Files up to 30 minutes (100+ MB WAV). Ensure no memory issues with `FileResponse`.
3. **Concurrent streams**: Multiple browsers streaming audio simultaneously. No locking needed (read-only), but bandwidth concerns.
4. **Format coverage**: Test with all supported formats: MP3, WAV, FLAC, OGG, WebM, MP4/M4A.
