# Devil's Advocate Review: MP3 Playback Feature

> **Date**: 2026-02-16
> **Reviewer**: Devil's Advocate (independent codebase verification)
> **Inputs**: 01-frontend-component.md, 02-backend-streaming.md, 03-integration-contract.md, 04-qa-edge-cases.md

---

## Findings

### Independent Codebase Verification

I independently reviewed the following files before reading any research docs to establish a baseline:

| File | Verified | Notes |
|------|----------|-------|
| `docs/api-contract.md` | Correct | v1.1.0, FROZEN, no streaming endpoint |
| `audio-ident-service/app/models/track.py` | Correct | `file_path` (Text, NOT NULL), `format` (String(20), nullable) |
| `audio-ident-service/app/audio/storage.py` | Correct | `raw_audio_path()` returns relative Path from `settings.audio_storage_root` |
| `audio-ident-service/app/routers/tracks.py` | Correct | GET list + GET detail, no streaming |
| `audio-ident-service/app/schemas/track.py` | Correct | `TrackDetail` does NOT expose `file_path` (good) |
| `audio-ident-service/app/settings.py` | Correct | `audio_storage_root = "./data"` (relative) |
| `audio-ident-service/app/main.py` | Correct | CORS config has NO `expose_headers`, no `FileResponse` usage anywhere |
| `audio-ident-ui/src/lib/api/client.ts` | Correct | `BASE_URL` from `VITE_API_BASE_URL`, no streaming URL function |
| `audio-ident-ui/src/lib/api/generated.ts` | Correct | No streaming path |
| `audio-ident-ui/src/routes/tracks/[id]/+page.svelte` | Correct | Track detail page, no audio playback |
| `audio-ident-ui/vite.config.ts` | Correct | Vite proxy: `/api` -> `localhost:17010` |
| `docker-compose.yml` | Correct | Service runs on host, not Docker (direct filesystem access) |
| `CLAUDE.md` | Correct | Contract-first workflow, contract must be updated FIRST |

---

## Critical Issues

### CRITICAL-1: Endpoint URL Inconsistency Across All Docs

**Severity**: BLOCKING — must resolve before any implementation

The four research docs propose **two different URL patterns** for the same endpoint:

| Document | Proposed URL |
|----------|-------------|
| 01 (Frontend) | `/api/v1/tracks/{trackId}/stream` |
| 02 (Backend) | `/api/v1/tracks/{track_id}/stream` |
| 03 (Integration) | `/api/v1/tracks/{id}/audio` |
| 04 (QA) | `/api/v1/tracks/{track_id}/stream` |

**Specific citations**:
- Doc 01 line 107: `streamUrl = $derived(\`/api/v1/tracks/${trackId}/stream\`)`
- Doc 01 line 451: "`GET /api/v1/tracks/{track_id}/stream`"
- Doc 02 line 107: "`GET /api/v1/tracks/{track_id}/stream`"
- Doc 03 line 75: "`GET /api/v1/tracks/{id}/audio`"

**Impact**: If an implementer follows doc 01 for the frontend and doc 03 for the contract, the URLs won't match.

**Recommendation**: Use `/api/v1/tracks/{id}/audio`. Rationale:
- Consistent with doc 03 (the integration/contract doc, which should be authoritative)
- `/audio` describes the resource (the audio file), while `/stream` describes the mechanism
- REST convention: endpoints should name the resource, not the delivery method

---

### CRITICAL-2: Doc 04 Violates Contract-First Sequencing

**Severity**: HIGH — directly contradicts CLAUDE.md

Doc 04 "Minimal Steps" section lists implementation order as:
1. Step 1: Backend Tests First
2. Step 2: CORS Fix
3. Step 3: Frontend Test Setup
4. Step 4: Frontend Component
5. Step 5: Integration Verification
6. **Step 6: API Contract Update** ← LAST

CLAUDE.md explicitly states:
> "When adding new endpoints: 1. Update the API contract FIRST (this is a blocking prerequisite)"
> "Never implement code before the contract is updated and copied."

Doc 03 correctly puts the contract update as Step 1. Doc 04's sequencing would violate the project's established workflow.

**Recommendation**: Doc 04's test plan content is excellent, but the sequencing section should be replaced with doc 03's sequencing (contract first, then backend, then types, then frontend).

---

### CRITICAL-3: Hardcoded `media_type="audio/mpeg"` in Backend Proposal

**Severity**: HIGH — will break non-MP3 tracks

Doc 02, implementation code block (line 173):
```python
return FileResponse(
    path=file_path,
    media_type="audio/mpeg",  # BUG: hardcoded to MP3
    ...
)
```

The system ingests MP3, WAV, FLAC, OGG, WebM, and MP4/M4A files. Serving a FLAC file with `Content-Type: audio/mpeg` will cause browser playback failures.

Doc 02 addresses this later (lines 199-217) with a MIME mapping dict and suggests letting Starlette auto-detect, but the **code block that an implementer will copy-paste** has the bug.

**Recommendation**: The implementation code must use the track's `format` column to determine MIME type, or pass `media_type=None` to let Starlette auto-detect from the file extension (which it does correctly for all supported formats).

---

## High-Priority Issues

### HIGH-1: Relative `file_path` Fragility

**Severity**: HIGH in production, LOW in dev

The ingestion pipeline stores `file_path` as a relative path (e.g., `./data/raw/ab/abcdef.mp3`). This is relative to the service's working directory.

- Doc 02 correctly identifies this risk (Open Question #6, lines 356-359)
- Doc 03 uses `Path(track.file_path)` directly (line 288)

**Verified in codebase**: `app/ingest/pipeline.py:217` stores `file_path=str(storage_path)` where `storage_path = raw_audio_path(file_hash, extension)`. The `raw_audio_path()` function in `storage.py:29` returns `Path(settings.audio_storage_root) / "raw" / prefix / f"{file_hash}.{ext}"` — which is relative when `audio_storage_root` is `"./data"`.

**Impact**: If the service is started from a different working directory (e.g., via Docker, systemd, or a different deployment path), ALL file paths in the database become invalid.

**Recommendation**: Use doc 02's suggestion — reconstruct the path using `raw_audio_path(track.file_hash_sha256, track.format)` instead of trusting the stored `file_path`. This uses the live `settings.audio_storage_root` which can be configured per environment.

```python
# SAFER: Reconstruct from hash + format
from app.audio.storage import raw_audio_path
file_path = raw_audio_path(track.file_hash_sha256, track.format or "bin")

# FRAGILE: Trust the stored path
file_path = Path(track.file_path)
```

---

### HIGH-2: `track.format` Can Be NULL

**Severity**: HIGH — will cause runtime error

The Track model has `format: Mapped[str | None]` (nullable). If `format` is NULL:
- Doc 02's MIME mapping: `AUDIO_MIME_TYPES.get(track.format or "", None)` returns `None`
- Doc 03's MIME mapping: `FORMAT_TO_MIME.get(track.format or "", "application/octet-stream")` falls back
- `raw_audio_path(hash, None)` would fail with `NoneType` error
- Starlette auto-detect with no extension would return `application/octet-stream`

**Verified**: `track.py:26` — `format: Mapped[str | None] = mapped_column(String(20), nullable=True)`

**Impact**: A track with NULL format will either crash the endpoint or serve with wrong Content-Type.

**Recommendation**: The endpoint must handle NULL format gracefully. If format is NULL, fall back to detecting from the file extension in `file_path`, or return a specific error ("Track format unknown, cannot stream").

---

### HIGH-3: Missing Browser Format Compatibility Discussion

**Severity**: HIGH for user experience

None of the docs adequately address that different browsers support different audio formats:

| Format | Chrome | Firefox | Safari |
|--------|--------|---------|--------|
| MP3 | Yes | Yes | Yes |
| WAV | Yes | Yes | Yes |
| FLAC | Yes | Yes (51+) | Yes (11+) |
| OGG/Vorbis | Yes | Yes | **NO** |
| WebM/Opus | Yes | Yes | **NO** (macOS 14+: partial) |
| MP4/AAC | Yes | Yes | Yes |

Doc 04 mentions this in Open Question #2 but proposes no mitigation. If a user has an OGG track and opens it in Safari, playback will silently fail.

**Recommendation**: For MVP, the frontend should check `audioEl.canPlayType(mimeType)` and show a clear message like "This audio format is not supported in your browser" rather than silently failing. The `error` event on `<audio>` does fire, but the error message is generic and unhelpful.

---

## Medium-Priority Issues

### MEDIUM-1: Scope Creep in Frontend Proposal

Doc 01 proposes a substantial amount of functionality:
- `Mp3Player.svelte` (~150 lines) — custom player with play/pause, seek slider, volume slider, buffering states
- `PlayerDialog.svelte` (~45 lines) — native `<dialog>` wrapper
- Module-level singleton for single-playback enforcement
- Volume persistence via localStorage
- Dialog integration on the `/tracks` list page

**Simpler alternative for MVP** (verified against codebase):

The track detail page (`src/routes/tracks/[id]/+page.svelte`) already has the track's data loaded. Adding playback is ~10 lines:

```svelte
<!-- In the track detail page, after the title section -->
{#if track}
  <div class="mt-4 rounded-xl border bg-white p-4">
    <audio
      src={`${BASE_URL}/api/v1/tracks/${track.id}/audio`}
      controls
      preload="metadata"
      class="w-full"
    >
      Your browser does not support audio playback.
    </audio>
  </div>
{/if}
```

This uses the browser's native audio controls (play, pause, seek, volume, time display, download) and requires zero new components. Native `<audio controls>` handles:
- Play/pause
- Seeking with Range requests
- Time display
- Volume control
- Buffering indication
- Error states

The custom player can be added later as an enhancement.

**Recommendation**: Start with native `<audio controls>` on the track detail page. Add the custom player/dialog as a separate follow-up if the native controls are insufficient.

---

### MEDIUM-2: CORS `expose_headers` — Conflicting Advice

- Doc 03 (line 315): "no CORS changes are needed"
- Doc 04 (RISK-02, line 93-94): "explicitly add `expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"]`"

**My independent analysis**: For the `<audio>` element with `src=` attribute, CORS `expose_headers` is NOT needed. The `<audio>` element makes its own HTTP requests internally, and the browser doesn't need JavaScript access to response headers. CORS only matters for programmatic `fetch`/`XMLHttpRequest` access.

Furthermore, in development, the Vite proxy makes this entirely moot — the `<audio>` element hits `localhost:17000/api/...` which is same-origin.

**Recommendation**: Doc 03 is correct for the current implementation. No CORS changes needed. If a future custom player uses `fetch()` to check file size or other headers, add `expose_headers` at that time.

---

### MEDIUM-3: `Content-Disposition` Filename Sanitization

Doc 03 proposes:
```
Content-Disposition: inline; filename="{title}.{ext}"
```

Track titles can contain characters that break HTTP headers:
- Quotes: `He said "hello"` → breaks header parsing
- Non-ASCII: `Für Elise` → requires RFC 5987 encoding
- Path separators: `Track 1/2` → ambiguous

**Recommendation**: Either omit the `filename` parameter entirely (it's optional for `inline`) or use Starlette's built-in filename handling which does proper encoding. The `FileResponse(filename=...)` parameter handles this, but the title must be sanitized to remove path separators.

---

### MEDIUM-4: Frontend URL Construction Without `BASE_URL`

Doc 01 constructs the streaming URL inline:
```typescript
let streamUrl = $derived(`/api/v1/tracks/${trackId}/stream`);
```

Doc 03 proposes a `trackAudioUrl()` function in `client.ts` that uses `BASE_URL`:
```typescript
export function trackAudioUrl(id: string): string {
    return `${BASE_URL}/api/v1/tracks/${id}/audio`;
}
```

**Verified**: `client.ts:13` — `const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';`

In development with the Vite proxy, `BASE_URL` is empty string, so both approaches produce the same relative URL. But if `VITE_API_BASE_URL` is set (e.g., in production or non-proxied setups), the inline URL from doc 01 would miss the base.

**Recommendation**: Use doc 03's `trackAudioUrl()` approach. All API URLs should go through `client.ts` for consistency.

---

### MEDIUM-5: Test File Name Inconsistency

Three different test file names proposed:
- Doc 02: extend existing `tests/test_tracks.py`
- Doc 03: new file `tests/test_tracks_audio.py`
- Doc 04: new file `tests/test_track_stream.py`

**Recommendation**: Use `tests/test_track_stream.py` (doc 04's name). Rationale:
- Separate from existing `test_tracks.py` to avoid bloating that file
- Name matches the feature being tested
- Doc 04 provides the most comprehensive test plan

---

## Low-Priority Issues

### LOW-1: `$effect` Conflating Autoplay and Cleanup

Doc 01 puts autoplay logic and cleanup in the same `$effect`:
```typescript
$effect(() => {
  if (autoplay && audioEl) { audioEl.play()... }
  return () => { /* cleanup */ };
});
```

This means the cleanup re-runs whenever `autoplay` or `audioEl` changes, not just on unmount. In practice this is fine for a simple component, but it's a footgun for future modifications.

**Recommendation**: Separate into two effects — one for autoplay, one for cleanup.

---

### LOW-2: No Mention of `preload` Attribute Trade-offs on Track List

Doc 01 uses `preload="metadata"` which is correct for the player. But if playback is added to the `/tracks` list page (with many tracks visible), even `preload="metadata"` causes N concurrent HTTP requests (one per visible track). The list page should use `preload="none"`.

---

### LOW-3: Audio Mock in Doc 04 Doesn't Match `<audio>` Element Pattern

Doc 04's Audio mock (`createAudioMock()`) is designed for `new Audio()` usage (`AudioConstructor`). But doc 01 uses a `<audio>` element in the template with `bind:this`. The testing approach needs to either:
- Mock `HTMLAudioElement` on the prototype chain, or
- Stub the Svelte-rendered `<audio>` element's properties

In jsdom, `<audio>` elements exist but have non-functional `play()`/`pause()`. The mock strategy should target the element returned by `bind:this`, not the `Audio` constructor.

---

## Contradiction Check

| Topic | Doc 01 | Doc 02 | Doc 03 | Doc 04 | Verdict |
|-------|--------|--------|--------|--------|---------|
| URL path | `/stream` | `/stream` | `/audio` | `/stream` | **CONFLICT** — use `/audio` |
| Contract sequencing | N/A | Step 2 | Step 1 (correct) | Step 6 (wrong) | Follow doc 03 |
| CORS changes | Not discussed | Not discussed | Not needed | Add `expose_headers` | Not needed (doc 03 correct) |
| MIME type handling | N/A | Hardcoded then auto-detect | Explicit mapping | Explicit mapping | Use explicit mapping or auto-detect, NOT hardcoded |
| Test file name | N/A | `test_tracks.py` | `test_tracks_audio.py` | `test_track_stream.py` | Use `test_track_stream.py` |
| File path resolution | N/A | Reconstruct from hash | Use `file_path` column | N/A | Reconstruct from hash (doc 02) |
| Frontend approach | Custom player + dialog | N/A | `trackAudioUrl()` in client.ts | N/A | Start with native `<audio controls>` |

---

## Recommendations

### Must Fix Before Implementation

1. **Agree on endpoint URL**: `/api/v1/tracks/{id}/audio` (from doc 03)
2. **Fix MIME type handling**: Use `track.format` → MIME mapping or Starlette auto-detect, NOT hardcoded `audio/mpeg`
3. **Fix sequencing**: Contract update FIRST, per CLAUDE.md
4. **Handle NULL format**: Endpoint must not crash when `track.format` is NULL
5. **Use `raw_audio_path()` reconstruction**: Don't trust stored `file_path` directly

### Should Fix Before Implementation

6. **Use `trackAudioUrl()` from `client.ts`**: Respect `BASE_URL`
7. **Align test file name**: `test_track_stream.py`
8. **Start with native `<audio controls>`**: Defer custom player to follow-up

### Nice to Have (Defer to Follow-up)

9. **Browser format compatibility check**: `canPlayType()` warning
10. **Content-Disposition filename sanitization**
11. **`Cache-Control: immutable`** (files are content-addressed)
12. **CORS `expose_headers`** (only if Web Audio API or fetch-based player is added later)

---

## Final Verdict

**NOT ready for implementation as-is.** The research is thorough and high-quality, but there are 5 issues that must be resolved first:

1. **Endpoint URL disagreement** — a 3:1 split between `/stream` and `/audio`. Pick one.
2. **Hardcoded MIME type** in the backend proposal code — will break WAV/FLAC/OGG tracks.
3. **Contract sequencing violation** in the QA doc — CLAUDE.md says contract first, doc 04 says last.
4. **NULL format field** — unhandled edge case that will cause runtime errors.
5. **Relative file path** — the reconstruction approach from doc 02 should be adopted.

**Once these 5 items are resolved**, the implementation plan from doc 03 is solid and the team can proceed. The backend implementation is straightforward (~25 lines for the endpoint), and the frontend can start with a native `<audio controls>` element and iterate from there.

**Estimated minimal implementation**: ~8 files touched, ~100 lines of new code (backend endpoint + frontend audio element + tests), which is very reasonable for this feature.
