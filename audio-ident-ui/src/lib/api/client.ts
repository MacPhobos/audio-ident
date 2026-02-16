import type { components } from './generated';

export type HealthResponse = components['schemas']['HealthResponse'];
export type VersionResponse = components['schemas']['VersionResponse'];
export type SearchMode = components['schemas']['SearchMode'];
export type SearchResponse = components['schemas']['SearchResponse'];
export type TrackInfo = components['schemas']['TrackInfo'];
export type TrackDetail = components['schemas']['TrackDetail'];
export type PaginatedTrackResponse = components['schemas']['PaginatedResponse_TrackInfo_'];
export type PaginationMeta = components['schemas']['PaginationMeta'];
export type IngestResponse = components['schemas']['IngestResponse'];

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '';

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export interface ApiError {
	code: string;
	message: string;
	status: number;
}

export class ApiRequestError extends Error {
	public readonly code: string;
	public readonly status: number;
	public readonly body?: { error?: { code?: string; message?: string; details?: unknown } };

	constructor(
		error: ApiError,
		body?: { error?: { code?: string; message?: string; details?: unknown } }
	) {
		super(error.message);
		this.name = 'ApiRequestError';
		this.code = error.code;
		this.status = error.status;
		this.body = body;
	}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async function fetchJSON<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE_URL}${path}`);
	if (!res.ok) {
		let apiError: ApiError = {
			code: 'UNKNOWN',
			message: `API error: ${res.status} ${res.statusText}`,
			status: res.status
		};
		let rawBody: { error?: { code?: string; message?: string; details?: unknown } } | undefined;

		try {
			const body = await res.json();
			rawBody = body;
			if (body?.error) {
				apiError = {
					code: body.error.code ?? 'UNKNOWN',
					message: body.error.message ?? apiError.message,
					status: res.status
				};
			} else if (body?.detail) {
				const msg = Array.isArray(body.detail)
					? body.detail.map((d: Record<string, unknown>) => d.msg).join('; ')
					: String(body.detail);
				apiError = { code: 'VALIDATION_ERROR', message: msg, status: res.status };
			}
		} catch {
			// Use the default error built above
		}

		throw new ApiRequestError(apiError, rawBody);
	}
	return res.json() as Promise<T>;
}

/** Map a MIME type to a file extension for multipart uploads. */
function mimeToExtension(mime: string): string {
	const map: Record<string, string> = {
		'audio/webm': 'webm',
		'audio/webm;codecs=opus': 'webm',
		'video/webm': 'webm',
		'audio/ogg': 'ogg',
		'audio/ogg;codecs=opus': 'ogg',
		'audio/mpeg': 'mp3',
		'audio/mp3': 'mp3',
		'audio/mp4': 'mp4',
		'audio/mp4;codecs=aac': 'mp4',
		'audio/x-m4a': 'm4a',
		'audio/wav': 'wav',
		'audio/x-wav': 'wav',
		'audio/flac': 'flac',
		'audio/x-flac': 'flac'
	};

	// Try exact match first, then try stripping codec params
	const ext = map[mime] ?? map[mime.split(';')[0]];
	return ext ?? 'bin';
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Build the URL for streaming a track's audio file.
 * Returns a URL string (not a fetch call) for use as an `<audio>` element `src`.
 */
export function trackAudioUrl(trackId: number | string): string {
	return `${BASE_URL}/api/v1/tracks/${trackId}/audio`;
}

export function fetchHealth(): Promise<HealthResponse> {
	return fetchJSON<HealthResponse>('/health');
}

export function fetchVersion(): Promise<VersionResponse> {
	return fetchJSON<VersionResponse>('/api/v1/version');
}

export function fetchTracks(
	page: number = 1,
	pageSize: number = 50,
	search?: string
): Promise<PaginatedTrackResponse> {
	const params = new URLSearchParams({
		page: String(page),
		pageSize: String(pageSize)
	});
	if (search) params.set('search', search);
	return fetchJSON<PaginatedTrackResponse>(`/api/v1/tracks?${params}`);
}

export function fetchTrackDetail(id: string): Promise<TrackDetail> {
	return fetchJSON<TrackDetail>(`/api/v1/tracks/${id}`);
}

export async function searchAudio(
	blob: Blob,
	mode: SearchMode,
	maxResults: number = 10,
	signal?: AbortSignal
): Promise<SearchResponse> {
	const ext = mimeToExtension(blob.type);
	const filename = `query.${ext}`;

	const form = new FormData();
	form.append('audio', blob, filename);
	form.append('mode', mode);
	form.append('max_results', String(maxResults));

	const res = await fetch(`${BASE_URL}/api/v1/search`, {
		method: 'POST',
		body: form,
		signal
	});

	if (!res.ok) {
		let apiError: ApiError = {
			code: 'UNKNOWN',
			message: `Search failed: ${res.status} ${res.statusText}`,
			status: res.status
		};

		try {
			const body = await res.json();
			if (body?.error) {
				apiError = {
					code: body.error.code ?? 'UNKNOWN',
					message: body.error.message ?? apiError.message,
					status: res.status
				};
			} else if (body?.detail) {
				const msg = Array.isArray(body.detail)
					? body.detail.map((d: Record<string, unknown>) => d.msg).join('; ')
					: String(body.detail);
				apiError = { code: 'VALIDATION_ERROR', message: msg, status: res.status };
			}
		} catch {
			// Use the default error built above
		}

		throw new ApiRequestError(apiError);
	}

	return res.json() as Promise<SearchResponse>;
}

export async function ingestAudio(
	file: File,
	adminKey: string,
	signal?: AbortSignal
): Promise<IngestResponse> {
	const form = new FormData();
	form.append('audio', file, file.name);

	const res = await fetch(`${BASE_URL}/api/v1/ingest`, {
		method: 'POST',
		body: form,
		headers: {
			'X-Admin-Key': adminKey
		},
		signal
	});

	if (!res.ok) {
		let apiError: ApiError = {
			code: 'UNKNOWN',
			message: `Ingest failed: ${res.status} ${res.statusText}`,
			status: res.status
		};

		try {
			const body = await res.json();
			if (body?.error) {
				apiError = {
					code: body.error.code ?? 'UNKNOWN',
					message: body.error.message ?? apiError.message,
					status: res.status
				};
			} else if (body?.detail) {
				const msg = Array.isArray(body.detail)
					? body.detail.map((d: Record<string, unknown>) => d.msg).join('; ')
					: typeof body.detail === 'object'
						? (body.detail.error?.message ?? JSON.stringify(body.detail))
						: String(body.detail);
				apiError = { code: 'VALIDATION_ERROR', message: msg, status: res.status };
			}
		} catch {
			// Use the default error built above
		}

		throw new ApiRequestError(apiError);
	}

	return res.json() as Promise<IngestResponse>;
}
