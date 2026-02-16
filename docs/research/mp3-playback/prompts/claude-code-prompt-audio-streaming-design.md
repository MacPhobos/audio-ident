# Claude Code Prompt — MP3 Track Registry + Range-Enabled Streaming + Svelte Audio Player

You are Claude Code operating inside this repository. Your job is to **correlate this design with the existing codebase**, identify what already exists, and implement the missing pieces with the smallest safe set of changes. Keep changes incremental, tested, and aligned with the project’s conventions.

## Context / Goal

We have:

- **Frontend:** SvelteKit (Svelte 5) UI that should provide a mp3 playback component.
- **Backend:** FastAPI service that is the **source of truth** for serving audio files and metadata.
- **Requirement:** UI playback must support **play/pause/resume** and **seeking** (time slider).
- **Critical technical requirement:** Seeking must work efficiently, which means the backend must support **HTTP Range requests** (206 Partial Content + Content-Range) for the MP3 stream endpoint. Use Starlette/FastAPI responses that support Range (typically `FileResponse`) rather than rolling custom range parsing unless required.

## High-Level Design

### Auth Constraints (Important)

The HTML `<audio>` element **cannot send custom Authorization headers**.

If the backend uses JWT bearer tokens in headers today, you must implement one of these for streaming:

- **Cookie-based auth** (httpOnly cookie; browser includes automatically)
- **Signed URL** for stream endpoint (`/stream?token=...`) with a short-lived token tied to `track_id`
- (Avoid) fetch→blob URL for playback, because it removes native Range behavior and makes seeking inefficient/hard.

### Track Registry

Backend owns the authoritative list of tracks. 

## Your Tasks (Do These In Order)

### 1) Repo Recon / Correlation (Read first)

- Locate FastAPI app entrypoint, routers, and existing auth/cors middleware:
  - Look for `app/` structure, `main.py`, router modules, dependency injection.
- Find existing track/audio domain objects:
  - Any SQLAlchemy models or Alembic migrations referencing “track”, “audio”, “file”, etc.
- Check if there are already endpoints serving files or returning audio metadata.
- Identify how settings are configured (pydantic-settings), especially:
  - Allowed media root directory
  - DB connection details
  - Auth mode

Output a short summary:
- What exists already
- What’s missing
- What you propose to change (minimal diff)

### 2) Implement Track Listing Endpoint

If not present:
- Add `GET /api/tracks` that lists available tracks from DB/registry.
- Define response schema (Pydantic model).
- Consider pagination if list can be large (optional).

If DB model exists:
- Reuse it and expose safe public fields.
- Avoid returning raw filesystem path.

### 3) Implement Range-Enabled Streaming Endpoint

Add `GET /api/tracks/{track_id}/stream`:

- Lookup track in DB by `track_id`
- Resolve filesystem path
- Enforce **allowed root** directory check:
  - `resolved_path = path.resolve()`
  - `resolved_path` must be within configured `MEDIA_ROOT`
- Validate file exists and is a regular file
- Return `FileResponse(path, media_type="audio/mpeg", filename=...)`

If the project uses custom auth:
- Ensure the stream endpoint uses the chosen auth method compatible with `<audio>`.

### 4) MIME and Safety Checks

Using `python-magic` (already in deps), optionally verify content type and/or extension:
- Accept only MP3 (`audio/mpeg`) for this feature
- Reject unexpected types

### 5) Track Registration / Ingest (If Needed)

If the service already knows file locations, integrate with that.
Otherwise implement one of:

- **Directory scan job** (on startup or via admin endpoint) that:
  - scans `MEDIA_ROOT` for `.mp3`
  - upserts into DB
  - computes size and duration via `mutagen`
- **Admin endpoint** to register file paths (safer if tightly controlled)

Prefer a controlled approach and prevent path traversal.

### 6) Frontend: Svelte UI Integration

Correlate with existing SvelteKit UI:

- Add UI view that:
  - fetches `/api/tracks`
  - renders list
  - selects a track
  - passes `streamUrl = /api/tracks/{id}/stream` to the audio player component
- Implement audio component using `<audio bind:currentTime bind:duration bind:paused>` and a range slider.
- Confirm seeking works (observe 206 responses + Range in network panel).

### 7) CORS & Dev Setup

If UI and API are on different origins in dev:
- Configure CORS middleware to allow the UI origin
- Ensure `Range` requests aren’t blocked by CORS policy

### 8) Tests

Add at least:

- Unit/integration test for list endpoint
- Streaming endpoint test:
  - request without Range returns 200
  - request with `Range: bytes=0-99` returns 206 and proper headers
  - (If using `FileResponse`, verify behavior in your stack)
- Path safety test (path must stay under media root)

Use existing test stack (`pytest`, `pytest-asyncio`, `httpx`).

### 9) Documentation

Update README or docs:
- how to configure `MEDIA_ROOT`
- expected API endpoints
- auth strategy for streaming
- any migration steps

## Constraints / Quality Bar

- Keep changes minimal and consistent with repository style.
- Avoid breaking existing endpoints.
- Ensure stream endpoint cannot expose arbitrary filesystem files.
- Prefer standard FastAPI/Starlette primitives over custom range parsing.
- Add/adjust Alembic migrations if new tables/columns are introduced.
- Prefer typed code (project uses strict mypy/pyright).

## Deliverables

1) PR-ready code changes implementing:
- `GET /api/tracks`
- `GET /api/tracks/{track_id}/stream` with Range support
- Required models/settings
- UI list + audio player integration
- Tests

2) A short implementation note:
- what you changed
- how to run locally
- how to validate seeking works (Range/206)

## Verification Checklist

- [ ] UI loads track list from backend
- [ ] Clicking a track plays audio
- [ ] Play/pause toggles
- [ ] Dragging slider seeks and continues playback
- [ ] Network panel shows Range requests and 206 responses when seeking
- [ ] Stream endpoint enforces allowed root path
- [ ] Tests pass

## Hints (Only if you need them)

- The browser will send `Range` automatically when seeking an `<audio>` element.
- If auth uses bearer headers, use cookies or signed URLs for the stream endpoint.
- `FileResponse` is typically the simplest way to get Range behavior.

Now proceed: inspect the repo, produce a plan/diff, then implement with tests.
