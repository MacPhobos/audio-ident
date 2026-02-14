import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchHealth, fetchVersion } from '../src/lib/api/client';

describe('API client', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('fetchHealth returns parsed JSON on success', async () => {
		const mockResponse = { status: 'ok', version: '0.1.0' };
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve(mockResponse)
			})
		);

		const result = await fetchHealth();
		expect(result).toEqual(mockResponse);
		expect(fetch).toHaveBeenCalledWith('/health');
	});

	it('fetchVersion returns parsed JSON on success', async () => {
		const mockResponse = {
			name: 'audio-ident-service',
			version: '0.1.0',
			git_sha: 'abc1234',
			build_time: 'unknown'
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve(mockResponse)
			})
		);

		const result = await fetchVersion();
		expect(result).toEqual(mockResponse);
		expect(fetch).toHaveBeenCalledWith('/api/v1/version');
	});

	it('fetchHealth throws on non-ok response', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 503,
				statusText: 'Service Unavailable'
			})
		);

		await expect(fetchHealth()).rejects.toThrow('API error: 503 Service Unavailable');
	});
});
