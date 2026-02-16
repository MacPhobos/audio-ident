# Backend Research: MP3 Streaming Endpoint (FastAPI/Starlette)

> **Date**: 2026-02-16
> **Scope**: Design a streaming endpoint for serving ingested audio files with HTTP Range request support

---

## Findings

### 1. Existing Codebase Patterns

**Router structure** (`app/routers/`):
- `health.py` — `GET /health` (no prefix)
- `version.py` — `GET /api/v1/version`
- `search.py` — `POST /api/v1/search` (multipart upload)
- `tracks.py` — `GET /api/v1/tracks`, `GET /api/v1/tracks/{track_id}`
- `ingest.py` — `POST /api/v1/ingest` (admin-only, multipart upload)

All routers are registered in `app/main.py:153-157` with `prefix="/api/v1"` (except health).

**Error handling convention**: All errors return `{"error": {"code": "...", "message": "..."}}` via `JSONResponse`. See `app/schemas/errors.py`.

**Auth**: Admin endpoints use `require_admin_key` dependency (`app/auth/admin.py`). Public endpoints (health, version, tracks list/detail, search) have no auth.

**DB session dependency**: `app/db/session.get_db` provides `AsyncSession` via `Depends(get_db)`.

### 2. Audio File Storage

**Storage module**: `app/audio/storage.py`

Files are stored at: `{audio_storage_root}/raw/{hash[:2]}/{hash}.{ext}`

- `settings.audio_storage_root` defaults to `"./data"` (see `app/settings.py:36`)
- Typical path: `./data/raw/57/57aba82c6e8100ccc1c69739385105fb5585ddf68902ab643283ab7cb5de52d4.mp3`
- All ingested files are copied to this location during pipeline step 4 (`app/ingest/pipeline.py:148-149`)

**Helper function** available:
```python
# app/audio/storage.py:15
def raw_audio_path(file_hash: str, extension: str) -> Path:
    ext = extension.lstrip(".")
    prefix = file_hash[:2]
    return Path(settings.audio_storage_root) / "raw" / prefix / f"{file_hash}.{ext}"
```

### 3. Track Database Model

**Model**: `app/models/track.py` — `Track` class

Key columns for streaming:
- `id` (UUID, primary key) — used as route parameter
- `file_path` (Text) — stored as relative path, e.g. `./data/raw/57/57aba8...mp3` (set at `pipeline.py:217`)
- `file_hash_sha256` (String(64)) — SHA-256 hex digest
- `format` (String(20), nullable) — file format hint, e.g. `"mp3"`, `"flac"`
- `file_size_bytes` (BigInteger) — original file size

**Important**: `file_path` is stored as the return value of `raw_audio_path()`, which produces a relative path from CWD (e.g., `./data/raw/ab/abcdef...mp3`). This path is relative to the service working directory, NOT absolute.

### 4. Starlette FileResponse — Range Request Support

**Version**: Starlette 0.52.1 (via FastAPI 0.129.0)

**Critical finding: Starlette's `FileResponse` has FULL native Range request support.** No custom range parsing needed.

Source analysis of `.venv/lib/python3.12/site-packages/starlette/responses.py`:

1. **`Accept-Ranges: bytes`** header set automatically (`line 321`)
2. **ETag** auto-generated from `mtime + size` (`lines 336-338`)
3. **`Last-Modified`** header set automatically (`line 335`)
4. **Range header parsing** (`_parse_range_header`, `lines 455-497`):
   - Parses `bytes=start-end` format
   - Handles suffix ranges (`bytes=-500` = last 500 bytes)
   - Handles open-ended ranges (`bytes=500-` = from 500 to EOF)
   - Validates range bounds, returns 416 Range Not Satisfiable if invalid
   - Handles malformed headers with 400 Bad Request
5. **206 Partial Content** for single ranges (`_handle_single_range`, `lines 398-414`):
   - Sets `Content-Range: bytes start-end/total`
   - Sets `Content-Length` to range size
   - Reads file with `seek()` + chunked reads (64KB chunks)
6. **Multipart ranges** supported (`_handle_multiple_ranges`, `lines 416-449`)
7. **`If-Range`** conditional support (`_should_use_range`, `line 451-452`)
8. **Chunked async file I/O** via `anyio.open_file` (non-blocking)

**Conclusion**: Simply returning `FileResponse(path)` from a FastAPI endpoint gives us complete Range request support out of the box. Zero custom code needed for Range handling.

### 5. Content-Type Mapping

Starlette's `FileResponse` auto-detects MIME type from file extension via `mimetypes.guess_type()` (`line 317`). For `.mp3` files, this returns `audio/mpeg`.

Other formats that may be stored:
| Extension | MIME Type |
|-----------|-----------|
| `.mp3` | `audio/mpeg` |
| `.wav` | `audio/wav` |
| `.flac` | `audio/flac` |
| `.ogg` | `audio/ogg` |
| `.webm` | `audio/webm` |
| `.mp4` / `.m4a` | `audio/mp4` |

---

## Proposal

### Endpoint Design

```
GET /api/v1/tracks/{track_id}/stream
```

**No authentication** — consistent with existing tracks endpoints (list/detail are public).

**Response behavior**:
- **200 OK** — Full file (no Range header, or `If-Range` mismatch)
- **206 Partial Content** — Range request (handled by Starlette FileResponse)
- **404 Not Found** — Track ID not in DB, or file missing from disk
- **416 Range Not Satisfiable** — Invalid range (handled by Starlette)

### Implementation

The endpoint is trivially simple because Starlette handles all the hard parts:

```python
# app/routers/tracks.py (add to existing router)

from pathlib import Path

from fastapi.responses import FileResponse

from app.audio.storage import raw_audio_path


@router.get(
    "/tracks/{track_id}/stream",
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "Full audio file"},
        206: {"description": "Partial content (Range request)"},
        404: {"description": "Track not found or file missing", "model": ErrorResponse},
    },
)
async def stream_track(
    track_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> FileResponse | JSONResponse:
    """Stream the audio file for a track.

    Supports HTTP Range requests for seeking (206 Partial Content).
    """
    # 1. Look up track in DB
    result = await db.execute(select(Track).where(Track.id == track_id))
    track = result.scalar_one_or_none()

    if track is None:
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(code="NOT_FOUND", message=f"No track found with id {track_id}")
            ).model_dump(),
        )

    # 2. Resolve file path
    file_path = Path(track.file_path)
    if not file_path.exists():
        return JSONResponse(
            status_code=404,
            content=ErrorResponse(
                error=ErrorDetail(code="FILE_NOT_FOUND", message="Audio file not found on disk")
            ).model_dump(),
        )

    # 3. Return FileResponse — Starlette handles Range, ETag, Content-Type
    return FileResponse(
        path=file_path,
        media_type="audio/mpeg",
        content_disposition_type="inline",
    )
```

### File Path Resolution — Security Analysis

**Path traversal risk**: LOW. The `file_path` column is populated exclusively by the ingestion pipeline (`app/ingest/pipeline.py:217`), which uses `raw_audio_path(file_hash, extension)`. The hash is computed by `compute_file_hash()` (SHA-256 hex digest — alphanumeric only), and the extension comes from `file_path.suffix`. There is no user-controlled input that could inject path traversal sequences.

**However**, for defense-in-depth, we should validate that the resolved path is within the expected storage root:

```python
# Defense-in-depth: verify path is within storage root
storage_root = Path(settings.audio_storage_root).resolve()
resolved = file_path.resolve()
if not str(resolved).startswith(str(storage_root)):
    logger.warning("Path traversal attempt: %s (track %s)", file_path, track_id)
    return JSONResponse(
        status_code=404,
        content=ErrorResponse(
            error=ErrorDetail(code="FILE_NOT_FOUND", message="Audio file not found on disk")
        ).model_dump(),
    )
```

### Content-Type Considerations

While most ingested files are MP3 (`audio/mpeg`), the system supports multiple formats. We should use the track's `format` column or detect from extension:

```python
AUDIO_MIME_TYPES: dict[str, str] = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
}

media_type = AUDIO_MIME_TYPES.get(track.format or "", None)
# Fall back to Starlette's auto-detection if format unknown
```

**Recommendation**: For the initial implementation, let Starlette auto-detect (`media_type=None`). It uses `mimetypes.guess_type()` on the file path, which works correctly for all supported extensions. Only override if a specific format needs special handling.

### Response Headers (all handled by Starlette FileResponse)

| Header | Value | Source |
|--------|-------|--------|
| `Content-Type` | `audio/mpeg` (or auto-detected) | `FileResponse(media_type=...)` or auto |
| `Content-Length` | File size in bytes | `set_stat_headers()` (auto) |
| `Accept-Ranges` | `bytes` | `FileResponse.__init__` (auto) |
| `ETag` | MD5 of `"{mtime}-{size}"` | `set_stat_headers()` (auto) |
| `Last-Modified` | HTTP date of file mtime | `set_stat_headers()` (auto) |
| `Content-Disposition` | `inline; filename="..."` | Configurable |
| `Content-Range` | `bytes start-end/total` | Only on 206 (auto) |

### Caching

Starlette provides `ETag` and `Last-Modified` automatically. For additional caching, we could add `Cache-Control`:

```python
# Audio files are immutable after ingestion (content-addressable by SHA-256)
# So aggressive caching is safe
headers = {"Cache-Control": "public, max-age=31536000, immutable"}
return FileResponse(path=file_path, headers=headers, ...)
```

This is a nice-to-have optimization. Since files are content-addressed (SHA-256 hash in filename), they are effectively immutable.

---

## Minimal Steps

### Step 1: Add stream endpoint to existing tracks router

Edit `app/routers/tracks.py`:

1. Add `from pathlib import Path` import
2. Add `from fastapi.responses import FileResponse` import
3. Add `from app.settings import settings` import (already used indirectly)
4. Add the `stream_track` endpoint function (~25 lines)

That's it for the backend code. One file changed, one function added.

### Step 2: Update API contract (REQUIRED by project conventions)

Add to `docs/api-contract.md` under Endpoints section:

```markdown
### Stream Track Audio

#### `GET /api/v1/tracks/{id}/stream`

Stream the audio file for a track. Supports HTTP Range requests for browser audio seeking.

**Path Parameters**

| Parameter | Type | Description |
|-----------|------|-------------|
| `id` | string (UUID) | Track identifier |

**Response** `200 OK` (full file) or `206 Partial Content` (range request)

Binary audio data with appropriate `Content-Type` header.

| Header | Description |
|--------|-------------|
| `Content-Type` | Audio MIME type (e.g. `audio/mpeg`) |
| `Content-Length` | File size (or range size for 206) |
| `Accept-Ranges` | `bytes` |
| `Content-Range` | `bytes start-end/total` (206 only) |
| `ETag` | Cache validator |
| `Last-Modified` | File modification time |

**Error Codes**

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `NOT_FOUND` | 404 | Track with the given ID does not exist |
| `FILE_NOT_FOUND` | 404 | Track exists but audio file is missing from disk |
```

### Step 3: Copy contract and regenerate types

Per CLAUDE.md conventions:
```bash
cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/
cp audio-ident-service/docs/api-contract.md docs/
make gen-client  # regenerate frontend types
```

### Step 4: Write tests

Add to `tests/test_tracks.py` (extend existing test module):

```python
class TestStreamTrack:
    async def test_stream_track_success(self, client, seed_tracks):
        # Need to create an actual file on disk for the seeded track
        ...

    async def test_stream_track_not_found(self, client):
        resp = await client.get(f"/api/v1/tracks/{uuid.uuid4()}/stream")
        assert resp.status_code == 404

    async def test_stream_track_range_request(self, client, seed_tracks):
        # Test 206 with Range header
        ...

    async def test_stream_track_file_missing_from_disk(self, client, seed_tracks):
        # Track in DB but file deleted — 404
        ...
```

---

## Open Questions

1. **Content-Disposition: `inline` vs `attachment`?**
   - `inline` = browser plays the file (correct for `<audio>` element)
   - `attachment` = browser downloads the file
   - **Recommendation**: Use `inline` for the stream endpoint. If we later want a download endpoint, that's a separate route.

2. **Should we add a `filename` to Content-Disposition?**
   - e.g., `inline; filename="Bohemian Rhapsody - Queen.mp3"`
   - Nice UX for "Save As" dialog, but requires sanitizing title/artist
   - **Recommendation**: Include it. Compose from `{title} - {artist}.{ext}` with filename-safe characters.

3. **Should the endpoint require auth?**
   - Currently, all tracks endpoints are public. The stream endpoint serves ingested content.
   - **Recommendation**: No auth for now (consistent with existing tracks endpoints). Add auth layer later if needed.

4. **Should we add `Cache-Control: immutable`?**
   - Files are content-addressed (SHA-256 hash), so they never change.
   - **Recommendation**: Yes, add aggressive caching headers. This is safe and reduces server load.

5. **API contract version bump required?**
   - Adding a new endpoint requires a version bump per CLAUDE.md: "Once an API version is published, its contract is frozen."
   - **Recommendation**: Bump to 1.2.0 since this adds a new endpoint without breaking existing ones.

6. **What about the relative `file_path` stored in the DB?**
   - Pipeline stores `file_path=str(storage_path)` where `storage_path = raw_audio_path(...)` returns a relative path like `./data/raw/57/57ab...mp3`.
   - `Path("./data/raw/57/file.mp3").exists()` works if CWD is the service directory (which it is when running via `uvicorn`).
   - **Risk**: If the service is started from a different working directory, relative paths break.
   - **Mitigation**: Use `Path(settings.audio_storage_root).resolve()` as the base, and reconstruct the full path from `file_hash_sha256` + `format` using `raw_audio_path()` instead of trusting the DB column directly.
