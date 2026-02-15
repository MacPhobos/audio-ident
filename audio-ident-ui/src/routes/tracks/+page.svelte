<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onDestroy } from 'svelte';
	import { createQuery } from '@tanstack/svelte-query';
	import {
		fetchTracks,
		type PaginatedTrackResponse
	} from '$lib/api/client';
	import { formatDuration } from '$lib/format';
	import {
		Library,
		Search,
		ChevronLeft,
		ChevronRight,
		AlertCircle,
		RefreshCw
	} from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// URL-driven state
	// ---------------------------------------------------------------------------

	let currentPage = $derived(Number(page.url.searchParams.get('page')) || 1);
	let currentPageSize = $derived(Number(page.url.searchParams.get('pageSize')) || 50);
	let currentSearch = $derived(page.url.searchParams.get('search') ?? '');

	// ---------------------------------------------------------------------------
	// Debounced search input
	// ---------------------------------------------------------------------------

	let searchInput = $state('');
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;

	// Sync the input when URL-driven search changes (e.g. back button)
	$effect(() => {
		searchInput = currentSearch;
	});

	function handleSearchInput(e: Event) {
		const value = (e.target as HTMLInputElement).value;
		searchInput = value;

		if (debounceTimer) clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			const url = new URL(page.url);
			url.searchParams.set('page', '1');
			if (value) {
				url.searchParams.set('search', value);
			} else {
				url.searchParams.delete('search');
			}
			goto(url.toString(), { replaceState: true, keepFocus: true });
		}, 300);
	}

	onDestroy(() => {
		if (debounceTimer) clearTimeout(debounceTimer);
	});

	// ---------------------------------------------------------------------------
	// Query
	// ---------------------------------------------------------------------------

	const tracksQuery = createQuery<PaginatedTrackResponse>(() => ({
		queryKey: ['tracks', currentPage, currentPageSize, currentSearch],
		queryFn: () => fetchTracks(currentPage, currentPageSize, currentSearch || undefined)
	}));

	// ---------------------------------------------------------------------------
	// Derived helpers
	// ---------------------------------------------------------------------------

	let tracks = $derived(tracksQuery.data?.data ?? []);
	let pagination = $derived(tracksQuery.data?.pagination);
	let totalPages = $derived(pagination?.totalPages ?? 0);
	let totalItems = $derived(pagination?.totalItems ?? 0);

	let hasPrev = $derived(currentPage > 1);
	let hasNext = $derived(currentPage < totalPages);

	// Visible page range (start/end indices for display)
	let rangeStart = $derived((currentPage - 1) * currentPageSize + 1);
	let rangeEnd = $derived(Math.min(currentPage * currentPageSize, totalItems));

	// ---------------------------------------------------------------------------
	// Navigation
	// ---------------------------------------------------------------------------

	function goToPage(p: number) {
		const url = new URL(page.url);
		url.searchParams.set('page', String(p));
		goto(url.toString());
	}
</script>

<svelte:head>
	<title>Track Library - audio-ident</title>
</svelte:head>

<div class="mx-auto max-w-5xl px-4 py-8">
	<!-- Header -->
	<header class="mb-6">
		<div class="flex items-center gap-3">
			<Library class="h-6 w-6 text-gray-700" />
			<h1 class="text-2xl font-bold tracking-tight sm:text-3xl">Track Library</h1>
		</div>
		<p class="mt-1 text-sm text-gray-500">
			Browse and search all ingested tracks
		</p>
	</header>

	<!-- Search Bar -->
	<div class="mb-6">
		<div class="relative">
			<Search class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
			<input
				type="search"
				placeholder="Search by title or artist..."
				value={searchInput}
				oninput={handleSearchInput}
				class="w-full rounded-lg border border-gray-300 bg-white py-2.5 pl-10 pr-4 text-sm
					placeholder:text-gray-400
					focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
				aria-label="Search tracks"
			/>
		</div>
	</div>

	<!-- Loading State -->
	{#if tracksQuery.isLoading}
		<div class="space-y-3" aria-busy="true" aria-label="Loading tracks">
			<!-- Desktop skeleton table -->
			<div class="hidden sm:block">
				<div class="overflow-hidden rounded-xl border bg-white">
					<div class="border-b bg-gray-50 px-4 py-3">
						<div class="flex gap-4">
							<div class="h-4 w-1/3 animate-pulse rounded bg-gray-200"></div>
							<div class="h-4 w-1/5 animate-pulse rounded bg-gray-200"></div>
							<div class="h-4 w-1/5 animate-pulse rounded bg-gray-200"></div>
							<div class="h-4 w-16 animate-pulse rounded bg-gray-200"></div>
						</div>
					</div>
					{#each Array(8) as _}
						<div class="border-b px-4 py-3 last:border-b-0">
							<div class="flex gap-4">
								<div class="h-4 w-1/3 animate-pulse rounded bg-gray-200"></div>
								<div class="h-4 w-1/5 animate-pulse rounded bg-gray-200"></div>
								<div class="h-4 w-1/5 animate-pulse rounded bg-gray-200"></div>
								<div class="h-4 w-16 animate-pulse rounded bg-gray-200"></div>
							</div>
						</div>
					{/each}
				</div>
			</div>
			<!-- Mobile skeleton cards -->
			<div class="space-y-3 sm:hidden">
				{#each Array(6) as _}
					<div class="animate-pulse rounded-xl border bg-white p-4">
						<div class="space-y-2">
							<div class="h-4 w-3/4 rounded bg-gray-200"></div>
							<div class="h-3 w-1/2 rounded bg-gray-200"></div>
							<div class="h-3 w-1/3 rounded bg-gray-200"></div>
						</div>
					</div>
				{/each}
			</div>
		</div>

	<!-- Error State -->
	{:else if tracksQuery.isError}
		<div
			class="flex flex-col items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-6 py-8"
			role="alert"
		>
			<AlertCircle class="h-8 w-8 text-red-500" />
			<p class="text-center text-sm font-medium text-red-700">
				{tracksQuery.error?.message ?? 'Failed to load tracks'}
			</p>
			<button
				onclick={() => tracksQuery.refetch()}
				class="inline-flex items-center gap-2 rounded-lg bg-red-100 px-4 py-2 text-sm font-medium text-red-700 transition-colors hover:bg-red-200"
			>
				<RefreshCw class="h-4 w-4" />
				Retry
			</button>
		</div>

	<!-- Empty State -->
	{:else if tracks.length === 0}
		<div class="flex flex-col items-center gap-3 rounded-xl border bg-white px-6 py-12">
			<Library class="h-10 w-10 text-gray-300" />
			{#if currentSearch}
				<p class="text-center font-medium text-gray-700">No tracks found</p>
				<p class="text-center text-sm text-gray-500">
					No tracks match "{currentSearch}". Try a different search term.
				</p>
			{:else}
				<p class="text-center font-medium text-gray-700">No tracks in library</p>
				<p class="text-center text-sm text-gray-500">
					Ingest audio files to populate the library.
				</p>
			{/if}
		</div>

	<!-- Results -->
	{:else}
		<div aria-live="polite" aria-atomic="true">
			<p class="sr-only">
				Showing {rangeStart} to {rangeEnd} of {totalItems} tracks{currentSearch ? ` matching "${currentSearch}"` : ''}
			</p>
		</div>
		<!-- Desktop Table (>= 640px) -->
		<div class="hidden sm:block">
			<div class="overflow-hidden rounded-xl border bg-white">
				<table class="w-full text-sm">
					<thead>
						<tr class="border-b bg-gray-50 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
							<th scope="col" class="px-4 py-3">Title</th>
							<th scope="col" class="px-4 py-3">Artist</th>
							<th scope="col" class="px-4 py-3">Album</th>
							<th scope="col" class="px-4 py-3 text-right">Duration</th>
						</tr>
					</thead>
					<tbody>
						{#each tracks as track (track.id)}
							<tr class="border-b last:border-b-0 transition-colors hover:bg-gray-50">
								<td class="px-4 py-3">
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic route -->
									<a
										href="/tracks/{track.id}"
										class="font-medium text-gray-900 hover:text-indigo-600 hover:underline"
									>
										{track.title}
									</a>
								</td>
								<td class="px-4 py-3 text-gray-600">
									{track.artist ?? '--'}
								</td>
								<td class="px-4 py-3 text-gray-600">
									{track.album ?? '--'}
								</td>
								<td class="px-4 py-3 text-right font-mono text-gray-600">
									{formatDuration(track.duration_seconds)}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>

		<!-- Mobile Cards (< 640px) -->
		<div class="space-y-3 sm:hidden">
			{#each tracks as track (track.id)}
				<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic route -->
				<a
					href="/tracks/{track.id}"
					class="block rounded-xl border bg-white p-4 transition-shadow hover:shadow-md"
				>
					<p class="truncate font-medium text-gray-900">{track.title}</p>
					{#if track.artist}
						<p class="truncate text-sm text-gray-600">{track.artist}</p>
					{/if}
					<div class="mt-1 flex items-center gap-2 text-xs text-gray-400">
						{#if track.album}
							<span class="truncate">{track.album}</span>
							<span>&middot;</span>
						{/if}
						<span class="font-mono">{formatDuration(track.duration_seconds)}</span>
					</div>
				</a>
			{/each}
		</div>

		<!-- Pagination -->
		{#if totalPages > 1}
			<nav class="mt-6 flex items-center justify-between" aria-label="Pagination">
				<p class="text-sm text-gray-500">
					Showing {rangeStart}-{rangeEnd} of {totalItems} tracks
				</p>
				<div class="flex items-center gap-2">
					<button
						onclick={() => goToPage(currentPage - 1)}
						disabled={!hasPrev}
						class="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors
							{hasPrev
								? 'text-gray-700 hover:bg-gray-50'
								: 'cursor-not-allowed text-gray-300'}"
						aria-disabled={!hasPrev}
						aria-label="Previous page"
					>
						<ChevronLeft class="h-4 w-4" />
						Prev
					</button>
					<span class="px-2 text-sm text-gray-500">
						Page {currentPage} of {totalPages}
					</span>
					<button
						onclick={() => goToPage(currentPage + 1)}
						disabled={!hasNext}
						class="inline-flex items-center gap-1 rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors
							{hasNext
								? 'text-gray-700 hover:bg-gray-50'
								: 'cursor-not-allowed text-gray-300'}"
						aria-disabled={!hasNext}
						aria-label="Next page"
					>
						Next
						<ChevronRight class="h-4 w-4" />
					</button>
				</div>
			</nav>
		{/if}
	{/if}
</div>
