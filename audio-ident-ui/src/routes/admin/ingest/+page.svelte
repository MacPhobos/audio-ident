<script lang="ts">
	import {
		Upload,
		FileAudio,
		ShieldAlert,
		CheckCircle,
		AlertTriangle,
		XCircle,
		Loader2
	} from 'lucide-svelte';
	import { ingestAudio, type IngestResponse } from '$lib/api/client';
	import { ApiRequestError } from '$lib/api/client';

	// ---------------------------------------------------------------------------
	// Types (local to component, not API types)
	// ---------------------------------------------------------------------------

	interface IngestResultEntry {
		filename: string;
		response: IngestResponse | null;
		error: string | null;
		timestamp: Date;
	}

	// ---------------------------------------------------------------------------
	// State
	// ---------------------------------------------------------------------------

	let selectedFile = $state<File | null>(null);
	let isIngesting = $state(false);
	let ingestError = $state<string | null>(null);
	let recentResults = $state<IngestResultEntry[]>([]);
	let confirmStep = $state(false);
	let isDragging = $state(false);

	const adminKey: string = import.meta.env.VITE_ADMIN_API_KEY ?? '';
	let hasAdminKey = $derived(adminKey.length > 0);

	// ---------------------------------------------------------------------------
	// Constants
	// ---------------------------------------------------------------------------

	const MAX_SIZE_BYTES = 50 * 1024 * 1024; // 50 MB
	const ALLOWED_EXTENSIONS = ['.mp3', '.wav', '.webm', '.ogg', '.mp4', '.m4a', '.flac'];
	const ALLOWED_TYPES = [
		'audio/mpeg',
		'audio/mp3',
		'audio/wav',
		'audio/x-wav',
		'audio/webm',
		'audio/ogg',
		'audio/mp4',
		'audio/x-m4a',
		'video/webm',
		'audio/flac',
		'audio/x-flac'
	];
	const ACCEPT_STRING = ALLOWED_EXTENSIONS.join(',');

	// ---------------------------------------------------------------------------
	// Helpers
	// ---------------------------------------------------------------------------

	function formatSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}

	function formatExtension(filename: string): string {
		const ext = filename.split('.').pop()?.toUpperCase();
		return ext ?? 'Unknown';
	}

	function formatTime(date: Date): string {
		return date.toLocaleTimeString();
	}

	function validateFile(file: File): string | null {
		if (file.size > MAX_SIZE_BYTES) {
			return `File too large (${formatSize(file.size)}). Maximum size is 50 MB.`;
		}

		const ext = '.' + (file.name.split('.').pop()?.toLowerCase() ?? '');
		const typeOk = ALLOWED_TYPES.includes(file.type) || ALLOWED_EXTENSIONS.includes(ext);

		if (!typeOk) {
			return `Unsupported format. Accepted: ${ALLOWED_EXTENSIONS.join(', ')}`;
		}

		return null;
	}

	// ---------------------------------------------------------------------------
	// File handling
	// ---------------------------------------------------------------------------

	function handleFile(file: File) {
		ingestError = null;
		confirmStep = false;

		const validationError = validateFile(file);
		if (validationError) {
			ingestError = validationError;
			selectedFile = null;
			return;
		}

		selectedFile = file;
	}

	function handleInputChange(e: Event) {
		const input = e.target as HTMLInputElement;
		if (input.files && input.files.length > 0) {
			handleFile(input.files[0]);
		}
		input.value = '';
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		isDragging = false;

		if (!e.dataTransfer?.files) return;

		if (e.dataTransfer.files.length > 0) {
			handleFile(e.dataTransfer.files[0]);
		}
	}

	function handleDragOver(e: DragEvent) {
		e.preventDefault();
		isDragging = true;
	}

	function handleDragLeave(e: DragEvent) {
		e.preventDefault();
		isDragging = false;
	}

	function clearFile() {
		selectedFile = null;
		confirmStep = false;
		ingestError = null;
	}

	// ---------------------------------------------------------------------------
	// Ingestion
	// ---------------------------------------------------------------------------

	function handleIngestClick() {
		if (!confirmStep) {
			confirmStep = true;
			return;
		}
		doIngest();
	}

	async function doIngest() {
		if (isIngesting) return; // Guard against double-submit
		if (!selectedFile || !hasAdminKey) return;

		isIngesting = true;
		ingestError = null;
		confirmStep = false;
		const filename = selectedFile.name;

		try {
			const response = await ingestAudio(selectedFile, adminKey);
			recentResults = [
				{ filename, response, error: null, timestamp: new Date() },
				...recentResults
			];
			selectedFile = null;
		} catch (err) {
			let errorMessage = 'An unexpected error occurred.';
			if (err instanceof ApiRequestError) {
				if (err.code === 'RATE_LIMITED') {
					errorMessage = 'Another ingestion is in progress. Please wait and try again.';
				} else {
					errorMessage = err.message;
				}
			} else if (err instanceof Error) {
				errorMessage = err.message;
			}
			ingestError = errorMessage;
			recentResults = [
				{ filename, response: null, error: errorMessage, timestamp: new Date() },
				...recentResults
			];
		} finally {
			isIngesting = false;
		}
	}
</script>

<svelte:head>
	<title>Ingest Audio - audio-ident</title>
</svelte:head>

<div class="mx-auto max-w-3xl px-4 py-8 sm:py-12" aria-busy={isIngesting}>
	<!-- Security Warning Banner -->
	{#if !hasAdminKey}
		<div
			class="mb-6 flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3"
			role="alert"
		>
			<ShieldAlert class="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
			<div>
				<p class="text-sm font-medium text-amber-800">Admin API key not configured</p>
				<p class="mt-1 text-sm text-amber-700">
					Set <code class="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs"
						>VITE_ADMIN_API_KEY</code
					> in your environment to enable ingestion.
				</p>
			</div>
		</div>
	{/if}

	<!-- Page Header -->
	<div class="mb-8">
		<h1 class="text-2xl font-bold tracking-tight text-gray-900 sm:text-3xl">Ingest Audio</h1>
		<p class="mt-2 text-sm text-gray-500">Add new tracks to the identification library.</p>
	</div>

	<!-- File Drop Zone -->
	<label
		class="flex w-full cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed
			px-6 py-8 transition-colors
			{isIngesting ? 'pointer-events-none opacity-50' : ''}
			{isDragging
			? 'border-blue-500 bg-blue-50'
			: 'border-gray-300 bg-white hover:border-gray-400 hover:bg-gray-50'}"
		ondrop={handleDrop}
		ondragover={handleDragOver}
		ondragleave={handleDragLeave}
		aria-label="Upload audio file for ingestion"
	>
		<div
			class="flex h-12 w-12 items-center justify-center rounded-full
				{isDragging ? 'bg-blue-100 text-blue-600' : 'bg-gray-100 text-gray-400'}"
		>
			<Upload class="h-6 w-6" />
		</div>

		<div class="text-center">
			<p class="text-sm font-medium text-gray-700">
				{#if isDragging}
					Drop your audio file here
				{:else}
					Drag and drop an audio file here, or click to browse
				{/if}
			</p>
			<p class="mt-1 text-xs text-gray-500">
				Supported: {ALLOWED_EXTENSIONS.join(', ')} -- up to 50 MB
			</p>
			<p class="mt-0.5 text-xs text-gray-400">Duration: 3 seconds to 30 minutes</p>
		</div>

		<input
			type="file"
			accept={ACCEPT_STRING}
			class="hidden"
			onchange={handleInputChange}
			aria-label="Select audio file for ingestion"
			disabled={isIngesting}
		/>
	</label>

	<!-- Selected File Preview -->
	{#if selectedFile}
		<div class="mt-4 flex w-full items-center gap-3 rounded-lg bg-blue-50 px-4 py-3">
			<FileAudio class="h-5 w-5 shrink-0 text-blue-600" />
			<div class="min-w-0 flex-1">
				<p class="truncate text-sm font-medium text-blue-800">
					{selectedFile.name}
				</p>
				<p class="text-xs text-blue-600">
					{formatSize(selectedFile.size)} &middot; {formatExtension(selectedFile.name)}
				</p>
			</div>
			<button
				onclick={clearFile}
				class="text-xs text-blue-600 underline hover:text-blue-800"
				disabled={isIngesting}
			>
				Clear
			</button>
		</div>
	{/if}

	<!-- Error Display -->
	{#if ingestError}
		<div
			class="mt-4 flex items-start gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700"
			role="alert"
		>
			<XCircle class="mt-0.5 h-4 w-4 shrink-0" />
			<span>{ingestError}</span>
		</div>
	{/if}

	<!-- Ingest Button -->
	<div class="mt-6">
		{#if isIngesting}
			<div class="flex items-center gap-3 rounded-lg bg-indigo-50 px-4 py-3">
				<Loader2 class="h-5 w-5 animate-spin text-indigo-600" />
				<div>
					<p class="text-sm font-medium text-indigo-800">Ingesting...</p>
					<p class="text-xs text-indigo-600">
						This may take 10-30 seconds for embedding generation.
					</p>
				</div>
			</div>
		{:else}
			<button
				onclick={handleIngestClick}
				disabled={!selectedFile || !hasAdminKey || isIngesting}
				aria-disabled={!selectedFile || !hasAdminKey || isIngesting}
				class="inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-medium transition-colors
					{!selectedFile || !hasAdminKey || isIngesting
					? 'cursor-not-allowed bg-gray-200 text-gray-400'
					: confirmStep
						? 'bg-amber-600 text-white hover:bg-amber-700'
						: 'bg-indigo-600 text-white hover:bg-indigo-700'}"
			>
				{#if confirmStep}
					<AlertTriangle class="h-4 w-4" />
					Are you sure? This will permanently add this track.
				{:else}
					<Upload class="h-4 w-4" />
					Ingest File
				{/if}
			</button>
			{#if confirmStep}
				<button
					onclick={clearFile}
					class="ml-3 text-sm text-gray-500 underline hover:text-gray-700"
				>
					Cancel
				</button>
			{/if}
		{/if}
	</div>

	<!-- Recent Results -->
	{#if recentResults.length > 0}
		<div class="mt-10" aria-live="polite">
			<h2 class="mb-4 text-lg font-semibold text-gray-900">Recent Ingestions</h2>
			<div class="space-y-2">
				{#each recentResults as entry (entry.timestamp.getTime() + entry.filename)}
					<div
						class="flex items-start gap-3 rounded-lg border px-4 py-3
							{entry.response?.status === 'ingested'
							? 'border-green-200 bg-green-50'
							: entry.response?.status === 'duplicate'
								? 'border-amber-200 bg-amber-50'
								: 'border-red-200 bg-red-50'}"
					>
						<!-- Status Icon -->
						{#if entry.response?.status === 'ingested'}
							<CheckCircle class="mt-0.5 h-5 w-5 shrink-0 text-green-600" />
						{:else if entry.response?.status === 'duplicate'}
							<AlertTriangle class="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
						{:else}
							<XCircle class="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
						{/if}

						<!-- Result Details -->
						<div class="min-w-0 flex-1">
							<p
								class="truncate text-sm font-medium
									{entry.response?.status === 'ingested'
									? 'text-green-800'
									: entry.response?.status === 'duplicate'
										? 'text-amber-800'
										: 'text-red-800'}"
							>
								{entry.filename}
							</p>
							{#if entry.response?.status === 'ingested'}
								<p class="text-xs text-green-700">
									Added to library: "{entry.response.title}"
									{#if entry.response.artist}
										by {entry.response.artist}
									{/if}
									--
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic route -->
									<a href="/tracks/{entry.response.track_id}" class="underline hover:text-green-900"
										>View track</a
									>
								</p>
							{:else if entry.response?.status === 'duplicate'}
								<p class="text-xs text-amber-700">
									This file is already in the library
									{#if entry.response.title}
										as "{entry.response.title}"
									{/if}
									--
									<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- dynamic route -->
									<a href="/tracks/{entry.response.track_id}" class="underline hover:text-amber-900"
										>View track</a
									>
								</p>
							{:else}
								<p class="text-xs text-red-700">{entry.error ?? 'Unknown error'}</p>
							{/if}
						</div>

						<!-- Timestamp -->
						<span class="shrink-0 text-xs text-gray-400">{formatTime(entry.timestamp)}</span>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>
