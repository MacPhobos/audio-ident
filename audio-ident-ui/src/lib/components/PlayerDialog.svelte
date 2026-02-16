<script lang="ts">
	import Mp3Player from './Mp3Player.svelte';
	import type { TrackInfo } from '$lib/api/client';

	// ---------------------------------------------------------------------------
	// Props
	// ---------------------------------------------------------------------------

	let {
		track = null,
		open = false,
		onClose
	}: {
		track: TrackInfo | null;
		open: boolean;
		onClose: () => void;
	} = $props();

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let dialogEl: HTMLDialogElement | null = $state(null);

	// ---------------------------------------------------------------------------
	// Sync open prop with native dialog
	// ---------------------------------------------------------------------------

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

	/**
	 * Close on backdrop click. The native <dialog> fires a click event on the
	 * dialog element itself (not a child) when the backdrop is clicked.
	 */
	function handleDialogClick(e: MouseEvent) {
		if (e.target === dialogEl) {
			onClose();
		}
	}
</script>

<dialog
	bind:this={dialogEl}
	onclose={handleDialogClose}
	onclick={handleDialogClick}
	class="w-full max-w-md rounded-xl border-none bg-transparent p-0 backdrop:bg-black/50"
	aria-label={track ? `Now playing: ${track.title}` : 'Audio player'}
>
	{#if track && open}
		<Mp3Player
			trackId={String(track.id)}
			title={track.title}
			artist={track.artist}
			durationSeconds={track.duration_seconds}
			autoplay={true}
			onClose={handleDialogClose}
			onEnded={handleDialogClose}
		/>
	{/if}
</dialog>
