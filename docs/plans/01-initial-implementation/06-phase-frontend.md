# Phase 6: Frontend (~4-5 days)

> **Depends on**: Phase 5 (orchestration — search endpoint must be working)
> **Blocks**: Phase 7 (evaluation — needs UI for browser recording tests)
> **Goal**: Search page at `/search` with mic recording, file upload, and results display

---

## Overview

The frontend consists of:
1. **AudioRecorder** — browser mic recording with WebM/Opus (128kbps)
2. **AudioUploader** — file picker + drag-and-drop for MP3/WAV/WebM
3. **SearchResults** — two-tab layout for Exact ID and Vibe matches
4. **Search page** — orchestrating all components with state management

**Tech stack**: SvelteKit + Svelte 5 (Runes API), TanStack Query, Tailwind CSS. All already installed.

**Corresponds to**: 06-implementation-plan.md Milestones 7-8

---

## Step 1: Type Generation (~2 hours)

**Reference**: CLAUDE.md "How to Add a New Endpoint"

### 1.1 Verify Backend OpenAPI Spec

```bash
make dev
# Wait for service to start
curl http://localhost:17010/openapi.json | python -m json.tool | grep SearchResponse
# Should show SearchResponse schema
```

### 1.2 Generate Frontend Types

```bash
make gen-client
```

### 1.3 Verify Generated Types

**File**: `audio-ident-ui/src/lib/api/generated.ts` (GENERATED — do not edit)

Verify it contains:
- `SearchResponse` type with `exact_matches` and `vibe_matches`
- `ExactMatch` type with `confidence`, `offset_seconds`, `aligned_hashes`
- `VibeMatch` type with `similarity`, `embedding_model`
- `TrackInfo` type with `id`, `title`, `artist`, `album`, `duration_seconds`
- `SearchMode` enum (`exact`, `vibe`, `both`)

### 1.4 Copy Contract

```bash
cp audio-ident-service/docs/api-contract.md audio-ident-ui/docs/api-contract.md
```

### Acceptance Criteria
- [ ] `make gen-client` runs without error
- [ ] `generated.ts` contains all search-related types
- [ ] Contract is synchronized (service → UI)

---

## Step 2: AudioRecorder Component (~8 hours)

**Reference**: 10-browser-recording.md (full Svelte 5 implementation)

### File: `audio-ident-ui/src/lib/components/AudioRecorder.svelte` (NEW)

Svelte 5 implementation using Runes API (`$state`, `$derived`, `$effect`, `$props`).

### Key Features

1. **Codec selection**: Use `getPreferredMimeType()` fallback chain:
   - `audio/webm;codecs=opus` (Chrome/Firefox)
   - `audio/webm` (fallback)
   - `audio/mp4;codecs=aac` (Safari)
   - `audio/ogg;codecs=opus` (Firefox alt)

2. **Audio constraints** (disable processing for music):
   ```typescript
   {
     channelCount: 1,
     sampleRate: 48000,
     echoCancellation: false,
     noiseSuppression: false,
     autoGainControl: false,
   }
   ```

3. **128kbps bitrate** (per 00-reconciliation-summary.md §6)

4. **20-bar audio level meter**: Using Web Audio API `AnalyserNode`
   - `fftSize: 256` (128 frequency bins)
   - `smoothingTimeConstant: 0.8`
   - RMS calculation → 0-1 range → 20 visual bars
   - `requestAnimationFrame` for smooth updates

5. **Duration enforcement**:
   - Min 3s: "Search" button disabled until reached, countdown displayed
   - Max 30s: Auto-stop at max duration

6. **Too-quiet detection**: If `audioLevel < 0.01` after 3 seconds, warn user

7. **Error handling**:
   - `NotAllowedError`: "Microphone permission denied"
   - `NotFoundError`: "No microphone found"
   - `NotReadableError`: "Microphone is in use"
   - MediaRecorder error event: generic error message

8. **Resource cleanup**: Stop all media tracks, close AudioContext, cancel animation frames on unmount

### Props

```typescript
let {
  minDuration = 3,
  maxDuration = 30,
  onRecordingComplete,
}: {
  minDuration?: number;
  maxDuration?: number;
  onRecordingComplete: (blob: Blob, duration: number) => void;
} = $props();
```

### State

```typescript
let isRecording = $state(false);
let isPreparing = $state(false);
let duration = $state(0);
let audioLevel = $state(0);
let error = $state<string | null>(null);

let canStop = $derived(duration >= minDuration);
let timeRemaining = $derived(Math.max(0, minDuration - duration));
```

### Acceptance Criteria
- [ ] Records audio in Chrome, Firefox, Safari (desktop)
- [ ] Records audio on Android Chrome and iOS Safari (mobile)
- [ ] Audio level meter shows real-time feedback
- [ ] "Search" button disabled until min duration reached
- [ ] Auto-stops at max duration
- [ ] Warns when audio is too quiet
- [ ] Handles permission denied gracefully
- [ ] Cleans up resources on unmount
- [ ] Outputs Blob + duration to parent component

---

## Step 3: AudioUploader Component (~4 hours)

### File: `audio-ident-ui/src/lib/components/AudioUploader.svelte` (NEW)

### Key Features

1. **File picker**: Accept `.mp3`, `.wav`, `.webm`, `.ogg`, `.mp4`, `.m4a`
2. **Drag-and-drop**: Drop zone with visual feedback
3. **Client-side validation**:
   - File size <= 10MB
   - Content type in allowed list
   - Optional: duration check via Web Audio API `decodeAudioData()`
4. **File info display**: Show filename, size, detected format

### Props

```typescript
let {
  onFileSelected,
}: {
  onFileSelected: (file: File) => void;
} = $props();
```

### State

```typescript
let isDragging = $state(false);
let selectedFile = $state<File | null>(null);
let error = $state<string | null>(null);
```

### Acceptance Criteria
- [ ] File picker opens and accepts audio files
- [ ] Drag-and-drop works with visual feedback
- [ ] Rejects files > 10MB with error message
- [ ] Rejects non-audio files with error message
- [ ] Displays selected file info (name, size)

---

## Step 4: SearchResults Component (~8 hours)

### File: `audio-ident-ui/src/lib/components/SearchResults.svelte` (NEW)

### Key Features

1. **Two-tab layout**: "Exact ID" and "Similar Vibe"
   - Tab priority: If `exact_matches[0].confidence >= 0.85`, show Exact ID tab first
   - Otherwise, show Similar Vibe tab first (or whichever has results)

2. **Exact match display**:
   - Track title, artist, album
   - Confidence badge (color-coded: green >= 0.85, yellow 0.5-0.84, red < 0.5)
   - Offset timestamp: "Match at 1:32" (from `offset_seconds`)
   - Aligned hashes count (for debugging/advanced users)

3. **Vibe match display**:
   - Track title, artist, album
   - Similarity score as percentage (e.g., "87% similar")
   - Rank number (1, 2, 3, ...)
   - Embedding model name (small, muted text)

4. **Loading state**: Skeleton placeholders or spinner during search

5. **Empty state**: "No matches found" with suggestions:
   - "Try recording in a quieter environment"
   - "Try a longer recording (at least 5 seconds)"
   - "Make sure the track is in the library"

6. **Error state**: Display error message from API

### Props

```typescript
let {
  response,
  isLoading,
  error,
}: {
  response: SearchResponse | null;
  isLoading: boolean;
  error: string | null;
} = $props();
```

### Acceptance Criteria
- [ ] Two tabs display correctly
- [ ] Tab priority based on exact match confidence works
- [ ] Confidence badges are color-coded
- [ ] Offset timestamp formats correctly (mm:ss)
- [ ] Loading skeleton shows during search
- [ ] Empty state shows helpful suggestions
- [ ] Error state displays API error message

---

## Step 5: Search Page (~8 hours)

### File: `audio-ident-ui/src/routes/search/+page.svelte` (NEW)

### Layout

```
┌────────────────────────────────────────────┐
│              Audio Search                    │
├────────────────────────────────────────────┤
│                                             │
│   ┌─────────────┐    ┌─────────────────┐  │
│   │   Record     │ or │   Upload File   │  │
│   │   🎤         │    │   📁            │  │
│   └─────────────┘    └─────────────────┘  │
│                                             │
│   Recording guidance:                       │
│   "Hold your phone near the speaker"        │
│   "Record in a quiet environment"           │
│                                             │
├────────────────────────────────────────────┤
│                                             │
│   ┌──────────────────────────────────────┐ │
│   │         Search Results               │ │
│   │  [Exact ID] [Similar Vibe]           │ │
│   │                                      │ │
│   │  1. Track Title - Artist   95%       │ │
│   │     Match at 1:32                    │ │
│   │                                      │ │
│   │  2. Track Title - Artist   78%       │ │
│   │     Match at 0:45                    │ │
│   │                                      │ │
│   └──────────────────────────────────────┘ │
│                                             │
└────────────────────────────────────────────┘
```

### State Machine

```
idle → recording → processing → searching → results
                ↘ uploading → searching → results
```

States:
- **idle**: Show record button + upload area
- **recording**: Show AudioRecorder with level meter
- **uploading**: Show file being prepared
- **searching**: Show loading spinner, API call in progress
- **results**: Show SearchResults component
- **error**: Show error message with retry button

### TanStack Query Integration

```typescript
import { createMutation } from '@tanstack/svelte-query';

const searchMutation = createMutation({
  mutationFn: async (audioBlob: Blob) => {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    formData.append('mode', 'both');
    formData.append('max_results', '10');

    const response = await fetch(`${API_BASE_URL}/api/v1/search`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json();
      throw new Error(errorData.error?.message || `Server error: ${response.status}`);
    }

    return response.json() as Promise<SearchResponse>;
  },
});
```

### Recording Guidance

Display contextual tips (from 09-reality-check.md §9.5):
- "Hold your phone near the speaker"
- "Record in a quiet environment for best results"
- "Try to capture a distinctive part of the song"
- "Recordings of 5-10 seconds work best"

### Acceptance Criteria
- [ ] `/search` route is accessible
- [ ] Recording → searching → results flow works end-to-end
- [ ] File upload → searching → results flow works end-to-end
- [ ] Recording guidance is displayed
- [ ] Error states are handled (API down, timeout, invalid response)
- [ ] State transitions are smooth (no flickering)
- [ ] Responsive layout on mobile

---

## Step 6: Browser Compatibility Testing (~4 hours)

> **[Updated]** This is the first phase where browser recording is actually tested with real hardware. Phase 1 validation created codec detection utilities and MediaRecorder wrappers but did not perform end-to-end browser recording tests. Budget extra time for unexpected issues.

**Reference**: 10-browser-recording.md (compatibility table)

### Test Matrix

| Browser | Platform | Codec | Test |
|---------|----------|-------|------|
| Chrome 100+ | Desktop (macOS) | WebM/Opus | Record → search → results |
| Chrome 100+ | Android | WebM/Opus | Record → search → results |
| Firefox 100+ | Desktop | WebM/Opus | Record → search → results |
| Safari 18.4+ | Desktop (macOS) | MP4/AAC | Record → search → results |
| Safari 18.4+ | iOS | MP4/AAC | Record → search → results |
| Edge 100+ | Desktop | WebM/Opus | Record → search → results |

### What to Test

1. **Codec selection**: Verify correct MIME type is chosen per browser
2. **Recording quality**: Verify decoded audio is usable (not silent, not garbled)
3. **Upload success**: Verify server accepts the recorded format
4. **Level meter**: Verify visual feedback works
5. **Mobile specifics**:
   - Android: MediaRecorder support, mic quality
   - iOS: Audio session interruptions, permission flow
6. **File upload**: Verify MP3, WAV, WebM file uploads work across browsers

### Edge Cases to Test

- Background tab behavior: Does recording stop when tab loses focus?
- Permission denial: Is the error message clear?
- No microphone (desktop without mic): Does it fail gracefully?
- Very slow connection: Does upload timeout gracefully?

### Acceptance Criteria
- [ ] Chrome desktop: full flow works
- [ ] Firefox desktop: full flow works
- [ ] Safari desktop: MP4/AAC fallback works
- [ ] Chrome Android: recording and upload work
- [ ] Safari iOS: recording and upload work (with MP4/AAC)
- [ ] File upload works across all browsers

---

## File Summary

| File | Purpose |
|------|---------|
| `src/lib/components/AudioRecorder.svelte` | Mic recording with level meter |
| `src/lib/components/AudioUploader.svelte` | File picker + drag-and-drop |
| `src/lib/components/SearchResults.svelte` | Two-tab results display |
| `src/routes/search/+page.svelte` | Search page (main orchestrator) |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| [Updated] Browser recording not yet tested with real hardware | High | Medium | Phase 1 created browser recording tools and codec detection code, but actual browser recording was not manually tested. The team accepted this risk -- browser testing will occur during this phase. Budget extra time for unexpected codec/permission issues, especially Safari iOS. |
| Safari WebM recording unreliable on older versions | Medium | Medium | MP4/AAC fallback always available; test on Safari 18.4+ |
| iOS audio session interrupted by phone call | Low | Low | Handle AudioContext state changes; re-request on resume |
| Mobile mic quality too poor for fingerprinting | Medium | Medium | Recording guidance tips; vibe search degrades more gracefully |
| Generated types don't match API response | Low | High | Run `make gen-client` after every backend change |
| TanStack Query caching stale results | Low | Low | Mutations don't cache by default; no issue |

## Rollback Procedures

```bash
rm -rf audio-ident-ui/src/lib/components/AudioRecorder.svelte
rm -rf audio-ident-ui/src/lib/components/AudioUploader.svelte
rm -rf audio-ident-ui/src/lib/components/SearchResults.svelte
rm -rf audio-ident-ui/src/routes/search/
```

---

## Effort Breakdown

| Task | Hours |
|------|-------|
| Type generation + verification | 2h |
| AudioRecorder component | 8h |
| AudioUploader component | 4h |
| SearchResults component | 8h |
| Search page + state management | 8h |
| Browser compatibility testing | 4h |
| Vitest tests for non-DOM logic | 2h |
| **Total** | **~36h (4.5 days)** |

---

## Devil's Advocate Review

> **Reviewer**: plan-reviewer
> **Date**: 2026-02-14
> **Reviewed against**: All research files in `docs/research/01-initial-research/`

### Confidence Assessment

**Overall: MEDIUM-HIGH** — The component design is well-specified and correctly follows the Svelte 5 Runes API from the research (10-browser-recording.md). The main risks are around browser compatibility edge cases and the tight coupling between component behavior and API response format.

### Gaps Identified

1. **`make gen-client` may not work if the backend isn't running.** Step 1.1 says to verify the OpenAPI spec by running the service, and Step 1.2 runs `make gen-client`. But the type generation depends on the running service's `/openapi.json` endpoint. If the backend has errors (e.g., CLAP model fails to load during lifespan), the service won't start and types can't be generated. Consider a fallback: generate types from a static OpenAPI JSON file checked into the repo, not only from the live server.

2. **No accessibility (a11y) considerations.** The AudioRecorder component has a visual 20-bar level meter but no ARIA attributes for screen readers. The recording state (idle, recording, processing) should be announced via `aria-live` regions. The "Search" button should have `aria-disabled` when duration < minDuration. Add at minimum:
   - `role="progressbar"` for the level meter
   - `aria-live="polite"` for status messages
   - Keyboard-accessible record/stop buttons

3. **No loading state management for the file upload path.** The state machine shows `uploading → searching → results`, but the AudioUploader component's `onFileSelected` callback fires immediately — there's no "uploading" state in the component itself. The file is selected instantly (it's local), so the state machine may jump directly from `idle → searching` for the upload path. Clarify what "uploading" means — is it "preparing the FormData" or "sending to server"? If the latter, it's the same as "searching."

4. **TanStack Query `createMutation` does not handle AbortController.** If the user starts a recording, submits it, then immediately starts a new recording while the first search is in progress, the old mutation isn't cancelled. This could lead to race conditions where stale results replace newer results. Add an `AbortController` to cancel in-flight requests when a new search starts.

5. **Bundle size impact of the AudioRecorder's Web Audio API usage.** The `AnalyserNode`, `AudioContext`, and `requestAnimationFrame` loop are standard Web APIs with zero bundle impact. But the `getPreferredMimeType()` fallback chain may need polyfilling on older browsers. Verify that `MediaRecorder.isTypeSupported()` is available in all target browsers without polyfills.

6. **`SearchResults` tab priority logic may flash.** "If `exact_matches[0].confidence >= 0.85`, show Exact ID tab first" — this check happens after the API returns. If the initial render shows a default tab (e.g., "Exact ID") and then switches to "Similar Vibe" based on results, the user sees a flash. Use a loading skeleton that doesn't show tabs until results arrive.

### Edge Cases Not Addressed

1. **iOS Safari audio session interruption.** On iOS, receiving a phone call or notification sound pauses the AudioContext. When the user returns, `AudioContext.state` is "interrupted." The plan mentions this in Risks but doesn't provide the specific recovery code: `audioContext.resume()` after the interruption ends. Add an `onstatechange` handler.

2. **Android Chrome background tab behavior.** When the user switches to another app while recording, Chrome on Android may suspend the tab and stop the MediaRecorder. The recording will be truncated. The plan should either: (a) warn the user to keep the tab in focus, or (b) detect the truncation and show an error.

3. **WebM container but MP4 extension.** Safari on iOS may report `audio/mp4` MIME type but the plan saves the Blob as `recording.webm`. This incorrect extension could confuse the server-side format detection. Use the actual MIME type to determine the file extension.

4. **Multiple rapid record/stop clicks.** If the user clicks "Record" and immediately clicks "Stop" (under 1 second), the MediaRecorder may not have received any data chunks. `ondataavailable` may fire with an empty Blob. Handle this edge case.

5. **Drag-and-drop of multiple files.** The AudioUploader accepts drag-and-drop but the `onFileSelected` callback takes a single `File`. If the user drops 3 files, which one is selected? Use only the first file and show a message: "Only one file can be searched at a time."

### Feasibility Concerns

1. **36h (4.5 days) is reasonable** but browser compatibility testing (Step 6, 4h) is likely underestimated. Testing on 6 browser/platform combinations requires physical devices or BrowserStack. Just getting iOS Safari testing set up can take an hour. Budget 6-8h for thorough cross-browser testing.

2. **The AudioRecorder component is complex (level meter + duration enforcement + codec detection + error handling).** 8h may be tight if Safari MP4/AAC fallback causes issues. The research (10-browser-recording.md) provides a near-complete implementation, which helps, but integration and debugging always take longer than the code suggests.

### Missing Dependencies

1. **`API_BASE_URL` configuration.** The TanStack Query mutation uses `${API_BASE_URL}/api/v1/search` but this variable isn't defined in the plan. Where does it come from? SvelteKit environment variables (`$env/static/public`)? A config file? This needs to be specified.

2. **CORS configuration.** The frontend (port 17000) makes requests to the backend (port 17010). Cross-origin requests require CORS headers on the backend. The plan doesn't mention CORS — is it already configured in the scaffold? If not, requests will fail with "CORS policy" errors in the browser.

3. **TanStack Query provider setup.** `createMutation` requires a `QueryClient` provider wrapping the application. If the scaffold already set this up, fine. If not, it needs to be added to the root layout.

### Recommended Changes

1. **Add accessibility attributes** to all interactive components (ARIA roles, `aria-live`, `aria-disabled`).
2. **Add AbortController** to cancel in-flight search requests when a new search starts.
3. **Fix the file extension** for recordings: use MIME type, not hardcoded `.webm`.
4. **Define `API_BASE_URL` configuration** — probably `PUBLIC_API_BASE_URL` in SvelteKit's `$env/static/public`.
5. **Verify CORS configuration** on the backend for cross-origin requests from the dev server.
6. **Increase browser testing estimate** to 6-8h.
7. **Add loading skeleton** that hides tabs until results arrive to prevent tab flash.
