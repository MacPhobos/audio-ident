<script lang="ts">
	import type { SearchResponse } from '$lib/api/generated';
	import { Music, Fingerprint, Waves, AlertCircle, Search } from 'lucide-svelte';

	let {
		response,
		isLoading,
		error
	}: {
		response: SearchResponse | null;
		isLoading: boolean;
		error: string | null;
	} = $props();

	// Tab priority: if top exact match has high confidence, show that first
	let defaultTab = $derived.by<'exact' | 'vibe'>(() => {
		if (!response) return 'exact';
		if (
			response.exact_matches.length > 0 &&
			response.exact_matches[0].confidence >= 0.85
		) {
			return 'exact';
		}
		if (response.vibe_matches.length > 0 && response.exact_matches.length === 0) {
			return 'vibe';
		}
		if (response.vibe_matches.length > 0 && response.exact_matches.length > 0) {
			if (response.exact_matches[0].confidence < 0.85) {
				return 'vibe';
			}
		}
		return 'exact';
	});

	let activeTab = $state<'exact' | 'vibe' | null>(null);

	// Reset manual tab selection when a new search response arrives
	$effect(() => {
		response; // dependency tracking
		activeTab = null;
	});

	let currentTab = $derived(activeTab ?? defaultTab);

	let hasExact = $derived((response?.exact_matches.length ?? 0) > 0);
	let hasVibe = $derived((response?.vibe_matches.length ?? 0) > 0);
	let hasAnyResults = $derived(hasExact || hasVibe);

	function formatOffset(seconds: number | null): string {
		if (seconds === null) return '';
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	function confidenceColor(confidence: number): string {
		if (confidence >= 0.85) return 'bg-green-100 text-green-800';
		if (confidence >= 0.5) return 'bg-yellow-100 text-yellow-800';
		return 'bg-red-100 text-red-800';
	}

	function confidenceLabel(confidence: number): string {
		if (confidence >= 0.85) return 'High';
		if (confidence >= 0.5) return 'Medium';
		return 'Low';
	}

	function setTab(tab: 'exact' | 'vibe') {
		activeTab = tab;
	}

	function handleTabKeydown(e: KeyboardEvent) {
		const tabs: Array<'exact' | 'vibe'> = ['exact', 'vibe'];
		const currentIndex = tabs.indexOf(activeTab ?? defaultTab);
		if (e.key === 'ArrowRight') {
			e.preventDefault();
			const next = tabs[(currentIndex + 1) % tabs.length];
			activeTab = next;
			(e.currentTarget as HTMLElement)?.parentElement
				?.querySelector<HTMLElement>(`[data-tab="${next}"]`)
				?.focus();
		} else if (e.key === 'ArrowLeft') {
			e.preventDefault();
			const prev = tabs[(currentIndex - 1 + tabs.length) % tabs.length];
			activeTab = prev;
			(e.currentTarget as HTMLElement)?.parentElement
				?.querySelector<HTMLElement>(`[data-tab="${prev}"]`)
				?.focus();
		}
	}
</script>

<div class="w-full">
	<!-- Loading Skeleton (no tabs shown to prevent tab flash) -->
	{#if isLoading}
		<div class="space-y-4" aria-busy="true" aria-label="Loading search results">
			{#each Array(3) as _}
				<div class="animate-pulse rounded-xl border bg-white p-5">
					<div class="flex items-start gap-4">
						<div class="h-10 w-10 rounded-lg bg-gray-200"></div>
						<div class="flex-1 space-y-2">
							<div class="h-4 w-2/3 rounded bg-gray-200"></div>
							<div class="h-3 w-1/3 rounded bg-gray-200"></div>
							<div class="h-3 w-1/4 rounded bg-gray-200"></div>
						</div>
						<div class="h-6 w-16 rounded-full bg-gray-200"></div>
					</div>
				</div>
			{/each}
		</div>

	<!-- Error State -->
	{:else if error}
		<div
			class="flex flex-col items-center gap-3 rounded-xl border border-red-200 bg-red-50 px-6 py-8"
			role="alert"
		>
			<AlertCircle class="h-8 w-8 text-red-500" />
			<p class="text-center text-sm font-medium text-red-700">{error}</p>
			<p class="text-center text-xs text-red-500">
				Try recording again or uploading a different file.
			</p>
		</div>

	<!-- No Results -->
	{:else if response && !hasAnyResults}
		<div class="flex flex-col items-center gap-3 rounded-xl border bg-white px-6 py-8">
			<Search class="h-8 w-8 text-gray-400" />
			<p class="text-center font-medium text-gray-700">No matches found</p>
			<ul class="space-y-1 text-center text-xs text-gray-500">
				<li>Try recording a longer clip (5-10 seconds)</li>
				<li>Move closer to the sound source</li>
				<li>Record in a quieter environment</li>
				<li>Make sure the track has been ingested</li>
			</ul>
		</div>

	<!-- Results -->
	{:else if response && hasAnyResults}
		<!-- Tabs -->
		<div class="mb-4 flex gap-1 rounded-lg bg-gray-100 p-1" role="tablist">
			<button
				role="tab"
				aria-selected={currentTab === 'exact'}
				aria-controls="panel-exact"
				id="tab-exact"
				data-tab="exact"
				tabindex={currentTab === 'exact' ? 0 : -1}
				onclick={() => setTab('exact')}
				onkeydown={handleTabKeydown}
				class="flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors
					{currentTab === 'exact'
					? 'bg-white text-gray-900 shadow-sm'
					: 'text-gray-600 hover:text-gray-900'}"
			>
				<Fingerprint class="h-4 w-4" />
				Exact ID
				{#if hasExact}
					<span class="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-semibold text-blue-700">
						{response.exact_matches.length}
					</span>
				{/if}
			</button>
			<button
				role="tab"
				aria-selected={currentTab === 'vibe'}
				aria-controls="panel-vibe"
				id="tab-vibe"
				data-tab="vibe"
				tabindex={currentTab === 'vibe' ? 0 : -1}
				onclick={() => setTab('vibe')}
				onkeydown={handleTabKeydown}
				class="flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors
					{currentTab === 'vibe'
					? 'bg-white text-gray-900 shadow-sm'
					: 'text-gray-600 hover:text-gray-900'}"
			>
				<Waves class="h-4 w-4" />
				Similar Vibe
				{#if hasVibe}
					<span class="rounded-full bg-purple-100 px-2 py-0.5 text-xs font-semibold text-purple-700">
						{response.vibe_matches.length}
					</span>
				{/if}
			</button>
		</div>

		<!-- Tab Panels -->
		<div aria-live="polite">
			<!-- Exact Matches Panel -->
			{#if currentTab === 'exact'}
				<div
					id="panel-exact"
					role="tabpanel"
					aria-labelledby="tab-exact"
					class="space-y-3"
				>
					{#if !hasExact}
						<div class="rounded-xl border bg-white px-6 py-6 text-center">
							<p class="text-sm text-gray-500">No exact fingerprint matches found.</p>
							<p class="mt-1 text-xs text-gray-400">Try the "Similar Vibe" tab for embedding-based results.</p>
						</div>
					{:else}
						{#each response.exact_matches as match, i}
							<div class="rounded-xl border bg-white p-4 transition-shadow hover:shadow-md">
								<div class="flex items-start gap-3">
									<div class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
										<Music class="h-5 w-5" />
									</div>
									<div class="min-w-0 flex-1">
										<p class="truncate font-medium text-gray-900">
											{match.track.title}
										</p>
										{#if match.track.artist}
											<p class="truncate text-sm text-gray-600">
												{match.track.artist}
											</p>
										{/if}
										{#if match.track.album}
											<p class="truncate text-xs text-gray-400">
												{match.track.album}
											</p>
										{/if}
										<div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
											{#if match.offset_seconds !== null}
												<span>Match at {formatOffset(match.offset_seconds)}</span>
												<span class="text-gray-300">&middot;</span>
											{/if}
											<span>{match.aligned_hashes} hashes aligned</span>
										</div>
									</div>
									<span
										class="shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold
											{confidenceColor(match.confidence)}"
									>
										{(match.confidence * 100).toFixed(0)}% {confidenceLabel(match.confidence)}
									</span>
								</div>
							</div>
						{/each}
					{/if}
				</div>
			{/if}

			<!-- Vibe Matches Panel -->
			{#if currentTab === 'vibe'}
				<div
					id="panel-vibe"
					role="tabpanel"
					aria-labelledby="tab-vibe"
					class="space-y-3"
				>
					{#if !hasVibe}
						<div class="rounded-xl border bg-white px-6 py-6 text-center">
							<p class="text-sm text-gray-500">No similar tracks found.</p>
							<p class="mt-1 text-xs text-gray-400">Try the "Exact ID" tab for fingerprint-based results.</p>
						</div>
					{:else}
						{#each response.vibe_matches as match, i}
							<div class="rounded-xl border bg-white p-4 transition-shadow hover:shadow-md">
								<div class="flex items-start gap-3">
									<div
										class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-purple-50 text-lg font-bold text-purple-600"
									>
										{i + 1}
									</div>
									<div class="min-w-0 flex-1">
										<p class="truncate font-medium text-gray-900">
											{match.track.title}
										</p>
										{#if match.track.artist}
											<p class="truncate text-sm text-gray-600">
												{match.track.artist}
											</p>
										{/if}
										{#if match.track.album}
											<p class="truncate text-xs text-gray-400">
												{match.track.album}
											</p>
										{/if}
										<p class="mt-1 text-xs text-gray-400">
											{match.embedding_model}
										</p>
									</div>
									<span class="shrink-0 rounded-full bg-purple-100 px-2.5 py-1 text-xs font-semibold text-purple-800">
										{(match.similarity * 100).toFixed(0)}%
									</span>
								</div>
							</div>
						{/each}
					{/if}
				</div>
			{/if}
		</div>

		<!-- Query Metadata -->
		<p class="mt-4 text-center text-xs text-gray-400">
			Searched in {response.query_duration_ms.toFixed(0)}ms &middot; Mode: {response.mode_used}
		</p>
	{/if}
</div>
