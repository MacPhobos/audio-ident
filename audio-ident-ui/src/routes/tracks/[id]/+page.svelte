<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { fetchTrackDetail, ApiRequestError, type TrackDetail } from '$lib/api/client';
	import {
		formatDuration,
		formatDate,
		formatFileSize,
		formatBitrate,
		formatSampleRate,
		formatChannels
	} from '$lib/format';
	import { ArrowLeft, Music, AlertCircle, Search } from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let trackId = $derived(page.params.id ?? '');

	let cameFromSearch = $state(false);

	$effect(() => {
		if (typeof sessionStorage === 'undefined') return;
		cameFromSearch = sessionStorage.getItem('audio-ident-search-state') !== null;
	});

	// ---------------------------------------------------------------------------
	// Query
	// ---------------------------------------------------------------------------

	const trackQuery = createQuery<TrackDetail>(() => ({
		queryKey: ['track', trackId],
		queryFn: () => fetchTrackDetail(trackId),
		retry: false
	}));

	// ---------------------------------------------------------------------------
	// Derived
	// ---------------------------------------------------------------------------

	let track = $derived(trackQuery.data);
	let is404 = $derived(
		trackQuery.isError &&
			trackQuery.error instanceof ApiRequestError &&
			trackQuery.error.status === 404
	);

	let embeddingIndexed = $derived(
		track?.embedding_model != null && track?.embedding_dim != null
	);
</script>

<svelte:head>
	<title>{track ? `${track.title} - audio-ident` : 'Track Detail - audio-ident'}</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-8">
	<!-- Back Links -->
	<div class="mb-6 flex flex-wrap items-center gap-4">
		<!-- eslint-disable svelte/no-navigation-without-resolve -- static route -->
		<a
			href="/tracks"
			class="inline-flex items-center gap-1.5 text-sm text-gray-500 transition-colors hover:text-gray-900"
		>
			<ArrowLeft class="h-4 w-4" />
			Back to Library
		</a>
		{#if cameFromSearch}
			<a
				href="/search"
				class="inline-flex items-center gap-1.5 text-sm text-gray-500 transition-colors hover:text-gray-900"
			>
				<Search class="h-4 w-4" />
				Back to Search Results
			</a>
		{/if}
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	</div>

	<!-- Loading State -->
	{#if trackQuery.isLoading}
		<div class="space-y-6" aria-busy="true" aria-label="Loading track details">
			<!-- Title skeleton -->
			<div class="space-y-2">
				<div class="h-8 w-2/3 animate-pulse rounded bg-gray-200"></div>
				<div class="h-5 w-1/3 animate-pulse rounded bg-gray-200"></div>
			</div>
			<!-- Cards skeleton -->
			<div class="grid gap-6 sm:grid-cols-2">
				<div class="animate-pulse rounded-xl border bg-white p-6">
					<div class="mb-4 h-5 w-1/3 rounded bg-gray-200"></div>
					<div class="space-y-3">
						{#each Array(5) as _}
							<div class="flex justify-between">
								<div class="h-4 w-1/4 rounded bg-gray-200"></div>
								<div class="h-4 w-1/3 rounded bg-gray-200"></div>
							</div>
						{/each}
					</div>
				</div>
				<div class="animate-pulse rounded-xl border bg-white p-6">
					<div class="mb-4 h-5 w-1/3 rounded bg-gray-200"></div>
					<div class="space-y-3">
						{#each Array(5) as _}
							<div class="flex justify-between">
								<div class="h-4 w-1/4 rounded bg-gray-200"></div>
								<div class="h-4 w-1/3 rounded bg-gray-200"></div>
							</div>
						{/each}
					</div>
				</div>
			</div>
		</div>

	<!-- 404 State -->
	{:else if is404}
		<div class="flex flex-col items-center gap-4 rounded-xl border bg-white px-6 py-12">
			<AlertCircle class="h-10 w-10 text-gray-300" />
			<p class="text-lg font-medium text-gray-700">Track not found</p>
			<p class="text-sm text-gray-500">
				The track you're looking for doesn't exist or has been removed.
			</p>
			<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- static route -->
			<a
				href="/tracks"
				class="mt-2 inline-flex items-center gap-1.5 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700"
			>
				<ArrowLeft class="h-4 w-4" />
				Go to Library
			</a>
		</div>

	<!-- Error State (non-404) -->
	{:else if trackQuery.isError}
		<div
			class="flex flex-col items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-6 py-8"
			role="alert"
		>
			<AlertCircle class="h-8 w-8 text-red-500" />
			<p class="text-center text-sm font-medium text-red-700">
				{trackQuery.error?.message ?? 'Failed to load track details'}
			</p>
			<button
				onclick={() => trackQuery.refetch()}
				class="rounded-lg bg-red-100 px-4 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-200"
			>
				Retry
			</button>
		</div>

	<!-- Track Detail -->
	{:else if track}
		<!-- Title Section -->
		<div class="mb-6">
			<div class="flex items-start gap-3">
				<div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
					<Music class="h-6 w-6" />
				</div>
				<div class="min-w-0">
					<h1 class="text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">
						{track.title}
					</h1>
					<p class="mt-0.5 text-gray-500">
						{track.artist ?? 'Unknown Artist'}
						{#if track.album}
							<span class="text-gray-300 mx-1">&mdash;</span>
							{track.album}
						{/if}
					</p>
				</div>
			</div>
		</div>

		<!-- Info Cards -->
		<div class="grid gap-6 sm:grid-cols-2">
			<!-- Track Information -->
			<div class="rounded-xl border bg-white p-6">
				<h2 class="mb-4 text-sm font-semibold uppercase tracking-wider text-gray-500">
					Track Information
				</h2>
				<dl class="space-y-3">
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Title</dt>
						<dd class="text-right text-gray-900">{track.title}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Artist</dt>
						<dd class="text-right text-gray-900">{track.artist ?? 'Unknown'}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Album</dt>
						<dd class="text-right text-gray-900">{track.album ?? 'Unknown'}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Duration</dt>
						<dd class="text-right font-mono text-gray-900">{formatDuration(track.duration_seconds)}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Ingested</dt>
						<dd class="text-right text-gray-900">{formatDate(track.ingested_at)}</dd>
					</div>
				</dl>
			</div>

			<!-- Audio Properties -->
			<div class="rounded-xl border bg-white p-6">
				<h2 class="mb-4 text-sm font-semibold uppercase tracking-wider text-gray-500">
					Audio Properties
				</h2>
				<dl class="space-y-3">
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Format</dt>
						<dd class="text-right font-mono text-gray-900">{track.format?.toUpperCase() ?? 'Unknown'}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Sample Rate</dt>
						<dd class="text-right font-mono text-gray-900">{formatSampleRate(track.sample_rate)}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Channels</dt>
						<dd class="text-right font-mono text-gray-900">{formatChannels(track.channels)}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">Bitrate</dt>
						<dd class="text-right font-mono text-gray-900">{formatBitrate(track.bitrate)}</dd>
					</div>
					<div class="flex justify-between text-sm">
						<dt class="font-medium text-gray-500">File Size</dt>
						<dd class="text-right font-mono text-gray-900">{formatFileSize(track.file_size_bytes)}</dd>
					</div>
				</dl>
			</div>
		</div>

		<!-- Indexing Status -->
		<div class="mt-6 rounded-xl border bg-white p-6">
			<h2 class="mb-4 text-sm font-semibold uppercase tracking-wider text-gray-500">
				Indexing Status
			</h2>
			<div class="space-y-3">
				<!-- Olaf fingerprint -->
				<div class="flex items-center justify-between text-sm">
					<span class="font-medium text-gray-500">Fingerprint (Olaf)</span>
					<span class="inline-flex items-center gap-2">
						{#if track.olaf_indexed}
							<span class="block h-2.5 w-2.5 rounded-full bg-green-500" aria-label="Indexed"></span>
							<span class="text-gray-900">Indexed</span>
						{:else}
							<span class="block h-2.5 w-2.5 rounded-full bg-red-500" aria-label="Not indexed"></span>
							<span class="text-gray-900">Not indexed</span>
						{/if}
					</span>
				</div>

				<!-- CLAP embeddings -->
				<div class="flex items-center justify-between text-sm">
					<span class="font-medium text-gray-500">Embeddings (CLAP)</span>
					<span class="inline-flex items-center gap-2">
						{#if embeddingIndexed}
							<span class="block h-2.5 w-2.5 rounded-full bg-green-500" aria-label="Indexed"></span>
							<span class="text-gray-900">Indexed ({track.embedding_dim}-dim)</span>
						{:else}
							<span class="block h-2.5 w-2.5 rounded-full bg-gray-400" aria-label="Not indexed"></span>
							<span class="text-gray-900">Not indexed</span>
						{/if}
					</span>
				</div>

				<!-- File Hash -->
				<div class="flex items-center justify-between text-sm">
					<span class="font-medium text-gray-500">File Hash (SHA-256)</span>
					<span class="truncate pl-4 font-mono text-xs text-gray-600" title={track.file_hash_sha256}>
						{track.file_hash_sha256.slice(0, 16)}...
					</span>
				</div>
			</div>
		</div>
	{/if}
</div>
