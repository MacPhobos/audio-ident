<script lang="ts">
	import { createQuery } from '@tanstack/svelte-query';
	import {
		fetchHealth,
		fetchVersion,
		type HealthResponse,
		type VersionResponse
	} from '$lib/api/client';
	import { Activity, CheckCircle, XCircle, Info } from 'lucide-svelte';

	const healthQuery = createQuery<HealthResponse>(() => ({
		queryKey: ['health'],
		queryFn: fetchHealth,
		refetchInterval: 10_000
	}));

	const versionQuery = createQuery<VersionResponse>(() => ({
		queryKey: ['version'],
		queryFn: fetchVersion
	}));

	let isLoading = $derived(healthQuery.isLoading || versionQuery.isLoading);
	let hasError = $derived(healthQuery.isError || versionQuery.isError);
</script>

<svelte:head>
	<title>audio-ident</title>
</svelte:head>

<main class="mx-auto max-w-2xl px-4 py-16">
	<header class="mb-12 text-center">
		<h1 class="mb-2 text-4xl font-bold tracking-tight">audio-ident</h1>
		<p class="text-lg text-gray-500">Audio identification service</p>
	</header>

	<section class="space-y-6">
		<!-- Health Status Card -->
		<div class="rounded-xl border bg-white p-6 shadow-sm">
			<div class="mb-4 flex items-center gap-3">
				<Activity class="h-5 w-5 text-gray-400" />
				<h2 class="text-lg font-semibold">Service Health</h2>
			</div>

			{#if healthQuery.isLoading}
				<div class="flex items-center gap-2 text-gray-400">
					<div
						class="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600"
					></div>
					<span>Checking health...</span>
				</div>
			{:else if healthQuery.isError}
				<div class="flex items-center gap-2 text-red-600">
					<XCircle class="h-5 w-5" />
					<span>API unreachable</span>
				</div>
				<p class="mt-2 text-sm text-gray-500">
					{healthQuery.error?.message ?? 'Could not connect to the backend service.'}
				</p>
			{:else if healthQuery.data}
				<div class="flex items-center gap-2 text-green-600">
					<CheckCircle class="h-5 w-5" />
					<span class="font-medium">API reachable</span>
				</div>
				<dl class="mt-3 grid grid-cols-2 gap-2 text-sm text-gray-600">
					<dt class="font-medium">Status</dt>
					<dd>{healthQuery.data.status}</dd>
					<dt class="font-medium">Version</dt>
					<dd class="font-mono">{healthQuery.data.version}</dd>
				</dl>
			{/if}
		</div>

		<!-- Version Info Card -->
		<div class="rounded-xl border bg-white p-6 shadow-sm">
			<div class="mb-4 flex items-center gap-3">
				<Info class="h-5 w-5 text-gray-400" />
				<h2 class="text-lg font-semibold">Version Info</h2>
			</div>

			{#if versionQuery.isLoading}
				<div class="flex items-center gap-2 text-gray-400">
					<div
						class="h-4 w-4 animate-spin rounded-full border-2 border-gray-300 border-t-gray-600"
					></div>
					<span>Loading version...</span>
				</div>
			{:else if versionQuery.isError}
				<div class="flex items-center gap-2 text-red-600">
					<XCircle class="h-5 w-5" />
					<span>Could not fetch version info</span>
				</div>
			{:else if versionQuery.data}
				<dl class="grid grid-cols-2 gap-2 text-sm text-gray-600">
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

		<!-- Connection Summary -->
		{#if !isLoading}
			<div
				class="rounded-lg p-4 text-center text-sm font-medium {hasError
					? 'bg-red-50 text-red-700'
					: 'bg-green-50 text-green-700'}"
			>
				{#if hasError}
					Backend service is not responding. Make sure it's running on port 17010.
				{:else}
					All systems operational.
				{/if}
			</div>
		{/if}
	</section>
</main>
