<script lang="ts">
	import { onMount } from 'svelte';
	import { Mic, Square, AlertTriangle } from 'lucide-svelte';

	let {
		minDuration = 3,
		maxDuration = 30,
		onRecordingComplete
	}: {
		minDuration?: number;
		maxDuration?: number;
		onRecordingComplete: (blob: Blob, duration: number) => void;
	} = $props();

	let isRecording = $state(false);
	let isPreparing = $state(false);
	let duration = $state(0);
	let audioLevel = $state(0);
	let error = $state<string | null>(null);
	let canStop = $derived(duration >= minDuration);
	let timeRemaining = $derived(Math.max(0, minDuration - duration));

	let mediaRecorder: MediaRecorder | null = null;
	let audioContext: AudioContext | null = null;
	let analyser: AnalyserNode | null = null;
	let animationFrameId: number | null = null;
	let durationIntervalId: ReturnType<typeof setInterval> | null = null;
	let activeStream: MediaStream | null = null;
	let chunks: Blob[] = [];
	let recordingStartTime = 0;
	let tooQuietWarning = $state(false);

	const LEVEL_BARS = 20;

	function getPreferredMimeType(): string {
		const candidates = [
			'audio/webm;codecs=opus',
			'audio/webm',
			'audio/mp4;codecs=aac',
			'audio/ogg;codecs=opus'
		];

		for (const mime of candidates) {
			if (MediaRecorder.isTypeSupported(mime)) {
				return mime;
			}
		}

		return '';
	}

	function formatTime(seconds: number): string {
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	function updateAudioLevel() {
		if (!analyser) return;

		const dataArray = new Float32Array(analyser.frequencyBinCount);
		analyser.getFloatTimeDomainData(dataArray);

		let sum = 0;
		for (let i = 0; i < dataArray.length; i++) {
			sum += dataArray[i] * dataArray[i];
		}
		const rms = Math.sqrt(sum / dataArray.length);
		audioLevel = Math.min(1, rms * 5);

		if (isRecording && duration >= 3 && audioLevel < 0.01) {
			tooQuietWarning = true;
		} else if (audioLevel >= 0.01) {
			tooQuietWarning = false;
		}

		animationFrameId = requestAnimationFrame(updateAudioLevel);
	}

	async function startRecording() {
		error = null;
		tooQuietWarning = false;
		isPreparing = true;

		try {
			const stream = await navigator.mediaDevices.getUserMedia({
				audio: {
					channelCount: 1,
					sampleRate: 48000,
					echoCancellation: false,
					noiseSuppression: false,
					autoGainControl: false
				}
			});

			activeStream = stream;

			const mimeType = getPreferredMimeType();
			const options: MediaRecorderOptions = {
				audioBitsPerSecond: 128000
			};
			if (mimeType) {
				options.mimeType = mimeType;
			}

			audioContext = new AudioContext({ sampleRate: 48000 });

			// Handle iOS AudioContext interrupted state
			if (audioContext.state === 'suspended') {
				await audioContext.resume();
			}

			// Detect iOS AudioContext interruptions (phone call, notification)
			audioContext.onstatechange = () => {
				if (audioContext?.state === 'interrupted') {
					stopRecording();
					error = 'Recording interrupted. Please try again.';
				}
			};

			const source = audioContext.createMediaStreamSource(stream);
			analyser = audioContext.createAnalyser();
			analyser.fftSize = 256;
			analyser.smoothingTimeConstant = 0.8;
			source.connect(analyser);

			chunks = [];
			mediaRecorder = new MediaRecorder(stream, options);

			mediaRecorder.ondataavailable = (e) => {
				if (e.data.size > 0) {
					chunks.push(e.data);
				}
			};

			mediaRecorder.onstop = () => {
				const actualDuration = (Date.now() - recordingStartTime) / 1000;

				if (chunks.length === 0 || actualDuration < minDuration) {
					// Rapid record/stop or too short: discard
					cleanupMediaResources(stream);
					return;
				}

				const recordedMime = mediaRecorder?.mimeType || mimeType || 'audio/webm';
				const blob = new Blob(chunks, { type: recordedMime });

				if (blob.size === 0) {
					cleanupMediaResources(stream);
					return;
				}

				cleanupMediaResources(stream);
				onRecordingComplete(blob, actualDuration);
			};

			mediaRecorder.onerror = () => {
				error = 'Recording failed. Please try again.';
				cleanupRecording(stream);
			};

			mediaRecorder.start(250);
			recordingStartTime = Date.now();
			isRecording = true;
			isPreparing = false;
			duration = 0;

			durationIntervalId = setInterval(() => {
				duration = (Date.now() - recordingStartTime) / 1000;

				if (duration >= maxDuration) {
					stopRecording();
				}
			}, 100);

			updateAudioLevel();
		} catch (err) {
			isPreparing = false;

			if (err instanceof DOMException) {
				switch (err.name) {
					case 'NotAllowedError':
						error = 'Microphone access denied. Please allow microphone access in your browser settings.';
						break;
					case 'NotFoundError':
						error = 'No microphone found. Please connect a microphone and try again.';
						break;
					case 'NotReadableError':
						error = 'Microphone is in use by another application. Please close it and try again.';
						break;
					default:
						error = `Microphone error: ${err.message}`;
				}
			} else {
				error = 'Failed to start recording. Please try again.';
			}
		}
	}

	function cleanupMediaResources(stream: MediaStream) {
		stream.getTracks().forEach((track) => track.stop());
		activeStream = null;

		if (animationFrameId !== null) {
			cancelAnimationFrame(animationFrameId);
			animationFrameId = null;
		}

		if (durationIntervalId !== null) {
			clearInterval(durationIntervalId);
			durationIntervalId = null;
		}

		if (audioContext) {
			audioContext.onstatechange = null;
			audioContext.close().catch(() => {});
			audioContext = null;
		}

		analyser = null;
		isRecording = false;
		audioLevel = 0;
		tooQuietWarning = false;
	}

	function cleanupRecording(stream: MediaStream) {
		cleanupMediaResources(stream);
		mediaRecorder = null;
		chunks = [];
	}

	function stopRecording() {
		// Clear interval immediately to prevent duration drift between call and onstop
		if (durationIntervalId !== null) {
			clearInterval(durationIntervalId);
			durationIntervalId = null;
		}

		if (mediaRecorder && mediaRecorder.state === 'recording') {
			mediaRecorder.stop();
		}
	}

	onMount(() => {
		return () => {
			if (mediaRecorder && mediaRecorder.state === 'recording') {
				mediaRecorder.stop();
			}

			// Stop stream tracks directly in case onstop doesn't fire
			if (activeStream) {
				activeStream.getTracks().forEach((track) => track.stop());
				activeStream = null;
			}

			if (animationFrameId !== null) {
				cancelAnimationFrame(animationFrameId);
			}

			if (durationIntervalId !== null) {
				clearInterval(durationIntervalId);
			}

			if (audioContext) {
				audioContext.onstatechange = null;
				audioContext.close().catch(() => {});
			}
		};
	});
</script>

<div class="flex flex-col items-center gap-4">
	<!-- Level Meter -->
	{#if isRecording}
		<div
			class="flex h-16 items-end gap-0.5"
			role="progressbar"
			aria-label="Audio level"
			aria-valuenow={Math.round(audioLevel * 100)}
			aria-valuemin={0}
			aria-valuemax={100}
		>
			{#each Array(LEVEL_BARS) as _, i}
				{@const barThreshold = (i + 1) / LEVEL_BARS}
				{@const isActive = audioLevel >= barThreshold}
				{@const barColor =
					barThreshold > 0.8
						? 'bg-red-500'
						: barThreshold > 0.6
							? 'bg-yellow-500'
							: 'bg-green-500'}
				<div
					class="w-2 rounded-sm transition-all duration-75 {isActive
						? barColor
						: 'bg-gray-200'}"
					style="height: {isActive ? Math.max(8, (i + 1) * (64 / LEVEL_BARS)) : 8}px"
				></div>
			{/each}
		</div>
	{/if}

	<!-- Duration Display -->
	{#if isRecording}
		<div class="text-center">
			<p class="text-2xl font-mono font-semibold tabular-nums">
				{formatTime(duration)}
			</p>
			{#if !canStop}
				<p class="text-sm text-gray-500">
					{timeRemaining.toFixed(0)}s more needed
				</p>
			{:else}
				<p class="text-sm text-green-600" aria-live="polite">Ready to stop</p>
			{/if}
		</div>
	{/if}

	<!-- Too Quiet Warning -->
	{#if tooQuietWarning}
		<div
			class="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-700"
			role="alert"
		>
			<AlertTriangle class="h-4 w-4 shrink-0" />
			<span>Audio level is very low. Move closer to the sound source.</span>
		</div>
	{/if}

	<!-- Record/Stop Button -->
	<button
		onclick={isRecording ? stopRecording : startRecording}
		disabled={isPreparing || (isRecording && !canStop)}
		aria-disabled={isPreparing || (isRecording && !canStop)}
		aria-label={isRecording ? 'Stop recording' : 'Start recording'}
		class="flex h-16 w-16 items-center justify-center rounded-full transition-all
			{isRecording
			? canStop
				? 'bg-red-600 hover:bg-red-700 text-white'
				: 'bg-red-300 text-white cursor-not-allowed'
			: isPreparing
				? 'bg-gray-300 text-gray-500 cursor-wait'
				: 'bg-red-600 hover:bg-red-700 text-white cursor-pointer'}"
	>
		{#if isPreparing}
			<div class="h-6 w-6 animate-spin rounded-full border-2 border-white border-t-transparent">
			</div>
		{:else if isRecording}
			<Square class="h-6 w-6" />
		{:else}
			<Mic class="h-6 w-6" />
		{/if}
	</button>

	<p class="text-sm text-gray-500">
		{#if isPreparing}
			Preparing microphone...
		{:else if isRecording}
			Recording...
		{:else}
			Tap to record
		{/if}
	</p>

	<!-- Error Display -->
	{#if error}
		<div
			class="flex items-center gap-2 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700"
			role="alert"
		>
			<AlertTriangle class="h-4 w-4 shrink-0" />
			<span>{error}</span>
		</div>
	{/if}
</div>
