<script lang="ts">
	import { page } from '$app/state';
	import { createQuery } from '@tanstack/svelte-query';
	import { fetchHealth, type HealthResponse } from '$lib/api/client';
	import { Mic } from 'lucide-svelte';

	// Active route detection
	let isSearchActive = $derived(page.url.pathname === '/search');
	let isTracksActive = $derived(page.url.pathname.startsWith('/tracks'));

	// Health status for the status dot
	const healthQuery = createQuery<HealthResponse>(() => ({
		queryKey: ['health'],
		queryFn: fetchHealth,
		refetchInterval: 30_000
	}));

	let isHealthy = $derived(healthQuery.data?.status === 'ok');
	let isHealthLoading = $derived(healthQuery.isLoading);

	let healthTooltip = $derived.by(() => {
		if (isHealthLoading) return 'Checking service status...';
		if (isHealthy) return `Service healthy (v${healthQuery.data?.version ?? '?'})`;
		return 'Service unreachable';
	});
</script>

<nav class="border-b border-gray-200 bg-white" aria-label="Main navigation">
	<div class="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
		<!-- Logo / Home link -->
		<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -- static route -->
		<a href="/" class="text-lg font-bold tracking-tight text-gray-900 sm:text-xl"> audio-ident </a>

		<!-- Nav items (always visible, even on mobile) -->
		<!-- eslint-disable svelte/no-navigation-without-resolve -- static routes -->
		<div class="flex items-center gap-2 sm:gap-4">
			<!-- Identify (primary CTA) -->
			<a
				href="/search"
				class="inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium
               transition-colors sm:px-4 sm:py-2 sm:text-sm
               {isSearchActive
					? 'bg-indigo-700 text-white'
					: 'bg-indigo-600 text-white hover:bg-indigo-700'}"
				aria-current={isSearchActive ? 'page' : undefined}
			>
				<Mic class="h-3.5 w-3.5 sm:h-4 sm:w-4" />
				Identify
			</a>

			<!-- Library (secondary link) -->
			<a
				href="/tracks"
				class="inline-flex items-center gap-1.5 px-2 py-1.5 text-xs font-medium
               transition-colors sm:px-3 sm:text-sm
               {isTracksActive ? 'text-gray-900' : 'text-gray-500 hover:text-gray-900'}"
				aria-current={isTracksActive ? 'page' : undefined}
			>
				Library
			</a>
			<!-- eslint-enable svelte/no-navigation-without-resolve -->

			<!-- Health status dot -->
			<div class="flex items-center" title={healthTooltip}>
				{#if isHealthLoading}
					<span class="block h-2 w-2 rounded-full bg-gray-300" aria-label="Checking service status"
					></span>
				{:else if isHealthy}
					<span class="block h-2 w-2 rounded-full bg-green-500" aria-label="Service healthy"></span>
				{:else}
					<span class="block h-2 w-2 rounded-full bg-red-500" aria-label="Service unreachable"
					></span>
				{/if}
			</div>
		</div>
	</div>
</nav>
