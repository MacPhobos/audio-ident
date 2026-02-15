/**
 * Auto-generated types from OpenAPI schema.
 *
 * Regenerate with: pnpm gen:api
 *
 * This file is a placeholder. Once the backend is running, run the gen:api
 * script to replace this with real types generated from the OpenAPI spec.
 */

export interface paths {
	'/health': {
		get: {
			responses: {
				200: {
					content: {
						'application/json': components['schemas']['HealthResponse'];
					};
				};
			};
		};
	};
	'/api/v1/version': {
		get: {
			responses: {
				200: {
					content: {
						'application/json': components['schemas']['VersionResponse'];
					};
				};
			};
		};
	};
}

export interface components {
	schemas: {
		HealthResponse: {
			status: string;
			version: string;
		};
		VersionResponse: {
			name: string;
			version: string;
			git_sha: string;
			build_time: string;
		};
	};
}

// ---------------------------------------------------------------------------
// Search types — placeholder until `make gen-client` regenerates from OpenAPI.
// These match the Pydantic schemas in audio-ident-service/app/schemas/search.py
// ---------------------------------------------------------------------------

export type SearchMode = 'exact' | 'vibe' | 'both';

export interface TrackInfo {
	id: string;
	title: string;
	artist: string | null;
	album: string | null;
	duration_seconds: number;
	ingested_at: string;
}

export interface ExactMatch {
	track: TrackInfo;
	confidence: number;
	offset_seconds: number | null;
	aligned_hashes: number;
}

export interface VibeMatch {
	track: TrackInfo;
	similarity: number;
	embedding_model: string;
}

export interface SearchResponse {
	request_id: string;
	query_duration_ms: number;
	exact_matches: ExactMatch[];
	vibe_matches: VibeMatch[];
	mode_used: SearchMode;
}
