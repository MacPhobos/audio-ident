# Section 10: Browser Recording Guidance

> **Status**: Research Complete
> **Date**: 2026-02-14
> **Scope**: MediaRecorder API configuration, SvelteKit integration, error handling, audio metering

---

## MediaRecorder API Configuration

### Codec Selection Strategy

```typescript
/**
 * Determine the best available audio MIME type for recording.
 * Preference order: WebM/Opus > WebM > MP4/AAC > OGG/Opus
 */
function getPreferredMimeType(): string {
  const candidates = [
    'audio/webm;codecs=opus',  // Best: smallest files, widely supported
    'audio/webm',              // Fallback: browser picks codec
    'audio/mp4;codecs=aac',    // Safari fallback (if WebM unsupported)
    'audio/ogg;codecs=opus',   // Firefox alternative
  ];

  for (const mimeType of candidates) {
    if (MediaRecorder.isTypeSupported(mimeType)) {
      return mimeType;
    }
  }

  // Last resort: let the browser decide
  return '';
}
```

### Browser Compatibility (2025-2026)

| Browser | WebM/Opus | WebM (default) | MP4/AAC | OGG/Opus |
|---------|-----------|---------------|---------|----------|
| Chrome 100+ | Yes | Yes | No | Yes |
| Firefox 100+ | Yes | Yes | No | Yes |
| Edge 100+ | Yes | Yes | No | Yes |
| Safari 14.1+ | No* | No* | Yes | No |
| Safari 16.4+ | Partial* | Partial* | Yes | No |
| Safari 18.4+ | Yes | Yes | Yes | No |

*Safari 16.4 added WebM recording support, but it was unreliable until Safari 18.4 ([WebKit bug 238546](https://bugs.webkit.org/show_bug.cgi?id=238546)). For production reliability, treat Safari 18.4+ as the baseline for WebM/Opus.*

**Recommendation**: Target `audio/webm;codecs=opus` as primary, with `audio/mp4;codecs=aac` as Safari fallback (all Safari versions). The server-side ffmpeg pipeline handles both formats identically.

### MediaRecorder Configuration

```typescript
const RECORDER_CONFIG = {
  mimeType: getPreferredMimeType(),
  audioBitsPerSecond: 128_000,  // 128 kbps — higher quality preserves spectral detail for fingerprinting (per Section 09)
  // Note: sampleRate and channelCount are set on the MediaStream, not MediaRecorder
};

const AUDIO_CONSTRAINTS: MediaStreamConstraints = {
  audio: {
    channelCount: 1,           // Mono (reduces file size, server normalizes anyway)
    sampleRate: 48000,         // Browser native rate (will be downsampled server-side to 16kHz)
    echoCancellation: false,   // Disable for music recording (preserves audio fidelity)
    noiseSuppression: false,   // Disable for music recording
    autoGainControl: false,    // Disable for music recording
  },
};
```

**Why disable audio processing?** The browser's echo cancellation, noise suppression, and auto gain control are designed for voice calls. For music identification, they degrade the audio quality and can remove the exact features (harmonics, dynamics) that fingerprinting relies on.

---

## SvelteKit Audio Recorder Component

### Full Implementation

```svelte
<!-- src/lib/components/AudioRecorder.svelte -->
<script lang="ts">
  import { onMount } from 'svelte';

  // Props
  let {
    minDuration = 3,
    maxDuration = 30,
    onRecordingComplete,
  }: {
    minDuration?: number;
    maxDuration?: number;
    onRecordingComplete: (blob: Blob, duration: number) => void;
  } = $props();

  // State
  let isRecording = $state(false);
  let isPreparing = $state(false);
  let duration = $state(0);
  let audioLevel = $state(0);
  let error = $state<string | null>(null);
  let hasPermission = $state<boolean | null>(null);

  // Internal refs
  let mediaRecorder: MediaRecorder | null = null;
  let audioContext: AudioContext | null = null;
  let analyser: AnalyserNode | null = null;
  let stream: MediaStream | null = null;
  let chunks: Blob[] = [];
  let durationInterval: ReturnType<typeof setInterval> | null = null;
  let levelAnimationFrame: number | null = null;
  let startTime = 0;

  // Derived
  let canStop = $derived(duration >= minDuration);
  let timeRemaining = $derived(Math.max(0, minDuration - duration));
  let isAtMaxDuration = $derived(duration >= maxDuration);

  function getPreferredMimeType(): string {
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4;codecs=aac',
      'audio/ogg;codecs=opus',
    ];
    for (const mimeType of candidates) {
      if (MediaRecorder.isTypeSupported(mimeType)) {
        return mimeType;
      }
    }
    return '';
  }

  async function startRecording() {
    error = null;
    isPreparing = true;

    try {
      // 1. Request microphone access
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 48000,
          echoCancellation: false,
          noiseSuppression: false,
          autoGainControl: false,
        },
      });
      hasPermission = true;

      // 2. Set up audio level metering
      audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(stream);
      analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.8;
      source.connect(analyser);
      startLevelMetering();

      // 3. Create MediaRecorder
      const mimeType = getPreferredMimeType();
      mediaRecorder = new MediaRecorder(stream, {
        mimeType,
        audioBitsPerSecond: 128_000,
      });

      chunks = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunks.push(event.data);
        }
      };

      mediaRecorder.onstop = () => {
        const blob = new Blob(chunks, { type: mimeType || 'audio/webm' });
        const finalDuration = duration;
        cleanup();
        onRecordingComplete(blob, finalDuration);
      };

      mediaRecorder.onerror = (event) => {
        error = `Recording error: ${(event as ErrorEvent).message || 'Unknown error'}`;
        cleanup();
      };

      // 4. Start recording
      mediaRecorder.start(1000); // Collect data every 1 second
      startTime = Date.now();
      isRecording = true;
      isPreparing = false;

      // 5. Track duration
      durationInterval = setInterval(() => {
        duration = (Date.now() - startTime) / 1000;

        // Auto-stop at max duration
        if (duration >= maxDuration) {
          stopRecording();
        }
      }, 100);

    } catch (err) {
      isPreparing = false;
      if (err instanceof DOMException) {
        switch (err.name) {
          case 'NotAllowedError':
            error = 'Microphone permission denied. Please allow microphone access and try again.';
            hasPermission = false;
            break;
          case 'NotFoundError':
            error = 'No microphone found. Please connect a microphone and try again.';
            break;
          case 'NotReadableError':
            error = 'Microphone is in use by another application.';
            break;
          default:
            error = `Microphone error: ${err.message}`;
        }
      } else {
        error = `Failed to start recording: ${err}`;
      }
    }
  }

  function stopRecording() {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      mediaRecorder.stop();
    }
  }

  function cancelRecording() {
    cleanup();
    chunks = [];
    duration = 0;
  }

  function startLevelMetering() {
    if (!analyser) return;

    const dataArray = new Uint8Array(analyser.frequencyBinCount);

    function updateLevel() {
      if (!analyser) return;
      analyser.getByteFrequencyData(dataArray);

      // Calculate RMS level (0-1 range)
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) {
        const normalized = dataArray[i] / 255;
        sum += normalized * normalized;
      }
      audioLevel = Math.sqrt(sum / dataArray.length);

      levelAnimationFrame = requestAnimationFrame(updateLevel);
    }

    updateLevel();
  }

  function cleanup() {
    isRecording = false;
    isPreparing = false;
    audioLevel = 0;

    if (durationInterval) {
      clearInterval(durationInterval);
      durationInterval = null;
    }

    if (levelAnimationFrame) {
      cancelAnimationFrame(levelAnimationFrame);
      levelAnimationFrame = null;
    }

    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }

    if (audioContext) {
      audioContext.close();
      audioContext = null;
      analyser = null;
    }
  }

  function formatTime(seconds: number): string {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }

  // Check if too quiet (no significant audio detected in first 3 seconds)
  $effect(() => {
    if (isRecording && duration >= 3 && audioLevel < 0.01) {
      // Audio is very quiet — warn user
      error = 'Audio level is very low. Make sure your microphone is working and audio is playing.';
    }
  });

  onMount(() => {
    return () => cleanup();
  });
</script>

<div class="flex flex-col items-center gap-4">
  {#if error}
    <div class="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
      {error}
    </div>
  {/if}

  <!-- Audio Level Meter -->
  {#if isRecording}
    <div class="flex items-center gap-2">
      <div class="flex gap-0.5">
        {#each Array(20) as _, i}
          <div
            class="h-8 w-1.5 rounded-full transition-all duration-75"
            class:bg-green-500={audioLevel * 20 > i}
            class:bg-gray-200={audioLevel * 20 <= i}
            class:dark:bg-gray-700={audioLevel * 20 <= i}
          ></div>
        {/each}
      </div>
    </div>

    <!-- Duration Display -->
    <div class="text-center">
      <p class="text-2xl font-mono tabular-nums">{formatTime(duration)}</p>
      {#if !canStop}
        <p class="text-sm text-amber-600 dark:text-amber-400">
          Minimum {timeRemaining.toFixed(0)}s remaining...
        </p>
      {:else}
        <p class="text-sm text-green-600 dark:text-green-400">Ready to search</p>
      {/if}
    </div>
  {/if}

  <!-- Controls -->
  <div class="flex gap-3">
    {#if !isRecording}
      <button
        onclick={startRecording}
        disabled={isPreparing}
        class="flex items-center gap-2 rounded-full bg-red-500 px-6 py-3 text-white hover:bg-red-600 disabled:opacity-50"
      >
        {#if isPreparing}
          <span class="h-4 w-4 animate-spin rounded-full border-2 border-white border-t-transparent"></span>
          Preparing...
        {:else}
          <span class="h-3 w-3 rounded-full bg-white"></span>
          Record
        {/if}
      </button>
    {:else}
      <button
        onclick={stopRecording}
        disabled={!canStop}
        class="flex items-center gap-2 rounded-full bg-blue-500 px-6 py-3 text-white hover:bg-blue-600 disabled:opacity-50"
      >
        <span class="h-3 w-3 rounded-sm bg-white"></span>
        Search
      </button>
      <button
        onclick={cancelRecording}
        class="rounded-full bg-gray-200 px-4 py-3 text-gray-700 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-300"
      >
        Cancel
      </button>
    {/if}
  </div>
</div>
```

---

## Minimum Duration Enforcement

The component enforces the minimum duration at two levels:

### 1. UI Level (Component)
- The "Search" button is **disabled** until `duration >= minDuration` (3s default)
- A countdown shows time remaining before the button becomes active
- Auto-stop triggers at `maxDuration` (30s default)

### 2. Server Level (API)
- After ffmpeg decoding, the server validates PCM duration
- If `duration < 3s`, returns `400 Bad Request` with error code `AUDIO_TOO_SHORT`
- If `duration > 30s`, truncates to 30 seconds (takes the first 30s)

---

## Audio Level Metering

The component uses the Web Audio API `AnalyserNode` to show real-time audio levels:

```
getUserMedia → MediaStreamSource → AnalyserNode → getByteFrequencyData → visual bar
```

**Key details:**
- `fftSize: 256` gives 128 frequency bins — enough for a simple VU meter
- `smoothingTimeConstant: 0.8` provides smooth visual animation
- RMS calculation normalizes to 0-1 range for consistent display
- 20 bars visualize the level with color-coded thresholds (green = active)
- **Too quiet detection**: If `audioLevel < 0.01` after 3 seconds of recording, warn the user

---

## Raw PCM vs Encoded WebM Tradeoff

| Factor | Raw PCM (Float32Array) | Encoded WebM/Opus |
|--------|----------------------|-------------------|
| **File size** (10s audio) | ~640 KB (mono 16kHz s16le) | ~160 KB (Opus 128kbps) |
| **Upload time** (3G) | ~2 seconds | ~0.03 seconds |
| **Browser support** | All (via AudioContext) | All modern (MediaRecorder) |
| **Server decode needed** | No | Yes (ffmpeg) |
| **Quality preservation** | Perfect | Excellent (Opus at 128kbps preserves spectral detail needed for fingerprinting) |
| **Implementation complexity** | Medium (manual PCM extraction) | Low (MediaRecorder handles it) |

**Decision: Encoded WebM/Opus (CHOSEN)**

The ~4x size reduction is significant. A 10-second WebM/Opus recording at 128kbps is ~160 KB, while the same in raw PCM is ~640 KB. The server-side ffmpeg decode is cheap (~10ms). WebM/Opus is the clear winner.

**When to use PCM instead:**
- If you need client-side audio processing before upload (e.g., spectral analysis in the browser)
- If targeting very old browsers without MediaRecorder support (unlikely in 2026)

---

## Error Handling

### Permission Denied

```typescript
// DOMException.name === 'NotAllowedError'
// User denied microphone access or browser policy blocks it

// Recovery: Show clear message + instructions
error = 'Microphone permission denied. Please allow microphone access in your browser settings and try again.';
```

### No Microphone Found

```typescript
// DOMException.name === 'NotFoundError'
// No audio input device available

error = 'No microphone found. Please connect a microphone and try again.';
```

### Microphone In Use

```typescript
// DOMException.name === 'NotReadableError'
// Device is being used by another application

error = 'Microphone is in use by another application. Please close other apps using the mic and try again.';
```

### Audio Too Quiet

```typescript
// Detected via audio level metering
// audioLevel < 0.01 for > 3 seconds

error = 'Audio level is very low. Make sure your microphone is working and audio is playing near the microphone.';
```

### Recording Error

```typescript
// MediaRecorder 'error' event
// Rare — usually hardware or permission issues

mediaRecorder.onerror = (event) => {
  error = `Recording error: ${(event as ErrorEvent).message || 'An unexpected error occurred'}`;
  cleanup();
};
```

### Upload Failure

```typescript
// Network error or server rejection during upload
// Handled in the search mutation (TanStack Query)

import { createMutation } from '@tanstack/svelte-query';

const searchMutation = createMutation({
  mutationFn: async (blob: Blob) => {
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');
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

    return response.json();
  },
  onError: (error) => {
    // Handle: network failure, timeout, server error
    console.error('Search failed:', error);
  },
});
```

---

## Recommended npm Packages

For the SvelteKit audio capture implementation, **no additional npm packages are needed**. The native browser APIs are sufficient:

| Capability | Native API | Notes |
|-----------|-----------|-------|
| Microphone access | `navigator.mediaDevices.getUserMedia()` | All modern browsers |
| Audio recording | `MediaRecorder` | All modern browsers (Safari 14.1+) |
| Audio level metering | `AudioContext` + `AnalyserNode` | Web Audio API, universal |
| Duration measurement | `Date.now()` | Trivial |
| File upload | `FormData` + `fetch()` | Universal |
| State management | Svelte 5 Runes (`$state`, `$derived`, `$effect`) | Already in the project |
| Server state | `@tanstack/svelte-query` | Already in the project |

**Optional packages** (not recommended for v1, but useful in v2):

| Package | Version | Purpose | When to Add |
|---------|---------|---------|-------------|
| `wavesurfer.js` | ^7.0 | Audio waveform visualization | v2 — if you want to show recorded waveform |
| `lamejs` | ^1.2 | Client-side MP3 encoding | Never — WebM/Opus is better |
| `recorderjs` | Deprecated | Old recording library | Never — MediaRecorder replaces it |

---

## Checklist for Implementation

- [ ] Use `getPreferredMimeType()` to select codec — don't hardcode
- [ ] Disable echoCancellation, noiseSuppression, autoGainControl for music
- [ ] Set `audioBitsPerSecond: 128000` (preserves spectral detail for fingerprinting — per Section 09)
- [ ] Enforce minimum duration (3s) via disabled button + server validation
- [ ] Enforce maximum duration (30s) via auto-stop timer
- [ ] Show audio level meter to confirm mic is active
- [ ] Detect "too quiet" condition after 3 seconds
- [ ] Handle all error states: permission denied, no mic, in use, too quiet, network error
- [ ] Use `requestAnimationFrame` for level metering (not `setInterval`)
- [ ] Clean up resources on component unmount (`stream.getTracks().forEach(t => t.stop())`)
- [ ] Upload as `multipart/form-data` with `FormData` — not base64
- [ ] File extension in FormData: `recording.webm` (or `.mp4` for Safari fallback)
