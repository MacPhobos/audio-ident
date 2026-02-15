<script lang="ts">
	import { Upload, FileAudio, AlertTriangle } from 'lucide-svelte';

	let { onFileSelected }: { onFileSelected: (file: File) => void } = $props();

	let isDragging = $state(false);
	let error = $state<string | null>(null);
	let warning = $state<string | null>(null);
	let selectedFile = $state<File | null>(null);

	const MAX_SIZE_BYTES = 10 * 1024 * 1024; // 10 MB
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

	function formatSize(bytes: number): string {
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}

	function formatExtension(filename: string): string {
		const ext = filename.split('.').pop()?.toUpperCase();
		return ext ?? 'Unknown';
	}

	function validateFile(file: File): string | null {
		if (file.size > MAX_SIZE_BYTES) {
			return `File too large (${formatSize(file.size)}). Maximum size is 10 MB.`;
		}

		const ext = '.' + (file.name.split('.').pop()?.toLowerCase() ?? '');
		const typeOk = ALLOWED_TYPES.includes(file.type) || ALLOWED_EXTENSIONS.includes(ext);

		if (!typeOk) {
			return `Unsupported format. Accepted: ${ALLOWED_EXTENSIONS.join(', ')}`;
		}

		return null;
	}

	function handleFile(file: File) {
		error = null;
		warning = null;

		const validationError = validateFile(file);
		if (validationError) {
			error = validationError;
			selectedFile = null;
			return;
		}

		selectedFile = file;
		onFileSelected(file);
	}

	function handleInputChange(e: Event) {
		const input = e.target as HTMLInputElement;
		if (input.files && input.files.length > 0) {
			handleFile(input.files[0]);
		}
		// Reset input so re-selecting the same file fires onchange again
		input.value = '';
	}

	function handleDrop(e: DragEvent) {
		e.preventDefault();
		isDragging = false;
		warning = null;

		if (!e.dataTransfer?.files) return;

		if (e.dataTransfer.files.length > 1) {
			warning = 'Multiple files dropped. Using the first file.';
		}

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
</script>

<div class="flex flex-col items-center gap-4">
	<!-- Drop Zone -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<label
		class="flex w-full cursor-pointer flex-col items-center gap-3 rounded-xl border-2 border-dashed
			px-6 py-8 transition-colors
			{isDragging
			? 'border-blue-500 bg-blue-50'
			: 'border-gray-300 bg-white hover:border-gray-400 hover:bg-gray-50'}"
		ondrop={handleDrop}
		ondragover={handleDragOver}
		ondragleave={handleDragLeave}
		aria-label="Upload audio file"
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
					Click to browse or drag and drop
				{/if}
			</p>
			<p class="mt-1 text-xs text-gray-500">
				{ALLOWED_EXTENSIONS.join(', ')} up to 10 MB
			</p>
		</div>

		<input
			type="file"
			accept={ACCEPT_STRING}
			class="hidden"
			onchange={handleInputChange}
			aria-label="Select audio file"
		/>
	</label>

	<!-- Selected File Info -->
	{#if selectedFile}
		<div class="flex w-full items-center gap-3 rounded-lg bg-green-50 px-4 py-3">
			<FileAudio class="h-5 w-5 shrink-0 text-green-600" />
			<div class="min-w-0 flex-1">
				<p class="truncate text-sm font-medium text-green-800">
					{selectedFile.name}
				</p>
				<p class="text-xs text-green-600">
					{formatSize(selectedFile.size)} &middot; {formatExtension(selectedFile.name)}
				</p>
			</div>
		</div>
	{/if}

	<!-- Warning Display -->
	{#if warning}
		<div
			class="flex w-full items-center gap-2 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-700"
			role="status"
		>
			<AlertTriangle class="h-4 w-4 shrink-0" />
			<span>{warning}</span>
		</div>
	{/if}

	<!-- Error Display -->
	{#if error}
		<div
			class="flex w-full items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700"
			role="alert"
		>
			<AlertTriangle class="h-4 w-4 shrink-0" />
			<span>{error}</span>
		</div>
	{/if}
</div>
