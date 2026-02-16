<script lang="ts">
	import { onMount } from 'svelte';
	import { trackAudioUrl } from '$lib/api/client';
	import { formatDuration } from '$lib/format';
	import { Play, Pause, Volume2, VolumeX, X, Loader2, Music, AlertCircle } from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// Module-level singleton: only one audio player plays at a time
	// ---------------------------------------------------------------------------

	let activePlayer: HTMLAudioElement | null = null;

	// ---------------------------------------------------------------------------
	// Props
	// ---------------------------------------------------------------------------

	let {
		trackId,
		title,
		artist = null,
		durationSeconds = 0,
		autoplay = false,
		onClose,
		onEnded,
		onError
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

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let audioEl: HTMLAudioElement | null = $state(null);
	let isPlaying = $state(false);
	let isLoading = $state(true);
	let isBuffering = $state(false);
	let hasError = $state(false);
	let errorMessage = $state<string | null>(null);
	let currentTime = $state(0);
	let audioDuration = $state(0);
	let volume = $state(1);
	let isMuted = $state(false);

	// ---------------------------------------------------------------------------
	// Derived
	// ---------------------------------------------------------------------------

	let streamUrl = $derived(trackAudioUrl(trackId));
	let displayDuration = $derived(audioDuration > 0 ? audioDuration : durationSeconds);
	let progress = $derived(displayDuration > 0 ? (currentTime / displayDuration) * 100 : 0);

	// ---------------------------------------------------------------------------
	// Volume persistence
	// ---------------------------------------------------------------------------

	const VOLUME_KEY = 'audio-ident-player-volume';

	$effect(() => {
		if (typeof localStorage !== 'undefined') {
			const saved = localStorage.getItem(VOLUME_KEY);
			if (saved !== null) {
				volume = parseFloat(saved);
			}
		}
	});

	$effect(() => {
		if (audioEl) {
			audioEl.volume = isMuted ? 0 : volume;
		}
		if (typeof localStorage !== 'undefined') {
			localStorage.setItem(VOLUME_KEY, String(volume));
		}
	});

	// ---------------------------------------------------------------------------
	// Singleton playback enforcement
	// ---------------------------------------------------------------------------

	$effect(() => {
		if (audioEl && isPlaying) {
			if (activePlayer && activePlayer !== audioEl) {
				activePlayer.pause();
			}
			activePlayer = audioEl;
		}
	});

	// ---------------------------------------------------------------------------
	// Audio event handlers
	// ---------------------------------------------------------------------------

	function handleCanPlay() {
		isLoading = false;
		isBuffering = false;
	}

	function handlePlaying() {
		isPlaying = true;
		isBuffering = false;
	}

	function handlePause() {
		isPlaying = false;
	}

	function handleWaiting() {
		isBuffering = true;
	}

	function handleTimeUpdate() {
		if (audioEl) {
			currentTime = audioEl.currentTime;
		}
	}

	function handleDurationChange() {
		if (audioEl && isFinite(audioEl.duration)) {
			audioDuration = audioEl.duration;
		}
	}

	function handleAudioEnded() {
		isPlaying = false;
		currentTime = 0;
		onEnded?.();
	}

	function handleAudioError() {
		isLoading = false;
		hasError = true;
		const code = audioEl?.error?.code;
		let msg = 'Failed to load audio.';
		switch (code) {
			case MediaError.MEDIA_ERR_ABORTED:
				msg = 'Audio playback was aborted.';
				break;
			case MediaError.MEDIA_ERR_NETWORK:
				msg = 'A network error prevented audio from loading.';
				break;
			case MediaError.MEDIA_ERR_DECODE:
				msg = 'Audio format is not supported by your browser.';
				break;
			case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
				msg = 'Audio file not found or format not supported.';
				break;
		}
		errorMessage = msg;
		onError?.(msg);
	}

	// ---------------------------------------------------------------------------
	// Player controls
	// ---------------------------------------------------------------------------

	function togglePlayPause() {
		if (!audioEl) return;
		if (isPlaying) {
			audioEl.pause();
		} else {
			audioEl.play().catch(() => {
				// Autoplay policy blocked — user will need to click again
			});
		}
	}

	function handleSeek(e: Event) {
		const input = e.target as HTMLInputElement;
		const newTime = (parseFloat(input.value) / 100) * displayDuration;
		if (audioEl) {
			audioEl.currentTime = newTime;
			currentTime = newTime;
		}
	}

	function handleVolumeChange(e: Event) {
		const input = e.target as HTMLInputElement;
		volume = parseFloat(input.value);
		if (isMuted && volume > 0) {
			isMuted = false;
		}
	}

	function toggleMute() {
		isMuted = !isMuted;
	}

	function handleKeyDown(e: KeyboardEvent) {
		if (!audioEl) return;
		switch (e.key) {
			case ' ':
				e.preventDefault();
				togglePlayPause();
				break;
			case 'ArrowLeft':
				e.preventDefault();
				audioEl.currentTime = Math.max(0, audioEl.currentTime - 5);
				break;
			case 'ArrowRight':
				e.preventDefault();
				audioEl.currentTime = Math.min(displayDuration, audioEl.currentTime + 5);
				break;
		}
	}

	// ---------------------------------------------------------------------------
	// Autoplay on mount
	// ---------------------------------------------------------------------------

	onMount(() => {
		if (autoplay && audioEl) {
			const tryAutoplay = () => {
				if (audioEl) {
					audioEl.play().catch(() => {
						// Autoplay blocked by browser policy
						isPlaying = false;
					});
				}
			};
			// Try immediately if ready, otherwise wait for canplay
			if (audioEl.readyState >= 3) {
				tryAutoplay();
			} else {
				audioEl.addEventListener('canplay', tryAutoplay, { once: true });
			}
		}

		// Cleanup on unmount
		return () => {
			if (audioEl) {
				audioEl.pause();
				audioEl.removeAttribute('src');
				audioEl.load();
			}
			if (activePlayer === audioEl) {
				activePlayer = null;
			}
		};
	});
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	class="rounded-xl border bg-white p-4 shadow-sm"
	role="region"
	aria-label="Audio player: {title}"
	onkeydown={handleKeyDown}
>
	<!-- Hidden audio element -->
	<audio
		bind:this={audioEl}
		src={streamUrl}
		preload="metadata"
		oncanplay={handleCanPlay}
		onplaying={handlePlaying}
		onpause={handlePause}
		onwaiting={handleWaiting}
		ontimeupdate={handleTimeUpdate}
		ondurationchange={handleDurationChange}
		onended={handleAudioEnded}
		onerror={handleAudioError}
	></audio>

	<!-- Header: Track Info + Close -->
	<div class="mb-3 flex items-start justify-between gap-3">
		<div class="flex items-center gap-3 min-w-0">
			<div
				class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600"
			>
				<Music class="h-5 w-5" />
			</div>
			<div class="min-w-0">
				<p class="truncate text-sm font-medium text-gray-900">{title}</p>
				{#if artist}
					<p class="truncate text-xs text-gray-500">{artist}</p>
				{/if}
			</div>
		</div>
		{#if onClose}
			<button
				onclick={onClose}
				class="shrink-0 rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
				aria-label="Close player"
			>
				<X class="h-4 w-4" />
			</button>
		{/if}
	</div>

	<!-- Error State -->
	{#if hasError}
		<div
			class="flex items-center gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
			role="alert"
		>
			<AlertCircle class="h-4 w-4 shrink-0" />
			<span>{errorMessage}</span>
		</div>
	{:else}
		<!-- Playback Controls -->
		<div class="flex items-center gap-3">
			<!-- Play/Pause Button -->
			<button
				onclick={togglePlayPause}
				disabled={isLoading}
				class="flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition-colors
					{isLoading
					? 'bg-gray-100 text-gray-400 cursor-wait'
					: 'bg-indigo-600 text-white hover:bg-indigo-700'}"
				aria-label={isPlaying ? 'Pause' : 'Play'}
				aria-disabled={isLoading}
			>
				{#if isLoading || isBuffering}
					<Loader2 class="h-4 w-4 animate-spin" />
				{:else if isPlaying}
					<Pause class="h-4 w-4" />
				{:else}
					<Play class="h-4 w-4 ml-0.5" />
				{/if}
			</button>

			<!-- Time + Seek -->
			<div class="flex flex-1 items-center gap-2">
				<span class="w-10 text-right font-mono text-xs text-gray-500 tabular-nums">
					{formatDuration(currentTime)}
				</span>
				<input
					type="range"
					min="0"
					max="100"
					step="0.1"
					value={progress}
					oninput={handleSeek}
					disabled={isLoading}
					class="audio-seek h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-gray-200
						disabled:cursor-not-allowed disabled:opacity-50
						[&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5
						[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
						[&::-webkit-slider-thumb]:bg-indigo-600 [&::-webkit-slider-thumb]:shadow-sm
						[&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:w-3.5
						[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none
						[&::-moz-range-thumb]:bg-indigo-600 [&::-moz-range-thumb]:shadow-sm"
					aria-label="Seek position"
					aria-valuemin={0}
					aria-valuemax={displayDuration}
					aria-valuenow={currentTime}
					aria-valuetext="{formatDuration(currentTime)} of {formatDuration(displayDuration)}"
				/>
				<span class="w-10 font-mono text-xs text-gray-500 tabular-nums">
					{formatDuration(displayDuration)}
				</span>
			</div>
		</div>

		<!-- Volume Control -->
		<div class="mt-2 flex items-center gap-2">
			<button
				onclick={toggleMute}
				class="rounded p-1 text-gray-400 transition-colors hover:text-gray-600"
				aria-label={isMuted ? 'Unmute' : 'Mute'}
			>
				{#if isMuted || volume === 0}
					<VolumeX class="h-4 w-4" />
				{:else}
					<Volume2 class="h-4 w-4" />
				{/if}
			</button>
			<input
				type="range"
				min="0"
				max="1"
				step="0.01"
				value={isMuted ? 0 : volume}
				oninput={handleVolumeChange}
				class="h-1 w-20 cursor-pointer appearance-none rounded-full bg-gray-200
					[&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:w-3
					[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
					[&::-webkit-slider-thumb]:bg-gray-500
					[&::-moz-range-thumb]:h-3 [&::-moz-range-thumb]:w-3
					[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-none
					[&::-moz-range-thumb]:bg-gray-500"
				aria-label="Volume"
				aria-valuemin={0}
				aria-valuemax={1}
				aria-valuenow={isMuted ? 0 : volume}
			/>
		</div>
	{/if}
</div>
