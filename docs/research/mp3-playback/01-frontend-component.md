# Frontend Research: Mp3Player Component (Svelte 5)

> **Date**: 2026-02-16
> **Scope**: Reusable `<Mp3Player>` component, /tracks dialog integration, state model

---

## Findings

### Codebase Scan Summary

**Framework & Stack**
- Svelte 5 with Runes API (`$state`, `$derived`, `$effect`, `$props`) — all components consistently use Runes
- SvelteKit file-based routing with `+page.svelte` and `+layout.svelte`
- Tailwind CSS v4 (imported via `@import 'tailwindcss'` in `src/app.css`)
- TanStack Query (`@tanstack/svelte-query`) for all server state
- Icons: `lucide-svelte` (used throughout: `Music`, `Search`, `Mic`, `AlertCircle`, etc.)
- API types auto-generated from OpenAPI via `openapi-typescript` at `src/lib/api/generated.ts`
- All API calls go through `src/lib/api/client.ts` — typed wrapper around `fetch`
- No shadcn-svelte components installed (CLAUDE.md mentions it, but `src/lib/components/ui/` does not exist)
- No dialog/modal components exist anywhere in the codebase

**Relevant Files**
| File | Purpose | Key Patterns |
|------|---------|-------------|
| `src/routes/tracks/+page.svelte` | Track Library page — paginated table/cards with search | URL-driven state via `$app/state`, TanStack `createQuery`, lucide icons, responsive (table desktop / cards mobile) |
| `src/routes/tracks/[id]/+page.svelte` | Track detail page | TanStack `createQuery`, `fetchTrackDetail()`, loading/error/404 states |
| `src/lib/api/client.ts` | Typed API client | `fetchJSON<T>()` helper, `ApiRequestError` class, `BASE_URL` from env |
| `src/lib/api/generated.ts` | Auto-generated OpenAPI types | `TrackInfo`, `TrackDetail`, `SearchResponse`, etc. |
| `src/lib/format.ts` | Display formatters | `formatDuration(seconds)` → `m:ss` (reusable for player) |
| `src/lib/components/AudioRecorder.svelte` | Microphone recording | Most complex audio component — uses `$state`, `$derived`, `$effect`, `onMount` for cleanup, `MediaRecorder` + `AudioContext` |
| `src/lib/components/AudioUploader.svelte` | File upload with D&D | `$props()` pattern, validation, callback props |
| `src/lib/components/SearchResults.svelte` | Search result tabs | Tab state management with `$state`/`$derived`, keyboard navigation |
| `src/lib/components/NavBar.svelte` | Navigation bar | Active route detection via `$app/state` |
| `src/routes/+layout.svelte` | Root layout | TanStack `QueryClientProvider`, `{@render children()}` |

**API Types Available**
- `TrackInfo`: `{ id, title, artist, album, duration_seconds, ingested_at }` — used in track list
- `TrackDetail`: extends `TrackInfo` with `sample_rate, channels, bitrate, format, file_hash_sha256, file_size_bytes, olaf_indexed, embedding_model, embedding_dim, updated_at`
- No streaming endpoint exists yet in the OpenAPI spec or generated types

**Backend Context**
- Track model has `file_path` column (ORM: `app/models/track.py:31`)
- Audio files stored at `{audio_storage_root}/raw/{hash[:2]}/{hash}.{ext}`
- Settings: `audio_storage_root` defaults to `./data`
- No streaming endpoint exists yet — needs to be added (Task #2 scope)
- Auth is stubbed (not enforced), so `<audio src=...>` can hit the endpoint directly without auth workarounds

**Component Patterns Observed**
1. Props via `$props()` with inline TypeScript types
2. Callback props (not events): `onRecordingComplete`, `onFileSelected`, `onTrackClick`
3. Cleanup via `onMount` return function (AudioRecorder) and `onDestroy` (tracks page debounce timer)
4. Error states rendered inline with `role="alert"`
5. ARIA attributes on all interactive elements
6. Responsive: desktop (hidden sm:block) / mobile (sm:hidden) patterns
7. No `createEventDispatcher` — all components use callback prop pattern

### Existing Audio Handling

The `AudioRecorder.svelte` component (`src/lib/components/AudioRecorder.svelte`) is the closest analog to what we're building. Key patterns to follow:

- Uses `AudioContext` + `AnalyserNode` for audio level visualization
- Manages `MediaRecorder` lifecycle with careful cleanup
- Uses `onMount` return for unmount cleanup (stops streams, cancels animation frames, clears intervals, closes AudioContext)
- Uses `$state` for UI state, `$derived` for computed values
- Follows the project's error handling pattern (inline alerts)

### Vite Proxy Configuration

`vite.config.ts` proxies `/api` and `/health` to the backend. This means the streaming URL (`/api/v1/tracks/{id}/stream`) will be proxied automatically in dev — the `<audio>` element can use a relative URL.

---

## Proposal

### Component: `<Mp3Player>`

**Location**: `src/lib/components/Mp3Player.svelte`

**Props Interface** (using `$props()` pattern consistent with codebase):

```svelte
<script lang="ts">
  let {
    trackId,
    title,
    artist = null,
    durationSeconds = 0,
    autoplay = false,
    onClose,
    onEnded,
    onError,
  }: {
    trackId: string;
    title: string;
    artist?: string | null;
    durationSeconds?: number;
    autoplay?: boolean;
    onClose?: () => void;
    onEnded?: () => void;
    onError?: (error: string) => void;
  } = $props();
</script>
```

**Design Rationale**:
- `trackId` → constructs the stream URL internally: `/api/v1/tracks/${trackId}/stream`
- `title` + `artist` → display in the player UI (already available from `TrackInfo`)
- `durationSeconds` → used for initial display before audio metadata loads; prevents layout shift
- `autoplay` → plays immediately when mounted (for dialog open)
- Callback props (`onClose`, `onEnded`, `onError`) — consistent with existing component patterns (no `createEventDispatcher`)
- No `streamUrl` prop needed — the URL pattern is deterministic and internal

### State Model

```typescript
// Core playback state (all $state)
let audioEl: HTMLAudioElement | null = $state(null);  // bound via bind:this
let isPlaying = $state(false);
let isPaused = $state(false);
let isLoading = $state(true);      // true until canplay fires
let isBuffering = $state(false);   // true during seeking/rebuffering
let isSeeking = $state(false);
let hasError = $state(false);
let errorMessage = $state<string | null>(null);
let currentTime = $state(0);       // seconds, updated via timeupdate
let duration = $state(0);          // from audio metadata (fallback: durationSeconds prop)
let volume = $state(1);            // 0-1, persisted to localStorage

// Derived state
let progress = $derived(duration > 0 ? (currentTime / duration) * 100 : 0);
let displayDuration = $derived(duration > 0 ? duration : durationSeconds);
let streamUrl = $derived(`/api/v1/tracks/${trackId}/stream`);
```

### HTML5 Audio API Integration

The component uses a native `<audio>` element (not `new Audio()`) for two reasons:
1. Svelte's `bind:` directives work with DOM elements
2. The browser handles Range requests automatically for seeking

```svelte
<audio
  bind:this={audioEl}
  src={streamUrl}
  preload="metadata"
  oncanplay={handleCanPlay}
  ontimeupdate={handleTimeUpdate}
  onended={handleEnded}
  onerror={handleError}
  onwaiting={handleWaiting}
  onplaying={handlePlaying}
  onseeking={() => isSeeking = true}
  onseeked={() => isSeeking = false}
></audio>
```

**Why `preload="metadata"` not `preload="auto"`**: For a library with many tracks, we don't want to eagerly download full files. `metadata` fetches just the duration/format info. When the user hits play, the browser starts streaming.

### Event → State Mapping

| Audio Event | State Change | Notes |
|-------------|-------------|-------|
| `canplay` | `isLoading = false` | Audio ready to play. If `autoplay`, call `.play()` here |
| `playing` | `isPlaying = true; isPaused = false; isBuffering = false` | Playback started/resumed |
| `pause` | `isPlaying = false; isPaused = true` | User paused |
| `waiting` | `isBuffering = true` | Network stall or seeking |
| `timeupdate` | `currentTime = audioEl.currentTime` | ~4 Hz update rate |
| `durationchange` | `duration = audioEl.duration` | May fire after `canplay` for VBR MP3 |
| `ended` | `isPlaying = false; isPaused = false` | Track finished. Call `onEnded?.()` |
| `error` | `hasError = true; errorMessage = ...` | Network/decode error. Call `onError?.(msg)` |
| `seeked` | `isSeeking = false` | Seek complete |

### Seeking on VBR MP3s

VBR (Variable Bit Rate) MP3 files are common. The browser may report inaccurate `duration` initially for VBR files without a Xing/VBRI header. Two mitigations:

1. **Backend**: The `durationSeconds` from the database (computed by ffprobe during ingestion) is accurate. Pass it as prop for display.
2. **Browser**: The `<audio>` element handles Range-based seeking natively. When the user drags the slider, `audioEl.currentTime = newTime` triggers a Range request. The browser and server negotiate byte offsets.

**Seeking implementation**:
```typescript
function handleSeek(e: Event) {
  const input = e.target as HTMLInputElement;
  const newTime = (parseFloat(input.value) / 100) * displayDuration;
  if (audioEl) {
    audioEl.currentTime = newTime;
  }
}
```

### Cleanup on Unmount

Following the `AudioRecorder.svelte` pattern:

```typescript
$effect(() => {
  // Autoplay when component mounts (if prop is set)
  if (autoplay && audioEl) {
    audioEl.play().catch(() => {
      // Autoplay blocked by browser policy — user must click play
      isPlaying = false;
    });
  }

  return () => {
    // Cleanup: pause and release audio resources
    if (audioEl) {
      audioEl.pause();
      audioEl.removeAttribute('src');
      audioEl.load(); // releases network resources
    }
  };
});
```

**Why `removeAttribute('src')` + `.load()`**: Simply pausing isn't enough — the browser may keep the connection open. Removing the source and calling `.load()` tells the browser to release the network connection and any buffered data.

### Volume Persistence

```typescript
const VOLUME_KEY = 'audio-ident-player-volume';

// Load saved volume on mount
$effect(() => {
  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem(VOLUME_KEY);
    if (saved !== null) {
      volume = parseFloat(saved);
    }
  }
});

// Sync volume to audio element and persist
$effect(() => {
  if (audioEl) {
    audioEl.volume = volume;
  }
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(VOLUME_KEY, String(volume));
  }
});
```

### Single-Playback Enforcement

Only one track should play at a time across the entire app. Two approaches:

**Option A: Module-level singleton** (Recommended — simplest)
```typescript
// In Mp3Player.svelte's <script> module context:
// When this component mounts, pause any previously playing instance.
let activePlayer: HTMLAudioElement | null = null;

// In component instance scope:
$effect(() => {
  if (audioEl && isPlaying) {
    if (activePlayer && activePlayer !== audioEl) {
      activePlayer.pause();
    }
    activePlayer = audioEl;
  }
});
```

**Option B: Custom event on `window`**: Dispatch `audio-ident:playback-start` event, other instances listen and pause. More complex, no benefit for our use case.

Recommend **Option A** — it's the same pattern used by most audio player libraries.

### UI Layout

```
+--------------------------------------------------+
|  [Music Icon]  Title                       [X]   |
|               Artist                              |
+--------------------------------------------------+
|  [Play/Pause]  0:42 ====|=============== 3:15    |
|                                                   |
|  [Volume Icon] =========|==                       |
+--------------------------------------------------+
```

Tailwind classes following existing codebase patterns:
- Rounded card: `rounded-xl border bg-white p-4`
- Icon sizes: `h-5 w-5` or `h-6 w-6`
- Text: `text-sm text-gray-600` for secondary, `font-medium text-gray-900` for primary
- Transitions: `transition-colors`
- Responsive: works at all sizes (no breakpoint-specific layout needed for the player itself)

### Icons Needed (from lucide-svelte)

- `Play` — play button
- `Pause` — pause button
- `Volume2` — volume icon (audible)
- `VolumeX` — mute icon
- `X` — close button
- `Loader2` — loading/buffering spinner (already used in admin/ingest page)
- `Music` — track icon (already used in track detail)
- `AlertCircle` — error state (already used throughout)

---

## /tracks Dialog Integration

### Current /tracks Page Structure

The tracks page (`src/routes/tracks/+page.svelte`) renders:
1. A header with title and search bar
2. Desktop: HTML `<table>` with rows linking to `/tracks/{id}`
3. Mobile: Card list linking to `/tracks/{id}`
4. Pagination controls

Each track row/card currently links to the detail page. We need to add a play button that opens a dialog **without navigating away**.

### Dialog Component: `<PlayerDialog>`

**Location**: `src/lib/components/PlayerDialog.svelte`

Since there's no existing dialog/modal in the codebase, we need one. We'll use the native HTML `<dialog>` element (widely supported, handles focus trapping, backdrop, Escape key).

```svelte
<script lang="ts">
  import Mp3Player from './Mp3Player.svelte';
  import type { TrackInfo } from '$lib/api/client';

  let {
    track = null,
    open = false,
    onClose,
  }: {
    track: TrackInfo | null;
    open: boolean;
    onClose: () => void;
  } = $props();

  let dialogEl: HTMLDialogElement | null = $state(null);

  // Sync open prop with dialog element
  $effect(() => {
    if (!dialogEl) return;
    if (open && !dialogEl.open) {
      dialogEl.showModal();
    } else if (!open && dialogEl.open) {
      dialogEl.close();
    }
  });

  function handleDialogClose() {
    onClose();
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialogEl}
  onclose={handleDialogClose}
  class="w-full max-w-md rounded-xl border-none bg-transparent p-0 backdrop:bg-black/50"
>
  {#if track && open}
    <Mp3Player
      trackId={track.id}
      title={track.title}
      artist={track.artist}
      durationSeconds={track.duration_seconds}
      autoplay={true}
      onClose={handleDialogClose}
      onEnded={handleDialogClose}
    />
  {/if}
</dialog>
```

**Why `{#if track && open}`**: Conditionally rendering the `Mp3Player` ensures:
1. Audio cleanup happens when dialog closes (component unmounts)
2. No stale audio connections linger
3. Fresh state on each open

**Why native `<dialog>`**:
- Built-in focus trapping (accessibility)
- Escape key closes it (standard behavior, `onclose` fires)
- `::backdrop` pseudo-element for overlay
- No third-party dependency needed
- Top-layer rendering (no z-index issues)

### /tracks Page Integration

Changes to `src/routes/tracks/+page.svelte`:

```svelte
<script lang="ts">
  // ... existing imports ...
  import { Play } from 'lucide-svelte';
  import PlayerDialog from '$lib/components/PlayerDialog.svelte';
  import type { TrackInfo } from '$lib/api/client';

  // ... existing state ...

  // Player dialog state
  let playerTrack = $state<TrackInfo | null>(null);
  let playerOpen = $state(false);

  function openPlayer(track: TrackInfo) {
    playerTrack = track;
    playerOpen = true;
  }

  function closePlayer() {
    playerOpen = false;
    playerTrack = null;
  }
</script>

<!-- In the desktop table, add a play button column -->
<th scope="col" class="w-12 px-4 py-3">
  <span class="sr-only">Play</span>
</th>
<!-- In each row: -->
<td class="px-4 py-3">
  <button
    onclick={() => openPlayer(track)}
    class="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-indigo-600"
    aria-label="Play {track.title}"
  >
    <Play class="h-4 w-4" />
  </button>
</td>

<!-- Mobile cards: add play button -->
<!-- In each card's top-right corner -->

<!-- Dialog at page bottom (outside scroll container) -->
<PlayerDialog track={playerTrack} open={playerOpen} onClose={closePlayer} />
```

### Flow Summary

1. User clicks Play button on a track row/card
2. `openPlayer(track)` sets `playerTrack` and `playerOpen = true`
3. `PlayerDialog` `$effect` calls `dialogEl.showModal()`
4. `Mp3Player` mounts, starts loading audio from `/api/v1/tracks/{trackId}/stream`
5. `autoplay={true}` → calls `.play()` on `canplay` event
6. User can play/pause/seek within the dialog
7. User presses Escape or clicks close → `onclose` fires → `closePlayer()`
8. `Mp3Player` unmounts → cleanup `$effect` pauses audio, removes src, calls `.load()`

---

## Minimal Steps (Ordered)

### Prerequisites (Backend — Task #2 scope, NOT this task)

1. Add `GET /api/v1/tracks/{track_id}/stream` endpoint to backend (Range-enabled, returns `FileResponse`)
2. Update API contract (`docs/api-contract.md`)
3. Regenerate frontend types (`make gen-client`)

### Frontend Implementation Steps

1. **Create `src/lib/components/Mp3Player.svelte`**
   - HTML5 `<audio>` element with event handlers
   - Play/pause button, time display, seek slider, volume slider
   - Loading/buffering/error states
   - Cleanup via `$effect` return
   - ARIA attributes for accessibility
   - ~120-160 lines

2. **Create `src/lib/components/PlayerDialog.svelte`**
   - Native `<dialog>` wrapper
   - Syncs `open` prop with `showModal()`/`close()`
   - Conditionally renders `Mp3Player` when open
   - ~40-50 lines

3. **Modify `src/routes/tracks/+page.svelte`**
   - Add `Play` icon import from lucide-svelte
   - Add `PlayerDialog` import
   - Add `playerTrack` and `playerOpen` state
   - Add play button column to desktop table
   - Add play button to mobile cards
   - Add `<PlayerDialog>` at bottom of template
   - ~30 lines of additions, no deletions

4. **Add `formatTime()` to `src/lib/format.ts`** (or reuse existing `formatDuration()`)
   - `formatDuration` already does `m:ss` — can be reused directly
   - No changes needed here

5. **Write tests in `tests/mp3-player.test.ts`**
   - Mock `HTMLAudioElement` behavior
   - Test play/pause toggle
   - Test seek interaction
   - Test cleanup on unmount
   - Test autoplay prop
   - Test error state rendering

6. **Optionally**: Add play button to track detail page (`src/routes/tracks/[id]/+page.svelte`)
   - Same `PlayerDialog` pattern but triggered from a button in the title section

### Files Created (2 new)

| File | Size Estimate |
|------|--------------|
| `src/lib/components/Mp3Player.svelte` | ~150 lines |
| `src/lib/components/PlayerDialog.svelte` | ~45 lines |

### Files Modified (1)

| File | Change |
|------|--------|
| `src/routes/tracks/+page.svelte` | Add play button + dialog (~30 lines added) |

### No API Contract Changes Needed for Frontend

The streaming endpoint (`GET /api/v1/tracks/{track_id}/stream`) needs to be added to the API contract (Task #2/3 scope), but the frontend component itself doesn't need generated types for it — it's a binary stream URL, not a JSON response.

---

## Open Questions

1. **Streaming endpoint URL pattern**: Proposed `/api/v1/tracks/{track_id}/stream`. Is this confirmed? The Vite proxy will handle it in dev since it matches `/api/*`.

2. **Auth for streaming**: Auth is currently stubbed/not enforced. When auth is added later, the `<audio>` element cannot send Bearer headers. The design prompt mentions cookie-based auth or signed URLs. **Recommendation**: Defer auth concern — implement without auth now (matches current codebase), add signed URL support later.

3. **Track detail page integration**: Should the player also appear on `/tracks/[id]`? Easy to add — same `PlayerDialog` component. Recommend yes, with a "Play Track" button in the title section.

4. **Error handling for missing audio files**: If a track exists in the database but the audio file is missing on disk, the streaming endpoint will 404. The `Mp3Player` should show a clear error message. The `<audio>` element's `error` event provides a `MediaError` code we can map to user-friendly messages.

5. **Mobile behavior**: On iOS, autoplay is blocked by browser policy unless the user has interacted with the page. The `play().catch()` pattern handles this gracefully — the player will show in "paused" state and the user taps play. No special handling needed.

6. **Volume control on mobile**: Mobile browsers typically ignore `volume` property on `<audio>` (volume is system-controlled). The volume slider should be hidden on mobile or marked as desktop-only.

---

## Appendix: Browser Compatibility Notes

### Range Requests and `<audio>`

When the user seeks on an `<audio>` element, the browser automatically sends a `Range: bytes=X-Y` header. The server must respond with:
- `206 Partial Content` + `Content-Range: bytes X-Y/Z` header
- FastAPI's `FileResponse` (from Starlette) handles this automatically when the browser sends a Range header

### Autoplay Policy

Modern browsers block autoplay with sound unless:
1. User has interacted with the page (clicked/tapped anything), OR
2. The media is muted

Since the user clicks "Play" to open the dialog, interaction #1 is satisfied. Autoplay will work.

### iOS-specific

- iOS Safari doesn't support `volume` property on `<audio>` (always returns 1)
- iOS requires user gesture for first `play()` — our flow satisfies this
- iOS may not fire `timeupdate` at exactly 4 Hz — the slider will still work, just slightly less smooth
