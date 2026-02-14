import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vitest/config';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		port: parseInt(process.env.PORT ?? '17000'),
		proxy: {
			'/health': {
				target: process.env.VITE_API_BASE_URL ?? 'http://localhost:17010',
				changeOrigin: true
			},
			'/api': {
				target: process.env.VITE_API_BASE_URL ?? 'http://localhost:17010',
				changeOrigin: true
			}
		}
	},
	test: {
		include: ['tests/**/*.test.ts'],
		environment: 'jsdom'
	}
});
