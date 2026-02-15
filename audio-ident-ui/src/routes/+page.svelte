<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import {
		fetchHealth,
		fetchVersion,
		type HealthResponse,
		type VersionResponse
	} from '$lib/api/client';
	import { Mic, Fingerprint, Waves, ChevronDown, ChevronUp } from 'lucide-svelte';

	const healthQuery = createQuery<HealthResponse>(() => ({
		queryKey: ['health'],
		queryFn: fetchHealth,
		refetchInterval: 10_000
	}));

	const versionQuery = createQuery<VersionResponse>(() => ({
		queryKey: ['version'],
		queryFn: fetchVersion
	}));

	let statusExpanded = $state(false);

	let isHealthy = $derived(healthQuery.data?.status === 'ok');
	let isLoading = $derived(healthQuery.isLoading || versionQuery.isLoading);
	let hasError = $derived(healthQuery.isError || versionQuery.isError);
</script>

<svelte:head>
	<title>audio-ident</title>
</svelte:head>

<div class="mx-auto max-w-4xl px-4 py-12 sm:py-20">
	<!-- Hero Section -->
	<section class="mb-16 text-center sm:mb-20">
		<h1 class="mb-4 text-4xl font-bold tracking-tight text-gray-900 sm:text-5xl">
			Identify Any Song
		</h1>
		<p class="mx-auto mb-8 max-w-xl text-lg text-gray-500">
			Record a clip or upload a file to find matching tracks using acoustic fingerprinting and AI
			similarity.
		</p>
		<!-- eslint-disable svelte/no-navigation-without-resolve -- static route -->
		<a
			href="/search"
			class="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-3
             text-base font-semibold text-white shadow-sm transition-colors
             hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500
             focus:ring-offset-2 sm:px-8 sm:py-4 sm:text-lg"
		>
			<Mic class="h-5 w-5" />
			Start Identifying
		</a>
		<!-- eslint-enable svelte/no-navigation-without-resolve -->
	</section>

	<!-- How It Works -->
	<section class="mb-16 sm:mb-20">
		<h2 class="mb-8 text-center text-xl font-semibold text-gray-900 sm:text-2xl">How it works</h2>
		<div class="grid gap-6 sm:grid-cols-3 sm:gap-8">
			<!-- Card 1: Record or Upload -->
			<div class="rounded-xl border bg-white p-6 text-center">
				<div
					class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-indigo-50 text-indigo-600"
				>
					<Mic class="h-6 w-6" />
				</div>
				<h3 class="mb-2 font-semibold text-gray-900">Record or Upload</h3>
				<p class="text-sm text-gray-500">
					Capture audio from your microphone or upload an audio file to identify.
				</p>
			</div>

			<!-- Card 2: Fingerprint Match -->
			<div class="rounded-xl border bg-white p-6 text-center">
				<div
					class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-green-50 text-green-600"
				>
					<Fingerprint class="h-6 w-6" />
				</div>
				<h3 class="mb-2 font-semibold text-gray-900">Fingerprint Match</h3>
				<p class="text-sm text-gray-500">
					Exact acoustic identification finds the precise track, like Shazam.
				</p>
			</div>

			<!-- Card 3: Vibe Match -->
			<div class="rounded-xl border bg-white p-6 text-center">
				<div
					class="mx-auto mb-4 flex h-12 w-12 items-center justify-center
                    rounded-full bg-purple-50 text-purple-600"
				>
					<Waves class="h-6 w-6" />
				</div>
				<h3 class="mb-2 font-semibold text-gray-900">Vibe Match</h3>
				<p class="text-sm text-gray-500">
					AI-powered similarity finds tracks that sound alike, even without an exact match.
				</p>
			</div>
		</div>
	</section>

	<!-- System Status (compact, expandable) -->
	<section class="rounded-xl border bg-white">
		<button
			onclick={() => (statusExpanded = !statusExpanded)}
			class="flex w-full items-center justify-between px-6 py-4 text-left
             transition-colors hover:bg-gray-50"
			aria-expanded={statusExpanded}
			aria-controls="status-detail"
		>
			<div class="flex items-center gap-3">
				{#if isLoading}
					<span class="block h-2.5 w-2.5 rounded-full bg-gray-300"></span>
					<span class="text-sm text-gray-500">Checking service status...</span>
				{:else if hasError}
					<span class="block h-2.5 w-2.5 rounded-full bg-red-500"></span>
					<span class="text-sm text-red-600"> Backend service is not responding </span>
				{:else if isHealthy}
					<span class="block h-2.5 w-2.5 rounded-full bg-green-500"></span>
					<span class="text-sm text-gray-600">
						Service healthy
						{#if versionQuery.data}
							&mdash; v{versionQuery.data.version}
							({versionQuery.data.git_sha})
						{/if}
					</span>
				{:else}
					<span class="block h-2.5 w-2.5 rounded-full bg-yellow-500"></span>
					<span class="text-sm text-gray-500">Service status unknown</span>
				{/if}
			</div>
			{#if statusExpanded}
				<ChevronUp class="h-4 w-4 text-gray-400" />
			{:else}
				<ChevronDown class="h-4 w-4 text-gray-400" />
			{/if}
		</button>

		{#if statusExpanded}
			<div id="status-detail" class="border-t px-6 py-4">
				<div class="grid gap-6 sm:grid-cols-2">
					<!-- Health Detail -->
					<div>
						<h3 class="mb-3 text-sm font-semibold text-gray-900">Service Health</h3>
						{#if healthQuery.isLoading}
							<p class="text-sm text-gray-400">Checking...</p>
						{:else if healthQuery.isError}
							<p class="text-sm text-red-600">
								{healthQuery.error?.message ?? 'Could not connect to backend.'}
							</p>
						{:else if healthQuery.data}
							<dl class="grid grid-cols-2 gap-1 text-sm text-gray-600">
								<dt class="font-medium">Status</dt>
								<dd>{healthQuery.data.status}</dd>
								<dt class="font-medium">Version</dt>
								<dd class="font-mono">{healthQuery.data.version}</dd>
							</dl>
						{/if}
					</div>

					<!-- Version Detail -->
					<div>
						<h3 class="mb-3 text-sm font-semibold text-gray-900">Version Info</h3>
						{#if versionQuery.isLoading}
							<p class="text-sm text-gray-400">Loading...</p>
						{:else if versionQuery.isError}
							<p class="text-sm text-red-600">Could not fetch version info.</p>
						{:else if versionQuery.data}
							<dl class="grid grid-cols-2 gap-1 text-sm text-gray-600">
								<dt class="font-medium">Name</dt>
								<dd>{versionQuery.data.name}</dd>
								<dt class="font-medium">Version</dt>
								<dd class="font-mono">{versionQuery.data.version}</dd>
								<dt class="font-medium">Git SHA</dt>
								<dd class="font-mono">{versionQuery.data.git_sha}</dd>
								<dt class="font-medium">Build Time</dt>
								<dd>{versionQuery.data.build_time}</dd>
							</dl>
						{/if}
					</div>
				</div>
			</div>
		{/if}
	</section>
</div>
