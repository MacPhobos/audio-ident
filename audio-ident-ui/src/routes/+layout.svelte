<script lang="ts">
	import '../app.css';
	import { QueryClient, QueryClientProvider } from '@tanstack/svelte-query';
	import NavBar from '$lib/components/NavBar.svelte';

	let { children } = $props();

	const queryClient = new QueryClient({
		defaultOptions: {
			queries: {
				retry: 1,
				staleTime: 30_000
			}
		}
	});
</script>

<QueryClientProvider client={queryClient}>
	<div class="min-h-screen bg-gray-50 text-gray-900">
		<NavBar />
		<main>
			{@render children()}
		</main>
	</div>
</QueryClientProvider>
