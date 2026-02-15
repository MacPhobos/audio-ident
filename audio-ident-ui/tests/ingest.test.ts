import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ingestAudio, ApiRequestError } from '../src/lib/api/client';

describe('ingestAudio', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('returns IngestResponse on 201 success', async () => {
		const mockResponse = {
			track_id: '550e8400-e29b-41d4-a716-446655440000',
			title: 'Test Song',
			artist: 'Test Artist',
			status: 'ingested'
		};

		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				status: 201,
				json: () => Promise.resolve(mockResponse)
			})
		);

		const file = new File(['audio-data'], 'test.mp3', { type: 'audio/mpeg' });
		const result = await ingestAudio(file, 'test-admin-key');

		expect(result).toEqual(mockResponse);
		expect(fetch).toHaveBeenCalledWith(
			'/api/v1/ingest',
			expect.objectContaining({
				method: 'POST',
				headers: { 'X-Admin-Key': 'test-admin-key' }
			})
		);
	});

	it('throws ApiRequestError with FORBIDDEN on 403', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 403,
				statusText: 'Forbidden',
				json: () =>
					Promise.resolve({
						error: {
							code: 'FORBIDDEN',
							message: 'Invalid or missing admin API key.'
						}
					})
			})
		);

		const file = new File(['audio-data'], 'test.mp3', { type: 'audio/mpeg' });

		try {
			await ingestAudio(file, 'wrong-key');
			expect.unreachable('Should have thrown');
		} catch (err) {
			expect(err).toBeInstanceOf(ApiRequestError);
			const apiErr = err as ApiRequestError;
			expect(apiErr.code).toBe('FORBIDDEN');
			expect(apiErr.status).toBe(403);
		}
	});

	it('throws ApiRequestError with RATE_LIMITED on 429', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 429,
				statusText: 'Too Many Requests',
				json: () =>
					Promise.resolve({
						error: {
							code: 'RATE_LIMITED',
							message: 'Another ingestion is in progress. Please try again in a moment.'
						}
					})
			})
		);

		const file = new File(['audio-data'], 'test.mp3', { type: 'audio/mpeg' });

		try {
			await ingestAudio(file, 'test-admin-key');
			expect.unreachable('Should have thrown');
		} catch (err) {
			expect(err).toBeInstanceOf(ApiRequestError);
			const apiErr = err as ApiRequestError;
			expect(apiErr.code).toBe('RATE_LIMITED');
			expect(apiErr.status).toBe(429);
		}
	});

	it('throws ApiRequestError on 400 validation error', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 400,
				statusText: 'Bad Request',
				json: () =>
					Promise.resolve({
						error: {
							code: 'UNSUPPORTED_FORMAT',
							message: 'Unsupported audio format: text/plain.'
						}
					})
			})
		);

		const file = new File(['not-audio'], 'test.txt', { type: 'text/plain' });

		try {
			await ingestAudio(file, 'test-admin-key');
			expect.unreachable('Should have thrown');
		} catch (err) {
			expect(err).toBeInstanceOf(ApiRequestError);
			const apiErr = err as ApiRequestError;
			expect(apiErr.code).toBe('UNSUPPORTED_FORMAT');
			expect(apiErr.status).toBe(400);
		}
	});
});
