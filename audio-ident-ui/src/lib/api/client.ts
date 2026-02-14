import type { components } from './generated';

export type HealthResponse = components['schemas']['HealthResponse'];
export type VersionResponse = components['schemas']['VersionResponse'];

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

async function fetchJSON<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE_URL}${path}`);
	if (!res.ok) {
		throw new Error(`API error: ${res.status} ${res.statusText}`);
	}
	return res.json() as Promise<T>;
}

export function fetchHealth(): Promise<HealthResponse> {
	return fetchJSON<HealthResponse>('/health');
}

export function fetchVersion(): Promise<VersionResponse> {
	return fetchJSON<VersionResponse>('/api/v1/version');
}
