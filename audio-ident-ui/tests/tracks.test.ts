import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchTracks, fetchTrackDetail } from '../src/lib/api/client';
import {
	formatDuration,
	formatFileSize,
	formatDate,
	formatBitrate,
	formatSampleRate,
	formatChannels
} from '../src/lib/format';

// ---------------------------------------------------------------------------
// API Client tests
// ---------------------------------------------------------------------------

describe('Tracks API client', () => {
	beforeEach(() => {
		vi.restoreAllMocks();
	});

	it('fetchTracks sends correct query params (defaults)', async () => {
		const mockResponse = {
			data: [],
			pagination: { page: 1, pageSize: 50, totalItems: 0, totalPages: 0 }
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve(mockResponse)
			})
		);

		const result = await fetchTracks();
		expect(result).toEqual(mockResponse);
		expect(fetch).toHaveBeenCalledWith('/api/v1/tracks?page=1&pageSize=50');
	});

	it('fetchTracks sends search param when provided', async () => {
		const mockResponse = {
			data: [],
			pagination: { page: 1, pageSize: 20, totalItems: 0, totalPages: 0 }
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve(mockResponse)
			})
		);

		await fetchTracks(2, 20, 'beatles');
		expect(fetch).toHaveBeenCalledWith('/api/v1/tracks?page=2&pageSize=20&search=beatles');
	});

	it('fetchTracks omits search param when undefined', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve({ data: [], pagination: {} })
			})
		);

		await fetchTracks(1, 50, undefined);
		expect(fetch).toHaveBeenCalledWith('/api/v1/tracks?page=1&pageSize=50');
	});

	it('fetchTrackDetail sends correct path', async () => {
		const mockTrack = {
			id: 'abc-123',
			title: 'Test Track',
			duration_seconds: 180,
			file_hash_sha256: 'deadbeef',
			file_size_bytes: 5000000,
			olaf_indexed: true,
			ingested_at: '2026-02-14T10:00:00Z',
			updated_at: '2026-02-14T10:00:00Z'
		};
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: true,
				json: () => Promise.resolve(mockTrack)
			})
		);

		const result = await fetchTrackDetail('abc-123');
		expect(result).toEqual(mockTrack);
		expect(fetch).toHaveBeenCalledWith('/api/v1/tracks/abc-123');
	});

	it('fetchTrackDetail throws on 404', async () => {
		vi.stubGlobal(
			'fetch',
			vi.fn().mockResolvedValue({
				ok: false,
				status: 404,
				statusText: 'Not Found'
			})
		);

		await expect(fetchTrackDetail('nonexistent')).rejects.toThrow('API error: 404 Not Found');
	});
});

// ---------------------------------------------------------------------------
// Formatting tests
// ---------------------------------------------------------------------------

describe('formatDuration', () => {
	it('formats 0 seconds', () => {
		expect(formatDuration(0)).toBe('0:00');
	});

	it('formats seconds less than a minute', () => {
		expect(formatDuration(45)).toBe('0:45');
	});

	it('formats exact minutes', () => {
		expect(formatDuration(120)).toBe('2:00');
	});

	it('formats minutes and seconds', () => {
		expect(formatDuration(355)).toBe('5:55');
	});

	it('pads single-digit seconds', () => {
		expect(formatDuration(62)).toBe('1:02');
	});

	it('handles large values', () => {
		expect(formatDuration(3661)).toBe('61:01');
	});

	it('truncates fractional seconds', () => {
		expect(formatDuration(90.7)).toBe('1:30');
	});
});

describe('formatFileSize', () => {
	it('formats bytes', () => {
		expect(formatFileSize(500)).toBe('500 B');
	});

	it('formats kilobytes', () => {
		expect(formatFileSize(1536)).toBe('1.5 KB');
	});

	it('formats megabytes', () => {
		expect(formatFileSize(13_842_000)).toBe('13.2 MB');
	});

	it('formats gigabytes', () => {
		expect(formatFileSize(2_147_483_648)).toBe('2.00 GB');
	});

	it('formats zero', () => {
		expect(formatFileSize(0)).toBe('0 B');
	});
});

describe('formatBitrate', () => {
	it('formats null as Unknown', () => {
		expect(formatBitrate(null)).toBe('Unknown');
	});

	it('formats undefined as Unknown', () => {
		expect(formatBitrate(undefined)).toBe('Unknown');
	});

	it('formats kbps value directly', () => {
		expect(formatBitrate(320)).toBe('320 kbps');
	});

	it('converts large bps to kbps', () => {
		expect(formatBitrate(320000)).toBe('320 kbps');
	});
});

describe('formatSampleRate', () => {
	it('formats null as Unknown', () => {
		expect(formatSampleRate(null)).toBe('Unknown');
	});

	it('formats Hz value', () => {
		expect(formatSampleRate(44100)).toBe('44100 Hz');
	});
});

describe('formatChannels', () => {
	it('formats null as Unknown', () => {
		expect(formatChannels(null)).toBe('Unknown');
	});

	it('formats 1 as mono', () => {
		expect(formatChannels(1)).toBe('1 (mono)');
	});

	it('formats 2 as stereo', () => {
		expect(formatChannels(2)).toBe('2 (stereo)');
	});

	it('formats other values as number', () => {
		expect(formatChannels(6)).toBe('6');
	});
});

describe('formatDate', () => {
	it('formats ISO date string', () => {
		const result = formatDate('2026-02-14T10:30:00Z');
		// Check it contains the expected parts (timezone-agnostic)
		expect(result).toContain('Feb');
		expect(result).toContain('14');
		expect(result).toContain('2026');
	});
});
