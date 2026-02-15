<script lang="ts">
	import { createMutation } from '@tanstack/svelte-query';
	import { searchAudio, ApiRequestError } from '$lib/api/client';
	import type { SearchMode, SearchResponse } from '$lib/api/client';
	import AudioRecorder from '$lib/components/AudioRecorder.svelte';
	import AudioUploader from '$lib/components/AudioUploader.svelte';
	import SearchResults from '$lib/components/SearchResults.svelte';
	import { Mic, Upload, Lightbulb } from 'lucide-svelte';

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	type InputMode = 'record' | 'upload';
	type PageState = 'idle' | 'recording' | 'searching' | 'results';

	let inputMode = $state<InputMode>('record');
	let pageState = $state<PageState>('idle');
	let searchMode = $state<SearchMode>('both');
	let searchResponse = $state<SearchResponse | null>(null);
	let searchError = $state<string | null>(null);

	let abortController: AbortController | null = null;

	// ---------------------------------------------------------------------------
	// Search state preservation (sessionStorage)
	// ---------------------------------------------------------------------------

	const SEARCH_STATE_KEY = 'audio-ident-search-state';

	function saveSearchState() {
		if (typeof sessionStorage === 'undefined') return;
		sessionStorage.setItem(
			SEARCH_STATE_KEY,
			JSON.stringify({
				response: searchResponse,
				mode: searchMode,
				inputMode: inputMode
			})
		);
	}

	// Restore state from sessionStorage when returning from track detail
	$effect(() => {
		if (typeof sessionStorage === 'undefined') return;
		const saved = sessionStorage.getItem(SEARCH_STATE_KEY);
		if (saved) {
			try {
				const state = JSON.parse(saved);
				searchResponse = state.response;
				searchMode = state.mode;
				inputMode = state.inputMode;
				pageState = 'results';
			} catch {
				// Ignore corrupt data
			}
			sessionStorage.removeItem(SEARCH_STATE_KEY);
		}
	});

	// ---------------------------------------------------------------------------
	// Mutation
	// ---------------------------------------------------------------------------

	const mutation = createMutation<SearchResponse, Error, { blob: Blob; duration: number }>(() => ({
		mutationFn: async ({ blob }: { blob: Blob; duration: number }) => {
			// Cancel any previous in-flight request
			if (abortController) {
				abortController.abort();
			}
			abortController = new AbortController();

			return searchAudio(blob, searchMode, 10, abortController.signal);
		},
		onSuccess: (data: SearchResponse) => {
			searchResponse = data;
			searchError = null;
			pageState = 'results';
		},
		onError: (err: Error) => {
			if (err instanceof ApiRequestError) {
				searchError = err.message;
			} else if (err.name === 'AbortError') {
				// Cancelled by user or new search, do not update state
				return;
			} else {
				searchError = 'An unexpected error occurred. Please try again.';
			}
			pageState = 'results';
		}
	}));

	// ---------------------------------------------------------------------------
	// Handlers
	// ---------------------------------------------------------------------------

	function handleRecordingComplete(blob: Blob, duration: number) {
		pageState = 'searching';
		mutation.mutate({ blob, duration });
	}

	function handleFileSelected(file: File) {
		pageState = 'searching';
		mutation.mutate({ blob: file, duration: 0 });
	}

	function resetSearch() {
		if (abortController) {
			abortController.abort();
			abortController = null;
		}
		pageState = 'idle';
		searchResponse = null;
		searchError = null;
	}

	function switchInputMode(mode: InputMode) {
		inputMode = mode;
		if (pageState === 'results') {
			resetSearch();
		}
	}
</script>

<svelte:head>
	<title>Search - audio-ident</title>
</svelte:head>

<div class="mx-auto max-w-2xl px-4 py-8 sm:py-12">
	<!-- Header -->
	<header class="mb-8 text-center">
		<h1 class="text-3xl font-bold tracking-tight sm:text-4xl">Identify Audio</h1>
		<p class="mt-2 text-gray-500">Record a clip or upload a file to find matching tracks</p>
	</header>

	<!-- Search Mode Selector -->
	{#if pageState === 'idle' || pageState === 'recording'}
		<div class="mb-6">
			<label class="mb-2 block text-sm font-medium text-gray-700" for="search-mode-select">
				Search mode
			</label>
			<select
				id="search-mode-select"
				bind:value={searchMode}
				class="w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm
					focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
			>
				<option value="both">Both (Fingerprint + Vibe)</option>
				<option value="exact">Exact ID (Fingerprint only)</option>
				<option value="vibe">Similar Vibe (Embedding only)</option>
			</select>
		</div>
	{/if}

	<!-- Input Mode Toggle -->
	{#if pageState === 'idle'}
		<div class="mb-6 flex gap-1 rounded-lg bg-gray-100 p-1" role="tablist" aria-label="Input mode">
			<button
				role="tab"
				aria-selected={inputMode === 'record'}
				onclick={() => switchInputMode('record')}
				class="flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2.5 text-sm font-medium transition-colors
					{inputMode === 'record' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'}"
			>
				<Mic class="h-4 w-4" />
				Record
			</button>
			<button
				role="tab"
				aria-selected={inputMode === 'upload'}
				onclick={() => switchInputMode('upload')}
				class="flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-2.5 text-sm font-medium transition-colors
					{inputMode === 'upload' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-600 hover:text-gray-900'}"
			>
				<Upload class="h-4 w-4" />
				Upload
			</button>
		</div>
	{/if}

	<!-- Input Section -->
	{#if pageState === 'idle' || pageState === 'recording'}
		<section class="rounded-xl border bg-white p-6">
			{#if inputMode === 'record'}
				<AudioRecorder onRecordingComplete={handleRecordingComplete} />

				<!-- Recording Tips -->
				<div class="mt-6 rounded-lg bg-amber-50 p-4">
					<div class="mb-2 flex items-center gap-2 text-sm font-medium text-amber-800">
						<Lightbulb class="h-4 w-4" />
						Tips for best results
					</div>
					<ul class="space-y-1 text-xs text-amber-700">
						<li>Hold your phone near the speaker</li>
						<li>Record in a quiet environment</li>
						<li>Try to capture a distinctive part of the song</li>
						<li>Recordings of 5-10 seconds work best</li>
					</ul>
				</div>
			{:else}
				<AudioUploader onFileSelected={handleFileSelected} />
			{/if}
		</section>
	{/if}

	<!-- Searching State -->
	{#if pageState === 'searching'}
		<section class="rounded-xl border bg-white p-8 text-center">
			<div
				class="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-4 border-gray-200 border-t-blue-600"
			></div>
			<p class="font-medium text-gray-700">Searching for matches...</p>
			<p class="mt-1 text-sm text-gray-500">Analyzing audio fingerprint and embedding</p>
			<button
				onclick={resetSearch}
				class="mt-4 text-sm text-gray-500 underline hover:text-gray-700"
			>
				Cancel
			</button>
		</section>
	{/if}

	<!-- Results Section -->
	{#if pageState === 'results'}
		<section class="space-y-4">
			<SearchResults
				response={searchResponse}
				isLoading={false}
				error={searchError}
				onTrackClick={saveSearchState}
			/>

			<div class="text-center">
				<button
					onclick={resetSearch}
					class="rounded-lg bg-gray-100 px-4 py-2 text-sm font-medium text-gray-700
						transition-colors hover:bg-gray-200"
				>
					Search again
				</button>
			</div>
		</section>
	{/if}
</div>
